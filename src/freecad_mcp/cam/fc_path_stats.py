# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Path statistics for FreeCAD CAM objects.

This module runs INSIDE FreeCAD (GUI through the bridge, or headless FreeCADCmd).
It must not import anything from freecad_mcp. The MCP tool sends its source and
calls :func:`stats_for` with plain arguments; the result is JSON-serialisable.

Everything is measured from the stored Path commands, in the frame the path is
stored in, unless ``frame="world"`` asks for the placed path.
"""

import math

import FreeCAD

RAPID = {"G0", "G00"}
LINE = {"G1", "G01"}
ARC = {"G2", "G02", "G3", "G03"}
FEED = LINE | ARC
EPS = 1e-6


def _r(v, n=3):
    return None if v is None else round(float(v), n)


def _q(obj, prop):
    """Value of a Quantity/float property in mm, or None."""
    v = getattr(obj, prop, None)
    if v is None:
        return None
    return _r(getattr(v, "Value", v))


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


def _arc_length(cmd, start, end):
    """Length of a G2/G3 in the XY plane (with helical Z), from I/J centre offsets."""
    p = cmd.Parameters
    cx, cy = start.x + p.get("I", 0.0), start.y + p.get("J", 0.0)
    r = math.hypot(start.x - cx, start.y - cy)
    a0 = math.atan2(start.y - cy, start.x - cx)
    a1 = math.atan2(end.y - cy, end.x - cx)
    sweep = a1 - a0
    if cmd.Name in ("G2", "G02"):  # clockwise
        if sweep >= -EPS:
            sweep -= 2 * math.pi
    elif sweep <= EPS:
        sweep += 2 * math.pi
    planar = abs(sweep) * r
    return math.hypot(planar, end.z - start.z)


def _is_linking(cmd):
    try:
        return "linking" in str(cmd.Annotations).lower()
    except Exception:
        return False


def path_stats(obj, head=0, frame="path"):
    """Statistics of obj.Path. See the MCP tool docstring for the fields."""
    path = obj.Path
    if frame == "world":
        try:
            import PathScripts.PathUtils as PathUtils

            path = PathUtils.getPathWithPlacement(obj)
        except Exception:
            frame = "path (no getPathWithPlacement in this build)"
    cmds = path.Commands

    counts = {}
    pos = None
    rapid_len = feed_len = horiz_len = plunge_len = ramp_len = 0.0
    up_feed = []
    zero_len = []
    cut_z = set()
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    first_cut = None
    links = []  # apex Z of each run of non-cutting moves between cuts
    link_apex = None
    link_annotated = 0
    moves = 0

    for i, c in enumerate(cmds):
        name = c.Name.upper()
        counts[name] = counts.get(name, 0) + 1
        if name not in RAPID and name not in FEED:
            continue
        p = c.Parameters
        if pos is None:
            pos = FreeCAD.Vector(p.get("X", 0.0), p.get("Y", 0.0), p.get("Z", 0.0))
            continue
        end = FreeCAD.Vector(p.get("X", pos.x), p.get("Y", pos.y), p.get("Z", pos.z))
        moves += 1
        d = end - pos
        dxy = math.hypot(d.x, d.y)
        length = _arc_length(c, pos, end) if name in ARC else d.Length
        if length < EPS and name not in ARC:
            if len(zero_len) < 20:
                zero_len.append({"index": i, "gcode": c.toGCode()})
        if _is_linking(c):
            link_annotated += 1

        cutting = False
        if name in RAPID:
            rapid_len += length
        else:
            feed_len += length
            for k, v in enumerate((end.x, end.y, end.z)):
                lo[k] = min(lo[k], v)
                hi[k] = max(hi[k], v)
            if d.z > EPS:
                up_feed.append({"index": i, "gcode": c.toGCode(), "dz": _r(d.z)})
            if dxy < EPS and name not in ARC:
                plunge_len += length if d.z < 0 else 0.0
            elif abs(d.z) < EPS:
                horiz_len += length
                cut_z.add(round(end.z, 3))
                cutting = not _is_linking(c)
            else:
                ramp_len += length
                cutting = not _is_linking(c)
            if cutting and first_cut is None:
                first_cut = {"index": i, "x": _r(end.x), "y": _r(end.y), "z": _r(end.z)}

        if cutting:
            if link_apex is not None:
                links.append(round(link_apex, 3))
                link_apex = None
        else:
            z = max(pos.z, end.z)
            link_apex = z if link_apex is None else max(link_apex, z)
        pos = end

    if link_apex is not None:
        links.append(round(link_apex, 3))  # final retract

    safe = _q(obj, "SafeHeight")
    clear = _q(obj, "ClearanceHeight")
    apex_hist = {}
    for z in links:  # string keys: XML-RPC cannot marshal numeric dict keys
        key = f"{z:g}"
        apex_hist[key] = apex_hist.get(key, 0) + 1

    out = {
        "name": obj.Name,
        "label": obj.Label,
        "type": obj.TypeId,
        "proxy": type(obj.Proxy).__module__ + "." + type(obj.Proxy).__name__
        if getattr(obj, "Proxy", None) is not None
        else None,
        "state": list(obj.State),
        # after a recompute, Touched/Invalid means the op failed and obj.Path is
        # whatever was stored before - not something this code generated
        "stale": bool({"Touched", "Invalid", "Error"} & set(obj.State)),
        "error": (obj.getStatusString() or None) if "Invalid" in obj.State else None,
        "active": getattr(obj, "Active", True),
        "frame": frame,
        "commands": len(cmds),
        "moves": moves,
        "counts": counts,
        "length": {
            "rapid": _r(rapid_len, 2),
            "feed": _r(feed_len, 2),
            "horizontal_feed": _r(horiz_len, 2),
            "plunge_feed": _r(plunge_len, 2),
            "ramp_feed": _r(ramp_len, 2),
        },
        "cut_z_levels": sorted(cut_z, reverse=True),
        "feed_bbox": None
        if first_cut is None and lo[0] == math.inf
        else {"min": [_r(v) for v in lo], "max": [_r(v) for v in hi]},
        "first_cut": first_cut,
        "links": {
            "count": len(links),
            "apex_z_histogram": dict(
                sorted(apex_hist.items(), key=lambda kv: float(kv[0]))
            ),
            "at_or_above_safe": sum(
                1 for z in links if safe is not None and z >= safe - 1e-3
            ),
            "at_or_above_clearance": sum(
                1 for z in links if clear is not None and z >= clear - 1e-3
            ),
            "annotated_linking_moves": link_annotated,
        },
        "up_feed_moves": {"count": len(up_feed), "first": up_feed[:10]},
        "zero_length_moves": {"count": len(zero_len), "first": zero_len[:10]},
        "heights": {
            k: _q(obj, k)
            for k in (
                "ClearanceHeight",
                "SafeHeight",
                "StartDepth",
                "FinalDepth",
                "StepDown",
            )
            if hasattr(obj, k)
        },
        "cycle_time": str(getattr(obj, "CycleTime", "")) or None,
    }
    tc = getattr(obj, "ToolController", None)
    if tc is not None:
        tool = getattr(tc, "Tool", None)
        out["tool_controller"] = {
            "label": tc.Label,
            "diameter": _q(tool, "Diameter") if tool is not None else None,
            "horiz_feed": _q(tc, "HorizFeed"),
            "vert_feed": _q(tc, "VertFeed"),
        }
    if head:
        out["head"] = [c.toGCode() for c in cmds[:head]]
    return out


def stats_for(doc_name=None, object_name="*", recompute=True, head=0, frame="path"):
    """Entry point used by the MCP tool. object_name "*" means every CAM object."""
    doc = FreeCAD.getDocument(doc_name) if doc_name else FreeCAD.ActiveDocument
    if doc is None:
        raise ValueError(
            "no document open" if not doc_name else f"no document {doc_name!r}"
        )
    if object_name == "*":
        objs = cam_operations(doc)
    else:
        obj = find_object(doc, object_name)
        if obj is None:
            raise ValueError(
                f"no object named or labelled {object_name!r} in {doc.Name}"
            )
        objs = [obj]
    if recompute:
        # A plain recompute does not re-run an op whose inputs did not change,
        # so stored paths would be compared against themselves. Touch first.
        for o in objs:
            o.touch()
        doc.recompute()
    return {"document": doc.Name, "objects": [path_stats(o, head, frame) for o in objs]}
