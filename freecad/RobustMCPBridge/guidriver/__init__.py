# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

# guidriver -- drive the FreeCAD GUI from Python like a user would (for the MCP
# bridge): toolbar commands, task panel widgets, OK/Cancel, modal dialogs
# answered by rule. CAM-specific rules and checks live in guidriver.cam.
#
#   import guidriver as gd
#   r = gd.run("PartDesign_Pad", modal=[gd.modal.message_box("OK")])
#   p = gd.panel()                      # the open task panel
#   p.dump()                            # pages/widgets: value, hidden, disabled
#   p.set("Length", "10 mm"); p.click("someButton")
#   p.ok()                              # the task view's real OK button
#   gd.edit(obj)                        # double-click equivalent
#
# Real XTest mouse/keys: guidriver.xmouse (imported on demand; needs X11).

from . import modal, taskpanel, widgets
from .modal import expect
from .taskpanel import edit, run, select

panel = taskpanel.current

__all__ = [
    "edit",
    "expect",
    "modal",
    "panel",
    "reload",
    "run",
    "select",
    "taskpanel",
    "widgets",
]


def _reload_order(name):
    # Generic modules first, then guidriver.cam's (deepest first), packages
    # last, so `from .x import f` bindings pick up the reloaded functions.
    parts = name.split(".")
    cam = len(parts) > 1 and parts[1] == "cam"
    return (cam, -len(parts), name)


def reload():
    """Reload all guidriver modules (after editing them)."""
    import importlib
    import sys

    for name in sorted(
        [n for n in sys.modules if n.startswith("guidriver.")], key=_reload_order
    ):
        importlib.reload(sys.modules[name])
    return importlib.reload(sys.modules["guidriver"])
