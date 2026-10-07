# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

# CAM job on the TWP test block (twp-test-block.FCStd, built by twp_model.py),
# entirely through the GUI, on the "NibblerBOT AC Trunnion" machine.
#
#   import guidriver.cam.recipes.twp_block as r; log = r.run(doc, upto=6)
#
# upto: how many operations to create (see OPS). The document is saved after
# every step, so a crash loses at most the step in progress.

import FreeCAD
import FreeCADGui
import guidriver.cam as cd
from PySide import QtCore

M = cd.modal
V = FreeCAD.Vector

MACHINE = "NibblerBOT AC Trunnion"
TOOLS = [42, 31, 30, 103, 102]
PRESETS = {
    42: "Hardwood Rough",
    31: "Hardwood Pocket",
    30: "Hardwood Slot",
    103: "Hardwood Drill",
    102: "Hardwood Drill",
}
TC = {
    42: 'TC: 1/2" 2FL COMP End Mill NACRO 1.500 loc 4.000 oal',
    31: 'TC: 1/4" 2 Flute Spiral Down Cut 1.000 loc 2.500 oal',
    30: 'TC: 3/16" 2 Flute Spiral Down Cut 0.7500 loc 2.5000 oal',
    103: 'TC: 1/4" Brad Point Boring Bit',
    102: "TC: 5mm Brad Point Boring Bit",
}
HARDWOOD = "ba2474ee-f62c-45f5-b388-823ea105847f"

N_FACET30 = V(0.5, 0, 0.8660254)
C_FACET30 = V(105, 40, 41.33975)
N_COMPOUND = V(-0.27950, 0.54856, 0.78801)
N_ANGLED = V(-0.34202, 0, 0.93969)


def _settle():
    """Let Qt finish deleting closed dialogs and drop Python wrappers of them
    before the next panel is built (stale wrappers are what crashed before)."""
    # not DeferredDelete: see README
    for _ in range(5):
        QtCore.QCoreApplication.sendPostedEvents(None, 0)


def _faces(clone, pred):
    return [f"Face{i}" for i, f in enumerate(clone.Shape.Faces, 1) if pred(f)]


def _plane(n, extra=lambda f: True):
    n = V(n)
    n.normalize()
    return lambda f: (
        f.Surface.__class__.__name__ == "Plane"
        and f.normalAt(0, 0).isEqual(n, 1e-3)
        and extra(f)
    )


def _cyl(radius, axis):
    a = V(axis)
    a.normalize()
    return lambda f: (
        f.Surface.__class__.__name__ == "Cylinder"
        and abs(f.Surface.Radius - radius) < 1e-3
        and (f.Surface.Axis.isEqual(a, 1e-3) or f.Surface.Axis.isEqual(-a, 1e-3))
    )


def _largest(clone, pred):
    c = [(i, f) for i, f in enumerate(clone.Shape.Faces, 1) if pred(f)]
    return "Face%d" % max(c, key=lambda t: t[1].Area)[0]


def run(doc=None, upto=6):
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
        cd.run("CAM_Job", modal=[M.job_create(["TWP Test Block"], "NibblerBOT")]),
    )
    job = doc.getObject("Job")
    p = cd.panel()
    p.set("jobMachine", MACHINE)
    p.set_many(
        {
            "stockExtXneg": "0.125 in",
            "stockExtXpos": "0.125 in",
            "stockExtYneg": "0.125 in",
            "stockExtYpos": "0.125 in",
            "stockExtZpos": "0.08 in",
        }
    )
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

    # ---- Work planes (face selected + CAM_Workplane, like the GUI)
    def workplane(label, face=None, placement=None):
        FreeCADGui.Selection.clearSelection()
        if face:
            cd.select([(clone, face)])
        r = cd.run("CAM_Workplane")
        wp = doc.getObject(r["new"][0][0])
        wp.Label = label
        if placement is not None:
            wp.Placement = placement
            doc.recompute()
        FreeCADGui.Selection.clearSelection()
        step(f"workplane {label}", str(wp.Placement))
        return wp

    workplane("WP Facet30", _largest(clone, _plane(N_FACET30)))
    workplane("WP Compound", _largest(clone, _plane(N_COMPOUND)))
    workplane(
        "WP Front",
        _largest(clone, _plane(V(0, -1, 0), lambda f: abs(f.BoundBox.YMax) < 1e-6)),
    )
    workplane(
        "WP Angled20",
        placement=FreeCAD.Placement(V(15, 25, 50), FreeCAD.Rotation(V(0, 1, 0), -20)),
    )

    # ---- Operations
    def op(command, tool, label, faces=(), plane=None, heights=None):
        FreeCADGui.Selection.clearSelection()
        r = cd.run(command, modal=[M.tc_chooser(tool)])
        o = doc.getObject(r["new"][0][0])
        p = cd.panel()
        if p is None:
            raise RuntimeError(f"{command}: task panel did not open (see Report view)")
        if faces:
            p.add_base([(clone, f) for f in faces])
        if plane:
            p.set("workplane", plane, page="Heights")
        p.set("toolController", TC[tool], page="Tool Controller")
        for widget, value in (heights or {}).items():
            p.set(widget, value, page="Heights", clear_expression=True)
        doc.recompute()
        p.ok()
        o.Label = label
        return step(
            label,
            {
                "op": o.Name,
                "SD": round(o.StartDepth.Value, 3),
                "FD": round(o.FinalDepth.Value, 3),
                "cmds": len(o.Path.Commands),
                "placement": str(o.Placement),
            },
        )

    top_floor = _faces(
        clone, _plane(V(0, 0, 1), lambda f: abs(f.BoundBox.ZMax - 38) < 1e-6)
    )
    facet_floor = _faces(
        clone,
        _plane(
            N_FACET30,
            lambda f: abs(N_FACET30.dot(f.CenterOfMass - C_FACET30) + 6) < 1e-3,
        ),
    )
    OPS = [
        lambda: op("CAM_MillFacing", 42, "Face Top"),
        lambda: op("CAM_Adaptive", 31, "Adaptive Top Pocket", top_floor),
        lambda: op(
            "CAM_Drilling",
            103,
            "Drill Top Holes",
            _faces(
                clone,
                lambda f: _cyl(3.0, (0, 0, 1))(f)
                and abs(f.BoundBox.Center.x - 84) < 0.5,
            ),
        ),
        lambda: op(
            "CAM_Drilling",
            103,
            "Drill Angled20",
            _faces(clone, _cyl(3.0, N_ANGLED)),
            "WP Angled20",
        ),
        # Final depth typed by hand: the op's own is 15 mm high (bounding-box bug)
        lambda: op(
            "CAM_MillFacing",
            42,
            "Face Facet30",
            plane="WP Facet30",
            heights={"finalDepth": "0 in"},
        ),
        # Start at the facet: the facing op above already cleared the wedge
        lambda: op(
            "CAM_Adaptive",
            31,
            "Adaptive Facet30 Pocket",
            facet_floor,
            "WP Facet30",
            heights={"startDepth": "0 in"},
        ),
        lambda: op(
            "CAM_Helix",
            30,
            "Helix Facet30 Holes",
            _faces(clone, _cyl(3.0, N_FACET30)),
            "WP Facet30",
        ),
    ]
    for make in OPS[:upto]:
        make()
    return log
