# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

# guidriver.cam.taskpanel -- CAM operation task panels.

import FreeCADGui

from ..taskpanel import Panel, select
from ..taskpanel import current as _current


class CamPanel(Panel):
    """Panel with the CAM operation page helpers."""

    def add_base(self, selections, clear=False, modal=()):
        """Select sub-elements in the 3D selection and press the Base Geometry
        page's Add button. selections: [(obj, 'Face19'), (obj, ['Edge1', ...]), obj]"""
        if clear:
            self.click("clearBase", page="Base Geometry")
        select(selections)
        r = self.click("addBase", page="Base Geometry", modal=modal)
        FreeCADGui.Selection.clearSelection()
        base = (
            [(b[0].Name, list(b[1])) for b in getattr(self.obj, "Base", [])]
            if self.obj
            else None
        )
        return {"base": base, "modals": r["modals"]}


def current():
    """The open task dialog as a CamPanel, or None."""
    return _current(CamPanel)
