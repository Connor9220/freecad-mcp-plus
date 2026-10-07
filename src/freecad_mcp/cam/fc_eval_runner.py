# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""fc_eval runner. Executed by headless FreeCADCmd via -c, never imported.

Reads its arguments from the JSON file named in the environment variable
FC_EVAL_ARGS:

    code     Python source to run; it sets _result_
    fcstd    optional document to open first (exposed as doc; never saved)
    out      JSON file to write

print() output from a -c script is lost by FreeCADCmd, so stdout/stderr of the
code are captured here and written to the JSON file with the result.
"""

import contextlib
import io
import json
import math
import os
import traceback

args = json.load(open(os.environ["FC_EVAL_ARGS"]))
out = {"ok": False, "result": None, "error": None, "stdout": "", "stderr": ""}
so, se = io.StringIO(), io.StringIO()
try:
    with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
        import FreeCAD

        g = {"FreeCAD": FreeCAD, "App": FreeCAD, "doc": None, "__name__": "fc_eval"}
        if args.get("fcstd"):
            g["doc"] = FreeCAD.openDocument(args["fcstd"])
        exec(compile(args.get("code") or "", "<fc_eval>", "exec"), g)
        out["result"] = g.get("_result_")
        out["ok"] = True
except BaseException:  # SystemExit/KeyboardInterrupt from the code are reported too
    out["error"] = traceback.format_exc()[-8000:]
finally:
    out["stdout"] = so.getvalue()[-20000:]
    out["stderr"] = se.getvalue()[-20000:]


def _jsonable(v):
    """JSON-safe copy of v: containers kept, vectors as lists, anything else repr()."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else repr(v)
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [_jsonable(x) for x in v]
    if all(hasattr(v, a) for a in ("x", "y", "z")) and type(v).__name__ == "Vector":
        return [v.x, v.y, v.z]
    return repr(v)


try:
    out["result"] = _jsonable(out["result"])
except Exception:  # never lose the report over a bad result
    out["result"] = repr(out["result"])
with open(args["out"], "w") as f:
    json.dump(out, f, indent=1, default=repr)
