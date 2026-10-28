# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

# guidriver.taskpanel -- drive task panels (task dialogs) through the GUI.

import FreeCAD
import FreeCADGui
from PySide import QtWidgets

from . import modal as M
from . import widgets as W


def _pump():
    QtWidgets.QApplication.processEvents()


def _python_panel(vp_proxy):
    """Find the Python task panel object hanging off a view provider proxy."""
    for attr in ("panel", "taskPanel", "TaskPanel", "taskpanel", "_panel"):
        p = getattr(vp_proxy, attr, None)
        if p is not None and (hasattr(p, "form") or hasattr(p, "featurePages")):
            return p
    for v in getattr(vp_proxy, "__dict__", {}).values():
        if hasattr(v, "featurePages") or (hasattr(v, "form") and hasattr(v, "accept")):
            return v
    return None


def _on_screen(p):
    if hasattr(p, "featurePages"):
        forms = [pg.form for pg in p.featurePages]
    else:
        forms = p.form if isinstance(p.form, (list, tuple)) else [p.form]
    try:
        return any(f is not None and f.isVisible() for f in forms)
    except RuntimeError:  # deleted C++ widget
        return False


def task_view():
    mw = FreeCADGui.getMainWindow()
    for w in mw.findChildren(QtWidgets.QWidget):
        try:
            if W.qt_class(w) == "Gui::TaskView::TaskView":
                return w
        except RuntimeError:  # wrapper outlived its C++ widget (a dialog just closed)
            continue
    return None


class Panel:
    """Wrapper around the currently open task dialog."""

    def __init__(self):
        vp = (
            FreeCADGui.ActiveDocument.getInEdit() if FreeCADGui.ActiveDocument else None
        )
        self.obj = vp.Object if vp is not None else None
        self.py = (
            _python_panel(vp.Proxy) if vp is not None and hasattr(vp, "Proxy") else None
        )
        if self.py is None:
            # Panels opened with Control.showDialog (e.g. the Job) aren't "in edit";
            # find the view provider whose panel form is on screen.
            for o in FreeCAD.ActiveDocument.Objects:
                proxy = getattr(getattr(o, "ViewObject", None), "Proxy", None)
                p = _python_panel(proxy) if proxy is not None else None
                if p is not None and _on_screen(p):
                    self.obj, self.py = o, p
                    break
        self.pages = []  # (title, class, form)
        if self.py is not None and hasattr(self.py, "featurePages"):
            for p in self.py.featurePages:
                self.pages.append(
                    (
                        getattr(p, "panelTitle", type(p).__name__),
                        type(p).__name__,
                        p.form,
                    )
                )
        elif self.py is not None:
            forms = (
                self.py.form
                if isinstance(self.py.form, (list, tuple))
                else [self.py.form]
            )
            for f in forms:
                self.pages.append(
                    (f.windowTitle() or f.objectName(), type(self.py).__name__, f)
                )
        else:
            tv = task_view()
            if tv is not None:
                self.pages.append(("TaskView", "TaskView", tv))

    # -- lookup --------------------------------------------------------------
    def page(self, key):
        if isinstance(key, int):
            return self.pages[key]
        hits = [p for p in self.pages if key in (p[0], p[1])]
        if not hits:
            raise KeyError(f"no page '{key}' in {[p[0] for p in self.pages]}")
        return hits[
            -1
        ]  # op page is last; the extension page is also titled 'Operation'

    def widget(self, name, page=None):
        pages = [self.page(page)] if page is not None else self.pages
        hits = [(p[0], w) for p in pages if (w := W.find(p[2], name)) is not None]
        if not hits:
            raise KeyError(
                f"no widget '{name}'" + (f" on page '{page}'" if page else "")
            )
        if len(hits) > 1 and page is None:
            raise KeyError(
                f"widget '{name}' is on several pages {[h[0] for h in hits]}; pass page="
            )
        return hits[0][1]

    # -- state -----------------------------------------------------------------
    def dump(self, page=None, full=False, labels=False, hidden=True):
        out = {
            "object": self.obj.Name if self.obj else None,
            "panel": type(self.py).__name__ if self.py else None,
            "pages": [],
        }
        for title, cls, form in [self.page(page)] if page is not None else self.pages:
            ws = []
            for w in W.children(form):
                d = W.describe(w, form, full=full, labels=labels)
                if d and (hidden or not d.get("hidden")):
                    ws.append(d)
            out["pages"].append({"title": title, "class": cls, "widgets": ws})
        return out

    # -- actions ---------------------------------------------------------------
    def set(
        self, name, value, page=None, typed=False, modal=(), clear_expression=False
    ):
        w = self.widget(name, page)
        with M.Watcher(modal) as wt:
            v = W.set(w, value, typed=typed, clear_expression_first=clear_expression)
        return {"widget": name, "value": v, "modals": wt.log}

    def set_many(self, values, page=None, modal=()):
        """values: {widget: value} applied in order (dict order)."""
        return [self.set(k, v, page=page, modal=modal) for k, v in values.items()]

    def click(self, name, page=None, modal=()):
        w = self.widget(name, page)
        with M.Watcher(modal) as wt:
            W.click(w)
        return {"clicked": name, "modals": wt.log}

    def select_rows(self, name, labels, column=0, page=None, whole_row=False):
        """Select rows of a table widget (e.g. Job 'toolControllerList') by text."""
        return W.select_rows(
            self.widget(name, page), labels, column, whole_row=whole_row
        )

    def rows(self, name, page=None):
        return W.table_rows(self.widget(name, page))

    def button(self, which="ok", modal=()):
        """Press a task-view standard button: ok, cancel, apply, close, yes, no."""
        tv = task_view()
        role = {
            "ok": QtWidgets.QDialogButtonBox.Ok,
            "cancel": QtWidgets.QDialogButtonBox.Cancel,
            "apply": QtWidgets.QDialogButtonBox.Apply,
            "close": QtWidgets.QDialogButtonBox.Close,
            "yes": QtWidgets.QDialogButtonBox.Yes,
            "no": QtWidgets.QDialogButtonBox.No,
        }[which]
        boxes = []
        for b in tv.findChildren(QtWidgets.QDialogButtonBox) if tv else []:
            try:
                if W.alive(b) and b.isVisible():
                    boxes.append(b)
            except RuntimeError:  # wrapper outlived its C++ widget
                continue
        btn = next((b.button(role) for b in boxes if b.button(role) is not None), None)
        if btn is None:
            raise RuntimeError(f"no visible '{which}' button in the task view")
        name = self.obj.Name if self.obj else None
        with M.Watcher(modal) as wt:
            btn.click()
            _pump()
        still = FreeCADGui.Control.activeDialog()
        return {
            "pressed": which,
            "object": name,
            "dialog_still_open": bool(still),
            "modals": wt.log,
        }

    def ok(self, modal=()):
        return self.button("ok", modal)

    def cancel(self, modal=()):
        return self.button("cancel", modal)

    def apply(self, modal=()):
        return self.button("apply", modal)


