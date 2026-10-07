# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Build a CAM test setup: model, Job, tool controller, one operation.

This module runs INSIDE FreeCAD (GUI through the bridge, or headless FreeCADCmd).
It must not import anything from freecad_mcp. It is sent AFTER the source of
fc_geometry.py, whose ``select_elements``/``find_object`` it looks up in the
shared namespace at call time. Entry point: :func:`build_fixture`; the result is
JSON-safe with string keys only.

Headless, nothing needs a ViewProvider. With the GUI up the Job and operation
get their normal view providers, but no task panel or chooser dialog opens.
"""

import FreeCAD
import Part

# friendly name (lower case, no spaces/underscores) -> (Path.Op module, op name)
OPERATIONS = {
    "profile": ("Profile", "Profile"),
    "pocketshape": ("PocketShape", "Pocket Shape"),
    "pocket3d": ("Pocket", "Pocket3D"),
    "pocket": ("Pocket", "Pocket3D"),
    "millface": ("MillFace", "MillFace"),
    "millfacing": ("MillFacing", "MillFacing"),
    "helix": ("Helix", "Helix"),
    "drilling": ("Drilling", "Drilling"),
    "tapping": ("Tapping", "Tapping"),
    "threadmilling": ("ThreadMilling", "ThreadMilling"),
    "engrave": ("Engrave", "Engrave"),
    "deburr": ("Deburr", "Deburr"),
    "adaptive": ("Adaptive", "Adaptive"),
    "slot": ("Slot", "Slot"),
    "vcarve": ("Vcarve", "Vcarve"),
    "surface": ("Surface", "Surface"),
    "waterline": ("Waterline", "Waterline"),
    "planarsurface": ("PlanarSurface", "PlanarSurface"),
    "rotarysurface": ("RotarySurface", "RotarySurface"),
    "flute": ("Flute", "Flute"),
    "custom": ("Custom", "Custom"),
    "probe": ("Probe", "Probe"),
}

RULE_KEYS = (
    "kind",
    "normal",
    "normal_tol_deg",
    "z",
    "z_tol",
    "surface",
    "radius",
    "radius_tol",
    "axis",
    "top_only",
    "largest",
    "of_face",
)

DEFAULT_HORIZ_FEED = "1000 mm/min"
DEFAULT_VERT_FEED = "300 mm/min"
DEFAULT_SPINDLE = 12000


def _g(name):
    """A helper from fc_geometry.py, sent in the same namespace."""
    fn = globals().get(name)
    if fn is None:
        raise RuntimeError(
            f"fc_geometry.py source must be sent before fc_fixture.py ({name})"
        )
    return fn


def _num(v):
    v = getattr(v, "Value", v)
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _bbox(bb):
    return {
        "min": [_num(bb.XMin), _num(bb.YMin), _num(bb.ZMin)],
        "max": [_num(bb.XMax), _num(bb.YMax), _num(bb.ZMax)],
    }


def build_shape(spec):
    """Part shape from {"box": [l,w,h]} / {"cylinder": [r,h]}, "at", "cut", "fuse"."""
    if not isinstance(spec, dict):
        raise ValueError(f"shape spec must be a dict, not {spec!r}")
    if "box" in spec:
        length, width, height = (float(x) for x in spec["box"])
        shape = Part.makeBox(length, width, height)
    elif "cylinder" in spec:
        r, h = (float(x) for x in spec["cylinder"])
        shape = Part.makeCylinder(r, h)
    else:
        raise ValueError(f"shape spec needs 'box' or 'cylinder': {spec!r}")
    at = spec.get("at")
    if at:
        shape.translate(FreeCAD.Vector(*[float(c) for c in at]))
    # cut first, then fuse: bosses/ribs/islands inside a cut pocket survive
    for sub in spec.get("cut", []) or []:
        shape = shape.cut(build_shape(sub))
    for sub in spec.get("fuse", []) or []:
        shape = shape.fuse(build_shape(sub))
    if spec.get("fuse") or spec.get("cut"):
        shape = shape.removeSplitter()
    return shape


def _get_model(doc, model):
    if isinstance(model, dict):
        obj = doc.addObject("Part::Feature", str(model.get("name", "Model")))
        obj.Shape = build_shape(model)
        doc.recompute()
        return obj, True
    obj = _g("find_object")(doc, str(model))
    if obj is None:
        raise ValueError(f"no model object {model!r} in document {doc.Name!r}")
    return obj, False


def _is_endmill(tool):
    for prop in ("ShapeType", "ShapeID", "ShapeName"):
        if "endmill" in str(getattr(tool, prop, "") or "").lower():
            return True
    return False


def _setup_tool_controller(job, diameter):
    """An end mill TC of ``diameter`` with non-zero feeds; returns (tc, notes)."""
    import Path.Tool.Controller as PathToolController

    notes = []
    tcs = list(job.Tools.Group)
    endmills = [tc for tc in tcs if getattr(tc, "Tool", None) and _is_endmill(tc.Tool)]
    tc = next(
        (t for t in endmills if abs(t.Tool.Diameter.Value - diameter) < 1e-6),
        None,
    )
    if tc is None and len(tcs) == 1 and endmills:
        tc = endmills[0]  # the job's single default TC: resize it
        tc.Tool.Diameter = diameter
        if tc.Label.startswith("TC: ") and "Endmill" in tc.Label:
            tc.Label = f"TC: {diameter:g}mm Endmill"
        notes.append(f"resized the job's default end mill TC to {diameter:g} mm")
    if tc is None:
        tc = PathToolController.Create(
            name=f"TC: {diameter:g}mm Endmill",
            toolNumber=max([t.ToolNumber for t in tcs] + [0]) + 1,
        )
        tc.Tool.Diameter = diameter
        job.Proxy.addToolController(tc)
        notes.append(f"added a new {diameter:g} mm end mill TC")
    if _num(tc.HorizFeed) == 0:
        tc.HorizFeed = DEFAULT_HORIZ_FEED
        notes.append(f"HorizFeed set to {DEFAULT_HORIZ_FEED}")
    if _num(tc.VertFeed) == 0:
        tc.VertFeed = DEFAULT_VERT_FEED
        notes.append(f"VertFeed set to {DEFAULT_VERT_FEED}")
    bound = {prop for prop, _expr in tc.ExpressionEngine}
    if _num(tc.RampFeed) == 0 and "RampFeed" not in bound:  # else it follows HorizFeed
        tc.RampFeed = DEFAULT_HORIZ_FEED
        notes.append(f"RampFeed set to {DEFAULT_HORIZ_FEED}")
    if _num(tc.SpindleSpeed) == 0:
        tc.SpindleSpeed = DEFAULT_SPINDLE
    return tc, notes


def _tc_info(tc):
    def mm_min(q):
        try:
            return round(q.getValueAs("mm/min").Value, 3)
        except Exception:
            return _num(q)

    return {
        "name": tc.Name,
        "label": tc.Label,
        "tool": tc.Tool.Label if tc.Tool else None,
        "shape": str(
            getattr(tc.Tool, "ShapeType", "") or getattr(tc.Tool, "ShapeID", "")
        ),
        "diameter": _num(tc.Tool.Diameter) if tc.Tool else None,
        "tool_number": tc.ToolNumber,
        "spindle_speed": _num(tc.SpindleSpeed),
        "feeds_mm_min": {
            "HorizFeed": mm_min(tc.HorizFeed),
            "VertFeed": mm_min(tc.VertFeed),
            "RampFeed": mm_min(tc.RampFeed),
        },
    }


def _model_clone(job, doc, name):
    """The job's model clone for ``name`` ("model"/None = first model)."""
    group = list(job.Model.Group)
    if not group:
        raise ValueError("job has no model")
    if name in (None, "", "model"):
        return group[0]
    obj = _g("find_object")(doc, str(name))
    if obj is None:
        raise ValueError(f"no base object {name!r}")
    if obj in group:
        return obj
    clone = job.Proxy.resourceClone(job, obj)
    if clone is None:
        raise ValueError(f"{obj.Name!r} is not a model of job {job.Name!r}")
    return clone


