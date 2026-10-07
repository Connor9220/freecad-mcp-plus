# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""GuiDriver tools for FreeCAD Robust MCP Server.

Drive the running FreeCAD GUI the way a user would, through the add-on's
``guidriver`` package (shipped in ``freecad/RobustMCPBridge/guidriver``, which
FreeCAD puts on ``sys.path``): run toolbar commands, read and set task panel
widgets, press OK/Cancel, and answer modal dialogs by rule. A modal dialog no
rule matches is rejected after 3 s and logged, so a call never hangs on it.

- gui_run_command: run a GUI command like a toolbar click.
- gui_panel: dump / set / click / OK / Cancel the open task panel.
- gui_edit: open an object's editor like a tree double-click.
- cam_gui_add_base: Base Geometry "Add" on the open CAM operation panel.
- cam_gui_check: pre-post sanity check (and summary) of a CAM job.
- cam_gui_post: post a CAM job, answering the post's own dialogs by rule.

Modal rules (``modal_rules``) are JSON specs: a rule factory by name
(``"input_int"``, ``{"rule": "message_box", "args": ["Yes"]}``, CAM ones such as
``{"rule": "tc_chooser", "args": [70]}``) or a declarative rule
(``{"match": {"title": "Delete"}, "do": {"button": "Yes"}}``).
"""

import os
from collections.abc import Awaitable, Callable
from typing import Any

_IMPORT = """\
try:
    import {module} as _gd
except ImportError as _e:
    raise ImportError(
        "guidriver is not importable in this FreeCAD: update the MCP+ add-on "
        "(it ships freecad/RobustMCPBridge/guidriver)"
    ) from _e
