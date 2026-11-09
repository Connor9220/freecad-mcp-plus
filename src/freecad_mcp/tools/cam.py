# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""CAM review tools for FreeCAD Robust MCP Server.

- cam_path_stats: toolpath statistics of CAM operations in the running FreeCAD.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

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
