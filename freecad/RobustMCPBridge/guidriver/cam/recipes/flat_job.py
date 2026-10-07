# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

# 2.5D regression job on the flat test plate (flat-test-plate.FCStd, built by flat_model.py),
# entirely through the GUI, on the 3-axis "NibblerBOT" machine: the TWP suite's operations
# with no work planes, to compare output between builds (main vs a work-plane PR).
#
#   import guidriver.cam.recipes.flat_job as r; log = r.run(doc)
#
# Runs on builds with and without work planes: it never touches the work plane widget.

import Draft
import FreeCAD
import FreeCADGui
import guidriver.cam as cd
from PySide import QtCore

M = cd.modal
V = FreeCAD.Vector

MACHINE = "NibblerBOT"
TOOLS = [42, 31, 30, 103, 10]
PRESETS = {
    42: "Hardwood Rough",
    31: "Hardwood Pocket",
    30: "Hardwood Slot",
    103: "Hardwood Drill",
    10: "Hardwood Chamfer",
}
TC = {
    42: 'TC: 1/2" 2FL COMP End Mill NACRO 1.500 loc 4.000 oal',
    31: 'TC: 1/4" 2 Flute Spiral Down Cut 1.000 loc 2.500 oal',
    30: 'TC: 3/16" 2 Flute Spiral Down Cut 0.7500 loc 2.5000 oal',
    103: 'TC: 1/4" Brad Point Boring Bit',
    10: 'TC: 1/2" 90 V-Groove Bit 0.2500 loc 1.875 oal',
}
HARDWOOD = "ba2474ee-f62c-45f5-b388-823ea105847f"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _settle():
    # not DeferredDelete: see README
    for _ in range(5):
        QtCore.QCoreApplication.sendPostedEvents(None, 0)


