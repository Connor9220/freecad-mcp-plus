# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Headless FreeCAD helpers: fc_eval (run Python) and cam_tests (run CAM unit tests).

Runs in the MCP server process (no FreeCAD import). Every FreeCAD process is
started from the tree's own build through pixi, in safe mode, with stdin from
/dev/null, niced, in its own process group (a timeout kills the whole group).

Known FreeCADCmd traps handled here:

- ``FreeCADCmd -c`` exits 0 and prints nothing when the script fails, and
  print() output is lost: scripts write a JSON report file instead, and a
  missing or empty report is a failure.
- A positional argument after ``-c`` drops FreeCAD into its console and hangs:
  scripts get their arguments from an environment variable naming a JSON file.
- ``-t TestCAMApp.<Name>`` fails for many modules: tests run as ``CAMTests.<Name>``.
"""

from __future__ import annotations

import contextlib
import difflib
import json
import os
import re
import resource
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from freecad_mcp.cam import ab

HERE = Path(__file__).parent
CAM_SRC = ab.CAM_SRC
BUILD_CAM = ab.BUILD_CAM
TESTS_DIR = f"{BUILD_CAM}/CAMTests"

# startup banner / safe-mode chatter printed by every FreeCAD run
_NOISE = re.compile(
    r"SAFE_MODE|Safe mode|SetupSheet|^\s*$|^FreeCAD \d+\.\d+.*Libs:|^\(C\) \d{4}"
    r"|FreeCAD is free and open-source"
)
_GUI_IMPORT = re.compile(r"^(import FreeCADGui|from FreeCADGui\b)", re.MULTILINE)
_SEP_EQ = "=" * 70
_SEP_DASH = "-" * 70


def strip_noise(text: str) -> str:
    """Drop FreeCAD's startup banner and blank lines from process output."""
    return "\n".join(ln for ln in text.splitlines() if not _NOISE.search(ln))


def _command(repo: Path, binary: str = "FreeCADCmd") -> list[str]:
    """Argv prefix that runs the tree's build of binary through pixi, in safe mode."""
    return [
        "pixi", "run", "--manifest-path", str(repo / "pixi.toml"), "--",
        str(repo / "build/release/bin" / binary), "--safe-mode",
    ]  # fmt: skip


def _kill_group(proc: subprocess.Popen[str]) -> None:
    """SIGKILL the process group proc leads (it was started in a new session)."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


def run_process(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_s: float,
    memory_gb: float | None = None,
) -> dict[str, Any]:
    """Run cmd niced, stdin=/dev/null, optional RLIMIT_AS cap, own process group.

    Returns {exit_code, stdout, stderr, timed_out, duration_s}. On timeout the
    whole process group (pixi + FreeCAD + anything it spawned) is killed.
    """
    limit = int(memory_gb * 1024**3) if memory_gb else None

    def pre() -> None:
        if limit:
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        os.nice(10)

    t0 = time.monotonic()
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, errors="replace",
        start_new_session=True, preexec_fn=pre,  # noqa: PLW1509 - rlimit/nice must be set in the child
    )  # fmt: skip
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        try:
            out, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:  # a stray child kept the pipes open
            out, err = "", ""
    except BaseException:
        _kill_group(proc)
        raise
    finally:
        _kill_group(proc)  # leftovers of a finished run, too
    return {
        "exit_code": proc.returncode,
        "stdout": out or "",
        "stderr": err or "",
        "timed_out": timed_out,
        "duration_s": round(time.monotonic() - t0, 2),
    }


def _read_report(path: Path) -> dict[str, Any] | None:
    """The JSON report a runner script wrote, or None if missing/empty/corrupt."""
    try:
        text = path.read_text()
    except OSError:
        return None
    if not text.strip():
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- fc_eval


def fc_eval(
    *,
    code: str = "",
    file: str | None = None,
    fcstd: str | None = None,
    repo: str | None = None,
    timeout_s: int = 120,
    memory_gb: int = 8,
) -> dict[str, Any]:
    """Run Python in the tree's headless FreeCADCmd; see the fc_eval MCP tool."""
    root = ab.find_repo(repo)
    src = code
    if file:
        src = Path(file).expanduser().read_text() + ("\n" + code if code else "")
    doc_path = None
    if fcstd:
        doc_path = Path(fcstd).expanduser().resolve()
        if not doc_path.is_file():
            raise FileNotFoundError(f"fcstd not found: {doc_path}")
    work = Path(tempfile.mkdtemp(prefix="fc_eval_"))
    try:
        out = work / "out.json"
        argf = work / "args.json"
        argf.write_text(
            json.dumps(
                {
                    "code": src,
                    "fcstd": str(doc_path) if doc_path else None,
                    "out": str(out),
                }
            )
        )
        runner = HERE / "fc_eval_runner.py"
        cmd = [*_command(root), "-c", f"exec(open({str(runner)!r}).read())"]
        env = dict(os.environ, FC_EVAL_ARGS=str(argf))
        proc = run_process(
            cmd, cwd=root, env=env, timeout_s=timeout_s, memory_gb=memory_gb
        )
        rep = _read_report(out)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    p_out, p_err = strip_noise(proc["stdout"]), strip_noise(proc["stderr"])
    rep = rep or {}
    error = rep.get("error")
    if proc["timed_out"]:
        error = f"timed out after {timeout_s}s (process group killed)" + (
            f"\n{error}" if error else ""
        )
    elif not rep:
        error = (
            f"FreeCADCmd exited {proc['exit_code']} without writing a result "
            f"(crash, or memory cap of {memory_gb} GB hit?)"
        )
    return {
        "ok": bool(rep.get("ok")) and not proc["timed_out"],
        "exit_code": proc["exit_code"],
        "result": rep.get("result"),
        "stdout": _join(rep.get("stdout", ""), p_out)[-20000:],
        "stderr": _join(rep.get("stderr", ""), p_err)[-20000:],
        "error": error,
        "duration_s": proc["duration_s"],
        "repo": str(root),
    }


