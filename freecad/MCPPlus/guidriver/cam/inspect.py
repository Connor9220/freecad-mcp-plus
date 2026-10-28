# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

# guidriver.cam.inspect -- summarize, sanity-check, post and snapshot a CAM job.

import os
import re

import FreeCAD
import FreeCADGui

Q = FreeCAD.Units.Quantity


def _job(job=None):
    if job is not None:
        return job
    import Path.Main.Job as PathJob

    jobs = [
        o
        for o in FreeCAD.ActiveDocument.Objects
        if isinstance(getattr(o, "Proxy", None), PathJob.ObjectJob)
    ]
    if len(jobs) != 1:
        raise RuntimeError(f"pass job=; found {[j.Name for j in jobs]}")
    return jobs[0]


def L(v):
    """Length in the user's unit schema."""
    return Q(v, FreeCAD.Units.Length).UserString


def F(q):
    return q.UserString if hasattr(q, "UserString") else str(q)


def _base_op(o):
    while (
        hasattr(o, "Base")
        and hasattr(o.Base, "Path")
        and not hasattr(o, "ToolController")
    ):
        o = o.Base
    return o


def _chain(o):
    out = [o]
    while (
        hasattr(o, "Base")
        and hasattr(o.Base, "Path")
        and o.Base is not None
        and not hasattr(o, "ToolController")
    ):
        o = o.Base
        out.append(o)
    return out


def path_stats(path):
    xs, ys, zs = [], [], []
    cut = rapid = 0
    for c in path.Commands:
        p = c.Parameters
        if c.Name in ("G0", "G00"):
            rapid += 1
        elif c.Name in ("G1", "G2", "G3", "G01", "G02", "G03"):
            cut += 1
            if "X" in p:
                xs.append(p["X"])
            if "Y" in p:
                ys.append(p["Y"])
            if "Z" in p:
                zs.append(p["Z"])
    s = {"cmds": len(path.Commands), "cut": cut, "rapid": rapid}
    if xs:
        s["x"] = [L(min(xs)), L(max(xs))]
    if ys:
        s["y"] = [L(min(ys)), L(max(ys))]
    if zs:
        s["z"] = [L(min(zs)), L(max(zs))]
        s["z_levels"] = len({round(z, 3) for z in zs})
    return s


OP_KEYS = (
    "Side",
    "Direction",
    "OperationType",
    "StepOverPercent",
    "StepOver",
    "OffsetExtra",
    "StockToLeave",
    "HelixMaxDiameterPercent",
    "UseStartPoint",
    "StartPoint",
    "CutMode",
    "OffsetPattern",
    "HandleMultipleFeatures",
    "StyleIn",
    "StyleOut",
    "ExtendIn",
    "AngleOut",
    "RadiusIn",
    "RadiusOut",
    "Height",
    "Width",
    "Angle",
)


def _props(o, keys=OP_KEYS):
    d = {}
    for k in keys:
        if hasattr(o, k):
            v = getattr(o, k)
            if isinstance(v, FreeCAD.Vector):
                v = [L(v.x), L(v.y), L(v.z)]
            elif hasattr(v, "UserString"):
                v = v.UserString
            d[k] = v
    return d


def summary(job=None):
    job = _job(job)
    s = job.Stock
    bb = s.Shape.BoundBox
    out = {
        "job": job.Name,
        "post": [job.PostProcessor, job.PostProcessorArgs],
        "stock": {
            "type": getattr(s, "StockType", None),
            "min": [L(bb.XMin), L(bb.YMin), L(bb.ZMin)],
            "max": [L(bb.XMax), L(bb.YMax), L(bb.ZMax)],
            "material": getattr(getattr(s, "ShapeMaterial", None), "Name", None),
        },
        "models": [],
        "tools": [],
        "ops": [],
    }
    for m in job.Model.Group:
        mb = m.Shape.BoundBox
        out["models"].append(
            {
                "name": m.Name,
                "min": [L(mb.XMin), L(mb.YMin), L(mb.ZMin)],
                "max": [L(mb.XMax), L(mb.YMax), L(mb.ZMax)],
            }
        )
    for tc in job.Tools.Group:
        t = tc.Tool
        out["tools"].append(
            {
                "T": tc.ToolNumber,
                "label": tc.Label,
                "dia": L(t.Diameter.Value) if t else None,
                "rpm": tc.SpindleSpeed,
                "feed": F(tc.HorizFeed),
                "plunge": F(tc.VertFeed),
                "provenance": dict(getattr(tc, "FeedSpeedProvenance", {}) or {}),
            }
        )
    for o in job.Operations.Group:
        chain = _chain(o)
        b = chain[-1]
        d = {
            "name": o.Name,
            "label": o.Label,
            "active": getattr(o, "Active", True),
            "chain": [f"{c.Name}({c.Proxy.__class__.__name__})" for c in chain],
            "state": o.State,
        }
        tc = getattr(b, "ToolController", None)
        if tc is not None:
            d["T"] = tc.ToolNumber
        if hasattr(b, "Base") and isinstance(b.Base, list):
            d["base"] = [(x[0].Name, list(x[1])) for x in b.Base]
        for k in (
            "StartDepth",
            "FinalDepth",
            "StepDown",
            "FinishDepth",
            "SafeHeight",
            "ClearanceHeight",
        ):
            if hasattr(b, k):
                d[k] = L(getattr(b, k).Value)
        d["props"] = _props(b)
        for c in chain[:-1]:
            d.setdefault("dressups", []).append({"name": c.Name, **_props(c)})
        d["path"] = path_stats(o.Path)
        out["ops"].append(d)
    return out