"""


def guidriver_code(
    func: str, kwargs: dict[str, Any], module: str = "guidriver.mcp"
) -> str:
    """Python run inside FreeCAD: import guidriver and call one entry point."""
    return _IMPORT.format(module=module) + f"_result_ = _gd.{func}(**{kwargs!r})\n"


async def _call(
    get_bridge: Callable[[], Awaitable[Any]],
    func: str,
    kwargs: dict[str, Any],
    timeout_ms: int,
    module: str = "guidriver.mcp",
) -> dict[str, Any]:
    bridge = await get_bridge()
    result = await bridge.execute_python(
        guidriver_code(func, kwargs, module), timeout_ms
    )
    if result.success:
        return result.result
    raise ValueError(
        result.error_traceback
        or f"guidriver {func} failed: {result.error_type}: {result.stderr}"
    )


def register_guidriver_tools(
    mcp: Any, get_bridge: Callable[[], Awaitable[Any]]
) -> None:
    """Register GuiDriver tools with the Robust MCP Server."""

    @mcp.tool()
    async def gui_run_command(
        command: str,
        modal_rules: list[Any] | None = None,
        select: list[Any] | None = None,
        doc_name: str | None = None,
        timeout_ms: int = 60000,
    ) -> dict[str, Any]:
        """Run a FreeCAD GUI command like a toolbar click (GUI gating included).

        Args:
            command: Command name, e.g. "PartDesign_Pad", "CAM_Profile".
            modal_rules: Rules answering dialogs the command opens, in order:
                a factory name ("input_int"), {"rule": name, "args": [...],
                "kwargs": {...}} (message_box, input_int; CAM: tc_chooser,
                job_create, toolbit_selector, stock_material, tc_editor,
                feeds_speeds, nibblerbot_post) or a declarative rule
                {"match": {"title"|"text"|"class"|"object"|"has": ...},
                "do": "accept" | "reject" | {"set": {widget: value},
                "button": "Yes"}, "name": ..., "once": true}.
            select: Select these first: "Name", ["Name", "Face1"],
                ["Name", ["Face1", "Edge2"]] or {"object": "Name", "subs": [...]}.
                Objects by Name, else unique Label.
            doc_name: Document to activate first (default: the active one).
            timeout_ms: Execution timeout inside FreeCAD.

        Returns:
            command, new [[Name, Label, TypeId]], modals (every dialog seen and
            the action taken; "UNHANDLED -> rejected" when no rule matched) and
            panel {object, pages} when the command left a task panel open.
        """
        kwargs = {
            "command": command,
            "modal": modal_rules,
            "select": select,
            "doc_name": doc_name,
        }
        return await _call(get_bridge, "run_command", kwargs, timeout_ms)

    @mcp.tool()
    async def gui_panel(
        action: str = "dump",
        widget: str | None = None,
        value: Any = None,
        values: dict[str, Any] | None = None,
        page: str | None = None,
        typed: bool = False,
        clear_expression: bool = False,
        modal_rules: list[Any] | None = None,
        hidden: bool = True,
        full: bool = False,
        labels: bool = False,
        rows: list[str] | None = None,
        column: int = 0,
        whole_row: bool = False,
        timeout_ms: int = 60000,
    ) -> dict[str, Any]:
        """Read or drive the open task panel through its real widgets.

        Setting goes through the widget's own API so the panel's handlers run;
        hidden, disabled and expression-bound widgets are refused (GUI gating).
        Buttons press the task view's real OK/Cancel, so the panel's own cleanup
        runs (never close a CAM Job panel any other way).

        Args:
            action: "dump" (pages -> widgets with value, tab, hidden, disabled),
                "set", "click", "select_rows", "rows" (table cells), or a button:
                "ok", "cancel", "apply", "close", "yes", "no".
            widget: Widget objectName for set/click/select_rows/rows.
            value: Value for set: combo text/index, bool, number or a quantity
                string such as "0.25 in" / "45 deg".
            values: Several {widget: value} to set in order (instead of widget).
            page: Page title or class when a name is on several pages, e.g.
                "Heights", "Base Geometry", "TaskPanelOpPage".
            typed: Type quantity text into the spin box editor instead of
                setting its raw value.
            clear_expression: Discard an expression binding first (f(x) popup).
            modal_rules: Rules answering dialogs the action opens. Same specs as gui_run_command.
            hidden: dump: include widgets hidden by the panel's logic.
            full: dump: combo items, list items, tooltips.
            labels: dump: include QLabels.
            rows: select_rows: cell texts (substring) to select.
            column: select_rows: column to match.
            whole_row: select_rows: select whole rows (row header click).
            timeout_ms: Execution timeout inside FreeCAD.

        Returns:
            dump: {open, object, panel, pages [{title, class, widgets}]}
            ({open: false} with no panel); set: {set: [{widget, value, modals}]};
            click: {clicked, modals}; select_rows: {selected}; rows: {rows};
            buttons: {pressed, object, dialog_still_open, modals}.
        """
        kwargs = {
            "action": action,
            "widget": widget,
            "value": value,
            "values": values,
            "page": page,
            "typed": typed,
            "clear_expression": clear_expression,
            "modal": modal_rules,
            "hidden": hidden,
            "full": full,
            "labels": labels,
            "rows": rows,
            "column": column,
            "whole_row": whole_row,
        }
        return await _call(get_bridge, "panel", kwargs, timeout_ms)

    @mcp.tool()
    async def gui_edit(
        object_name: str,
        modal_rules: list[Any] | None = None,
        doc_name: str | None = None,
        timeout_ms: int = 60000,
    ) -> dict[str, Any]:
        """Open an object's editor the way double-clicking it in the tree does.

        Fails if a task dialog is already open. The editor only opens for the
        document shown in the active 3D view.

        Args:
            object_name: Object Name, else unique Label.
            modal_rules: Rules answering dialogs the editor opens. Same specs as gui_run_command.
            doc_name: Document to activate first (default: the active one).
            timeout_ms: Execution timeout inside FreeCAD.

        Returns:
            object, modals, pages (task panel page titles; None if none opened).
        """
        kwargs = {
            "object_name": object_name,
            "modal": modal_rules,
            "doc_name": doc_name,
        }
        return await _call(get_bridge, "edit", kwargs, timeout_ms)

    @mcp.tool()
    async def cam_gui_add_base(
        selections: list[Any],
        clear: bool = False,
        modal_rules: list[Any] | None = None,
        doc_name: str | None = None,
        timeout_ms: int = 60000,
    ) -> dict[str, Any]:
        """Add base geometry to the open CAM operation panel like a user would.

        Selects the sub-elements in the 3D view, then presses the Base Geometry
        page's Add button (Clear first with clear=True).

        Args:
            selections: ["Name", "Face1"], ["Name", ["Face1", "Edge2"]],
                {"object": "Name", "subs": [...]} or "Name" (whole object).
            clear: Press Clear before adding.
            modal_rules: Rules answering dialogs Add opens. Same specs as gui_run_command.
            doc_name: Document to activate first (default: the active one).
            timeout_ms: Execution timeout inside FreeCAD.

        Returns:
            base [[Name, [subs]]] of the operation afterwards, modals.
        """
        kwargs = {
            "selections": selections,
            "clear": clear,
            "modal": modal_rules,
            "doc_name": doc_name,
        }
        return await _call(
            get_bridge, "add_base", kwargs, timeout_ms, "guidriver.cam.mcp"
        )

    @mcp.tool()
    async def cam_gui_check(
        job: str | None = None,
        tabs: bool = False,
        include_summary: bool = False,
        doc_name: str | None = None,
        timeout_ms: int = 60000,
    ) -> dict[str, Any]:
        """Heuristic warnings a CAM user would want before posting a job.

        Flags zero feeds/RPM, non-integer RPM, ops in Invalid/Touched state or
        with empty paths, deleteOnReject left set (Cancel would delete the op),
        expressions referencing another op's chain, missing tool controllers and
        StepDown deeper than the applied feeds & speeds preset allows.

        Args:
            job: Job Name or Label. Optional when the document has one job.
            tabs: Also flag through-cuts without holding tags.
            include_summary: Also return the job summary (stock, models, tools
                with feeds and provenance, ops with depths, props, dressups and
                path stats).
            doc_name: Document to activate first (default: the active one).
            timeout_ms: Execution timeout inside FreeCAD.

        Returns:
            job, warnings [str], and summary when asked.
        """
        kwargs = {
            "job_name": job,
            "tabs": tabs,
            "summary": include_summary,
            "doc_name": doc_name,
        }
        return await _call(get_bridge, "check", kwargs, timeout_ms, "guidriver.cam.mcp")

    @mcp.tool()
    async def cam_gui_post(
        job: str | None = None,
        outdir: str | None = None,
        postprocessor: str | None = None,
        args: str | None = None,
        modal_rules: list[Any] | None = None,
        dust: list[bool] | None = None,
        show_editor: bool = False,
        doc_name: str | None = None,
        timeout_ms: int = 120000,
    ) -> dict[str, Any]:
        """Post a CAM job in the GUI, answering the post's own dialogs by rule.

        Unlike cam_post (which suppresses every post dialog), this lets the post
        open its dialogs and answers them like a user: rules from modal_rules,
        the NibblerBOT post's Dust Collection / Export Gcode dialogs from dust /
        show_editor; anything else is rejected after 3 s and reported. Safety:
        dialogs whose title looks like an upload/remote/username step are always
        cancelled, the post's remote_post() hook is disabled, and posts matching
        FREECAD_CAM_POST_DENY (comma-separated, server environment) are refused.

        Args:
            job: Job Name or Label. Optional when the document has one job.
            outdir: Directory for the .nc files (default: FreeCAD's user cache
                dir /guidriver).
            postprocessor: Post name; default the job's own post.
            args: Post arguments used for this run (job is left unchanged).
            modal_rules: Extra rules for the post's dialogs. Same specs as gui_run_command.
            dust: NibblerBOT Dust Collection checkbox states in order (None keeps
                the defaults).
            show_editor: Accept the G-code editor dialog instead of rejecting it.
            doc_name: Document to activate first (default: the active one).
            timeout_ms: Execution timeout inside FreeCAD.

        Returns:
            job, files, dialogs [[title, action]], digest {lines, tool_changes,
            spindle, feeds, units, head, tail}; error when the post returned
            nothing.
        """
        kwargs = {
            "job_name": job,
            "outdir": outdir,
            "postname": postprocessor,
            "args": args,
            "modal": modal_rules,
            "dust": dust,
            "show_editor": show_editor,
            "deny": [
                d.strip()
                for d in os.environ.get("FREECAD_CAM_POST_DENY", "").split(",")
                if d.strip()
            ],
            "doc_name": doc_name,
        }
        return await _call(get_bridge, "post", kwargs, timeout_ms, "guidriver.cam.mcp")
