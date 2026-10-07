# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""CAM toolpath checks for FreeCAD Robust MCP Server.

- cam_gouge_check: does the swept tool of a CAM operation cut into the model?
- cam_coverage: how much of a target floor/face an operation actually clears.

Both run fc_checks.py inside the connected FreeCAD (GUI bridge or headless).
"""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

_CHECKS_SRC = (Path(__file__).parent.parent / "cam" / "fc_checks.py").read_text()


async def _run(
    get_bridge: Callable[[], Awaitable[Any]], call: str, timeout_ms: int, tool: str
) -> dict[str, Any]:
    bridge = await get_bridge()
    result = await bridge.execute_python(_CHECKS_SRC + "\n" + call, timeout_ms)
    if result.success:
        return result.result
    raise ValueError(
        result.error_traceback
        or f"{tool} failed: {result.error_type}: {getattr(result, 'error_message', None) or result.stderr}"
    )


def register_check_tools(mcp: Any, get_bridge: Callable[[], Awaitable[Any]]) -> None:
    """Register the CAM gouge and coverage check tools."""

    @mcp.tool()
    async def cam_gouge_check(
        object_name: str = "*",
        doc_name: str | None = None,
        against: str = "model",
        moves: str = "all",
        clearance: float = 0.0,
        tolerance: float = 0.01,
        recompute: bool = True,
        max_reports: int = 20,
        timeout_ms: int = 300000,
    ) -> dict[str, Any]:
        """Check whether CAM toolpaths cut into the job's model (gouges).

        Each move (arcs split into short chords) is swept with a FLAT end mill of
        the tool controller's diameter whose shank runs up past the model top.
        Bounding boxes and distToShape reject moves that stay clear; only
        candidates get a small boolean common with each model solid, one move at
        a time (never a fused toolpath solid). The tool radius is shrunk by
        ``tolerance + clearance`` and its tip lifted by ``tolerance``, so riding
        exactly on a finished wall or floor is not a gouge. Non-flat tools
        (ball, V-bit, drill): cutting moves are skipped, rapids/links still
        checked. Work stops at 20000 chords per object or ~80% of the timeout
        and reports "capped".

        Args:
            object_name: Operation/dressup Name or Label, or "*" for every CAM
                object (a dressup and its base op are both checked).
            doc_name: Document name. Uses the active document if None.
            against: Only "model" (the job's model clones) is supported.
            moves: "all", or a comma list of "rapids" (G0), "links" (moves
                annotated linking, straight feed retracts, feed moves at/above
                Safe Height) and "cuts" (all other feed moves).
            clearance: Extra radial allowance (e.g. a stock-to-leave that must be
                respected) subtracted from the tool radius.
            tolerance: Overlap depth treated as touching (mm).
            recompute: Touch and recompute the objects first (default).
            max_reports: Gouging moves listed per object.
            timeout_ms: Execution timeout inside FreeCAD.

        Returns:
            verdict, gouge_totals per category, capped, seconds, and per object:
            tool (diameter, shape, flat), categories {moves, segments,
            candidates, gouges}, gouges [{index, category, gcode, start, end,
            overlap_volume, depth_est (smallest overlap bbox side),
            overlap_bbox}], stale/error (recompute failed: stored path checked),
            capped, notes.
        """
        budget = max(5.0, timeout_ms / 1000.0 * 0.8)
        call = (
            f"_result_ = gouge_check({object_name!r}, {doc_name!r}, {against!r}, "
            f"{moves!r}, clearance={float(clearance)!r}, "
            f"tolerance={float(tolerance)!r}, recompute={bool(recompute)!r}, "
            f"max_reports={int(max_reports)!r}, budget_s={budget!r})"
        )
        return await _run(get_bridge, call, timeout_ms, "cam_gouge_check")

    @mcp.tool()
    async def cam_coverage(
        object_name: str,
        doc_name: str | None = None,
        region: str = "auto",
        face: str | None = None,
        z: float | None = None,
        grid: float = 0.5,
        z_tol: float = 0.05,
        recompute: bool = True,
        max_points: int = 400000,
        timeout_ms: int = 300000,
    ) -> dict[str, Any]:
        """Measure how much of a target area an operation actually clears.

        The region is sampled on a grid. A point is covered when a feed move at
        or below ``z + z_tol`` (ramps clipped to that level, arcs chorded) passes
        within the tool radius of it in XY. Points no flat tool can reach
        (inside corners: outside the opening of the region by the tool disc,
        where the tool centre must stay a radius from walls = boundary edges with
        model material above the face) are counted as "unreachable", not missed.
        Open boundary edges (no wall) do not limit the tool.

        Args:
            object_name: Operation/dressup Name or Label.
            doc_name: Document name. Uses the active document if None.
            region: "auto" (horizontal planar Base faces of the op, e.g. pocket
                floors; facing ops fall back to the stock top), "face" (see
                ``face``) or "stock_top".
            face: "Face12" on the job's first model clone, or "Object:Face12".
            z: Target height. Default: each face's own height.
            grid: Sample spacing in mm.
            z_tol: A move counts at depth when it is at or below z + z_tol.
            recompute: Touch and recompute the object first (default).
            max_points: Grid cap; the spacing grows (and "capped" is set) above it.
            timeout_ms: Execution timeout inside FreeCAD.

        Returns:
            tool, tool_radius, region_area, points_total, covered, unreachable,
            missed, uncut_pct (missed / (total - unreachable) in %),
            missed_area_est, unreachable_area_est, largest_missed (up to 20
            clusters: face, centroid, area, points, bbox), per-face details
            (target_z, grid, wall/open boundary segments, cut segments at depth),
            stale, error, capped, seconds.
        """
        call = (
            f"_result_ = coverage({object_name!r}, {doc_name!r}, {region!r}, "
            f"{face!r}, {None if z is None else float(z)!r}, grid={float(grid)!r}, "
            f"z_tol={float(z_tol)!r}, recompute={bool(recompute)!r}, "
            f"max_points={int(max_points)!r})"
        )
        return await _run(get_bridge, call, timeout_ms, "cam_coverage")
