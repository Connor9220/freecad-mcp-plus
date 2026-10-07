# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""GUI state checks and reset for the running FreeCAD. Runs INSIDE FreeCAD (GUI).

Must not import freecad_mcp. Entry points: :func:`gui_health`, :func:`gui_reset`.
Results are JSON/XML-RPC safe (string keys only).
"""

import gc
import os
import re

import FreeCAD
import FreeCADGui
from PySide import QtWidgets

_ERR = re.compile(
    r"Traceback|Error|Exception|<class '.*Error'>|Segmentation|pyException", re.I
)
_WARN = re.compile(r"Warning|WARNING", re.I)


def _report_text():
    """Plain text of the Report view (the QTextEdit that holds the console log)."""
    mw = FreeCADGui.getMainWindow()
    best = ""
    for w in mw.findChildren(QtWidgets.QTextEdit):
        if w.objectName() in ("Report view", "ReportOutput") or "Report" in (
            w.parentWidget().windowTitle() if w.parentWidget() else ""
        ):
            return w.toPlainText()
        t = w.toPlainText()
        if len(t) > len(best):
            best = t
    return best


def _report_since_last():
    """Report-view lines added since the previous call (per FreeCAD session)."""
    text = _report_text()
    last = getattr(FreeCADGui, "_mcp_report_len", 0)
    if last > len(text):  # the view was cleared
        last = 0
    new = text[last:]
    FreeCADGui._mcp_report_len = len(text)
    lines = [ln for ln in new.splitlines() if ln.strip()]
    errors = [ln for ln in lines if _ERR.search(ln)]
    warnings = [ln for ln in lines if _WARN.search(ln) and ln not in errors]
    return {
        "new_lines": len(lines),
        "errors": errors[-30:],
        "warnings": warnings[-15:],
        "tracebacks": sum(1 for ln in lines if "Traceback" in ln),
    }


def _widget_info(w):
    return {
        "class": w.metaObject().className(),
        "title": w.windowTitle(),
        "name": w.objectName(),
    }


def _task_panel():
    """Title(s) of the open task panel and the object in edit, if any."""
    info = {
        "open": bool(FreeCADGui.Control.activeDialog()),
        "in_edit": None,
        "titles": [],
    }
    gdoc = FreeCADGui.ActiveDocument
    if gdoc is not None:
        vp = gdoc.getInEdit()
        if vp is not None:
            info["in_edit"] = {"name": vp.Object.Name, "label": vp.Object.Label}
    if info["open"]:
        mw = FreeCADGui.getMainWindow()
        for tv in mw.findChildren(QtWidgets.QWidget):
            if _alive(tv) and tv.metaObject().className() == "Gui::TaskView::TaskView":
                for box in tv.findChildren(QtWidgets.QWidget):
                    if (
                        _alive(box)
                        and box.metaObject().className() == "Gui::TaskView::TaskBox"
                    ):
                        t = box.windowTitle() or box.property("title") or ""
                        if t:
                            info["titles"].append(str(t))
    return info


def gui_health(include_report=True):
    """Snapshot of the GUI state. See the MCP tool for the fields."""
    app = QtWidgets.QApplication
    mw = FreeCADGui.getMainWindow()
    modal = app.activeModalWidget()
    popup = app.activePopupWidget()
    others = [
        _widget_info(w)
        for w in app.topLevelWidgets()
        if w.isVisible()
        and w is not mw
        and w.metaObject().className() not in ("QMenu", "QToolTip")
    ]
    docs = []
    for name, doc in FreeCAD.listDocuments().items():
        docs.append(
            {
                "name": name,
                "label": doc.Label,
                "file": doc.FileName or None,
                "modified": bool(getattr(doc, "isTouched", lambda: False)())
                or any("Touched" in o.State for o in doc.Objects),
                "objects": len(doc.Objects),
                "invalid": [o.Label for o in doc.Objects if "Invalid" in o.State][:20],
            }
        )
    mdi = mw.findChild(QtWidgets.QMdiArea)
    sub = mdi.activeSubWindow() if mdi else None
    import Path

    out = {
        "freecad": ".".join(FreeCAD.Version()[:3]),
        "home": FreeCAD.getHomePath(),
        "cam_python": os.path.dirname(os.path.dirname(Path.__file__)),
        "display": os.environ.get("DISPLAY"),
        "documents": docs,
        "active_document": FreeCAD.ActiveDocument.Name
        if FreeCAD.ActiveDocument
        else None,
        "active_window": sub.windowTitle() if sub else None,
        "task_panel": _task_panel(),
        "modal_dialog": _widget_info(modal) if modal else None,
        "popup": _widget_info(popup) if popup else None,
        "other_windows": others,
        "selection": [
            {"object": s.ObjectName, "subs": list(s.SubElementNames)}
            for s in FreeCADGui.Selection.getSelectionEx()
        ],
        "workbench": FreeCADGui.activeWorkbench().name()
        if FreeCADGui.activeWorkbench()
        else None,
    }
    try:
        import faulthandler

        out["faulthandler"] = faulthandler.is_enabled()
    except Exception:
        out["faulthandler"] = None
    if include_report:
        out["report"] = _report_since_last()
    out["ok"] = not (
        out["modal_dialog"]
        or out["popup"]
        or out["task_panel"]["open"]
        or (include_report and out["report"]["tracebacks"])
    )
    return out


def _alive(w):
    """False for a Python wrapper whose Qt widget was already deleted."""
    try:
        import shiboken6 as shiboken
    except ImportError:
        try:
            import shiboken2 as shiboken
        except ImportError:
            shiboken = None
    if shiboken is not None:
        return shiboken.isValid(w)
    try:
        w.objectName()
        return True
    except RuntimeError:
        return False


def _click_task_button(names):
    """Press a visible task-panel button (Cancel/Close) like a user would."""
    QtWidgets.QApplication.processEvents()
    mw = FreeCADGui.getMainWindow()
    for tv in mw.findChildren(QtWidgets.QWidget):
        if not _alive(tv) or tv.metaObject().className() != "Gui::TaskView::TaskView":
            continue
        for b in tv.findChildren(QtWidgets.QPushButton):
            if not _alive(b):
                continue
            label = b.text().replace("&", "")
            if b.isVisible() and b.isEnabled() and label in names:
                b.click()
                return label
    return None


def gui_reset(
    close_dialogs=True,
    close_task_panel=True,
    clear_selection=True,
    close_documents=False,
):
    """Bring the GUI back to a known state, the way a user would, and report it."""
    app = QtWidgets.QApplication
    done = []
    if close_dialogs:
        for _ in range(5):
            w = app.activeModalWidget() or app.activePopupWidget()
            if w is None:
                break
            done.append(
                "rejected " + w.metaObject().className() + " " + repr(w.windowTitle())
            )
            if hasattr(w, "reject"):
                w.reject()
            else:
                w.close()
            app.processEvents()
    if close_task_panel and FreeCADGui.Control.activeDialog():
        # Cancel runs the panel's own reject(), which removes its observers.
        # Closing the dialog directly leaks them (the Job panel keeps raising
        # 'NoneType has no attribute setOrigin' on every selection change).
        pressed = _click_task_button(("Cancel", "Close"))
        app.processEvents()
        if pressed:
            done.append(f"task panel: pressed {pressed}")
        if FreeCADGui.Control.activeDialog():
            gdoc = FreeCADGui.ActiveDocument
            if gdoc is not None and gdoc.getInEdit() is not None:
                gdoc.resetEdit()
            FreeCADGui.Control.closeDialog()
            done.append("task panel: forced close (observers may leak)")
        app.processEvents()
    if clear_selection:
        FreeCADGui.Selection.clearSelection()
    if close_documents:
        for name in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(name)
            done.append(f"closed document {name} (not saved)")
    app.processEvents()
    gc.collect()
    health = gui_health(include_report=True)
    health["actions"] = done
    return health
