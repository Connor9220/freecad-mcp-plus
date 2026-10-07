# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Demo recording tool for FreeCAD Robust MCP Server.

- cam_record_demo: record a captioned, mouse-driven video of a CAM feature in the
  running FreeCAD (nested test displays only), for PR reviews.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from freecad_mcp.cam import demo


def register_demo_tools(mcp: Any, get_bridge: Callable[[], Awaitable[Any]]) -> None:
    """Register the demo recording tool with the Robust MCP Server."""

    async def execute(code: str, timeout_ms: int) -> Any:
        bridge = await get_bridge()
        result = await bridge.execute_python(code, timeout_ms)
        if result.success:
            return result.result
        raise RuntimeError(
            result.error_traceback
            or f"{result.error_type}: {getattr(result, 'error_message', None) or result.stderr}"
        )

    @mcp.tool()
    async def cam_record_demo(
        steps: list[dict[str, Any]],
        output: str,
        title: str | None = None,
        hold_s: float = 3.5,
        glide_ms: int = 450,
        fps: int = 30,
        trim: bool = True,
        timeout_s: int = 900,
    ) -> dict[str, Any]:
        """Record a captioned demo video of the running FreeCAD, driven by the mouse.

        Only works when FreeCAD runs on a nested test display (":2" or higher);
        the user's real screen (":0", ":1", DISPLAY unset) is refused. The display
        is recorded with ffmpeg while the steps run inside FreeCAD on Qt timers
        (the GUI never blocks). Real X11 mouse/keyboard input (XTest) glides to
        tree items, Property View cells, dropdown items and buttons. Each step
        shows a caption overlay (title, caption, detail, "step i/n"); after its
        actions the selection is cleared by clicking empty 3D space (so paths show
        in normal colours), touched objects are recomputed, and the step holds
        for hold_s. Build the scene first (e.g. cam_fixture); do not open a modal
        dialog before calling.

        Args:
            steps: [{"caption": str, "detail": str (optional), "actions": [...]}].
                Actions (one key each):
                {"python": code} runs in FreeCAD (FreeCAD, Gui, doc, QtCore/QtGui/
                QtWidgets; namespace kept across steps; must not open a modal
                dialog). {"command": "CAM_Profile"} runs a GUI command (may open a
                task panel or dialog). {"view": "iso"|"front"|"top"|"right"|
                "left"|"rear"|"bottom"|"fit"} (all fit the view). {"wait": s}.
                {"click_tree": label or name}. {"property": {"object": label or
                name, "name": Property View label ("Step Down") or property name,
                "value": text}}: clicks the object in the tree, then the value
                cell; enums pick the dropdown item, bools click the checkbox only
                if the value differs, others Ctrl+A + type + Return. Bare numbers
                get an explicit unit (mm, deg, mm/min) because the Property View
                uses the user's unit schema. An expression on the property is
                cleared first (noted in the log). If a mouse edit misses, the
                value is set directly and noted. {"panel_combo": {"widget":
                objectName, "item": text}} picks a task-panel combo item
                (switching to its tab first). {"click_button": visible text}.
            output: Final .mp4 path. The raw capture (<stem>.raw.mp4) is written
                next to it and removed after the final encode; <stem>.chapters.json
                is written next to it.
            title: Heading shown above every caption (e.g. "PR #30402 - Linking").
            hold_s: Seconds to hold after each step's actions.
            glide_ms: Mouse glide duration per click.
            fps: Capture frame rate.
            trim: Cut the lead-in before step 1's caption and the idle tail after
                the last hold. The final encode (H.264, faststart, at most 1600 px
                wide) happens either way.
            timeout_s: Upper bound for the whole run; on timeout the run is
                aborted and what was recorded is kept.

        Returns:
            {"output", "duration_s", "chapters": [{"step", "t", "caption",
            "detail"}] (t = seconds into the output video), "chapters_json",
            "log" (per step: action time, notes, errors), "warnings", "display",
            "size", "cut" ([start, end] in the raw capture)}.
        """
        return await demo.record_demo(
            execute, steps, output, title=title, hold_s=hold_s, glide_ms=glide_ms,
            fps=fps, trim=trim, timeout_s=timeout_s,
        )  # fmt: skip