def _resolve_base(job, doc, base):
    resolved = []
    for entry in base or []:
        if not isinstance(entry, dict):
            raise ValueError(f"base entries are dicts, not {entry!r}")
        clone = _model_clone(job, doc, entry.get("object"))
        subs = list(entry.get("faces") or []) + list(entry.get("edges") or [])
        rule = entry.get("rule")
        if rule:
            unknown = set(rule) - set(RULE_KEYS)
            if unknown:
                raise ValueError(
                    f"unknown rule keys {sorted(unknown)}; use {list(RULE_KEYS)}"
                )
            found = _g("select_elements")(clone.Shape, **rule)
            if not found:
                raise ValueError(f"base rule {rule!r} matched nothing on {clone.Name}")
            subs += [f["name"] for f in found]
        for sub in subs:
            clone.Shape.getElement(sub)  # raises on a bad name
        resolved.append((clone, tuple(dict.fromkeys(subs))))
    return resolved


def _set_properties(op, properties):
    applied = {}
    for key, value in (properties or {}).items():
        if key not in op.PropertiesList:
            raise ValueError(f"{op.Name} has no property {key!r}")
        op.setExpression(key, None)  # depths/heights are expression-bound
        setattr(op, key, value)
        got = getattr(op, key)
        applied[key] = _num(got) if _num(got) is not None else str(got)
    return applied


