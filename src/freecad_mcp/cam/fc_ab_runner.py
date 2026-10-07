# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""One side of a cam_ab run. Executed by headless FreeCADCmd, never imported.

Reads its arguments from the JSON file named in the environment variable
CAM_AB_ARGS:

    overlay      directory holding this side's CAM Python (put first on sys.path)
    expect       {module_file_relpath: sha256} for every overlaid file
    stats_src    path of fc_path_stats.py (made available to the probe)
    fcstd        optional document to open (every CAM object is touched + recomputed)
    probe        Python source; must set _result_ (default: path stats of all CAM objects)
    out          JSON file to write

The probe sees: FreeCAD, doc (the opened document or None), stats_for,
path_stats, cam_operations, find_object.
"""

import hashlib
import json
import os
import sys
import traceback

args = json.load(open(os.environ["CAM_AB_ARGS"]))
out = {"ok": False, "result": None, "error": None, "loaded": {}, "overlay_problems": []}
try:
    overlay = args["overlay"]
    top = {n[:-3] for n in os.listdir(overlay) if n.endswith(".py")}
    top |= {n for n in os.listdir(overlay) if os.path.isdir(os.path.join(overlay, n))}
    for m in list(sys.modules):
        if m.split(".")[0] in top:
            del sys.modules[m]
    sys.path.insert(0, overlay)

    import FreeCAD

    g = {"FreeCAD": FreeCAD, "doc": None, "__name__": "cam_ab_probe"}
    exec(open(args["stats_src"]).read(), g)

    if args.get("fcstd"):
        doc = FreeCAD.openDocument(args["fcstd"])
        g["doc"] = doc
        for o in g["cam_operations"](doc):
            o.touch()
        doc.recompute()

    probe = (
        args.get("probe")
        or "_result_ = stats_for(doc.Name if doc else None, '*', recompute=False)"
    )
    exec(probe, g)
    out["result"] = g.get("_result_")

    # Prove which code ran: every overlaid module that got imported must come
    # from the overlay and match the expected content.
    for rel, digest in args.get("expect", {}).items():
        mod = rel[:-3].replace("/", ".")
        if mod.endswith(".__init__"):
            mod = mod[: -len(".__init__")]
        m = sys.modules.get(mod)
        if m is None:
            out["loaded"][rel] = "not imported"
            continue
        f = os.path.realpath(getattr(m, "__file__", "") or "")
        if not f.startswith(os.path.realpath(overlay)):
            out["loaded"][rel] = "WRONG FILE " + f
            out["overlay_problems"].append(rel)
            continue
        got = hashlib.sha256(open(f, "rb").read()).hexdigest()
        out["loaded"][rel] = "ok" if got == digest else "HASH MISMATCH"
        if got != digest:
            out["overlay_problems"].append(rel)
    out["ok"] = True
except Exception:
    out["error"] = traceback.format_exc()[-4000:]


def _jsonable(v):
    try:
        json.dumps(v)
        return v
    except TypeError:
        return repr(v)


out["result"] = _jsonable(out["result"])
json.dump(out, open(args["out"], "w"), indent=1, default=repr)
