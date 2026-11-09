# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""The user's say before code written by the AI runs, and before another computer connects.

The MCP server asks here before a tool runs Python the AI wrote (execute_python and the
like). The user runs it once, for the rest of this FreeCAD session, or always (saved in the
preferences), or not at all. Headless FreeCAD can't ask, so it runs the AI's code only when
the user turned that on in the preferences (off by default).

Another computer (one with the token, over TLS-PSK) connects only once the user lets it, for
this session or always; headless FreeCAD lets in only those always allowed.
"""

from __future__ import annotations

import threading
from typing import Any

try:
    import FreeCAD

    FREECAD_AVAILABLE = True
except ImportError:
    FreeCAD = None
    FREECAD_AVAILABLE = False

PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/MCPPlus"
ALWAYS_PARAM = "RunCodeWithoutAsking"
HEADLESS_PARAM = "RunCodeHeadless"
# Other computers the user always lets connect (their addresses, space-separated)
COMPUTERS_PARAM = "AllowedComputers"
# An unanswered dialog closes after this long, as "don't run"
ANSWER_TIMEOUT_S = 600

ONCE = "once"
SESSION = "session"
ALWAYS = "always"

_session_allowed = threading.Event()
# Other computers let in, or turned away, until FreeCAD closes
_session_computers: set[str] = set()
_blocked_computers: set[str] = set()


def always_allowed() -> bool:
    """Whether the user chose to run the AI's code without asking (default off)."""
    if not FREECAD_AVAILABLE:
        return False
    return bool(FreeCAD.ParamGet(PARAM_PATH).GetBool(ALWAYS_PARAM, False))


def set_always_allowed(enabled: bool) -> None:
    """Save the choice; turning it off also ends an allowance for this session."""
    FreeCAD.ParamGet(PARAM_PATH).SetBool(ALWAYS_PARAM, enabled)
    if not enabled:
        _session_allowed.clear()


def headless_allowed() -> bool:
    """Whether a headless FreeCAD runs the AI's code (default off: nobody there to ask)."""
    if not FREECAD_AVAILABLE:
        return False
    return bool(FreeCAD.ParamGet(PARAM_PATH).GetBool(HEADLESS_PARAM, False))


def set_headless_allowed(enabled: bool) -> None:
    """Save whether a headless FreeCAD runs the AI's code."""
    FreeCAD.ParamGet(PARAM_PATH).SetBool(HEADLESS_PARAM, enabled)


def always_allowed_computers() -> list[str]:
    """The other computers the user always lets connect."""
    if not FREECAD_AVAILABLE:
        return []
    return FreeCAD.ParamGet(PARAM_PATH).GetString(COMPUTERS_PARAM, "").split()


def forget_computers() -> None:
    """Ask again about every other computer, including those always allowed."""
    FreeCAD.ParamGet(PARAM_PATH).SetString(COMPUTERS_PARAM, "")
    _session_computers.clear()
    _blocked_computers.clear()


def computer_allowed(address: str) -> bool | None:
    """True if the computer at ``address`` may connect, False if turned away, None to ask."""
    if address in _session_computers or address in always_allowed_computers():
        return True
    if address in _blocked_computers:
        return False
    return None


def ask_about_computer(address: str) -> dict[str, Any]:
    """Ask with the dialog (main thread only) whether the computer at ``address`` may connect.

    Unanswered (the dialog timed out) asks again next time; Cancel turns it away until FreeCAD
    closes.
    """
    known = computer_allowed(address)
    if known is not None:
        return {"decision": "allow" if known else "deny"}
    from ..Qt.approval_dialog import ask_connection

    answer = ask_connection(address, ANSWER_TIMEOUT_S)
    if answer == ALWAYS:
        computers = " ".join([*always_allowed_computers(), address])
        FreeCAD.ParamGet(PARAM_PATH).SetString(COMPUTERS_PARAM, computers)
    elif answer == SESSION:
        _session_computers.add(address)
    elif answer is not None:
        _blocked_computers.add(address)
    return {"decision": "allow" if answer in (SESSION, ALWAYS) else "deny"}


def set_standing_permission(scope: str | None) -> None:
    """Set what the preferences show: SESSION, ALWAYS, or anything else to ask every time."""
    set_always_allowed(scope == ALWAYS)
    if scope == SESSION:
        _session_allowed.set()


def standing_permission() -> str | None:
    """ALWAYS or SESSION when the AI's code may run without asking, else None."""
    if always_allowed():
        return ALWAYS
    if _session_allowed.is_set():
        return SESSION
    return None


def ask_user(tool: str, code: str, detail: str = "") -> dict[str, Any]:
    """Ask with the dialog (main thread only): ``{"decision": "allow"|"deny", "scope"}``."""
    scope = standing_permission()
    if scope:
        return {"decision": "allow", "scope": scope}
    from ..Qt.approval_dialog import ApprovalDialog

    scope = ApprovalDialog.ask(tool, code, detail, ANSWER_TIMEOUT_S)
    if scope is None:
        return {"decision": "deny"}
    set_standing_permission(scope)
    return {"decision": "allow", "scope": scope}
