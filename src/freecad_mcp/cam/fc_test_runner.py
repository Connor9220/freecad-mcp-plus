# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""cam_tests overlay runner. Executed by headless FreeCADCmd via -c, never imported.

FreeCADCmd -t cannot run tests against a different sys.path, so this script puts
an overlay (the working tree's or a baseline ref's CAM Python) first on
sys.path, drops any already imported CAM modules, and runs the unittest ids itself.

Arguments come from the JSON file named in the environment variable
CAM_TESTS_ARGS:

    overlay   directory holding the CAM Python to test (working tree or baseline ref)
    ids       unittest ids to run (e.g. CAMTests.TestPathPocket.TestPathPocket.test_x)
    expect    overlaid module files (relpaths); imported ones must load from the overlay
    out       JSON file to write
"""

import importlib
import io
import json
import os
import sys
import traceback
import unittest


def load(tid):
    """Suite for tid = CAMTests.<Module>[.<Class>[.<method>]].

    unittest's own name lookup walks attributes from the package down, and the
    CAMTests package re-exports every test class under its module's name
    (CAMTests.TestPathPocket is the class, not the module), so import the test
    module explicitly and resolve the rest inside it.
    """
    parts = tid.split(".")
    module = importlib.import_module(".".join(parts[:2]))
    rest = ".".join(parts[2:])
    loader = unittest.defaultTestLoader
    if not rest:
        return loader.loadTestsFromModule(module)
    return loader.loadTestsFromName(rest, module)


args = json.load(open(os.environ["CAM_TESTS_ARGS"]))
out = {"ok": False, "error": None, "results": {}, "ran": 0, "output": "", "loaded": {}}
try:
    overlay = args["overlay"]
    top = {n[:-3] for n in os.listdir(overlay) if n.endswith(".py")}
    top |= {n for n in os.listdir(overlay) if os.path.isdir(os.path.join(overlay, n))}
    for m in list(sys.modules):
        if m.split(".")[0] in top:
            del sys.modules[m]
    sys.path.insert(0, overlay)

    import FreeCAD  # noqa: F401 - initialises the application before CAM imports

    stream = io.StringIO()
    for tid in args["ids"]:
        try:
            suite = load(tid)
        except Exception:  # the test module does not exist, or fails to import
            tb = traceback.format_exc()
            gone = f"No module named '{'.'.join(tid.split('.')[:2])}'" in tb
            out["results"][tid] = {
                "status": "missing" if gone else "import_error",
                "detail": tb[-1500:],
                "ran": 0,
                "failures": 0,
                "errors": 1,
                "skipped": 0,
                "failing": [{"id": tid, "kind": "error", "detail": tb[-3000:]}],
            }
            continue
        res = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        out["ran"] += res.testsRun
        bad = [(t, "error", tb) for t, tb in res.errors]
        bad += [(t, "fail", tb) for t, tb in res.failures]
        # the loader reports an id it cannot import/find as a _FailedTest
        missing = bool(bad) and all(
            type(t).__name__ == "_FailedTest" for t, _, _ in bad
        )
        if missing and any("ImportError" in tb for _, _, tb in bad):
            status = "import_error"
        elif missing:
            status = "missing"
        elif bad:
            status = bad[0][1]
        elif res.testsRun == len(res.skipped):
            status = "skipped"
        else:
            status = "pass"
        out["results"][tid] = {
            "status": status,
            "detail": "\n".join(tb[-1500:] for _, _, tb in bad),
            "ran": res.testsRun,
            "failures": len(res.failures),
            "errors": len(res.errors),
            "skipped": len(res.skipped),
            "failing": [
                {"id": t.id(), "kind": kind, "detail": tb[-3000:]}
                for t, kind, tb in bad
            ],
        }
    # which file each test module came from (must be the overlay)
    for tid in args["ids"]:
        parts = tid.split(".")
        for i in range(len(parts), 0, -1):
            m = sys.modules.get(".".join(parts[:i]))
            if m is not None:
                out["loaded"][".".join(parts[:i])] = getattr(m, "__file__", None)
                break
    # every overlaid module that got imported must come from the overlay
    real_ov = os.path.realpath(overlay)
    out["overlay_problems"] = []
    for rel in args.get("expect", []):
        mod = rel[:-3].replace("/", ".").removesuffix(".__init__")
        m = sys.modules.get(mod)
        f = os.path.realpath(getattr(m, "__file__", "") or "") if m else ""
        if m is not None and not f.startswith(real_ov):
            out["overlay_problems"].append(f"{rel}: loaded from {f}")
    out["output"] = stream.getvalue()[-20000:]
    out["ok"] = True
except BaseException:  # report everything, the process exit code is useless
    out["error"] = traceback.format_exc()[-6000:]
with open(args["out"], "w") as f:
    json.dump(out, f, indent=1, default=repr)
