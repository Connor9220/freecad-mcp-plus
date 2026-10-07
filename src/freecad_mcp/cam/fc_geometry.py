# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Find faces and edges of a FreeCAD object by geometric rules.

This module runs INSIDE FreeCAD (GUI through the bridge, or headless FreeCADCmd).
It must not import anything from freecad_mcp. The MCP tool sends its source and
calls :func:`find_geometry` with plain arguments; the result is JSON-safe and
uses string keys only (XML-RPC cannot marshal other keys).

:func:`select_elements` is the rule engine; fc_fixture.py reuses it to resolve
an operation's Base geometry, so it is sent together with this source.

Coordinates are those of ``obj.Shape`` (placement applied), so a CAM job's
model clone reports where the job actually sees the geometry.
"""

import math

import FreeCAD

EPS = 1e-9


def _r(v, n=4):
    return None if v is None else round(float(v), n)


def _xyz(v, n=4):
    return [_r(v.x, n), _r(v.y, n), _r(v.z, n)]


def _vec(v):
    if v is None:
        return None
    vec = FreeCAD.Vector(*[float(c) for c in v])
    if vec.Length < EPS:
        raise ValueError(f"direction {list(v)} has zero length")
    return vec.normalize()


def find_object(doc, name):
    """Object by Name, falling back to Label (a common mix-up)."""
    obj = doc.getObject(name)
    if obj is None:
        hits = doc.getObjectsByLabel(name)
        obj = hits[0] if hits else None
    return obj


def get_document(doc_name=None):
    """Document by name, or the active one."""
    doc = FreeCAD.getDocument(doc_name) if doc_name else FreeCAD.ActiveDocument
    if doc is None:
        raise ValueError(
            "no active document" if not doc_name else f"no document {doc_name!r}"
        )
    return doc


def _type_name(geom):
    return type(geom).__name__ if geom is not None else None


def _angle_deg(a, b):
    c = max(-1.0, min(1.0, a.dot(b)))
    return math.degrees(math.acos(c))


def _mid_params(face):
    u0, u1, v0, v1 = face.ParameterRange
    return (u0 + u1) / 2.0, (v0 + v1) / 2.0


def face_normal(face):
    """Outward normal of a planar face (face orientation respected), else None."""
    if _type_name(face.Surface) != "Plane":
        return None
    u, v = _mid_params(face)
    n = face.normalAt(u, v)
    return n.normalize() if n.Length > EPS else None


def _surface_axis(surf):
    axis = getattr(surf, "Axis", None)
    return axis.normalize() if axis is not None and axis.Length > EPS else None


def face_info(name, face, ztol=1e-3):
    """JSON-safe description of a face."""
    surf = face.Surface
    bb = face.BoundBox
    info = {
        "name": name,
        "surface": _type_name(surf),
        "area": _r(face.Area),
        "center": _xyz(face.CenterOfMass),
        "z_min": _r(bb.ZMin),
        "z_max": _r(bb.ZMax),
        "horizontal": bool(bb.ZLength <= ztol),
    }
    n = face_normal(face)
    if n is not None:
        info["normal"] = _xyz(n, 6)
    radius = getattr(surf, "Radius", None)
    if radius is not None and not callable(radius):
        info["radius"] = _r(radius)
    axis = _surface_axis(surf) if info["surface"] != "Plane" else None
    if axis is not None:
        info["axis"] = _xyz(axis, 6)
    if info["surface"] == "Cylinder" and axis is not None:
        # hole (concave) vs boss: does the face normal point toward the axis?
        u, v = _mid_params(face)
        p = face.valueAt(u, v)
        rel = p - surf.Center
        radial = rel - axis * rel.dot(axis)
        info["hole"] = bool(face.normalAt(u, v).dot(radial) < 0)
        info["axis_point"] = _xyz(surf.Center)
    return info


def edge_info(name, edge):
    """JSON-safe description of an edge."""
    curve = getattr(edge, "Curve", None)
    bb = edge.BoundBox
    info = {
        "name": name,
        "curve": _type_name(curve),
        "length": _r(edge.Length),
        "closed": bool(edge.isClosed()),
        "start": _xyz(edge.Vertexes[0].Point) if edge.Vertexes else None,
        "end": _xyz(edge.Vertexes[-1].Point) if edge.Vertexes else None,
        "z_min": _r(bb.ZMin),
        "z_max": _r(bb.ZMax),
    }
    radius = getattr(curve, "Radius", None)
    if radius is not None and not callable(radius):
        info["radius"] = _r(radius)
        info["center"] = _xyz(curve.Center)
    axis = getattr(curve, "Axis", None)
    if axis is not None and axis.Length > EPS:
        info["axis"] = _xyz(axis.normalize(), 6)
    return info


def _index_of(elements, element):
    for i, e in enumerate(elements):
        if e.isSame(element):
            return i
    return None


def _face_boundary(shape, of_face):
    """[(index, wire_index)] of a face's boundary edges, outer wire first."""
    face = shape.getElement(of_face)
    outer = face.OuterWire
    wires = [outer] + [w for w in face.Wires if not w.isSame(outer)]
    out = []
    for wi, wire in enumerate(wires):
        for e in wire.OrderedEdges if hasattr(wire, "OrderedEdges") else wire.Edges:
            idx = _index_of(shape.Edges, e)
            if idx is not None and idx not in [o[0] for o in out]:
                out.append((idx, wi))
    return out


