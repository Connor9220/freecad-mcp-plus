# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

# guidriver.cam -- the CAM side of guidriver: rules for CAM dialogs, the CAM
# operation panel (Base Geometry), job summary/check/post/snapshot, recipes.
# A drop-in for the old `import camdriver as cd`:
#
#   import guidriver.cam as cd
#   r = cd.run("CAM_Adaptive", modal=[cd.modal.tc_chooser(70)])
#   p = cd.panel()                      # CamPanel: Panel + add_base()
#   p.add_base([(clone, "Face19")])     # select + Base Geometry "Add"
#   p.set("stepDown", "0.25 in", page="Heights", clear_expression=True)
#   p.ok()
#   cd.summary(); cd.check(); cd.post(modal=cd.modal.nibblerbot_post()); cd.snapshot(path)

from ..taskpanel import edit, run, select
from . import inspect, modal, taskpanel
from .inspect import check, post, snapshot, summary
from .modal import expect

panel = taskpanel.current

__all__ = [
    "check",
    "edit",
    "expect",
    "inspect",
    "modal",
    "panel",
    "post",
    "run",
    "select",
    "snapshot",
    "summary",
    "taskpanel",
]
