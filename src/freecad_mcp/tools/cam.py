# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""CAM review tools for FreeCAD Robust MCP Server.

- cam_path_stats: toolpath statistics of CAM operations in the running FreeCAD.
- cam_ab: the same headless probe against two versions of the CAM Python
  (e.g. main vs a PR), with proof of which code ran.
"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from freecad_mcp.cam import ab

_STATS_SRC = (Path(__file__).parent.parent / "cam" / "fc_path_stats.py").read_text()


def register_cam_tools(mcp: Any, get_bridge: Callable[[], Awaitable[Any]]) -> None:
    """Register CAM review tools with the Robust MCP Server."""

    @mcp.tool()
    async def cam_path_stats(
        object_name: str = "*",
        doc_name: str | None = None,
        recompute: bool = True,
        head: int = 0,
        frame: str = "path",
        timeout_ms: int = 120000,
    ) -> dict[str, Any]:
        """Toolpath statistics for CAM operations/dressups in the running FreeCAD.

        Args:
            object_name: Object Name or Label, or "*" for every CAM operation and
                dressup in the document (tool controllers and the Job are skipped).
            doc_name: Document name. Uses the active document if None.
            recompute: Touch the objects and recompute first (default). A plain
                recompute skips unchanged ops, so their stored path would be read.
            head: Also return the first N G-code lines of each path.
            frame: "path" (as stored) or "world" (placed path, work-plane builds).
            timeout_ms: Execution timeout inside FreeCAD.

        Returns:
            {"document", "objects": [...]} with per object: state, stale (recompute
            failed, stored path measured), error, command counts per G-code,
            lengths (rapid, feed, horizontal/plunge/ramp feed), cut Z levels, feed
            bounding box, first cut, links (runs of non-cutting moves between cuts:
            count, apex-Z histogram, how many reach Safe/Clearance height, moves
            annotated as linking), upward feed moves, zero-length moves, heights,
            cycle time, tool controller.
        """
        call = (
            f"_result_ = stats_for({doc_name!r}, {object_name!r}, "
            f"recompute={recompute!r}, head={int(head)}, frame={frame!r})"
        )
        bridge = await get_bridge()
        result = await bridge.execute_python(_STATS_SRC + "\n" + call, timeout_ms)
        if result.success:
            return result.result
        raise ValueError(
            result.error_traceback
            or f"cam_path_stats failed: {result.error_type}: {getattr(result, 'error_message', None) or result.stderr}"
        )

    @mcp.tool()
    async def cam_ab(
        probe: str = "",
        fcstd: str | None = None,
        ref_a: str = "main",
        ref_b: str = "worktree",
        repo: str | None = None,
        timeout_s: int = 600,
        memory_gb: int = 8,
    ) -> dict[str, Any]:
        """Run the same headless probe against two versions of the CAM Python.

        Each side runs in its own headless FreeCADCmd from the tree's build, with
        that side's version of every changed src/Mod/CAM/*.py put first on the
        import path. Sides run one at a time under a memory cap. The result says
        which modules actually loaded from the swapped files, and the verdict is
        INVALID (never "identical") when a side fails or returns nothing, and
        CAUTION when an op failed to recompute so its stored path was measured.

        Args:
            probe: Python run inside FreeCAD that sets _result_. It can use
                FreeCAD, doc (the opened fcstd), stats_for, path_stats,
                cam_operations, find_object. Empty: path stats of every CAM
                object in fcstd.
            fcstd: Document to open on both sides. Every CAM object is touched and
                recomputed before the probe runs.
            ref_a: Git ref for side A (default "main").
            ref_b: Git ref for side B, or "worktree" for the files on disk (default).
            repo: FreeCAD source tree. Default: FREECAD_CAM_REPO from the server's
                environment (one server per review tree), else its working directory.
            timeout_s: Timeout per side.
            memory_gb: Address-space cap per side.

        Returns:
            verdict, swapped_python_files, not_swappable (.ui/.cpp changes: both
            sides run the built version), stale_objects, differences (path: A vs
            B), and the full A/B results including which overlaid modules loaded.
        """
        return await asyncio.to_thread(
            lambda: ab.cam_ab(
                probe=probe, fcstd=fcstd, ref_a=ref_a, ref_b=ref_b, repo=repo,
                timeout_s=timeout_s, memory_gb=memory_gb,
            )
        )  # fmt: skip
