# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""CAM model tools for FreeCAD Robust MCP Server.

- cam_find_geometry: sub-element names (Face12, Edge7) of a model or job clone
  that match geometric rules, with the data needed to pick the right one.
- cam_fixture: build a CAM test setup (model, Job, tool controller, operation
  with resolved Base geometry) in one call, GUI or headless.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

_CAM_DIR = Path(__file__).parent.parent / "cam"
_GEOMETRY_SRC = (_CAM_DIR / "fc_geometry.py").read_text()
_FIXTURE_SRC = (_CAM_DIR / "fc_fixture.py").read_text()


async def _run(
    get_bridge: Callable[[], Awaitable[Any]], code: str, timeout_ms: int, tool: str
) -> Any:
    bridge = await get_bridge()
    result = await bridge.execute_python(code, timeout_ms)
    if result.success:
        return result.result
    raise ValueError(
        result.error_traceback
        or f"{tool} failed: {result.error_type}: {getattr(result, 'error_message', None) or result.stderr}"
    )


def register_model_tools(mcp: Any, get_bridge: Callable[[], Awaitable[Any]]) -> None:
    """Register CAM model/fixture tools with the Robust MCP Server."""

    @mcp.tool()
    async def cam_find_geometry(
        object_name: str,
        doc_name: str | None = None,
        kind: str = "face",
        normal: list[float] | None = None,
        normal_tol_deg: float = 1.0,
        z: float | None = None,
        z_tol: float = 1e-3,
        surface: str | None = None,
        radius: float | None = None,
        radius_tol: float = 1e-3,
        axis: list[float] | None = None,
        top_only: bool = False,
        largest: bool = False,
        of_face: str | None = None,
        limit: int = 50,
        timeout_ms: int = 60000,
    ) -> dict[str, Any]:
        """Find faces or edges of an object (model or CAM job clone) by rules.

        Every given rule must match. Coordinates are the placed shape's, so a
        job's model clone reports where the job sees the geometry.

        Args:
            object_name: Object Name, falling back to Label.
            doc_name: Document name. Uses the active document if None.
            kind: "face" or "edge".
            normal: Planar faces whose outward normal is within normal_tol_deg of
                this direction. Direction matters: [0, 0, 1] means up-facing.
            normal_tol_deg: Angle tolerance for normal and axis.
            z: Planar faces lying at this height, or edges entirely at it
                (pocket floors, rims).
            z_tol: Tolerance for z and for top_only.
            surface: Surface type for faces (Plane, Cylinder, Cone, Sphere,
                Toroid, BSplineSurface, ...) or curve type for edges (Line,
                Circle, Ellipse, BSplineCurve, ...). Case-insensitive.
            radius: Cylinder/sphere faces or circle/arc edges of this radius.
            radius_tol: Tolerance for radius.
            axis: Cylinder/cone faces or circle edges whose axis is parallel to
                this direction (either sense).
            top_only: Among matches keep only those with the highest ZMax.
            largest: Keep only the largest-area face / longest edge.
            of_face: With kind="edge": boundary edges of this face ("Face3"),
                outer wire first, each tagged with its wire index (0 = outer).
            limit: Maximum elements returned in detail ("names" lists all).
            timeout_ms: Execution timeout inside FreeCAD.

        Returns:
            {"document", "object", "label", "kind", "total", "matched", "names",
            "elements", "truncated", "bbox"}. Faces carry area, center (of mass),
            z_min/z_max, horizontal, surface, normal (planes), radius/axis
            (cylinders, cones, ...), hole (cylinder is concave) and axis_point;
            edges carry length, curve, closed, start/end, z range, radius/center/
            axis (circles).
        """
        call = (
            f"_result_ = find_geometry({doc_name!r}, {object_name!r}, kind={kind!r}, "
            f"normal={normal!r}, normal_tol_deg={normal_tol_deg!r}, z={z!r}, "
            f"z_tol={z_tol!r}, surface={surface!r}, radius={radius!r}, "
            f"radius_tol={radius_tol!r}, axis={axis!r}, top_only={top_only!r}, "
            f"largest={largest!r}, of_face={of_face!r}, limit={int(limit)})"
        )
        return await _run(
            get_bridge, _GEOMETRY_SRC + "\n" + call, timeout_ms, "cam_find_geometry"
        )

    @mcp.tool()
    async def cam_fixture(
        model: str | dict[str, Any],
        operation: str,
        base: list[dict[str, Any]] | None = None,
        tool_diameter: float = 5.0,
        properties: dict[str, Any] | None = None,
        job_template: str | None = None,
        save_as: str | None = None,
        doc_name: str | None = None,
        timeout_ms: int = 180000,
    ) -> dict[str, Any]:
        """Build a CAM test setup: model, Job, end mill tool controller, one operation.

        Works headless or with the GUI (view providers are attached, but no task
        panel or chooser dialog opens).

        Args:
            model: Name/Label of an existing object, or a spec to create a
                Part::Feature: {"box": [l, w, h]} or {"cylinder": [r, h]}, with
                optional "at": [x, y, z] (box corner / cylinder base centre),
                "cut" is applied before "fuse" (islands inside pockets survive),
                "cut": [spec, ...], "fuse": [spec, ...], "name". Example:
                {"box": [120, 70, 20], "cut": [{"box": [40, 40, 8],
                "at": [10, 15, 12]}, {"cylinder": [8, 14], "at": [85, 35, 6]}]}.
            operation: Profile, Pocket Shape, Pocket 3D, MillFace, MillFacing,
                Helix, Drilling, Tapping, ThreadMilling, Engrave, Deburr,
                Adaptive, Slot, Vcarve, Surface, Waterline, PlanarSurface,
                RotarySurface, Flute, Custom, Probe (spaces/case ignored).
            base: Base geometry, resolved against the job's model CLONE:
                [{"object": "model" (default) or a model Name/Label,
                "faces": [...] and/or "edges": [...] explicit names, and/or
                "rule": {cam_find_geometry rules: kind, normal, z, surface,
                radius, axis, top_only, largest, of_face, tolerances}}].
                None: the op's default (e.g. Profile follows the model outline).
            tool_diameter: End mill diameter. A matching end mill TC is reused;
                else the job's single default TC is resized; else one is added.
                Zero feeds are set to 1000 mm/min (horizontal/ramp) and
                300 mm/min (vertical).
            properties: Operation properties to set. Each one's expression is
                cleared first (depths and heights are expression-bound and would
                ignore a plain assignment). Numbers are mm for lengths.
            job_template: Job template JSON path (None: preferences default).
            save_as: Save the document to this .FCStd path.
            doc_name: Existing document to build in; None creates a new one.
            timeout_ms: Execution timeout inside FreeCAD.

        Returns:
            {"document", "job" (name, label, stock bbox), "tool_controller"
            (label, diameter, feeds_mm_min, spindle_speed), "tool_controller_notes",
            "operation" (name, label, type, base with resolved sub-names, state,
            error when not Up-to-date, commands, depths, properties_set),
            "model" (name, clone, bbox), "saved_as"}.
        """
        call = (
            f"_result_ = build_fixture({model!r}, {operation!r}, base={base!r}, "
            f"tool_diameter={float(tool_diameter)!r}, properties={properties!r}, "
            f"job_template={job_template!r}, save_as={save_as!r}, doc_name={doc_name!r})"
        )
        code = _GEOMETRY_SRC + "\n" + _FIXTURE_SRC + "\n" + call
        return await _run(get_bridge, code, timeout_ms, "cam_fixture")