def current(cls=None):
    """The open task dialog as a Panel (cls: a Panel subclass), or None."""
    if not FreeCADGui.Control.activeDialog():
        return None
    return (cls or Panel)()


def select(selections):
    FreeCADGui.Selection.clearSelection()
    doc = FreeCAD.ActiveDocument
    for s in selections:
        if isinstance(s, (tuple, list)):
            obj, subs = s
            subs = [subs] if isinstance(subs, str) else subs
            for sub in subs:
                FreeCADGui.Selection.addSelection(doc.Name, obj.Name, sub)
        else:
            FreeCADGui.Selection.addSelection(s)
    _pump()


def run(command, select_first=None, modal=()):
    """Run a GUI command like a toolbar click. Returns new objects, modals seen
    and the task panel it left open (if any)."""
    doc = FreeCAD.ActiveDocument
    before = {o.Name for o in doc.Objects}
    if select_first is not None:
        select(select_first)
    with M.Watcher(modal) as wt:
        FreeCADGui.runCommand(command)
        _pump()
    doc = FreeCAD.ActiveDocument
    new = [o for o in doc.Objects if o.Name not in before]
    p = current()
    return {
        "command": command,
        "new": [(o.Name, o.Label, o.TypeId) for o in new],
        "modals": wt.log,
        "panel": (
            None
            if p is None
            else {
                "object": p.obj.Name if p.obj else None,
                "pages": [t for t, _, _ in p.pages],
            }
        ),
    }


def edit(obj, modal=()):
    """Open obj's editor the way double-clicking it in the tree does."""
    if FreeCADGui.Control.activeDialog():
        raise RuntimeError("a task dialog is already open")
    with M.Watcher(modal) as wt:
        FreeCADGui.ActiveDocument.setEdit(obj.ViewObject, 0)
        _pump()
    p = current()
    return {
        "object": obj.Name,
        "modals": wt.log,
        "pages": None if p is None else [t for t, _, _ in p.pages],
    }
