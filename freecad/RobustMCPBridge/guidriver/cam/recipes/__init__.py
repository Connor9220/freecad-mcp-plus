# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

# Recipes build whole CAM jobs through the GUI. They were recorded on one shop's setup; point them at
# yours with these environment variables (read when a recipe is imported). The older CAMDRIVER_*
# names still work.
#
#   GUIDRIVER_TEMPLATE    Job template to create jobs from          (default "NibblerBOT")
#   GUIDRIVER_LIBRARY     tool library the tool bits come from      (default "NibblerBOT")
#   GUIDRIVER_MACHINE     3-axis machine (flat_job)                 (default "NibblerBOT")
#   GUIDRIVER_MACHINE_5X  5-axis / trunnion machine (twp_block*)    (default "NibblerBOT AC Trunnion")
#   GUIDRIVER_TOOLS_TRAY, GUIDRIVER_TOOLS_TWP, GUIDRIVER_TOOLS_TWP2, GUIDRIVER_TOOLS_FLAT
#                         comma-separated tool numbers in that library for each recipe
import os
from types import SimpleNamespace


def _setting(name, default):
    return (
        os.environ.get(f"GUIDRIVER_{name}")
        or os.environ.get(f"CAMDRIVER_{name}")
        or default
    )


def _tools(name, default):
    value = _setting(name, "")
    return [int(t) for t in value.split(",")] if value else default


SETUP = SimpleNamespace(
    template=_setting("TEMPLATE", "NibblerBOT"),
    library=_setting("LIBRARY", "NibblerBOT"),
    machine=_setting("MACHINE", "NibblerBOT"),
    machine_5x=_setting("MACHINE_5X", "NibblerBOT AC Trunnion"),
    tools_tray=_tools("TOOLS_TRAY", [70, 31, 41]),
    tools_twp=_tools("TOOLS_TWP", [42, 31, 30, 103, 102]),
    tools_twp2=_tools("TOOLS_TWP2", [42, 31, 103]),
    tools_flat=_tools("TOOLS_FLAT", [42, 31, 30, 103, 10]),
)
