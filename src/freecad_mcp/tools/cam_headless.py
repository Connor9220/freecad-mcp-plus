# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Headless FreeCAD tools for FreeCAD Robust MCP Server.

These never touch the running FreeCAD: each call starts a fresh headless
process from the FreeCAD source tree's own build.

- fc_eval: run Python in headless FreeCADCmd and get a JSON-safe result back.
- cam_tests: run CAM unit tests by name, with build-sync check and optional
  classification of failures against a baseline git ref.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from freecad_mcp.cam import headless


def register_headless_tools(
    mcp: Any,
    get_bridge: Callable[[], Awaitable[Any]],  # noqa: ARG001 - common register_* signature; these tools never use the bridge
) -> None:
    """Register headless FreeCAD tools with the Robust MCP Server."""

    @mcp.tool()
    async def fc_eval(
        code: str = "",
        file: str | None = None,
        fcstd: str | None = None,
        repo: str | None = None,
        timeout_s: int = 120,
        memory_gb: int = 8,
    ) -> dict[str, Any]:
        """Run Python in a fresh headless FreeCADCmd built from the source tree.

        Nothing is sent to the running FreeCAD GUI. The process runs in safe
        mode with stdin from /dev/null, niced, under a memory cap; on timeout its
        whole process group is killed. Failures are always reported (FreeCADCmd
        itself exits 0 and prints nothing when a script fails).

        Args:
            code: Python source. Set _result_ to return a value. Names available:
                FreeCAD (also App) and doc (the opened fcstd, else None).
                print() output is captured and returned.
            file: Path of a .py file to run instead of (or before) code.
            fcstd: Document to open first, as doc. It is never saved by the tool.
            repo: FreeCAD source tree with build/release. Default: FREECAD_CAM_REPO
                from the server's environment, else its working directory.
            timeout_s: Wall-clock limit for the whole process.
            memory_gb: Address-space cap for the process.

        Returns:
            ok, exit_code, result (JSON-safe: vectors as [x, y, z], other objects
            as repr), stdout, stderr (startup banner stripped), error (traceback,
            timeout or "no result written"), duration_s, repo.
        """
        return await asyncio.to_thread(
            lambda: headless.fc_eval(
                code=code, file=file, fcstd=fcstd, repo=repo,
                timeout_s=timeout_s, memory_gb=memory_gb,
            )
        )  # fmt: skip

    @mcp.tool()
    async def cam_tests(
        tests: list[str],
        repo: str | None = None,
        baseline: str | None = None,
        timeout_s: int = 900,
    ) -> dict[str, Any]:
        """Run CAM unit tests from the source tree's build, one module at a time.

        Names are resolved against build/release/Mod/CAM/CAMTests and run as
        CAMTests.<id> with FreeCADCmd -t; GUI tests (the TestCAMGui suite, or
        modules importing FreeCADGui) run with FreeCAD -t on an offscreen display.
        Before running, every src/Mod/CAM/**.py is compared with its build copy:
        differing files are listed as stale_build_files (the tests ran the OLD
        code), with a warning.

        Args:
            tests: Test names, e.g. "TestPathProfile", "CAMTests.TestPathProfile",
                or a dotted id down to a class or method
                ("TestPathPocket.TestPathPocket.test_01").
            repo: FreeCAD source tree with build/release. Default: FREECAD_CAM_REPO
                from the server's environment, else its working directory.
            baseline: Optional git ref (e.g. "main"). Failing tests are re-run with
                that ref's CAM Python overlaid (tests included) and classified as
                "pre-existing" (also fail there) or "new".
            timeout_s: Timeout per test process (no memory cap for tests).

        Returns:
            ok, total (ran/failures/errors/skipped), runs (per name: id, runner,
            status OK/FAILED/NO RESULT, counts, failing tests with traceback tail,
            log_tail when there is no summary), unknown names with the reason,
            stale_build_files, not_in_build, warnings, and baseline
            (classification per failing test id, swapped_python_files).
        """
        if isinstance(tests, str):
            tests = [tests]
        return await asyncio.to_thread(
            lambda: headless.cam_tests(
                tests=list(tests), repo=repo, baseline=baseline, timeout_s=timeout_s
            )
        )
