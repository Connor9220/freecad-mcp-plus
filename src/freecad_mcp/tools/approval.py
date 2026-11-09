# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Asking the user before a tool runs Python the AI wrote.

FreeCAD shows the code in a dialog: run it only this time, for the rest of the FreeCAD
session, always, or not at all. Headless FreeCAD has no window to ask in, so it runs the AI's
code only when the user turned that on in FreeCAD's preferences. Tools that start their own
FreeCADCmd (fc_eval, cam_ab) ask in the FreeCAD that is running; with none, they refuse.
"""

from collections.abc import Awaitable, Callable
from typing import Any

_HOW_TO_ALLOW = (
    "Tell the user; they can allow it in FreeCAD (Edit > Preferences > MCP+)."
)
DECLINED = (
    "The user declined to run this code. Don't run it another way, and don't ask again "
    "unless the user asks for it."
)
HEADLESS_OFF = (
    "Nothing ran: FreeCAD is running headless, where nobody can be asked, and running the "
    "AI's code there is turned off. Tell the user; they can allow it with 'Headless AI "
    "Permission' in Edit > Preferences > MCP+ (headless FreeCAD reads it too)."
)
NO_FREECAD = (
    "Nothing ran: this code needs the user's approval, and no FreeCAD is running to ask in. "
    + _HOW_TO_ALLOW
)
OLD_ADDON = (
    "Nothing ran: the MCP+ add-on in FreeCAD is too old to ask the user about the AI's code. "
    "Tell the user to update it."
)


async def require_approval(
    get_bridge: Callable[[], Awaitable[Any]],
    tool: str,
    code: str,
    detail: str = "",
    runs_in_freecad: bool = True,
) -> None:
    """Return once the user lets ``code`` run; raise PermissionError otherwise.

    Args:
        get_bridge: The server's bridge getter; FreeCAD asks with its dialog.
        tool: Name of the tool, shown to the user.
        code: The code that will run, exactly.
        detail: One more line for the user (a file name, where the code runs).
        runs_in_freecad: The code runs in the connected FreeCAD, so without one the tool
            fails anyway (fc_eval and cam_ab start their own FreeCADCmd: False).
    """
    try:
        bridge = await get_bridge()
    except Exception:
        if runs_in_freecad:
            raise
        raise PermissionError(NO_FREECAD) from None
    try:
        answer = await bridge.request_approval(tool, code, detail)
    except Exception as e:
        if "approve" in str(e) or "method" in str(e).lower():
            raise PermissionError(OLD_ADDON) from None
        raise
    decision = answer.get("decision") if isinstance(answer, dict) else None
    if decision == "allow":
        return
    if decision == "headless_off":
        raise PermissionError(HEADLESS_OFF)
    raise PermissionError(DECLINED)
