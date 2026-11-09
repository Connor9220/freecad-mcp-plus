# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""The FreeCAD source tree fc_eval and cam_tests run in, and CAM Python overlays for it.

Runs in the MCP server process (no FreeCAD import). An overlay is a copy of the
tree's built CAM Python with changed ``src/Mod/CAM/**.py`` files replaced by
another version (a git ref, or the working tree).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

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