def select_elements(
    shape,
    kind="face",
    normal=None,
    normal_tol_deg=1.0,
    z=None,
    z_tol=1e-3,
    surface=None,
    radius=None,
    radius_tol=1e-3,
    axis=None,
    top_only=False,
    largest=False,
    of_face=None,
):
    """Info dicts of the sub-elements of ``shape`` matching every given rule."""
    kind = (kind or "face").lower().rstrip("s")
    if kind not in ("face", "edge"):
        raise ValueError(f"kind must be 'face' or 'edge', not {kind!r}")
    if of_face and kind != "edge":
        raise ValueError("of_face returns boundary edges: use kind='edge'")
    if normal is not None and kind != "face":
        raise ValueError("normal applies to faces (use axis for circle edges)")
    nvec = _vec(normal)
    avec = _vec(axis)
    stype = surface.lower() if surface else None

    if kind == "face":
        candidates = [(i, None) for i in range(len(shape.Faces))]
    elif of_face:
        candidates = _face_boundary(shape, of_face)
    else:
        candidates = [(i, None) for i in range(len(shape.Edges))]

    matches = []
    for i, wire_index in candidates:
        if kind == "face":
            elem = shape.Faces[i]
            info = face_info(f"Face{i + 1}", elem, z_tol)
            size = elem.Area
            etype = info["surface"]
        else:
            elem = shape.Edges[i]
            info = edge_info(f"Edge{i + 1}", elem)
            if wire_index is not None:
                info["wire"] = wire_index
            size = elem.Length
            etype = info["curve"]
        if stype and (etype or "").lower() != stype:
            continue
        if nvec is not None:
            n = face_normal(elem)
            if n is None or _angle_deg(n, nvec) > normal_tol_deg:
                continue
        if z is not None:
            bb = elem.BoundBox
            if abs(bb.ZMin - z) > z_tol or abs(bb.ZMax - z) > z_tol:
                continue
            if kind == "face" and info["surface"] != "Plane":
                continue
        if radius is not None:
            r = info.get("radius")
            if r is None or abs(r - radius) > radius_tol:
                continue
        if avec is not None:
            ax = info.get("axis")
            if ax is None:
                continue
            ang = _angle_deg(FreeCAD.Vector(*ax), avec)
            if min(ang, 180.0 - ang) > normal_tol_deg:
                continue
        matches.append((size, elem.BoundBox.ZMax, info))

    if top_only and matches:
        top = max(m[1] for m in matches)
        matches = [m for m in matches if m[1] >= top - z_tol]
    if largest and matches:
        matches = [max(matches, key=lambda m: m[0])]
    return [m[2] for m in matches]


def find_geometry(
    doc_name,
    object_name,
    kind="face",
    normal=None,
    normal_tol_deg=1.0,
    z=None,
    z_tol=1e-3,
    surface=None,
    radius=None,
    radius_tol=1e-3,
    axis=None,
    top_only=False,
    largest=False,
    of_face=None,
    limit=50,
):
    """Matching sub-elements of an object; see the cam_find_geometry tool."""
    doc = get_document(doc_name)
    obj = find_object(doc, object_name)
    if obj is None:
        raise ValueError(f"no object {object_name!r} in document {doc.Name!r}")
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        raise ValueError(f"object {obj.Name!r} has no shape")
    found = select_elements(
        shape,
        kind=kind,
        normal=normal,
        normal_tol_deg=normal_tol_deg,
        z=z,
        z_tol=z_tol,
        surface=surface,
        radius=radius,
        radius_tol=radius_tol,
        axis=axis,
        top_only=top_only,
        largest=largest,
        of_face=of_face,
    )
    bb = shape.BoundBox
    is_face = (kind or "face").lower().startswith("face")
    return {
        "document": doc.Name,
        "object": obj.Name,
        "label": obj.Label,
        "kind": "face" if is_face else "edge",
        "total": len(shape.Faces) if is_face else len(shape.Edges),
        "matched": len(found),
        "names": [f["name"] for f in found],
        "elements": found[: max(0, int(limit))] if limit else found,
        "truncated": bool(limit) and len(found) > int(limit),
        "bbox": {
            "min": [_r(bb.XMin), _r(bb.YMin), _r(bb.ZMin)],
            "max": [_r(bb.XMax), _r(bb.YMax), _r(bb.ZMax)],
        },
    }
