# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

# guidriver.mcp -- JSON-safe entry points for the MCP+ gui_* tools.
#
# The MCP server sends a one-liner such as
#   import guidriver.mcp as g; _result_ = g.run_command("Part_Box")
# through the bridge. Everything returned here is plain dicts/lists/strings
# with string keys, so XML-RPC can marshal it.
#
# Modal rules arrive as JSON specs:
#   "input_int"                                   factory by name, no arguments
#   {"rule": "message_box", "args": ["Yes"]}      factory with args/kwargs
#   {"rule": "tc_chooser", "args": [70]}          CAM factories (guidriver.cam.modal)
#   {"match": {"title": "Delete"}, "do": {"button": "Yes"}, "name": "..."}
#                                                 declarative rule (see modal.perform)

import FreeCAD
import FreeCADGui

from . import modal as M
from . import taskpanel as T

_PLAIN = (str, int, float, bool, type(None))


def jsonable(v):
    """Plain JSON/XML-RPC-safe copy of v (string keys, lists, user strings)."""
    if isinstance(v, _PLAIN):
        return v
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [jsonable(x) for x in v]
    if isinstance(v, FreeCAD.Vector):
        return [v.x, v.y, v.z]
    if hasattr(v, "UserString"):
        return v.UserString
    return str(v)


def _factory(name):
    cam = name.startswith("cam.")
    name = name[4:] if cam else name
    tables = [] if cam else [M.RULE_FACTORIES]
    try:
        from .cam import modal as CM

        tables.append(CM.RULE_FACTORIES)
    except ImportError:
        pass
    for table in tables:
        if name in table:
            return table[name]
    known = sorted({n for t in tables for n in t})
    raise ValueError(f"unknown modal rule '{name}'; known: {known}")


def rules(specs):
    """Build Watcher rules from JSON specs (see the module comment)."""
    out = []
    for spec in specs or ():
        if isinstance(spec, str):
            spec = {"rule": spec}
        if not isinstance(spec, dict):
            raise ValueError(f"bad modal rule spec {spec!r}")
        if "rule" in spec:
            r = _factory(spec["rule"])(*spec.get("args", ()), **spec.get("kwargs", {}))
            out.extend(r if isinstance(r, list) else [r])
        elif "match" in spec and "do" in spec:
            if not isinstance(spec["do"], (str, dict)):
                raise ValueError(f"bad modal action {spec['do']!r}")
            out.append(dict(spec))
        else:
            raise ValueError(f"modal rule spec needs 'rule' or 'match'+'do': {spec!r}")
    return out


def activate(doc_name=None):
    """Make doc_name the active document (App and GUI); return the document."""
    if doc_name:
        doc = FreeCAD.getDocument(doc_name)
        FreeCAD.setActiveDocument(doc.Name)
        FreeCADGui.setActiveDocument(doc.Name)
        return doc
    doc = FreeCAD.ActiveDocument
    if doc is None:
        raise RuntimeError("no active document (pass doc_name)")
    return doc


def obj(doc, name):
    """Object by Name, else by unique Label."""
    o = doc.getObject(name)
    if o is not None:
        return o
    hits = doc.getObjectsByLabel(name)
    if len(hits) != 1:
        raise ValueError(f"no single object named or labelled '{name}' in {doc.Name}")
    return hits[0]


def selections(doc, specs):
    """JSON selection specs -> taskpanel.select() input.

    Each spec: "Name", ["Name", "Face1"], ["Name", ["Face1", "Edge2"]] or
    {"object": "Name", "subs": ["Face1"]}."""
    out = []
    for s in specs or ():
        if isinstance(s, str):
            out.append(obj(doc, s))
        elif isinstance(s, dict):
            subs = s.get("subs") or []
            out.append((obj(doc, s["object"]), subs) if subs else obj(doc, s["object"]))
        elif isinstance(s, (list, tuple)) and len(s) == 2:
            out.append((obj(doc, s[0]), s[1]))
        else:
            raise ValueError(f"bad selection spec {s!r}")
    return out


def run_command(command, modal=None, select=None, doc_name=None):
    """Run a GUI command like a toolbar click (guidriver.run)."""
    doc = activate(doc_name)
    sel = selections(doc, select) if select else None
    return jsonable(T.run(command, select_first=sel, modal=rules(modal)))


def edit(object_name, modal=None, doc_name=None):
    """Open an object's editor like a tree double-click (guidriver.edit)."""
    doc = activate(doc_name)
    return jsonable(T.edit(obj(doc, object_name), modal=rules(modal)))


BUTTONS = ("ok", "cancel", "apply", "close", "yes", "no")
ACTIONS = ("dump", "set", "click", "select_rows", "rows", *BUTTONS)


def panel(
    action="dump",
    widget=None,
    value=None,
    values=None,
    page=None,
    typed=False,
    clear_expression=False,
    modal=None,
    hidden=True,
    full=False,
    labels=False,
    rows=None,
    column=0,
    whole_row=False,
    panel_cls=None,
):
    """Act on the open task panel. action: dump, set (widget+value, or values
    {widget: value} in order), click, select_rows, rows, or a task-view button
    (ok, cancel, apply, close, yes, no)."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}, not {action!r}")
    p = T.current(panel_cls)
    if p is None:
        if action == "dump":
            return {"open": False}
        raise RuntimeError("no task panel is open")
    r = rules(modal)
    if action == "dump":
        out = p.dump(page=page, full=full, labels=labels, hidden=hidden)
        out["open"] = True
        return jsonable(out)
    if action == "set":
        if values is None:
            if widget is None:
                raise ValueError("set needs widget+value or values")
            values = {widget: value}
        out = [
            p.set(
                k, v, page=page, typed=typed, modal=r, clear_expression=clear_expression
            )
            for k, v in values.items()
        ]
        return jsonable({"set": out})
    if widget is None and action in ("click", "select_rows", "rows"):
        raise ValueError(f"{action} needs widget")
    if action == "click":
        return jsonable(p.click(widget, page=page, modal=r))
    if action == "select_rows":
        picked = p.select_rows(
            widget, rows or [], column=column, page=page, whole_row=whole_row
        )
        return jsonable({"selected": picked})
    if action == "rows":
        return jsonable({"rows": p.rows(widget, page=page)})
    return jsonable(p.button(action, modal=r))
