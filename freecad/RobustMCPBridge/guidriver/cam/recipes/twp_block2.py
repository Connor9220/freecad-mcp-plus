# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

# CAM job on TWP Test Block 2 (twp-test-block2.FCStd, inches), through the GUI
# commands, on the "NibblerBOT AC Trunnion" machine: top + four work planes
# (+X 25 deg, -X 35 deg, +Y 40 deg, front 90 deg), each faced, pocketed and
# drilled; outside profile last. No adaptive, no tabs (tape workholding).
#
#   import guidriver.cam.recipes.twp_block2 as r; log = r.run(doc)
#
# The document is saved after every step.

import math

import FreeCAD
import FreeCADGui
import guidriver.cam as cd
from guidriver.cam.recipes.twp_block import _cyl, _faces, _largest, _plane, _settle

M = cd.modal
V = FreeCAD.Vector

MACHINE = "NibblerBOT AC Trunnion"
TOOLS = [42, 31, 103]
PRESETS = {42: "Hardwood Rough", 31: "Hardwood Pocket", 103: "Hardwood Drill"}
TC = {
    42: 'TC: 1/2" 2FL COMP End Mill NACRO 1.500 loc 4.000 oal',
    31: 'TC: 1/4" 2 Flute Spiral Down Cut 1.000 loc 2.500 oal',
    103: 'TC: 1/4" Brad Point Boring Bit',
}
HARDWOOD = "ba2474ee-f62c-45f5-b388-823ea105847f"
HOLE_R = 0.125 * 25.4


def _n(axis, deg, sign=1):
    a = math.radians(deg)
    return {
        "x": V(sign * math.sin(a), 0, math.cos(a)),
        "y": V(0, sign * math.sin(a), math.cos(a)),
    }[axis]


SURFACES = [
    # label, normal
    ("PX25", _n("x", 25)),
    ("NX35", _n("x", 35, -1)),
    ("PY40", _n("y", 40)),
    ("Front", V(0, -1, 0)),
]


def _floors(clone, n):
    """Pocket floors on the surface with normal n: planar faces with that normal,
    sunk at most 0.5 in below the surface (other pockets' walls are farther)."""
    big = _largest(clone, _plane(n))
    top = clone.getSubObject(big).CenterOfMass
    return [
        f
        for f in _faces(clone, _plane(n))
        if f != big and 0 < n.dot(top - clone.getSubObject(f).CenterOfMass) <= 12.7
    ]


def run(doc=None, upto=None, job_only=False):
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
        cd.run("CAM_Job", modal=[M.job_create(["TWP Test Block 2"], "NibblerBOT")]),
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
    def workplane(label, face):
        FreeCADGui.Selection.clearSelection()
        cd.select([(clone, face)])
        r = cd.run("CAM_Workplane")
        wp = doc.getObject(r["new"][0][0])
        wp.Label = label
        FreeCADGui.Selection.clearSelection()
        step(f"workplane {label}", str(wp.Placement))
        return wp

    for name, n in SURFACES:
        workplane(f"WP {name}", _largest(clone, _plane(n)))
    if job_only:
        return log
    return ops(doc, upto, log)


def ops(doc=None, upto=None, log=None, start=0):
    """Operations + lead in/out on an existing job (after run(job_only=True))."""
    doc = doc or FreeCAD.ActiveDocument
    log = [] if log is None else log
    job = doc.getObject("Job")
    clone = job.Model.Group[0]

    def step(name, result=None):
        log.append({"step": name, "result": result})
        doc.save()
        _settle()
        return result

    # ---- Operations
    def op(command, tool, label, faces=(), plane=None, heights=None, fields=None):
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
        for (page, widget), value in (fields or {}).items():
            p.set(widget, value, page=page)
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

    def holes(n):
        return _faces(clone, _cyl(HOLE_R, n))

    OPS = [
        lambda: op("CAM_MillFacing", 42, "Face Top"),
        lambda: op("CAM_Pocket_Shape", 31, "Pocket Top", _floors(clone, V(0, 0, 1))),
        lambda: op("CAM_Drilling", 103, "Drill Top", holes(V(0, 0, 1))),
    ]
    for name, n in SURFACES:
        wp = f"WP {name}"
        OPS += [
            # Final depth typed by hand: the op's own comes from the stock bounding box
            lambda name=name, wp=wp: op(
                "CAM_MillFacing",
                42,
                f"Face {name}",
                plane=wp,
                heights={"finalDepth": "0 in"},
            ),
            # Start at the surface: the facing op above already cleared the wedge
            lambda name=name, n=n, wp=wp: op(
                "CAM_Pocket_Shape",
                31,
                f"Pocket {name}",
                _floors(clone, n),
                wp,
                heights={"startDepth": "0 in"},
            ),
            lambda name=name, n=n, wp=wp: op(
                "CAM_Drilling", 103, f"Drill {name}", holes(n), wp
            ),
        ]
    bottom = _largest(clone, _plane(V(0, 0, -1)))
    # New ops inherit the previous op's work plane: put the profile back on the job XY
    OPS.append(
        lambda: op("CAM_Profile", 42, "Profile Outside", [bottom], "None (Job XY)")
    )
    for make in OPS[start:upto]:
        make()

    # Lead in/out on the profile: long tangent lead-in against rub marks
    profile = next(
        (o for o in job.Operations.Group if o.Label == "Profile Outside"), None
    )
    if profile is None:
        return log
    step("CAM_DressupLeadInOut", cd.run("CAM_DressupLeadInOut", select_first=[profile]))
    p = cd.panel()
    step(
        "leadinout fields",
        [
            p.set("cboStyleIn", "Tangent"),
            p.set("dspExtendIn", "1 in"),
            p.set("cboStyleOut", "Arc"),
            p.set("dspAngleOut", "90 deg"),
        ],
    )
    step("leadinout OK", p.ok())
    return log