def _join(*parts: str) -> str:
    return "\n".join(p.rstrip("\n") for p in parts if p and p.strip())


# ---------------------------------------------------------------- cam_tests


def stale_build_files(repo: Path) -> tuple[list[str], list[str]]:
    """CAM Python whose build copy differs from src, and src files absent from the build.

    Returns (stale, not_in_build), paths relative to src/Mod/CAM.
    """
    src_root, build_root = repo / CAM_SRC, repo / BUILD_CAM
    stale: list[str] = []
    missing: list[str] = []
    for p in sorted(src_root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(src_root).as_posix()
        built = build_root / rel
        if not built.exists():
            missing.append(rel)
        elif built.read_bytes() != p.read_bytes():
            stale.append(rel)
    return stale, missing


def resolve_test(repo: Path, name: str) -> dict[str, Any]:  # noqa: PLR0911 - one early return per reason a name is rejected
    """Map a short test name to a runnable unittest id.

    Accepts "TestPathProfile", "CAMTests.TestPathProfile", "TestCAMApp.TestPathProfile",
    "CAMTests/TestPathProfile.py", or a dotted id down to class/method. Returns
    {name, id, module, gui} or {name, error}.
    """
    n = name.strip().replace("/", ".")
    n = n.removesuffix(".py")
    for prefix in ("CAMTests.", "TestCAMApp.", "TestCAMGui."):
        n = n.removeprefix(prefix)
    parts = n.split(".")
    module = parts[0]
    if not module or not all(re.fullmatch(r"\w+", p) for p in parts):
        return {"name": name, "error": f"not a test name: {name!r}"}
    tests = repo / TESTS_DIR
    path = tests / f"{module}.py"
    if not path.is_file():
        if (repo / CAM_SRC / "CAMTests" / f"{module}.py").is_file():
            return {
                "name": name,
                "error": f"{module} is in src/Mod/CAM/CAMTests but not in the build - rebuild first",
            }
        known = sorted(p.stem for p in tests.glob("Test*.py"))
        close = difflib.get_close_matches(module, known, n=3, cutoff=0.6)
        hint = f"; did you mean {', '.join(close)}?" if close else ""
        return {"name": name, "error": f"unknown CAM test module {module!r}{hint}"}
    source = path.read_text(errors="replace")
    if len(parts) > 1 and not re.search(rf"^class {parts[1]}\b", source, re.MULTILINE):
        return {"name": name, "error": f"no class {parts[1]!r} in {module}"}
    if len(parts) > 2 and not re.search(rf"^\s+def {parts[2]}\b", source, re.MULTILINE):
        return {"name": name, "error": f"no test {parts[2]!r} in {module}.{parts[1]}"}
    if len(parts) > 3:
        return {"name": name, "error": f"too many components in {name!r}"}
    gui_suite = repo / BUILD_CAM / "TestCAMGui.py"
    in_gui_suite = gui_suite.is_file() and re.search(
        rf"\bCAMTests\.{module}\b", gui_suite.read_text(errors="replace")
    )
    return {
        "name": name,
        "id": "CAMTests." + ".".join(parts),
        "module": module,
        "gui": bool(in_gui_suite or _GUI_IMPORT.search(source)),
    }


def _test_id(method: str, paren: str) -> str:
    """Dotted id from a unittest 'ERROR: method (paren)' header."""
    if paren.endswith("." + method) or method in {"setUpModule", "tearDownModule"}:
        return paren
    if method in {"setUpClass", "tearDownClass"}:
        return paren
    return f"{paren}.{method}"


def parse_unittest(text: str) -> dict[str, Any]:
    """Summary of unittest text output: counts, status and failing tests with tracebacks."""
    ran = re.findall(r"^Ran (\d+) tests? in", text, re.MULTILINE)
    status = re.findall(r"^(OK|FAILED)(?: \(([^)]*)\))?\s*$", text, re.MULTILINE)
    res: dict[str, Any] = {
        "ran": int(ran[-1]) if ran else None,
        "status": status[-1][0] if status else "NO RESULT",
        "failures": 0,
        "errors": 0,
        "skipped": 0,
    }
    if status:
        for key, val in re.findall(r"([a-z ]+)=(\d+)", status[-1][1]):
            res[key.strip().replace(" ", "_")] = int(val)
    failing = []
    for block in text.split(_SEP_EQ)[1:]:
        m = re.match(r"\s*(ERROR|FAIL): (\S+) \(([^)]*)\)", block)
        if not m:
            continue
        body = block.split(_SEP_DASH, 1)
        detail = body[1] if len(body) > 1 else block
        detail = detail.split("\n" + _SEP_DASH + "\nRan ", 1)[0].strip()
        failing.append(
            {
                "id": _test_id(m.group(2), m.group(3)),
                "kind": m.group(1).lower(),
                "detail": _tail(detail, 30),
            }
        )
    res["failing"] = failing
    return res


def _tail(text: str, lines: int) -> str:
    return "\n".join(text.splitlines()[-lines:])


def _run_tests(root: Path, t: dict[str, Any], timeout_s: int) -> dict[str, Any]:
    """Run one resolved test id from the build: FreeCADCmd -t, or offscreen FreeCAD -t."""
    env = dict(os.environ)
    if t["gui"]:
        env["QT_QPA_PLATFORM"] = "offscreen"
        cmd = [*_command(root, "FreeCAD"), "-t", t["id"]]
    else:
        cmd = [*_command(root), "-t", t["id"]]
    # no memory cap: full test modules (TestCAMApp) abort under ulimit -v 8 GB
    proc = run_process(cmd, cwd=root, env=env, timeout_s=timeout_s)
    text = proc["stdout"] + "\n" + proc["stderr"]
    parsed = parse_unittest(text)
    run = {
        "name": t["name"],
        "id": t["id"],
        "mode": "build",
        "runner": "FreeCAD -t (offscreen GUI)" if t["gui"] else "FreeCADCmd -t",
        "exit_code": proc["exit_code"],
        "timed_out": proc["timed_out"],
        "duration_s": proc["duration_s"],
        **parsed,
    }
    run["passed"] = parsed["status"] == "OK" and not proc["timed_out"]
    if not run["passed"] and not parsed["failing"]:
        run["log_tail"] = _tail(strip_noise(text), 60)
    return run


def _overlay_run(
    root: Path,
    *,
    overlay: Path,
    expect: list[str],
    ids: list[str],
    timeout_s: int,
    work: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Run unittest ids in FreeCADCmd with overlay first on sys.path (fc_test_runner)."""
    fd, tmp = tempfile.mkstemp(dir=work, suffix=".json")
    os.close(fd)
    out = Path(tmp)
    argf = out.with_suffix(".args.json")
    argf.write_text(
        json.dumps(
            {"overlay": str(overlay), "ids": ids, "expect": expect, "out": str(out)}
        )
    )
    runner = HERE / "fc_test_runner.py"
    cmd = [*_command(root), "-c", f"exec(open({str(runner)!r}).read())"]
    env = dict(os.environ, CAM_TESTS_ARGS=str(argf))
    # no memory cap, as for -t runs
    proc = run_process(cmd, cwd=root, env=env, timeout_s=timeout_s)
    return proc, _read_report(out)


def _run_tests_source(
    root: Path,
    *,
    t: dict[str, Any],
    overlay: Path,
    expect: list[str],
    timeout_s: int,
    work: Path,
) -> dict[str, Any]:
    """Run one resolved CLI test id against the working tree's source (overlay)."""
    proc, rep = _overlay_run(
        root,
        overlay=overlay,
        expect=expect,
        ids=[t["id"]],
        timeout_s=timeout_s,
        work=work,
    )
    r = (rep or {}).get("results", {}).get(t["id"])
    run: dict[str, Any] = {
        "name": t["name"],
        "id": t["id"],
        "mode": "source overlay",
        "runner": "FreeCADCmd -c fc_test_runner (working-tree CAM Python first on sys.path)",
        "exit_code": proc["exit_code"],
        "timed_out": proc["timed_out"],
        "duration_s": proc["duration_s"],
    }
    if r is None:
        run.update(
            ran=None, status="NO RESULT", failures=0, errors=0, skipped=0, failing=[]
        )
        run["log_tail"] = (rep or {}).get("error") or _tail(
            strip_noise(proc["stdout"] + "\n" + proc["stderr"]), 60
        )
    else:
        failing = [
            {"id": f["id"], "kind": f["kind"], "detail": _tail(f["detail"], 30)}
            for f in r["failing"]
        ]
        run.update(
            ran=r["ran"],
            status="FAILED" if failing else "OK",
            failures=r["failures"],
            errors=r["errors"],
            skipped=r["skipped"],
            failing=failing,
        )
        if r["status"] in {"import_error", "missing"}:
            run["load_error"] = (
                f"{r['status']}: the test could not be loaded from the working-tree "
                "source (see failing[0].detail)"
            )
    run["overlay_problems"] = (rep or {}).get("overlay_problems", [])
    run["loaded_from"] = (rep or {}).get("loaded")
    run["passed"] = (
        run["status"] == "OK" and not proc["timed_out"] and not run["overlay_problems"]
    )
    return run


def _classify(r: dict[str, Any] | None) -> str:
    if r is None:
        return "unknown (baseline run failed)"
    if r["status"] in {"error", "fail"}:
        return "pre-existing"
    if r["status"] == "missing":
        return "new (test does not exist on baseline)"
    if r["status"] == "import_error":
        return "unknown (the test module fails to import on the baseline)"
    return f"new (baseline: {r['status']})"


def _baseline(
    root: Path,
    *,
    ref: str,
    files: list[str],
    failing: list[str],
    gui_ids: set[str],
    timeout_s: int,
    work: Path,
) -> dict[str, Any]:
    """Re-run failing ids with the CAM Python of ref overlaid and classify them."""
    classification: dict[str, str] = {}
    details: dict[str, Any] = {}
    for i in failing:
        if i in gui_ids:
            classification[i] = (
                "unknown (GUI test: ran from the build, baseline re-run not supported)"
            )
    ov = work / "overlay_baseline"
    ov.mkdir()
    expect = list(ab.build_overlay(root, ref, files, ov))
    by_module: dict[str, list[str]] = {}
    for i in failing:
        if i not in gui_ids:
            by_module.setdefault(i.split(".")[1], []).append(i)
    runs = []
    for module, ids in by_module.items():
        proc, rep = _overlay_run(
            root, overlay=ov, expect=expect, ids=ids, timeout_s=timeout_s, work=work
        )
        runs.append(
            {
                "module": module,
                "duration_s": proc["duration_s"],
                "timed_out": proc["timed_out"],
                "loaded_from": rep.get("loaded") if rep else None,
                "overlay_problems": (rep or {}).get("overlay_problems", []),
                "error": None if rep and rep.get("ok") else
                (rep or {}).get("error") or _tail(strip_noise(proc["stdout"] + proc["stderr"]), 40),
            }
        )  # fmt: skip
        results = (rep or {}).get("results", {})
        for i in ids:
            r = results.get(i)
            classification[i] = _classify(r)
            if r:
                details[i] = {
                    "baseline_status": r["status"],
                    "baseline_detail": _tail(r["detail"], 15),
                }
    return {
        "ref": ref,
        "mode": "source overlay",
        "classification": classification,
        "details": details,
        "runs": runs,
    }


def _warnings(
    stale: list[str], unknown: list[dict[str, Any]], runs: list[dict[str, Any]]
) -> list[str]:
    """Human-readable warnings for a cam_tests result."""
    warnings = []
    if stale:
        warnings.append(
            f"{len(stale)} CAM Python file(s) in src differ from the build: non-GUI "
            "tests ran from the source (overlay); GUI tests, .ui and C++ still come "
            "from the build - rebuild to test those"
        )
    if unknown:
        warnings.append(
            "unknown test name(s): " + "; ".join(u["error"] for u in unknown)
        )
    if any(r["status"] == "NO RESULT" for r in runs):
        warnings.append(
            "some runs produced no unittest summary (crash/timeout) - see log_tail"
        )
    if any(r.get("overlay_problems") for r in runs):
        warnings.append(
            "some modules did not load from the overlay - see overlay_problems"
        )
    return warnings


def cam_tests(
    *,
    tests: list[str],
    repo: str | None = None,
    baseline: str | None = None,
    timeout_s: int = 900,
) -> dict[str, Any]:
    """Run CAM unit tests by name; see the cam_tests MCP tool for the result.

    Mode rule: when a baseline is given or the build is stale, non-GUI tests run
    in "source overlay" mode (working-tree CAM Python first on sys.path, via
    fc_test_runner), so the current side tests the source and a baseline
    comparison is source-vs-source. Otherwise they run from the build with
    FreeCADCmd -t ("build" mode). GUI tests always run from the build.
    """
    root = ab.find_repo(repo)
    if baseline:
        # a bad ref raises here, before anything runs
        ab._git(root, "rev-parse", "--verify", f"{baseline}^{{commit}}")
    stale, missing = stale_build_files(root)
    resolved, unknown, seen = [], [], set()
    for name in tests:
        t = resolve_test(root, name)
        if "error" in t:
            unknown.append(t)
        elif t["id"] not in seen:
            seen.add(t["id"])
            resolved.append(t)

    use_source = bool(baseline or stale)
    files: list[str] = []
    expect: list[str] = []
    base = None
    work = Path(tempfile.mkdtemp(prefix="cam_tests_"))
    ov = work / "overlay_worktree"
    try:
        if use_source:
            # every CAM .py that differs between baseline, working tree and build,
            # plus working-tree files the build lacks: the same swap set on both sides
            files, _other = ab.changed_files(root, [baseline] if baseline else [])
            files = sorted(set(files) | {f"{CAM_SRC}/{rel}" for rel in stale + missing})
            ov.mkdir()
            expect = list(ab.build_overlay(root, ab.WORKTREE, files, ov))
        runs = [
            _run_tests_source(
                root, t=t, overlay=ov, expect=expect, timeout_s=timeout_s, work=work
            )
            if use_source and not t["gui"]
            else _run_tests(root, t, timeout_s)
            for t in resolved
        ]
        failing = [f["id"] for r in runs for f in r["failing"]]
        gui_ids = {
            f["id"]
            for r, t in zip(runs, resolved, strict=True)
            if t["gui"]
            for f in r["failing"]
        }
        if baseline and failing:
            base = _baseline(
                root, ref=baseline, files=files, failing=failing, gui_ids=gui_ids,
                timeout_s=timeout_s, work=work,
            )  # fmt: skip
    finally:
        shutil.rmtree(work, ignore_errors=True)

    total = {"ran": 0, "failures": 0, "errors": 0, "skipped": 0}
    for r in runs:
        for k in total:
            total[k] += r.get(k) or 0
    warnings = _warnings(stale, unknown, runs)
    return {
        "ok": bool(runs) and all(r["passed"] for r in runs) and not unknown,
        "repo": str(root),
        "mode": "source overlay" if use_source else "build",
        "total": total,
        "runs": runs,
        "unknown": unknown,
        "stale_build_files": stale,
        "not_in_build": missing,
        "swapped_python_files": files,
        "warnings": warnings,
        "baseline": base,
    }
