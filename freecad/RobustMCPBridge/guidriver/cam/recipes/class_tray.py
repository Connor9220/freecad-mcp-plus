# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

# Rebuild the CAM 101 Lesson 3 class tray job entirely through the GUI.
#
#   import guidriver.cam.recipes.class_tray as r; log = r.run(doc)      # doc = opened class-tray.FCStd
#
# Each step returns what the GUI did (new objects, dialogs answered), so a run
# against a PR build can be diffed against one on main.

import FreeCAD
import guidriver.cam as cd
from PySide import QtCore

M = cd.modal


def run(doc=None, stop_before_post=False, copy_dressups=True, machine=None):
    """copy_dressups=False: the finish pass copies only the Profile op, then
    gets its own Lead In/Out (same settings) instead of a copied dressup chain.
    machine: pick this Job machine in the Job panel (machine-based post flow)."""
    doc = doc or FreeCAD.ActiveDocument
    FreeCAD.setActiveDocument(doc.Name)
    log = []

    def step(name, result):
        log.append({"step": name, "result": result})
        # let Qt finish deleting closed dialogs before the next step
        # not DeferredDelete: see README
        for _ in range(5):
            QtCore.QCoreApplication.sendPostedEvents(None, 0)
        return result

    # Job: model + NibblerBOT template
    step("CAM_Job", cd.run("CAM_Job", modal=[M.job_create(["Body"], "NibblerBOT")]))
    job = doc.getObject("Job")
    p = cd.panel()
    step(
        "stock extents",
        p.set_many(
            {
                "stockExtXneg": "1.5 in",
                "stockExtXpos": "1.5 in",
                "stockExtYneg": "0.625 in",
                "stockExtYpos": "0.625 in",
            }
        ),
    )
    doc.recompute()
    stock = job.Stock
    idx = min(
        range(len(stock.Shape.Vertexes)),
        key=lambda i: tuple(
            round(c, 6)
            for c in (
                stock.Shape.Vertexes[i].Point.z,
                stock.Shape.Vertexes[i].Point.x,
                stock.Shape.Vertexes[i].Point.y,
            )
        ),
    )
    cd.select([(stock, f"Vertex{idx + 1}")])
    step("set origin", p.click("setOrigin"))
    step(
        "material",
        p.click(
            "btnMaterial",
            modal=[M.stock_material("ba2474ee-f62c-45f5-b388-823ea105847f")],
        ),
    )
    # A job must keep one tool controller: add first, then delete the template default.
    step(
        "add tools",
        p.click(
            "toolControllerAdd",
            modal=[M.toolbit_selector([70, 31, 41], "NibblerBOT"), M.input_int()],
        ),
    )
    p.select_rows("toolControllerList", ["5mm Endmill"])
    step("delete default TC", p.click("toolControllerDelete"))
    for label, preset in (
        ('1-1/4" Bowl', "Hardwood Rough"),
        ("Down Cut", "Hardwood Profile Rough"),
        ("COMP", "Hardwood Profile Finish"),
    ):
        p.select_rows("toolControllerList", [label])
        step(f"F&S {label}", p.click("toolControllerEdit", modal=M.tc_editor(preset)))
    if machine:
        step("machine", p.set("jobMachine", machine))
    step("job OK", p.ok())

    # Adaptive, T70, pocket floor
    step("CAM_Adaptive", cd.run("CAM_Adaptive", modal=[M.tc_chooser(70)]))
    p = cd.panel()
    clone = job.Model.Group[0]
    floor = next(
        f"Face{i}"
        for i, f in enumerate(clone.Shape.Faces, 1)
        if f.Surface.__class__.__name__ == "Plane"
        and f.normalAt(0, 0).z > 0.99
        and f.BoundBox.ZMin > clone.Shape.BoundBox.ZMin + 1e-6
        and f.BoundBox.ZMax < clone.Shape.BoundBox.ZMax - 1e-6
    )
    step("adaptive base", p.add_base([(clone, floor)]))
    step(
        "adaptive fields",
        [
            p.set("stepOver", 30, page="TaskPanelOpPage"),
            p.set("HelixMaxDiameterPercent", 40, page="TaskPanelOpPage"),
            p.set("stepDown", "0.25 in", page="Heights", clear_expression=True),
        ],
    )
    step("adaptive OK", p.ok())

    # Rough profile, T31 (new ops inherit the previous op's TC -- change it in the panel)
    step("CAM_Profile", cd.run("CAM_Profile", modal=[M.tc_chooser(31)]))
    p = cd.panel()
    profile = doc.getObject(log[-1]["result"]["new"][0][0])
    step(
        "profile fields",
        [
            p.set("toolController", "Down Cut", page="Tool Controller"),
            p.set("extraOffset", "1/16 in", page="TaskPanelOpPage"),
            p.set("stepDown", "0.25 in", page="Heights", clear_expression=True),
            p.set("useStartPoint", True, page="TaskPanelOpPage"),
        ],
    )
    front = next(
        i
        for i, f in enumerate(stock.Shape.Faces, 1)
        if abs(f.BoundBox.YMin) < 1e-6 and abs(f.BoundBox.YMax) < 1e-6
    )
    import FreeCADGui

    FreeCADGui.Selection.clearSelection()
    FreeCADGui.Selection.addSelection(
        doc.Name, stock.Name, f"Face{front}", 127.0, 0.0, stock.Shape.BoundBox.ZMax
    )
    step("start point", p.click("setStartPoint", page="TaskPanelOpPage"))
    FreeCADGui.Selection.clearSelection()
    step("profile OK", p.ok())

    # Lead in/out
    def lead_in_out(op, tag=""):
        step(
            "CAM_DressupLeadInOut" + tag,
            cd.run("CAM_DressupLeadInOut", select_first=[op]),
        )
        p = cd.panel()
        step(
            "leadinout fields" + tag,
            [
                p.set("cboStyleIn", "Tangent"),
                p.set("dspExtendIn", "1 in"),
                p.set("cboStyleOut", "Arc"),
                p.set("dspAngleOut", "90 deg"),
            ],
        )
        d = doc.getObject(log[-2]["result"]["new"][0][0])
        step("leadinout OK" + tag, p.ok())
        return d

    dressup = lead_in_out(profile)

    # Finish pass: Copy Operation, then edit the copied profile
    source = dressup if copy_dressups else profile
    r = step("CAM_OperationCopy", cd.run("CAM_OperationCopy", select_first=[source]))
    copy = next(
        doc.getObject(n)
        for n, _, _ in r["new"]
        if doc.getObject(n).Proxy.__class__.__name__ == "ObjectProfile"
    )
    step("edit copy", cd.edit(copy))
    p = cd.panel()
    step(
        "finish fields",
        [
            p.set("toolController", "COMP", page="Tool Controller"),
            p.set("extraOffset", "0 in", page="TaskPanelOpPage"),
            p.set("stepDown", "1 in", page="Heights"),
        ],
    )
    step("finish OK", p.ok())
    if not copy_dressups:
        lead_in_out(copy, " (finish)")

    out = {"log": log, "check": cd.check(job), "summary": cd.summary(job)}
    if not stop_before_post:
        out["post"] = cd.post(job, modal=M.nibblerbot_post())
    return out
