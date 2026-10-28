# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

# guidriver.cam.modal -- ready-made modal-dialog rules for CAM dialogs.
#
# The generic machinery (Watcher, expect, message_box, input_int, ...) lives in
# guidriver.modal and is re-exported here, so `M = guidriver.cam.modal` gives a
# recipe every rule it needs.

from PySide import QtCore, QtWidgets

from .. import widgets as W
from ..modal import (
    RULES,
    Watcher,
    clear,
    expect,
    find_button,
    info,
    input_int,
    message_box,
    perform,
)

__all__ = [
    # generic, re-exported from guidriver.modal
    "RULES",
    "Watcher",
    "clear",
    "expect",
    "find_button",
    "info",
    "input_int",
    "message_box",
    "perform",
    # CAM dialogs
    "feeds_speeds",
    "job_create",
    "nibblerbot_post",
    "stock_material",
    "tc_chooser",
    "tc_editor",
    "toolbit_selector",
]


def tc_chooser(tool):
    """'Choose a Tool Controller' (DlgTCChooser). tool = T number or label text."""

    def do(w):
        combo = W.find(w, "uiToolController")
        if isinstance(tool, int):
            label = next(
                combo.itemText(i)
                for i in range(combo.count())
                if _tc_number(combo.itemText(i)) == tool
            )
        else:
            label = tool
        W.set(combo, label)
        w.accept()

    return {
        "name": f"tc_chooser({tool})",
        "match": {"has": "uiToolController"},
        "do": do,
    }


def _tc_number(label):
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    for o in doc.Objects if doc else []:
        if o.Label == label and hasattr(o, "ToolNumber"):
            return o.ToolNumber
    return None


def job_create(models, template=None):
    """'Create Job' dialog: tick models by label, pick template by name/path."""

    def do(w):
        W.check_tree_items(W.find(w, "modelTree"), list(models))
        if template is not None:
            combo = W.find(w, "jobTemplate")
            W.set(combo, template)
        w.accept()

    return {
        "name": f"job_create({models}, {template})",
        "match": {"has": "modelTree"},
        "do": do,
    }


def toolbit_selector(numbers, library):
    """'Toolbit Selector' dock: pick the library, ctrl-click the bits by the tool
    number shown in the list, press 'Add to Job'. Tool-number prompts that
    follow are answered by input_int() if included in the rules."""

    def do(w):
        combo = next(
            c
            for c in w.findChildren(QtWidgets.QComboBox)
            if c.isVisible() and c.findText(library) >= 0
        )
        W.set(combo, library)
        view = next(l for l in w.findChildren(QtWidgets.QListWidget) if l.isVisible())
        view.clearSelection()
        for n in numbers:
            _select_numbered(view, n)
        find_button(w, "Add to Job").click()
        QtWidgets.QApplication.processEvents()
        if w.isVisible():
            find_button(w, "Close").click()

    return {
        "name": f"toolbit_selector({numbers}, {library})",
        "match": {"object": "ToolSelector"},
        "do": do,
    }


def _strip_tags(s):
    import re

    return re.sub(r"<[^>]+>", "", s).strip()


def _select_numbered(view, number):
    """Select the item whose first label (the library's tool number) is number."""
    for i in range(view.count()):
        it = view.item(i)
        iw = view.itemWidget(it)
        labels = (
            [
                _strip_tags(c.text())
                for c in iw.findChildren(QtWidgets.QLabel)
                if c.text()
            ]
            if iw
            else []
        )
        if labels and labels[0] == str(number):
            view.setCurrentItem(it, QtCore.QItemSelectionModel.Select)
            return labels[1] if len(labels) > 1 else labels[0]
    raise ValueError(f"no tool #{number} shown in the list (is a library selected?)")


def nibblerbot_post(dust=None, show_editor=False, upload=False):
    """Rules for the NibblerBOT post's dialogs: Dust Collection Options (accept
    defaults, or set checkbox states in order), G-code editor, remote upload."""

    def do_dust(w):
        if dust is not None:
            for cb, state in zip(w.findChildren(QtWidgets.QCheckBox), dust):
                W.set(cb, state)
        w.accept()

    return [
        {
            "name": f"dust_collection({dust})",
            "match": {"title": "Dust Collection Options"},
            "do": do_dust,
        },
        {
            "name": f"gcode_editor({'accept' if show_editor else 'reject'})",
            "match": {"title": "Export Gcode"},
            "do": "accept" if show_editor else "reject",
        },
        {
            "name": f"remote_upload({upload})",
            "match": {"title": "Remote User Folder"},
            "do": "accept" if upload else "reject",
        },
    ]


def stock_material(uuid_or_name):
    """Job panel 'Assign Stock Material' dialog."""

    def do(w):
        import Materials

        uuid = uuid_or_name
        if "-" not in uuid_or_name:
            mm = Materials.MaterialManager()
            uuid = next(
                u
                for u, m in mm.Materials.items()
                if m.Name.lower() == uuid_or_name.lower()
            )
        w.materialTreeWidget.UUID = uuid
        w.onMaterial(uuid)
        w.accept()

    return {
        "name": f"stock_material({uuid_or_name})",
        "match": {"title": "Assign Stock Material"},
        "do": do,
    }


def _button_by_tooltip(w, tip):
    for b in w.findChildren(QtWidgets.QAbstractButton):
        if b.toolTip() == tip and b.isVisible():
            return b
    raise ValueError(f"no visible button with tooltip '{tip}'")


def tc_editor(preset=None, op_type=None, values=None):
    """Job 'Edit' tool controller dialog: optionally set fields, run the F&S
    wizard with a named preset, then OK."""

    def do(w):
        for name, value in (values or {}).items():
            W.set(W.find(w, name), value)
        if preset is not None or op_type is not None:
            _button_by_tooltip(w, "Feeds and Speeds Wizard").click()
            QtWidgets.QApplication.processEvents()
        w.accept()

    return [
        {"name": f"tc_editor({preset})", "match": {"has": "tcNumber"}, "do": do},
        feeds_speeds(preset, op_type),
    ]


def feeds_speeds(preset=None, op_type=None):
    """Feeds & Speeds wizard: pick a named preset (or op type for Auto) and Apply."""

    def do(w):
        if op_type is not None:
            W.set(w.op_type_combo, op_type)
        if preset is not None:
            W.set(w.preset_combo, preset)
        if not w.apply_button.isEnabled():
            raise RuntimeError("Apply disabled: " + w.warnings_label.text())
        w.apply_button.click()

    return {
        "name": f"feeds_speeds({preset}, {op_type})",
        "match": {"title": "Suggest Feeds & Speeds"},
        "do": do,
    }


# CAM rule factories callable by name from the MCP tools (guidriver.mcp.rules).
RULE_FACTORIES = {
    "tc_chooser": tc_chooser,
    "job_create": job_create,
    "toolbit_selector": toolbit_selector,
    "nibblerbot_post": nibblerbot_post,
    "stock_material": stock_material,
    "tc_editor": tc_editor,
    "feeds_speeds": feeds_speeds,
}