def check(job=None, tabs=False):
    """Heuristic warnings a CAM user would want before posting.

    tabs=True also flags through-cuts without holding tags (off by default:
    Billy holds parts with tape)."""
    job = _job(job)
    warns = []
    zmin = job.Stock.Shape.BoundBox.ZMin
    for tc in job.Tools.Group:
        if tc.HorizFeed.Value == 0 or tc.VertFeed.Value == 0:
            warns.append(f"T{tc.ToolNumber}: feed is zero")
        if tc.SpindleSpeed == 0:
            warns.append(f"T{tc.ToolNumber}: spindle speed is zero")
        elif abs(tc.SpindleSpeed - round(tc.SpindleSpeed)) > 1e-6:
            warns.append(
                f"T{tc.ToolNumber}: non-integer RPM {tc.SpindleSpeed} (posts may truncate)"
            )
    for o in job.Operations.Group:
        chain = _chain(o)
        b = chain[-1]
        name = o.Label
        if "Invalid" in o.State or "Touched" in o.State:
            warns.append(f"{name}: state {o.State}")
        if len(o.Path.Commands) == 0:
            warns.append(f"{name}: empty path")
        vp = getattr(b.ViewObject, "Proxy", None)
        if getattr(vp, "deleteOnReject", False):
            warns.append(
                f"{name}: deleteOnReject still set -- Cancel in its editor will delete it"
            )
        for c in chain:
            for prop, expr in c.ExpressionEngine:
                for ref in re.findall(r"\b([A-Za-z_]\w*)\.ToolController", expr):
                    if ref not in [x.Name for x in chain]:
                        warns.append(
                            f"{name}: {c.Name}.{prop} expression references {ref}, not in its own chain"
                        )
        tc = getattr(b, "ToolController", None)
        if tc is None and hasattr(b, "ToolController"):
            warns.append(f"{name}: no tool controller")
        if (
            hasattr(b, "FinalDepth")
            and b.FinalDepth.Value <= zmin + 1e-6
            and getattr(b, "Side", None) == "Outside"
        ):
            if tabs and not any("Tag" in c.Proxy.__class__.__name__ for c in chain):
                warns.append(f"{name}: cuts through stock bottom with no tags")
        if tc is not None and hasattr(b, "StepDown"):
            limit = _preset_doc_limit(tc, job)
            if limit and b.StepDown.Value > limit + 1e-6:
                warns.append(
                    f"{name}: StepDown {L(b.StepDown.Value)} exceeds applied preset DOC <= {L(limit)}"
                )
    return warns


def _preset_doc_limit(tc, job):
    prov = dict(getattr(tc, "FeedSpeedProvenance", {}) or {})
    src = prov.get("HorizFeed", "")
    label = src.rsplit("/", 1)[-1] if src else None
    if not label:
        return None
    try:
        from Path.Tool.FeedsSpeeds.presets import derive_preset_label, get_presets
    except ImportError:
        return None
    for p in get_presets(tc.Tool):
        if derive_preset_label(p) == label:
            m = re.search(r"DOC\s*(?:<=|to)\s*([\d.]+)\s*in", p.get("notes", ""))
            if m:
                return float(m.group(1)) * 25.4
    return None


def post(job=None, outdir=None, postname=None, args=None, modal=(), no_remote=False):
    """Run the job's post into outdir. Returns file paths, dialogs the post
    raised (answered by `modal` rules, else rejected) and a G-code digest.

    no_remote=True disables the post's remote_post() hook (export2 upload step).
    """
    from .. import modal as M

    job = _job(job)
    from Path.Post.Processor import PostProcessorFactory

    outdir = outdir or os.path.join(FreeCAD.getUserCachePath(), "guidriver")
    os.makedirs(outdir, exist_ok=True)
    saved_args = job.PostProcessorArgs
    if args is not None:
        job.PostProcessorArgs = args
    try:
        with M.Watcher(modal) as wt:
            pp = PostProcessorFactory.get_post_processor(
                job, postname or job.PostProcessor
            )
            if no_remote:
                pp.remote_post = lambda *_a, **_k: None
            new_flow = bool(getattr(job, "Machine", None))
            data = pp.export2() if new_flow else pp.export()
    finally:
        job.PostProcessorArgs = saved_args
    dialogs = [(m["title"], m["action"]) for m in wt.log]
    if not data:
        return {"error": "post returned nothing", "dialogs": dialogs}
    files = []
    for i, (part, gcode) in enumerate(data):
        fn = os.path.join(outdir, f"{job.Label}_{part or i}.nc".replace(" ", "_"))
        with open(fn, "w") as f:
            f.write(gcode or "")
        files.append(fn)
    allg = "\n".join(g or "" for _, g in data)
    return {"files": files, "dialogs": dialogs, "digest": gcode_digest(allg)}


def gcode_digest(g):
    lines = [l.strip() for l in g.splitlines() if l.strip()]
    tools = re.findall(r"\bM0?6\s*T(\d+)|\bT(\d+)\s*M0?6", g)
    s = re.findall(r"\bS([\d.]+)", g)
    f = re.findall(r"\bF([\d.]+)", g)
    return {
        "lines": len(lines),
        "tool_changes": [a or b for a, b in tools],
        "spindle": sorted(set(s), key=float),
        "feeds": sorted(set(f), key=float)[:12],
        "units": "G20" if "G20" in g else ("G21" if "G21" in g else None),
        "head": lines[:15],
        "tail": lines[-8:],
    }


def snapshot(path, view="Isometric", width=1200, height=800, fit=True):
    v = FreeCADGui.ActiveDocument.ActiveView
    if view:
        getattr(v, "view" + view)()
    if fit:
        v.fitAll()
    v.saveImage(path, width, height, "Current")
    return path