def _create_op(job, module_name, op_name):
    import importlib

    import PathScripts.PathUtils as PathUtils

    module = importlib.import_module(f"Path.Op.{module_name}")
    saved = PathUtils.UserInput
    PathUtils.UserInput = None  # never pop a job/TC chooser dialog
    try:
        op = module.Create(op_name, obj=None, parentJob=job)
    finally:
        PathUtils.UserInput = saved
    if FreeCAD.GuiUp and op.ViewObject is not None and op.ViewObject.Proxy is None:
        try:
            gui = importlib.import_module(f"Path.Op.Gui.{module_name}")
            import Path.Op.Gui.Base as PathOpGui

            op.ViewObject.Proxy = PathOpGui.ViewProvider(op.ViewObject, gui.Command.res)
        except Exception as exc:  # the op still works without its view provider
            FreeCAD.Console.PrintWarning(
                f"cam_fixture: no view provider for {op.Name}: {exc}\n"
            )
    return op


def _create_job(model_obj, job_template):
    import Path.Main.Job as PathJob

    if FreeCAD.GuiUp:
        import Path.Main.Gui.Job as PathJobGui

        job = PathJobGui.Create([model_obj], job_template or None, openTaskPanel=False)
        if job is None:
            raise RuntimeError("Job creation failed (see the report view)")
        return job
    return PathJob.Create("Job", [model_obj], job_template or None)


def _recompute(doc, op):
    for _ in range(
        2
    ):  # a Base change can leave expression-bound depths one step behind
        op.touch()
        doc.recompute()
        if "Touched" not in op.State and "Invalid" not in op.State:
            break


def build_fixture(
    model,
    operation,
    base=None,
    tool_diameter=5.0,
    properties=None,
    job_template=None,
    save_as=None,
    doc_name=None,
):
    """Create model, Job, tool controller and operation; see the cam_fixture tool."""
    key = str(operation).lower().replace(" ", "").replace("_", "").replace("-", "")
    if key not in OPERATIONS:
        raise ValueError(
            f"unknown operation {operation!r}; known: {sorted(OPERATIONS)}"
        )
    module_name, op_name = OPERATIONS[key]

    doc = (
        FreeCAD.getDocument(doc_name) if doc_name else FreeCAD.newDocument("CamFixture")
    )
    FreeCAD.setActiveDocument(doc.Name)
    if FreeCAD.GuiUp:
        import FreeCADGui

        FreeCADGui.Selection.clearSelection()  # a selected TC would be adopted by the op

    model_obj, created = _get_model(doc, model)
    job = _create_job(model_obj, job_template)
    tc, notes = _setup_tool_controller(job, float(tool_diameter))

    op = _create_op(job, module_name, op_name)
    op.ToolController = tc
    if hasattr(op, "OpToolDiameter"):
        op.OpToolDiameter = tc.Tool.Diameter
    resolved = _resolve_base(job, doc, base)
    if resolved:
        op.Base = resolved
    applied = _set_properties(op, properties)
    _recompute(doc, op)

    state = list(op.State)
    status = (
        "Invalid"
        if "Invalid" in state
        else "Touched"
        if "Touched" in state
        else "Up-to-date"
    )
    op_info = {
        "name": op.Name,
        "label": op.Label,
        "type": type(op.Proxy).__name__ if op.Proxy else None,
        "module": f"Path.Op.{module_name}",
        "base": [{"object": b.Name, "subs": list(s)} for b, s in (op.Base or [])]
        if hasattr(op, "Base")
        else [],
        "state": status,
        "properties_set": applied,
        "commands": len(op.Path.Commands),
    }
    if status != "Up-to-date":
        op_info["error"] = op.getStatusString()
    depths = {}
    for prop in (
        "StartDepth",
        "FinalDepth",
        "StepDown",
        "SafeHeight",
        "ClearanceHeight",
    ):
        if hasattr(op, prop):
            depths[prop] = _num(getattr(op, prop))
    op_info["depths"] = depths

    clone = job.Model.Group[0] if job.Model.Group else None
    result = {
        "document": doc.Name,
        "job": {
            "name": job.Name,
            "label": job.Label,
            "stock": _bbox(job.Stock.Shape.BoundBox),
        },
        "tool_controller": _tc_info(tc),
        "tool_controller_notes": notes,
        "operation": op_info,
        "model": {
            "name": model_obj.Name,
            "label": model_obj.Label,
            "created": created,
            "clone": clone.Name if clone else None,
            "bbox": _bbox(model_obj.Shape.BoundBox),
        },
    }
    if save_as:
        doc.saveAs(str(save_as))
        result["saved_as"] = doc.FileName
    return result