def run(doc=None, post=True, outdir=None):
    """outdir: where the two posts go, as machine/ and legacy/ subfolders (default: FreeCAD cache)."""
    doc = doc or FreeCAD.ActiveDocument
    FreeCAD.setActiveDocument(doc.Name)
    log = []

    def step(name, result=None):
        log.append({"step": name, "result": result})
        doc.save()
        _settle()
        return result

    # ---- Job
    step(
        "CAM_Job",
        cd.run("CAM_Job", modal=[M.job_create(["2.5D Test Plate"], "NibblerBOT")]),
    )
    job = doc.getObject("Job")
    p = cd.panel()
    p.set("jobMachine", MACHINE)
    p.set_many(
        {
            k: "0.125 in"
            for k in ("stockExtXneg", "stockExtXpos", "stockExtYneg", "stockExtYpos")
        }
    )
    p.set("stockExtZpos", "0.08 in")
    p.click("btnMaterial", modal=[M.stock_material(HARDWOOD)])
    p.click(
        "toolControllerAdd",
        modal=[M.toolbit_selector(TOOLS, "NibblerBOT"), M.input_int()],
    )
    p.select_rows("toolControllerList", ["5mm Endmill"])
    p.click("toolControllerDelete")
    for tc in list(job.Tools.Group):
        p.select_rows("toolControllerList", [tc.Label.replace("TC: ", "")])
        p.click("toolControllerEdit", modal=M.tc_editor(PRESETS[tc.ToolNumber]))
    step("job OK", p.ok())
    clone = job.Model.Group[0]
    shape = clone.Shape

    def faces(pred):
        return ["Face%d" % i for i, f in enumerate(shape.Faces, 1) if pred(f)]

    def up(z):
        return lambda f: (
            f.Surface.__class__.__name__ == "Plane"
            and f.normalAt(0, 0).isEqual(V(0, 0, 1), 1e-3)
            and abs(f.BoundBox.ZMax - z) < 1e-6
        )

    def holes(r, pred=lambda f: True):
        return lambda f: (
            f.Surface.__class__.__name__ == "Cylinder"
            and abs(f.Surface.Radius - r) < 1e-3
            and pred(f)
        )

    def op(
        command, tool, label, base=(), select=None, fields=None, heights=None, modal=()
    ):
        FreeCADGui.Selection.clearSelection()
        if select is not None:
            FreeCADGui.Selection.addSelection(select)
        r = cd.run(command, modal=list(modal) + [M.tc_chooser(tool)])
        o = doc.getObject(r["new"][0][0])
        p = cd.panel()
        if base:
            p.add_base([(clone, b) for b in base])
        p.set("toolController", TC[tool], page="Tool Controller")
        _settle()
        for (page, widget), value in (fields or {}).items():
            p.set(widget, value, page=page)
            _settle()
        for widget, value in (heights or {}).items():
            p.set(widget, value, page="Heights", clear_expression=True)
            _settle()
        p.ok()
        o.Label = label
        return step(label, {"op": o.Name, "cmds": len(o.Path.Commands)}), o

    def dressup(command, base, label):
        r = cd.run(command, select_first=[base])
        d = doc.getObject(r["new"][0][0])
        p = cd.panel()
        if p:
            p.ok()
        d.Label = label
        return step(label, {"op": d.Name, "cmds": len(d.Path.Commands)})

    top = 20.0
    island_floor = faces(up(14.0))
    slot_floor = faces(lambda f: up(15.0)(f) and f.BoundBox.XMin > 70)
    sharp_floor = faces(
        lambda f: up(15.0)(f)
        and f.BoundBox.XMax < 105
        and f.BoundBox.YMax < 45
        and f.BoundBox.XMin > 75
    )
    corner = faces(
        holes(3.0, lambda f: f.BoundBox.Center.x < 10 or f.BoundBox.Center.x > 110)
    )
    blind = faces(holes(5.0))
    cbore = faces(holes(6.0))
    cb_thru = faces(holes(3.0, lambda f: abs(f.BoundBox.Center.x - 70) < 0.5))

    op("CAM_MillFacing", 42, "Face Top")
    op("CAM_Adaptive", 31, "Adaptive Island Pocket", island_floor)
    op("CAM_Drilling", 103, "Drill Corner Holes", corner)
    op("CAM_Helix", 30, "Helix Blind Holes", blind)
    op("CAM_Helix", 31, "Helix Counterbore", cbore)
    op("CAM_Drilling", 103, "Drill Counterbore Thru", cb_thru)
    op("CAM_Pocket_Shape", 31, "Pocket Slot", slot_floor)
    _, slot_prof = op(
        "CAM_Profile",
        31,
        "Profile Slot",
        slot_floor,
        fields={("TaskPanelOpPage", "cutSide"): "Inside"},
    )
    dressup("CAM_DressupRampEntry", slot_prof, "Ramp Slot")
    _, isl_prof = op(
        "CAM_Profile",
        31,
        "Profile Island Pocket",
        island_floor,
        fields={("TaskPanelOpPage", "cutSide"): "Inside"},
    )
    dressup("CAM_DressupLeadInOut", isl_prof, "LeadInOut Island Pocket")
    _, sharp_prof = op(
        "CAM_Profile",
        31,
        "Profile Sharp Pocket",
        sharp_floor,
        fields={("TaskPanelOpPage", "cutSide"): "Inside"},
    )
    dressup("CAM_DressupDogbone", sharp_prof, "Dogbone Sharp Pocket")

    # Deburr the island pocket's rim, on the top face
    topface = max((f for f in shape.Faces if up(top)(f)), key=lambda f: f.Area)
    rim_wire = next(
        w
        for w in topface.Wires
        if not w.isSame(topface.OuterWire)
        and any(e.Curve.__class__.__name__ == "Line" for e in w.Edges)
        and w.BoundBox.XMax < 65
    )
    rim = [
        "Edge%d" % i
        for i, e in enumerate(shape.Edges, 1)
        if any(e.isSame(r) for r in rim_wire.Edges)
    ]
    FreeCADGui.Selection.clearSelection()
    for e in rim:
        FreeCADGui.Selection.addSelection(doc.Name, clone.Name, e)
    r = cd.run("CAM_Deburr", modal=[M.tc_chooser(10)])
    deb = doc.getObject(r["new"][0][0])
    p = cd.panel()
    p.set("toolController", TC[10], page="Tool Controller")
    _settle()
    p.ok()
    deb.Label = "Deburr Island Pocket"
    step(deb.Label, {"op": deb.Name, "cmds": len(deb.Path.Commands)})

    # Engrave text on the top face
    ss = Draft.make_shapestring(String="2.5D 32903", FontFile=FONT, Size=5.0)
    ss.Label = "Engrave Text"
    ss.Placement = FreeCAD.Placement(V(12, 66, top), FreeCAD.Rotation())
    doc.recompute()

    def pick_job(w):
        w.setTextValue(next(i for i in w.comboBoxItems() if i.startswith("Job")))
        w.accept()

    op(
        "CAM_Engrave",
        10,
        "Engrave Text",
        select=ss,
        modal=[
            {
                "name": "choose_job",
                "match": {"title": "Choose a CAM Job"},
                "do": pick_job,
            }
        ],
        heights={"startDepth": "20 mm", "finalDepth": "19.5 mm"},
    )

    # Outside profile + tags
    _, outside = op("CAM_Profile", 31, "Profile Outside")
    dressup("CAM_DressupTag", outside, "Tags Outside")

    if post:
        import os

        base = outdir or os.path.join(FreeCAD.getUserCachePath(), "guidriver")
        machine = cd.post(
            job, outdir=os.path.join(base, "machine"), modal=M.nibblerbot_post()
        )
        cd.edit(job)
        p = cd.panel()
        p.set("jobMachine", "<any>")
        p.ok()
        legacy = cd.post(
            job, outdir=os.path.join(base, "legacy"), modal=M.nibblerbot_post()
        )
        cd.edit(job)
        p = cd.panel()
        p.set("jobMachine", MACHINE)
        p.ok()
        step("posts", {"machine": machine, "legacy": legacy})
    return log
