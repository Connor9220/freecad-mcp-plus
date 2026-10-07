# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""cam_ab: run the same headless probe against two versions of FreeCAD's CAM Python.

Runs in the MCP server process (no FreeCAD import). Each side gets an overlay:
a copy of the tree's built CAM Python with every changed ``src/Mod/CAM/**.py``
replaced by that side's version (a git ref, or the working tree). A headless
FreeCADCmd then runs with the overlay first on ``sys.path``. Sides run one at a
time under a memory cap, and each run reports which overlaid modules actually
loaded, so a comparison can never silently test the wrong code.
"""

from __future__ import annotations

import hashlib
import json
import os
import resource
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
CAM_SRC = "src/Mod/CAM"
BUILD_CAM = "build/release/Mod/CAM"
WORKTREE = "worktree"  # pseudo-ref: the files on disk


def _git(repo: Path, *args: str, check: bool = True) -> str:
    """Run git in repo and return stdout."""
    r = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(repo), *args],  # noqa: S607 - git from PATH, like the rest of the tooling
        capture_output=True,
        text=True,
        check=False,
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def _git_bytes(repo: Path, spec: str) -> bytes | None:
    """Contents of ref:path, or None when it does not exist."""
    r = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(repo), "show", spec],  # noqa: S607
        capture_output=True,
        check=False,
    )
    return r.stdout if r.returncode == 0 else None


def find_repo(repo: str | None) -> Path:
    """The FreeCAD tree to use.

    Explicit argument, else FREECAD_CAM_REPO (set per MCP server, one server per
    review tree), else the server's working directory.
    """
    chosen = repo or os.environ.get("FREECAD_CAM_REPO")
    start = Path(chosen).expanduser() if chosen else Path.cwd()
    top = Path(_git(start, "rev-parse", "--show-toplevel").strip())
    if not (top / "build/release/bin/FreeCADCmd").exists():
        raise RuntimeError(
            f"{top} has no build/release/bin/FreeCADCmd - build it first"
        )
    return top


def changed_files(repo: Path, refs: list[str]) -> tuple[list[str], list[str]]:
    """CAM .py files that differ between any side and the build, plus non-Python changes.

    Returns (python_files, other_files), paths relative to the repo root.
    """
    py: set[str] = set()
    other: set[str] = set()
    for ref in refs:
        if ref == WORKTREE:
            continue
        for f in _git(repo, "diff", "--name-only", ref, "--", CAM_SRC).split():
            (py if f.endswith(".py") else other).add(f)
    # untracked CAM .py files exist only in the working tree
    for f in _git(
        repo, "ls-files", "--others", "--exclude-standard", "--", CAM_SRC
    ).split():
        if f.endswith(".py"):
            py.add(f)
    # working-tree files whose build copy is stale also count
    for f in _git(repo, "ls-files", "--", f"{CAM_SRC}/*.py").split():
        built = repo / BUILD_CAM / f[len(CAM_SRC) + 1 :]
        src = repo / f
        if built.exists() and src.exists() and built.read_bytes() != src.read_bytes():
            py.add(f)
    return sorted(py), sorted(other)


def build_overlay(repo: Path, ref: str, files: list[str], dest: Path) -> dict[str, str]:
    """Copy the built CAM Python into dest and apply ref's version of files.

    Returns {module relpath: sha256} for every file written from ref.
    """
    src_root = repo / BUILD_CAM
    for item in src_root.iterdir():
        if item.is_dir() and not item.name.startswith(("__", ".")):
            if any(item.rglob("*.py")):
                shutil.copytree(
                    item, dest / item.name, ignore=shutil.ignore_patterns("__pycache__")
                )
        elif item.suffix == ".py":
            shutil.copy2(item, dest / item.name)
    expect: dict[str, str] = {}
    for f in files:
        rel = f[len(CAM_SRC) + 1 :]
        target = dest / rel
        if ref == WORKTREE:
            data = (repo / f).read_bytes() if (repo / f).exists() else None
        else:
            data = _git_bytes(repo, f"{ref}:{f}")
        if data is None:  # the file does not exist on this side
            if target.exists():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        expect[rel] = hashlib.sha256(data).hexdigest()
    return expect


def run_side(
    *,
    repo: Path,
    overlay: Path,
    expect: dict[str, str],
    probe: str,
    fcstd: str | None,
    timeout_s: int,
    memory_gb: int,
    work: Path,
    label: str,
) -> dict[str, Any]:
    """Run one side in headless FreeCADCmd and return its JSON report."""
    out = work / f"{label}.json"
    args = {
        "overlay": str(overlay),
        "expect": expect,
        "stats_src": str(HERE / "fc_path_stats.py"),
        "fcstd": str(Path(fcstd).expanduser()) if fcstd else None,
        "probe": probe,
        "out": str(out),
    }
    argf = work / f"{label}.args.json"
    argf.write_text(json.dumps(args))
    runner = HERE / "fc_ab_runner.py"
    cmd = [
        "pixi", "run", "--manifest-path", str(repo / "pixi.toml"), "--",
        str(repo / "build/release/bin/FreeCADCmd"), "--safe-mode",
        "-c", f"exec(open({str(runner)!r}).read())",
    ]  # fmt: skip
    limit = memory_gb * 1024**3

    def cap() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        os.nice(10)

    env = dict(os.environ, CAM_AB_ARGS=str(argf))
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv built above, no shell
            cmd, cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=timeout_s, preexec_fn=cap, check=False,
        )  # fmt: skip
        tail = (proc.stdout + proc.stderr)[-3000:]
        code: int | str = proc.returncode
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {timeout_s}s", "result": None}
    if not out.exists():
        return {
            "ok": False,
            "error": f"FreeCADCmd exited {code} without output",
            "log": tail,
            "result": None,
        }
    res = json.loads(out.read_text())
    res["exit_code"] = code
    if not res.get("ok"):
        res["log"] = tail
    return res


def diff(
    a: Any, b: Any, path: str = "", out: list[str] | None = None, limit: int = 200
) -> list[str]:
    """Readable list of differences between two JSON-like values."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a:
                out.append(f"{path}/{k}: only in B = {json.dumps(b[k])[:200]}")
            elif k not in b:
                out.append(f"{path}/{k}: only in A = {json.dumps(a[k])[:200]}")
            else:
                diff(a[k], b[k], f"{path}/{k}", out, limit)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            diff(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append(f"{path}: A={json.dumps(a)[:200]}  B={json.dumps(b)[:200]}")
    return out


def cam_ab(
    *,
    probe: str = "",
    fcstd: str | None = None,
    ref_a: str = "main",
    ref_b: str = WORKTREE,
    repo: str | None = None,
    timeout_s: int = 600,
    memory_gb: int = 8,
    keep: bool = False,
) -> dict[str, Any]:
    """Run probe on ref_a and ref_b; see the cam_ab MCP tool for the result."""
    root = find_repo(repo)
    files, other = changed_files(root, [ref_a, ref_b])
    work = Path(tempfile.mkdtemp(prefix="cam_ab_"))
    sides: dict[str, Any] = {}
    try:
        for label, ref in (("A", ref_a), ("B", ref_b)):
            ov = work / f"overlay_{label}"
            ov.mkdir()
            expect = build_overlay(root, ref, files, ov)
            sides[label] = run_side(
                repo=root, overlay=ov, expect=expect, probe=probe, fcstd=fcstd,
                timeout_s=timeout_s, memory_gb=memory_gb, work=work, label=label,
            )  # fmt: skip
            sides[label]["ref"] = ref
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)

    a, b = sides["A"], sides["B"]
    problems = []
    for label, s in sides.items():
        if not s.get("ok"):
            problems.append(f"side {label} ({s['ref']}) failed")
        elif s.get("result") in (None, {}, [], ""):
            problems.append(f"side {label} ({s['ref']}) returned nothing")
        if s.get("overlay_problems"):
            problems.append(
                f"side {label} loaded the wrong code for {s['overlay_problems']}"
            )
    stale = {}
    for label, s in sides.items():
        res = s.get("result")
        objs = res.get("objects", []) if isinstance(res, dict) else []
        bad = [
            f"{o['label']}: {o.get('error') or o['state']}"
            for o in objs
            if o.get("stale")
        ]
        if bad:
            stale[label] = bad
    if problems:
        verdict = "INVALID: " + "; ".join(problems)
        changes: list[str] = []
    else:
        changes = diff(a["result"], b["result"])
        verdict = (
            "IDENTICAL" if not changes else f"DIFFERENT ({len(changes)} differences)"
        )
        if stale:
            verdict = (
                "CAUTION - some ops failed to recompute, so their STORED path was measured: "
                + "; ".join(f"{k}: {v}" for k, v in stale.items())
                + " | "
                + verdict
            )
    return {
        "verdict": verdict,
        "repo": str(root),
        "refs": {"A": ref_a, "B": ref_b},
        "swapped_python_files": files,
        "not_swappable": other,  # .ui/.cpp/... changes: both sides run the built version
        "stale_objects": stale,
        "differences": changes,
        "A": a,
        "B": b,
        "workdir": str(work) if keep else None,
    }
