# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""GUI state tools for FreeCAD Robust MCP Server.

- gui_health: what the running FreeCAD GUI is doing right now.
- gui_reset: bring it back to a known state the way a user would.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

_GUI_SRC = (Path(__file__).parent.parent / "cam" / "fc_gui.py").read_text()


async def _run(
    get_bridge: Callable[[], Awaitable[Any]], call: str, what: str
) -> dict[str, Any]:
    bridge = await get_bridge()
    # while_busy: gui_reset must get in even when a run is stuck behind a modal dialog
    result = await bridge.execute_python(_GUI_SRC + "\n" + call, 60000, while_busy=True)
    if result.success:
        return result.result
    msg = (
        result.error_traceback or f"{what} failed: {result.error_type}: {result.stderr}"
    )
    raise ValueError(msg)


def register_gui_tools(mcp: Any, get_bridge: Callable[[], Awaitable[Any]]) -> None:
    """Register GUI state tools with the Robust MCP Server."""

    @mcp.tool()
    async def gui_health(include_report: bool = True) -> dict[str, Any]:
        """Snapshot of the running FreeCAD GUI, to check before and after GUI steps.

        Args:
            include_report: Also return Report-view lines added since the previous
                gui_health/gui_reset call (errors, warnings, traceback count).

        Returns:
            ok (no modal dialog, popup, open task panel or new traceback),
            freecad version, home (build) and cam_python (where CAM code loads
            from), display, documents (modified, invalid objects), active document
            and window, task_panel (open, object in edit), modal_dialog, popup,
            other_windows, selection, workbench, faulthandler, report.
        """
        return await _run(
            get_bridge, f"_result_ = gui_health({include_report!r})", "gui_health"
        )

    @mcp.tool()
    async def gui_reset(
        close_dialogs: bool = True,
        close_task_panel: bool = True,
        clear_selection: bool = True,
        close_documents: bool = False,
    ) -> dict[str, Any]:
        """Bring the FreeCAD GUI back to a known state, the way a user would.

        Rejects modal dialogs and popups, closes an open task panel by pressing its
        Cancel button (so the panel's own cleanup runs; closing it directly leaks
        selection observers that then raise on every selection change), clears
        the selection and optionally closes all documents WITHOUT saving.

        Args:
            close_dialogs: Reject modal dialogs and popups.
            close_task_panel: Cancel the open task panel.
            clear_selection: Clear the 3D/tree selection.
            close_documents: Close every document without saving.

        Returns:
            gui_health after the reset, plus "actions" (what was done).
        """
        call = (
            f"_result_ = gui_reset({close_dialogs!r}, {close_task_panel!r}, "
            f"{clear_selection!r}, {close_documents!r})"
        )
        return await _run(get_bridge, call, "gui_reset")
