# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Gouge and coverage checks for FreeCAD CAM toolpaths.

This module runs INSIDE FreeCAD (GUI through the bridge, or headless FreeCADCmd).
It must not import anything from freecad_mcp. The MCP tools send its source and
call :func:`gouge_check` or :func:`coverage` with plain arguments; results are
JSON-safe with string keys only (XML-RPC cannot marshal other keys).

Both checks model the tool as a FLAT end mill of the tool controller's diameter
and read the path in the frame it is stored in (the job frame, like the model
clones). G17 arcs only; canned cycles are not walked.

Memory safety: shapes are built per move and dropped straight away. Nothing is
fused except the three primitives of one candidate move, the model solids are
checked one by one, and both checks stop at a hard cap (reported as "capped").
"""

import math
import time

import FreeCAD
import Part

RAPID = {"G0", "G00"}
LINE = {"G1", "G01"}
ARC = {"G2", "G02", "G3", "G03"}
FEED = LINE | ARC
EPS = 1e-6
MAX_SEGMENTS = 20000  # per object, gouge check (arcs count per chord)
MAX_CHORDS_PER_ARC = 360
TOUCH = 1e-6  # distToShape below this means "touching or overlapping"
MIN_VOLUME = 1e-6  # mm^3; smaller common volumes are numerical noise


def _r(v, n=3):
    return None if v is None else round(float(v), n)


def _xyz(v, n=3):
    return [_r(v[0], n), _r(v[1], n), _r(v[2], n)]


def find_object(doc, name):
    """Object by Name, falling back to Label (a common mix-up)."""
    obj = doc.getObject(name)
    if obj is None:
        hits = doc.getObjectsByLabel(name)
        obj = hits[0] if hits else None
    return obj


def cam_operations(doc):
    """Objects that carry a toolpath: operations and dressups, excluding the Job."""
    out = []
    for o in doc.Objects:
        if not hasattr(o, "Path") or o.TypeId.startswith("Path::FeatureCompound"):
            continue
        if hasattr(o, "Operations") and hasattr(o, "Tools"):  # the Job itself
            continue
        if hasattr(o, "SpindleSpeed") and hasattr(o, "HorizFeed"):  # a tool controller
            continue
        if hasattr(o, "Proxy") and o.Proxy is not None:
            out.append(o)
    return out


def _get_doc(doc_name):
    doc = FreeCAD.getDocument(doc_name) if doc_name else FreeCAD.ActiveDocument
    if doc is None:
        raise ValueError(
            "no document open" if not doc_name else f"no document {doc_name!r}"
        )
    return doc


def _recompute_objs(doc, objs):
    # A plain recompute does not re-run an op whose inputs did not change.
    for o in objs:
        o.touch()
    doc.recompute()


def _state(obj):
    state = list(obj.State)
    return {
        "stale": bool({"Touched", "Invalid", "Error"} & set(state)),
        "error": (obj.getStatusString() or None) if "Invalid" in state else None,
    }


def _is_linking(cmd):
    try:
        return "linking" in str(cmd.Annotations).lower()
    except Exception:
        return False


def _base_op(obj):
    """The operation under a chain of dressups (dressups keep it in Base)."""
    seen = set()
    while obj is not None and obj.Name not in seen:
        seen.add(obj.Name)
        base = getattr(obj, "Base", None)
        if isinstance(base, FreeCAD.DocumentObject) and hasattr(base, "Path"):
            obj = base
        else:
            break
    return obj


def _tool_controller(obj):
    seen = set()
    while obj is not None and obj.Name not in seen:
        seen.add(obj.Name)
        tc = getattr(obj, "ToolController", None)
        if tc is not None:
            return tc
        base = getattr(obj, "Base", None)
        obj = base if isinstance(base, FreeCAD.DocumentObject) else None
    return None


def _tool_info(obj):
    tc = _tool_controller(obj)
    tool = getattr(tc, "Tool", None) if tc is not None else None
    if tool is None:
        return None
    dia = getattr(tool, "Diameter", None)
    dia = float(getattr(dia, "Value", dia)) if dia is not None else None
    shape = ""
    for prop in ("ShapeType", "ShapeID", "ShapeName"):
        shape = str(getattr(tool, prop, "") or "")
        if shape:
            break
    low = shape.lower()
    flat = ("endmill" in low or "end mill" in low) and "ball" not in low
    return {
        "controller": tc.Label,
        "diameter": dia,
        "radius": dia / 2.0 if dia else None,
        "shape": shape or None,
        "flat": flat,
    }


def _find_job(obj):
    try:
        import PathScripts.PathUtils as PathUtils

        job = PathUtils.findParentJob(obj)
        if job is not None:
            return job
    except Exception:
        pass
    for o in obj.Document.Objects:  # fallback: the single Job in the document
        if hasattr(o, "Operations") and hasattr(o, "Tools") and hasattr(o, "Model"):
            return o
    return None


def _model_solids(job):
    solids = []
    for m in job.Model.Group if job is not None else []:
        shape = getattr(m, "Shape", None)
        if shape is None or shape.isNull():
            continue
        solids.extend(shape.Solids)
    return solids


# ---------------------------------------------------------------- move walking


def _arc_points(p0, p1, params, clockwise, chord_tol):
    """XYZ chord vertices (excluding p0) of a G17 arc from p0 to p1."""
    cx, cy = p0[0] + params.get("I", 0.0), p0[1] + params.get("J", 0.0)
    rad = math.hypot(p0[0] - cx, p0[1] - cy)
    a0 = math.atan2(p0[1] - cy, p0[0] - cx)
    a1 = math.atan2(p1[1] - cy, p1[0] - cx)
    sweep = a1 - a0
    if clockwise:
        if sweep >= -EPS:
            sweep -= 2 * math.pi
    elif sweep <= EPS:
        sweep += 2 * math.pi
    if rad < EPS:
        return [p1]
    ratio = max(-1.0, min(1.0, 1.0 - chord_tol / rad))
    step = 2.0 * math.acos(ratio) if ratio < 1.0 else math.pi / 18
    step = max(step, 2 * math.pi / 720)
    n = int(min(MAX_CHORDS_PER_ARC, max(1, math.ceil(abs(sweep) / step))))
    pts = []
    for k in range(1, n + 1):
        t = k / n
        a = a0 + sweep * t
        pts.append(
            (
                cx + rad * math.cos(a),
                cy + rad * math.sin(a),
                p0[2] + (p1[2] - p0[2]) * t,
            )
        )
    pts[-1] = p1  # land exactly on the programmed end point
    return pts


def walk_moves(path, chord_tol=0.005):
    """Yield (index, gcode_name, cmd, start, end, [(a, b) chords]) per move.

    Positions are modal. The first positioning command only sets the start.
    """
    pos = None
    for i, c in enumerate(path.Commands):
        name = c.Name.upper()
        if name not in RAPID and name not in FEED:
            continue
        p = c.Parameters
        if pos is None:
            pos = (p.get("X", 0.0), p.get("Y", 0.0), p.get("Z", 0.0))
            continue
        end = (p.get("X", pos[0]), p.get("Y", pos[1]), p.get("Z", pos[2]))
        if name in ARC:
            pts = _arc_points(pos, end, p, name in ("G2", "G02"), chord_tol)
            chords = []
            a = pos
            for b in pts:
                chords.append((a, b))
                a = b
        else:
            chords = [(pos, end)]
        yield i, name, c, pos, end, chords
        pos = end


def _category(name, cmd, start, end, safe_z):
    if name in RAPID:
        return "rapids"
    if _is_linking(cmd):
        return "links"
    dxy = math.hypot(end[0] - start[0], end[1] - start[1])
    if dxy < EPS and end[2] > start[2] + EPS:  # straight retract at feed
        return "links"
    if safe_z is not None and min(start[2], end[2]) >= safe_z - 1e-3:
        return "links"  # feed traverse at/above Safe Height
    return "cuts"


# ---------------------------------------------------------------- gouge check


def _swept_parts(a, b, r, top_z):
    """Primitives whose union is the flat tool swept from a to b (bottoms at tip z)."""
    parts = []
    for p in (a, b) if (a != b) else (a,):
        h = top_z - p[2]
        if h > EPS:
            parts.append(Part.makeCylinder(r, h, FreeCAD.Vector(p[0], p[1], p[2])))
    dx, dy = b[0] - a[0], b[1] - a[1]
    dxy = math.hypot(dx, dy)
    if dxy > EPS:
        nx, ny = -dy / dxy * r, dx / dxy * r  # perpendicular, length r
        z0 = a[2]
        # top raised so the far end still exits the model on a descending move
        z1 = top_z + max(0.0, a[2] - b[2])
        # the tool's vertical cross-section at a, swept along a->b
        quad = Part.makePolygon(
            [
                FreeCAD.Vector(a[0] + nx, a[1] + ny, z0),
                FreeCAD.Vector(a[0] - nx, a[1] - ny, z0),
                FreeCAD.Vector(a[0] - nx, a[1] - ny, z1),
                FreeCAD.Vector(a[0] + nx, a[1] + ny, z1),
                FreeCAD.Vector(a[0] + nx, a[1] + ny, z0),
            ]
        )
        vec = FreeCAD.Vector(dx, dy, b[2] - a[2])
        parts.append(Part.Face(quad).extrude(vec))
    return parts


def _bbox_hit(xmin, xmax, ymin, ymax, zmin, bb):
    return (
        not (xmax < bb.XMin or xmin > bb.XMax or ymax < bb.YMin or ymin > bb.YMax)
        and zmin < bb.ZMax
    )


def _check_segment(a, b, r_eff, lift, solids, zmax_model):
    """((volume, overlap bbox) or None, needed_boolean) of the swept tool vs the model."""
    a = (a[0], a[1], a[2] + lift)
    b = (b[0], b[1], b[2] + lift)
    xmin, xmax = min(a[0], b[0]) - r_eff, max(a[0], b[0]) + r_eff
    ymin, ymax = min(a[1], b[1]) - r_eff, max(a[1], b[1]) + r_eff
    zmin = min(a[2], b[2])
    near = [s for s in solids if _bbox_hit(xmin, xmax, ymin, ymax, zmin, s.BoundBox)]
    if not near:
        return None, False
    top_z = max(zmax_model, a[2], b[2]) + 1.0  # the shank always exits the model
    parts = _swept_parts(a, b, r_eff, top_z)
    if not parts:
        return None, False
    touching = []
    for s in near:
        for prim in parts:
            if prim.distToShape(s)[0] < TOUCH:
                touching.append(s)
                break
    if not touching:
        return None, False
    swept = parts[0] if len(parts) == 1 else parts[0].fuse(parts[1:])
    vol = 0.0
    bb = None
    for s in touching:
        common = swept.common(s)
        v = common.Volume
        if v > MIN_VOLUME:
            vol += v
            cb = common.BoundBox
            if bb is None:
                bb = cb
            else:
                bb.add(cb)
    if vol <= MIN_VOLUME:
        return None, True
    return (vol, bb), True


def _gouge_one(
    obj, solids, zmax_model, want, tolerance, clearance, max_reports, budget
):
    info = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
    info.update(_state(obj))
    tool = _tool_info(obj)
    info["tool"] = tool
    cats = {k: {"moves": 0, "segments": 0, "candidates": 0, "gouges": 0} for k in want}
    info["categories"] = cats
    info["gouges"] = []
    info["capped"] = False
    info["notes"] = []
    if tool is None or not tool.get("radius"):
        info["notes"].append("no tool controller/diameter: not checked")
        return info
    if not getattr(obj, "Active", True):
        info["notes"].append("operation is inactive (checked anyway)")
    check = set(want)
    if not tool["flat"] and "cuts" in check:
        check.discard("cuts")
        info["notes"].append(
            f"tool shape {tool['shape']!r} is not a flat end mill: cutting moves "
            "skipped (the flat model would report false gouges)"
        )
    r_eff = tool["radius"] - tolerance - clearance
    if r_eff <= 0:
        info["notes"].append("radius minus tolerance/clearance <= 0: not checked")
        return info
    lift = tolerance
    safe = getattr(obj, "SafeHeight", None)
    safe_z = float(getattr(safe, "Value", safe)) if safe is not None else None
    segments = 0
    t0 = time.time()
    for i, name, cmd, start, end, chords in walk_moves(obj.Path, tolerance / 2.0):
        cat = _category(name, cmd, start, end, safe_z)
        if cat not in check:
            continue
        stats = cats[cat]
        stats["moves"] += 1
        worst = None
        for a, b in chords:
            if segments >= MAX_SEGMENTS or time.time() - t0 > budget:
                info["capped"] = True
                break
            segments += 1
            stats["segments"] += 1
            hit, cand = _check_segment(a, b, r_eff, lift, solids, zmax_model)
            if cand:
                stats["candidates"] += 1
            if hit is not None and (worst is None or hit[0] > worst[0]):
                worst = hit
        if worst is not None:
            stats["gouges"] += 1
            if len(info["gouges"]) < max_reports:
                vol, bb = worst
                info["gouges"].append(
                    {
                        "index": i,
                        "category": cat,
                        "gcode": cmd.toGCode(),
                        "start": _xyz(start),
                        "end": _xyz(end),
                        "overlap_volume": _r(vol, 4),
                        "depth_est": _r(min(bb.XLength, bb.YLength, bb.ZLength), 4),
                        "overlap_bbox": {
                            "min": _xyz((bb.XMin, bb.YMin, bb.ZMin)),
                            "max": _xyz((bb.XMax, bb.YMax, bb.ZMax)),
                        },
                    }
                )
        if info["capped"]:
            break
    for k in want:
        if k not in check:
            cats[k]["skipped"] = True
    info["seconds"] = _r(time.time() - t0, 2)
    return info


def gouge_check(
    object_name="*",
    doc_name=None,
    against="model",
    moves="all",
    clearance=0.0,
    tolerance=0.01,
    recompute=True,
    max_reports=20,
    budget_s=240.0,
):
    """Entry point of cam_gouge_check; see the MCP tool docstring."""
    if against != "model":
        raise ValueError("only against='model' is supported")
    kinds = ("rapids", "links", "cuts")
    want = list(kinds) if moves == "all" else [m.strip() for m in moves.split(",")]
    bad = [m for m in want if m not in kinds]
    if bad:
        raise ValueError(f"moves must be 'all' or a subset of {kinds}, not {bad}")
    doc = _get_doc(doc_name)
    if object_name == "*":
        objs = cam_operations(doc)
    else:
        obj = find_object(doc, object_name)
        if obj is None:
            raise ValueError(
                f"no object named or labelled {object_name!r} in {doc.Name}"
            )
        objs = [obj]
    if recompute and objs:
        _recompute_objs(doc, objs)
    t0 = time.time()
    results = []
    capped = False
    for obj in objs:
        job = _find_job(obj)
        solids = _model_solids(job)
        if not solids:
            results.append(
                {"name": obj.Name, "label": obj.Label, "notes": ["no model solids"]}
            )
            continue
        zmax = max(s.BoundBox.ZMax for s in solids)
        left = budget_s - (time.time() - t0)
        if left <= 0:
            capped = True
            results.append(
                {
                    "name": obj.Name,
                    "label": obj.Label,
                    "capped": True,
                    "notes": ["time budget used up"],
                }
            )
            continue
        res = _gouge_one(
            obj, solids, zmax, want, tolerance, clearance, max_reports, left
        )
        capped = capped or res.get("capped", False)
        results.append(res)
    totals = dict.fromkeys(kinds, 0)
    stale = []
    for res in results:
        for k, st in res.get("categories", {}).items():
            totals[k] += st["gouges"]
        if res.get("stale"):
            stale.append(res["name"])
    n = sum(totals.values())
    if n:
        verdict = "GOUGES: " + ", ".join(f"{v} {k}" for k, v in totals.items() if v)
    else:
        verdict = "no gouges found"
    if stale:
        verdict += f" (CAUTION: stale path on {', '.join(stale)})"
    if capped:
        verdict += " (CAPPED: not every move was checked)"
    return {
        "document": doc.Name,
        "verdict": verdict,
        "gouge_totals": totals,
        "tolerance": tolerance,
        "clearance": clearance,
        "capped": capped,
        "seconds": _r(time.time() - t0, 2),
        "objects": results,
    }


# ---------------------------------------------------------------- coverage


def _is_horizontal_plane(face):
    if type(face.Surface).__name__ != "Plane":
        return False
    bb = face.BoundBox
    return bb.ZLength < 1e-6


def _parse_face(job, doc, face):
    """(object, face) from "Face12" (first model clone) or "Object:Face12"."""
    if ":" in face:
        oname, sub = face.split(":", 1)
        obj = find_object(doc, oname.strip())
        if obj is None:
            raise ValueError(f"no object {oname!r}")
    else:
        group = list(job.Model.Group) if job is not None else []
        if not group:
            raise ValueError("job has no model clone to take the face from")
        obj, sub = group[0], face
    return obj, obj.Shape.getElement(sub.strip())


def _stock_top(job):
    stock = getattr(job, "Stock", None)
    if stock is None or stock.Shape.isNull():
        raise ValueError("job has no stock")
    faces = [f for f in stock.Shape.Faces if _is_horizontal_plane(f)]
    if not faces:
        raise ValueError("stock has no horizontal planar face")
    return max(faces, key=lambda f: (round(f.BoundBox.ZMax, 6), f.Area))


def _region_faces(obj, job, doc, region, face):
    """[(label, Part.Face)] of the target region."""
    if region == "face" or (face and region == "auto"):
        if not face:
            raise ValueError("region='face' needs face='Face12' or 'Object:Face12'")
        o, f = _parse_face(job, doc, face)
        if not _is_horizontal_plane(f):
            raise ValueError(f"{face} is not a horizontal planar face")
        return [(f"{o.Name}:{face.split(':')[-1]}", f)]
    if region == "stock_top":
        return [("Stock:top", _stock_top(job))]
    if region != "auto":
        raise ValueError("region must be 'auto', 'face' or 'stock_top'")
    op = _base_op(obj)
    out = []
    for base, subs in getattr(op, "Base", None) or []:
        for sub in subs:
            if not sub.startswith("Face"):
                continue
            f = base.Shape.getElement(sub)
            if _is_horizontal_plane(f):
                out.append((f"{base.Name}:{sub}", f))
    if out:
        return out
    proxy = type(getattr(op, "Proxy", None)).__name__.lower()
    if "face" in proxy or "facing" in op.TypeId.lower():
        return [("Stock:top", _stock_top(job))]
    raise ValueError(
        f"{obj.Name}: no horizontal planar Base faces for region='auto'; "
        "pass region='face' with face=..., or region='stock_top'"
    )


def _wire_polys(face, step):
    """Closed 2D rings (numpy Nx2) of every wire of a horizontal face."""
    import numpy as np

    rings = []
    for w in face.Wires:
        pts = w.discretize(Distance=step) if w.Length > step * 4 else w.discretize(8)
        # a circular hole needs more than Distance on short wires
        if len(pts) < 8:
            pts = w.discretize(16)
        arr = np.array([(p.x, p.y) for p in pts], dtype=float)
        if len(arr) >= 3:
            rings.append(arr)
    return rings


def _inside(px, py, rings):
    """Even-odd point-in-polygon for all points against all rings (holes too)."""
    import numpy as np

    inside = np.zeros(px.shape, dtype=bool)
    for ring in rings:
        x1, y1 = ring[:, 0], ring[:, 1]
        x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
        for k in range(len(ring)):
            ya, yb = y1[k], y2[k]
            if ya == yb:
                continue
            cond = (ya > py) != (yb > py)
            if not cond.any():
                continue
            xc = x1[k] + (py - ya) * (x2[k] - x1[k]) / (yb - ya)
            inside ^= cond & (px < xc)
    return inside


def _seg_dist(px, py, ax, ay, bx, by):
    import numpy as np

    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    if ll < 1e-18:
        return np.hypot(px - ax, py - ay)
    t = np.clip(((px - ax) * dx + (py - ay) * dy) / ll, 0.0, 1.0)
    return np.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _wall_segments(rings, face_z, solids, probe=0.05, above=0.2):
    """Boundary segments with model material right outside them (walls)."""
    walls = []
    open_count = 0
    for ring in rings:
        n = len(ring)
        for k in range(n):
            a, b = ring[k], ring[(k + 1) % n]
            dx, dy = b[0] - a[0], b[1] - a[1]
            ln = math.hypot(dx, dy)
            if ln < 1e-9:
                continue
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            wall = False
            # material on either side above the face is a wall (face side is air)
            for sgn in (1.0, -1.0):
                pt = FreeCAD.Vector(
                    mx + sgn * -dy / ln * probe,
                    my + sgn * dx / ln * probe,
                    face_z + above,
                )
                if any(s.isInside(pt, 1e-6, True) for s in solids):
                    wall = True
                    break
            if wall:
                walls.append((a[0], a[1], b[0], b[1]))
            else:
                open_count += 1
    return walls, open_count


def _dilate(mask, radius_cells):
    """Boolean dilation of a 2D mask by a disc, scipy if available."""
    try:
        from scipy import ndimage

        dist = ndimage.distance_transform_edt(~mask)
        return dist <= radius_cells
    except ImportError:
        pass
    out = mask.copy()
    rc = math.floor(radius_cells)
    ny, nx = mask.shape
    for dy in range(-rc, rc + 1):
        for dx in range(-rc, rc + 1):
            if dx * dx + dy * dy > radius_cells * radius_cells:
                continue
            src = mask[max(0, -dy) : ny - max(0, dy), max(0, -dx) : nx - max(0, dx)]
            out[max(0, dy) : ny - max(0, -dy), max(0, dx) : nx - max(0, -dx)] |= src
    return out


def _clusters(mask, grid, x0, y0, limit=20):
    """Largest 4-connected groups of True cells: centroid, area, bbox."""
    import numpy as np

    try:
        from scipy import ndimage

        labels, n = ndimage.label(mask)
        groups = []
        if n:
            idx = np.arange(1, n + 1)
            counts = ndimage.sum_labels(mask, labels, idx)
            order = np.argsort(-counts)[:limit]
            objs = ndimage.find_objects(labels)
            for o in order:
                lab = int(idx[o])
                sl = objs[lab - 1]
                ys, xs = np.nonzero(labels[sl] == lab)
                ys, xs = ys + sl[0].start, xs + sl[1].start
                groups.append((len(xs), xs, ys))
        total = int(n)
    except ImportError:
        seen = np.zeros(mask.shape, dtype=bool)
        groups = []
        ny, nx = mask.shape
        for sy, sx in zip(*np.nonzero(mask), strict=True):
            if seen[sy, sx]:
                continue
            stack = [(sy, sx)]
            seen[sy, sx] = True
            cells = []
            while stack:
                cy, cx = stack.pop()
                cells.append((cy, cx))
                for ny2, nx2 in (
                    (cy + 1, cx),
                    (cy - 1, cx),
                    (cy, cx + 1),
                    (cy, cx - 1),
                ):
                    if (
                        0 <= ny2 < ny
                        and 0 <= nx2 < nx
                        and mask[ny2, nx2]
                        and not seen[ny2, nx2]
                    ):
                        seen[ny2, nx2] = True
                        stack.append((ny2, nx2))
            arr = np.array(cells)
            groups.append((len(cells), arr[:, 1], arr[:, 0]))
        total = len(groups)
        groups.sort(key=lambda g: -g[0])
        groups = groups[:limit]
    out = []
    for count, xs, ys in groups:
        out.append(
            {
                "centroid": [
                    _r(x0 + (float(xs.mean()) + 0.5) * grid),
                    _r(y0 + (float(ys.mean()) + 0.5) * grid),
                ],
                "area": _r(count * grid * grid, 3),
                "points": int(count),
                "bbox": {
                    "min": [_r(x0 + xs.min() * grid), _r(y0 + ys.min() * grid)],
                    "max": [
                        _r(x0 + (xs.max() + 1) * grid),
                        _r(y0 + (ys.max() + 1) * grid),
                    ],
                },
            }
        )
    return out, total


def _cut_segments(obj, target_z, z_tol):
    """XY chords of feed moves at or below target_z + z_tol (ramps clipped)."""
    lim = target_z + z_tol
    segs = []
    for _i, name, _cmd, _s, _e, chords in walk_moves(obj.Path, 0.005):
        if name not in FEED:
            continue
        for ca, cb in chords:
            za, zb = ca[2], cb[2]
            if za > lim and zb > lim:
                continue
            a, b = ca, cb
            if za > lim or zb > lim:  # clip the part of a ramp above the level
                t = (lim - za) / (zb - za)
                m = (ca[0] + (cb[0] - ca[0]) * t, ca[1] + (cb[1] - ca[1]) * t)
                if za > lim:
                    a = m
                else:
                    b = m
            segs.append((a[0], a[1], b[0], b[1]))
    return segs


def _coverage_face(label, face, z, obj, radius, solids, grid, z_tol, max_points):
    import numpy as np

    bb = face.BoundBox
    target_z = bb.ZMax if z is None else float(z)
    capped = False
    g = float(grid)
    nx = max(1, math.ceil(bb.XLength / g))
    ny = max(1, math.ceil(bb.YLength / g))
    if nx * ny > max_points:
        g = math.sqrt(bb.XLength * bb.YLength / max_points)
        nx = max(1, math.ceil(bb.XLength / g))
        ny = max(1, math.ceil(bb.YLength / g))
        capped = True
    x0, y0 = bb.XMin, bb.YMin
    xs = x0 + (np.arange(nx) + 0.5) * g
    ys = y0 + (np.arange(ny) + 0.5) * g
    px, py = np.meshgrid(xs, ys)  # shape (ny, nx)

    rings = _wire_polys(face, min(g / 2.0, 0.5))
    inside = _inside(px, py, rings)

    # unreachable: inside points no tool position (centre >= r from every wall)
    # can cover; the morphological opening of the region by the tool disc
    walls, open_edges = _wall_segments(rings, bb.ZMax, solids)
    unreachable = np.zeros(inside.shape, dtype=bool)
    if walls:
        dwall = np.full(inside.shape, np.inf)
        for ax, ay, bx, by in walls:
            np.minimum(dwall, _seg_dist(px, py, ax, ay, bx, by), out=dwall)
        centre_ok = inside & (dwall >= radius)
        # half a cell diagonal of slack: the true centre locus lies between cells
        reach = _dilate(centre_ok, radius / g + 0.71)
        unreachable = inside & ~reach

    covered = np.zeros(inside.shape, dtype=bool)
    segs = _cut_segments(obj, target_z, z_tol)
    r2 = radius
    for ax, ay, bx, by in segs:
        i0 = max(0, math.floor((min(ax, bx) - r2 - x0) / g))
        i1 = min(nx, math.ceil((max(ax, bx) + r2 - x0) / g) + 1)
        j0 = max(0, math.floor((min(ay, by) - r2 - y0) / g))
        j1 = min(ny, math.ceil((max(ay, by) + r2 - y0) / g) + 1)
        if i0 >= i1 or j0 >= j1:
            continue
        sub_x = px[j0:j1, i0:i1]
        sub_y = py[j0:j1, i0:i1]
        covered[j0:j1, i0:i1] |= _seg_dist(sub_x, sub_y, ax, ay, bx, by) <= r2

    total = int(inside.sum())
    n_cov = int((inside & covered).sum())
    n_unr = int((unreachable & ~covered).sum())
    missed = inside & ~covered & ~unreachable
    n_miss = int(missed.sum())
    clusters, n_clusters = _clusters(missed, g, x0, y0)
    denom = total - n_unr
    return {
        "face": label,
        "target_z": _r(target_z),
        "area": _r(face.Area, 3),
        "grid": _r(g, 4),
        "points_total": total,
        "covered": n_cov,
        "unreachable": n_unr,
        "missed": n_miss,
        "uncut_pct": _r(100.0 * n_miss / denom, 3) if denom > 0 else None,
        "missed_area_est": _r(n_miss * g * g, 3),
        "unreachable_area_est": _r(n_unr * g * g, 3),
        "cut_segments_at_depth": len(segs),
        "wall_segments": len(walls),
        "open_boundary_segments": open_edges,
        "missed_clusters": n_clusters,
        "largest_missed": clusters,
        "capped": capped,
    }, clusters


def coverage(
    object_name,
    doc_name=None,
    region="auto",
    face=None,
    z=None,
    grid=0.5,
    z_tol=0.05,
    recompute=True,
    max_points=400000,
):
    """Entry point of cam_coverage; see the MCP tool docstring."""
    t0 = time.time()
    doc = _get_doc(doc_name)
    obj = find_object(doc, object_name)
    if obj is None:
        raise ValueError(f"no object named or labelled {object_name!r} in {doc.Name}")
    if grid <= 0:
        raise ValueError("grid must be > 0")
    if recompute:
        _recompute_objs(doc, [obj])
    job = _find_job(obj)
    tool = _tool_info(obj)
    if tool is None or not tool.get("radius"):
        raise ValueError(f"{obj.Name}: no tool controller with a diameter")
    solids = _model_solids(job)
    faces = _region_faces(obj, job, doc, region, face)
    per_face = []
    all_clusters = []
    budget = int(max_points)
    for label, f in faces:
        res, cl = _coverage_face(
            label,
            f,
            z,
            obj,
            tool["radius"],
            solids,
            grid,
            z_tol,
            max(1000, budget // len(faces)),
        )
        per_face.append(res)
        all_clusters.extend(dict(c, face=label) for c in cl)
    keys = ("points_total", "covered", "unreachable", "missed")
    tot = {k: sum(p[k] for p in per_face) for k in keys}
    denom = tot["points_total"] - tot["unreachable"]
    all_clusters.sort(key=lambda c: -c["area"])
    out = {
        "document": doc.Name,
        "name": obj.Name,
        "label": obj.Label,
        "tool": tool,
        "tool_radius": _r(tool["radius"], 4),
        "region": region,
        "region_area": _r(sum(f.Area for _l, f in faces), 3),
        **tot,
        "uncut_pct": _r(100.0 * tot["missed"] / denom, 3) if denom > 0 else None,
        "missed_area_est": _r(sum(p["missed_area_est"] for p in per_face), 3),
        "unreachable_area_est": _r(sum(p["unreachable_area_est"] for p in per_face), 3),
        "largest_missed": all_clusters[:20],
        "faces": per_face,
        "capped": any(p["capped"] for p in per_face),
        "seconds": _r(time.time() - t0, 2),
    }
    out.update(_state(obj))
    if not tool["flat"]:
        out["note"] = f"tool shape {tool['shape']!r} treated as a flat end mill"
    return out
