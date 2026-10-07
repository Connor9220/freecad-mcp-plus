# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Captioned, mouse-driven demo runner. Runs INSIDE FreeCAD (GUI, via the bridge).

Must not import freecad_mcp. Entry points: :func:`demo_probe`, :func:`demo_start`,
:func:`demo_status`, :func:`demo_abort`. Results are XML-RPC safe (string keys).

The GUI thread is never blocked: every step is a chain of small actions run one
after another by ``QTimer.singleShot`` (glides, clicks, key presses, waits), so a
bridge call only starts the run and returns. The run object lives on
``FreeCADGui._mcp_demo`` and the server polls :func:`demo_status` with short calls.

Mouse and keyboard input is real X11 input made with XTest (ctypes), sent to the
display FreeCAD runs on. Only nested test displays (":2" and up) are driven; the
user's own screen (":0", ":1", unset) is refused. No Xlib query functions are
called through ctypes (XDisplayWidth crashed FreeCAD); only XOpenDisplay, the
keysym lookups, XTest fake events and XFlush are used.
"""

import contextlib
import ctypes
import ctypes.util
import html
import os
import re
import time
import traceback

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui, QtWidgets

CAPTION_NAME = "mcpDemoCaption"
STALE_S = 60.0  # a "running" run with no activity for this long can be replaced

_DISPLAY_RE = re.compile(r"^(?:localhost|unix)?:(\d+)(?:\.\d+)?$")

# characters typed with their X keysym name, and those that need Shift (US layout)
_KEYSYMS = {
    " ": "space",
    ".": "period",
    ",": "comma",
    "-": "minus",
    "/": "slash",
    "=": "equal",
    "'": "apostrophe",
    ";": "semicolon",
    "\n": "Return",
    "\t": "Tab",
    "+": "plus",
    "*": "asterisk",
    "(": "parenleft",
    ")": "parenright",
    "_": "underscore",
    ":": "colon",
    '"': "quotedbl",
    "%": "percent",
    "^": "asciicircum",
}
_SHIFTED = set('+*()_:"%^')

_VIEWS = {
    "iso": "viewIsometric",
    "isometric": "viewIsometric",
    "front": "viewFront",
    "top": "viewTop",
    "right": "viewRight",
    "left": "viewLeft",
    "rear": "viewRear",
    "bottom": "viewBottom",
}


def display_ok(display):
    """(ok, reason): only nested test displays (":2" and up) may be driven."""
    if not display:
        return False, "DISPLAY is not set"
    m = _DISPLAY_RE.match(str(display).strip())
    if not m:
        return False, f"DISPLAY {display!r} is not a local X display"
    if int(m.group(1)) < 2:
        return False, (
            f"DISPLAY {display!r} looks like the user's real screen; demos are only "
            "recorded on nested test displays (:2 or higher)"
        )
    return True, ""


def _alive(w):
    """False for a Python wrapper whose Qt object was already deleted."""
    if w is None:
        return False
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


def _mw():
    return FreeCADGui.getMainWindow()


# ---------------------------------------------------------------- X11 input
class _XInput:
    """Real pointer/keyboard events through XTest on one X display."""

    def __init__(self, display):
        x11 = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11") or "libX11.so.6")
        xt = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Xtst") or "libXtst.so.6")
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        x11.XStringToKeysym.restype = ctypes.c_ulong
        x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x11.XFlush.argtypes = [ctypes.c_void_p]
        xt.XTestFakeMotionEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        xt.XTestFakeButtonEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        xt.XTestFakeKeyEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        self.x11, self.xt = x11, xt
        self.dpy = x11.XOpenDisplay(display.encode())
        if not self.dpy:
            raise RuntimeError(f"cannot open X display {display!r} for XTest")
        self.display = display

    def move(self, x, y):
        self.xt.XTestFakeMotionEvent(self.dpy, -1, int(x), int(y), 0)
        self.x11.XFlush(self.dpy)

    def button(self, down, b=1):
        self.xt.XTestFakeButtonEvent(self.dpy, b, 1 if down else 0, 0)
        self.x11.XFlush(self.dpy)

    def key(self, name, down):
        ks = self.x11.XStringToKeysym(_KEYSYMS.get(name, name).encode())
        kc = self.x11.XKeysymToKeycode(self.dpy, ks) if ks else 0
        if not kc:
            raise ValueError(f"no key for {name!r}")
        self.xt.XTestFakeKeyEvent(self.dpy, kc, 1 if down else 0, 0)
        self.x11.XFlush(self.dpy)


def _xinput(display):
    cache = getattr(FreeCADGui, "_mcp_demo_x11", None)
    if cache is None:
        cache = FreeCADGui._mcp_demo_x11 = {}
    if display not in cache:
        cache[display] = _XInput(display)
    return cache[display]


def _cursor():
    p = QtGui.QCursor.pos()
    return p.x(), p.y()


# ---------------------------------------------------------------- action queue
class Queue:
    """Chain of (delay_ms, fn) actions, each run on a QTimer after the previous one."""

    def __init__(self, run, on_done=None, root=None):
        self.run = run
        self.items = []
        self.on_done = on_done
        self.root = root or self  # the queue that is actually running

    def add(self, delay, fn=None):
        """Run fn (default: nothing) delay ms after the previous action."""
        self.items.append((int(delay), fn or (lambda: None)))
        return self

    def wait(self, ms):
        """Pause for ms."""
        return self.add(ms)

    def defer(self, builder, delay=0):
        """builder(sub_queue) runs when reached; the actions it adds run next."""

        def expand():
            sub = Queue(self.run, root=self.root)
            builder(sub)
            self.root.items[0:0] = sub.items

        return self.add(delay, expand)

    def click(self, target_fn, glide_ms=None, b=1, on_fail=None, what="click"):
        """Glide to target_fn() -> (x, y) and click.

        If the target is not found, nothing is clicked; the error is recorded and
        on_fail() runs instead.
        """
        run = self.run
        ms = run.glide_ms if glide_ms is None else glide_ms

        def begin(next_):
            try:
                x1, y1 = target_fn()
            except Exception as exc:
                run.last_click_ok = False
                if on_fail is None:
                    run.error(f"{what}: {exc}")
                else:
                    try:
                        on_fail()
                        run.note(f"{what}: {exc}; done without the mouse")
                    except Exception as exc2:
                        run.error(f"{what}: {exc}; fallback failed: {exc2}")
                next_()
                return
            x0, y0 = _cursor()
            n = max(1, ms // 16)
            state = {"i": 0}

            def tick():
                if run.stopped:
                    return
                state["i"] += 1
                t = state["i"] / n
                s = t * t * (3 - 2 * t)
                run.x.move(x0 + (x1 - x0) * s, y0 + (y1 - y0) * s)
                if state["i"] < n:
                    QtCore.QTimer.singleShot(16, tick)
                else:
                    QtCore.QTimer.singleShot(60, press)

            def press():
                run.x.button(True, b)
                QtCore.QTimer.singleShot(70, release)

            def release():
                run.x.button(False, b)
                run.last_click_ok = True
                next_()

            tick()

        self.items.append((0, ("async", begin)))
        return self

    def type(self, text, per=60):
        """Type text key by key (Shift for capitals and shifted symbols)."""
        for ch in str(text):
            shift = ch.isupper() or ch in _SHIFTED
            name = ch.lower() if ch.isalpha() else ch

            def press(n=name, sh=shift):
                x = self.run.x
                if sh:
                    x.key("Shift_L", True)
                try:
                    x.key(n, True)
                    x.key(n, False)
                finally:
                    if sh:
                        x.key("Shift_L", False)

            self.add(per, press)
        return self

    def keys(self, *combo):
        """Press a key combination such as Control_L + a."""

        def f():
            x = self.run.x
            for k in combo:
                x.key(k, True)
            for k in reversed(combo):
                x.key(k, False)

        return self.add(80, f)

    def start(self):
        """Run the chain (returns at once)."""
        self._next()

    def _next(self):
        run = self.run
        if run.stopped:
            return
        run.touch()
        run.keep_caption_on_top()
        if not self.items:
            if self.on_done:
                QtCore.QTimer.singleShot(0, self.on_done)
            return
        delay, fn = self.items.pop(0)
        if isinstance(fn, tuple):  # async action: calls next_ itself
            QtCore.QTimer.singleShot(delay, lambda: self._guard_async(fn[1]))
            return

        def go():
            if run.stopped:
                return
            try:
                fn()
            except Exception as exc:
                run.error(f"{type(exc).__name__}: {exc}")
            finally:
                self._next()

        QtCore.QTimer.singleShot(delay, go)

    def _guard_async(self, begin):
        if self.run.stopped:
            return
        try:
            begin(self._next)
        except Exception as exc:
            self.run.error(f"{type(exc).__name__}: {exc}")
            self._next()


# ---------------------------------------------------------------- target finders
def _center(w, rect=None):
    r = rect if rect is not None else w.rect()
    g = w.mapToGlobal(r.center())
    return g.x(), g.y()


def _visible_tab(names):
    """(bar, index) of a visible tab whose text is one of names, else None."""
    for bar in _mw().findChildren(QtWidgets.QTabBar):
        if not _alive(bar) or not bar.isVisible():
            continue
        for i in range(bar.count()):
            if bar.tabText(i).replace("&", "") in names:
                return bar, i
    return None


def _tab_needs_click(names):
    found = _visible_tab(names)
    return found is not None and found[0].currentIndex() != found[1]


def _tab_target(names):
    def f():
        found = _visible_tab(names)
        if found is None:
            raise LookupError(f"tab {names[0]!r} not found")
        bar, i = found
        return _center(bar, bar.tabRect(i))

    return f


def _prop_editor():
    for t in _mw().findChildren(QtWidgets.QTreeView):
        if _alive(t) and t.objectName() == "propertyEditorData" and t.isVisible():
            return t
    raise LookupError("Property View (Data) is not visible")


def _prop_index(label, prop_name, col=1):
    p = _prop_editor()
    m = p.model()
    squeezed = prop_name.replace(" ", "")
    for g in range(m.rowCount()):
        gi = m.index(g, 0)
        for r in range(m.rowCount(gi)):
            i = m.index(r, 0, gi)
            text = str(m.data(i) or "")
            if text in (label, prop_name) or text.replace(" ", "") == squeezed:
                return m.index(r, col, gi)
    raise LookupError(f"property {label!r} is not in the Property View")


def _prop_cell(label, prop_name, xfrac=0.35):
    def f():
        p = _prop_editor()
        i = _prop_index(label, prop_name)
        p.scrollTo(i)
        r = p.visualRect(i)
        g = p.viewport().mapToGlobal(
            QtCore.QPoint(r.left() + int(r.width() * xfrac), r.center().y())
        )
        return g.x(), g.y()

    return f


def _tree_widget():
    for t in _mw().findChildren(QtWidgets.QTreeWidget):
        if _alive(t) and t.metaObject().className() == "Gui::TreeWidget":
            return t
    raise LookupError("tree view not found")


def _tree_item(label):
    def f():
        t = _tree_widget()
        if not t.isVisible():
            raise LookupError("the tree view is hidden (auto-hide overlay?)")
        flags = QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive
        items = [i for i in t.findItems(label, flags, 0) if _alive(i)]
        if not items:
            raise LookupError(f"tree item {label!r} not found")
        ancestors = []
        parent = items[0].parent()
        while parent is not None and _alive(parent):
            ancestors.append(parent)
            parent = parent.parent()
        collapsed = [a for a in reversed(ancestors) if not a.isExpanded()]
        for a in collapsed:
            if _alive(a):
                a.setExpanded(True)  # FreeCAD may rebuild items on expand
        if collapsed:
            items = [i for i in t.findItems(label, flags, 0) if _alive(i)]
            if not items:
                raise LookupError(f"tree item {label!r} vanished on expand")
        it = items[0]
        t.scrollToItem(it)
        r = t.visualItemRect(it)
        if not t.isVisible() or r.isEmpty():
            raise LookupError(f"tree item {label!r} is not visible")
        g = t.viewport().mapToGlobal(
            QtCore.QPoint(r.left() + min(40, r.width() // 2), r.center().y())
        )
        return g.x(), g.y()

    return f


def _popup_item(text):
    def f():
        w = QtWidgets.QApplication.activePopupWidget()
        if w is None:
            raise LookupError("no dropdown is open")
        view = (
            w
            if isinstance(w, QtWidgets.QAbstractItemView)
            else w.findChild(QtWidgets.QAbstractItemView)
        )
        if view is None:
            raise LookupError("the open popup has no item list")
        m = view.model()
        for r in range(m.rowCount()):
            i = m.index(r, 0)
            if str(m.data(i)) == str(text):
                view.scrollTo(i)
                g = view.viewport().mapToGlobal(view.visualRect(i).center())
                return g.x(), g.y()
        raise LookupError(f"dropdown has no item {text!r}")

    return f


def _view3d_empty():
    v = _mw().findChild(QtWidgets.QMdiArea)
    if v is None:
        raise LookupError("no 3D view area")
    g = v.mapToGlobal(QtCore.QPoint(int(v.width() * 0.08), int(v.height() * 0.88)))
    return g.x(), g.y()


def _all_widgets(cls):
    seen = []
    for top in [_mw(), *QtWidgets.QApplication.topLevelWidgets()]:
        if not _alive(top) or not top.isVisible():
            continue
        for w in top.findChildren(cls):
            if _alive(w) and w not in seen:
                seen.append(w)
    return seen


def _button(text):
    def f():
        for b in _all_widgets(QtWidgets.QAbstractButton):
            if b.isVisible() and b.isEnabled() and b.text().replace("&", "") == text:
                return _center(b)
        raise LookupError(f"no visible button {text!r}")

    return f


def _named_widget(name):
    found = [w for w in _all_widgets(QtWidgets.QWidget) if w.objectName() == name]
    if not found:
        raise LookupError(f"no widget named {name!r}")
    visible = [w for w in found if w.isVisible()]
    return (visible or found)[0]


def _tab_of(w):
    """Target for the QTabWidget tab that holds w, or None if w is not in one."""
    node = w
    while node is not None:
        p = node.parentWidget()
        if isinstance(p, QtWidgets.QStackedWidget) and isinstance(
            p.parentWidget(), QtWidgets.QTabWidget
        ):
            tw = p.parentWidget()
            idx = tw.indexOf(node)
            if tw.currentIndex() == idx:
                return None
            bar = tw.tabBar()
            return lambda: _center(bar, bar.tabRect(idx))
        node = p
    return None


def _close_popup():
    w = QtWidgets.QApplication.activePopupWidget()
    if w is not None:
        w.close()
        return True
    return False


# ---------------------------------------------------------------- properties
def _find_object(ref):
    doc = FreeCAD.ActiveDocument
    if doc is None:
        raise LookupError("no active document")
    obj = doc.getObject(ref)
    if obj is None:
        matches = doc.getObjectsByLabel(ref)
        obj = matches[0] if matches else None
    if obj is None:
        raise LookupError(f"no object {ref!r} in {doc.Name}")
    return obj


def _find_property(obj, name):
    squeezed = str(name).replace(" ", "")
    for p in obj.PropertiesList:
        if p in (name, squeezed) or p.lower() == squeezed.lower():
            return p
    raise LookupError(f"{obj.Label} has no property {name!r}")


def _as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "checked")
    return bool(value)


def _with_units(obj, prop, value):
    """Text to type: bare numbers get an explicit unit.

    The Property View shows (and parses) values in the user's unit schema, which
    is often inches.
    """
    text = str(value).strip()
    cur = getattr(obj, prop)
    if not isinstance(cur, FreeCAD.Units.Quantity):
        return text
    try:
        float(text)
    except ValueError:
        return text  # already carries a unit (or is an expression)
    for suffix in ("mm", "deg", "mm/min", "s"):
        with contextlib.suppress(Exception):
            if cur.Unit == FreeCAD.Units.Quantity(f"1 {suffix}").Unit:
                return f"{text} {suffix}"
    return text


def _value_matches(obj, prop, kind, value):  # noqa: PLR0911 - one return per type
    cur = getattr(obj, prop)
    if kind == "bool":
        return bool(cur) == _as_bool(value)
    if kind == "enum":
        return str(cur) == str(value)
    if isinstance(cur, FreeCAD.Units.Quantity):
        try:
            want = FreeCAD.Units.Quantity(str(value))
        except Exception:
            return False
        return abs(cur.Value - want.Value) <= 1e-6 * max(1.0, abs(want.Value))
    if isinstance(cur, bool):
        return cur == _as_bool(value)
    if isinstance(cur, (int, float)):
        try:
            return abs(float(cur) - float(value)) <= 1e-9 * max(1.0, abs(float(value)))
        except ValueError:
            return False
    return str(cur) == str(value)


def _set_directly(obj, prop, kind, value):
    cur = getattr(obj, prop)
    if kind == "bool":
        setattr(obj, prop, _as_bool(value))
    elif kind == "enum" or isinstance(cur, FreeCAD.Units.Quantity):
        setattr(obj, prop, str(value))
    elif isinstance(cur, bool):
        setattr(obj, prop, _as_bool(value))
    elif isinstance(cur, int):
        setattr(obj, prop, int(float(value)))
    elif isinstance(cur, float):
        setattr(obj, prop, float(value))
    else:
        setattr(obj, prop, value)


# ---------------------------------------------------------------- the run
class DemoRun:
    """One demo run: steps, caption, log. Lives on FreeCADGui._mcp_demo."""

    def __init__(self, steps, title, hold_ms, glide_ms, display):
        self.steps = steps
        self.title = title or ""
        self.hold_ms = int(hold_ms)
        self.glide_ms = int(glide_ms)
        self.display = display
        self.x = _xinput(display)
        self.state = "starting"
        self.stopped = False
        self.i = 0
        self.t_start = time.time()
        self.t_first = None
        self.t_done = None
        self.last = time.time()
        self.log = []
        self.cur = None
        self.errors = []
        self.last_click_ok = False
        self.label = None
        self.ns = {
            "__builtins__": __builtins__,
            "FreeCAD": FreeCAD,
            "App": FreeCAD,
            "FreeCADGui": FreeCADGui,
            "Gui": FreeCADGui,
            "QtCore": QtCore,
            "QtGui": QtGui,
            "QtWidgets": QtWidgets,
        }

    # -- bookkeeping
    def touch(self):
        """Mark activity (a stale run can be replaced)."""
        self.last = time.time()

    def keep_caption_on_top(self):
        """Docks and overlays can restack above the caption; raise it again."""
        if _alive(self.label) and self.label.isVisible():
            self.label.raise_()

    def error(self, msg):
        """Record an error on the current step (or the run)."""
        target = self.cur["errors"] if self.cur is not None else self.errors
        target.append(str(msg))

    def note(self, msg):
        """Record a note on the current step."""
        if self.cur is not None:
            self.cur["notes"].append(str(msg))

    def status(self):
        """XML-RPC safe snapshot of the run."""
        return {
            "state": self.state,
            "step": self.i,
            "n": len(self.steps),
            "t_start": self.t_start,
            "t_first": self.t_first,
            "t_done": self.t_done,
            "idle_s": round(time.time() - self.last, 2),
            "log": [dict(e) for e in self.log],
            "errors": list(self.errors),
            "display": self.display,
        }

    # -- caption overlay
    def _caption(self, caption, detail, n, total):
        mw = _mw()
        lab = self.label if _alive(self.label) else None
        if lab is None:
            _remove_captions()
            lab = QtWidgets.QLabel(mw)
            lab.setObjectName(CAPTION_NAME)
            lab.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
            lab.setStyleSheet(
                "QLabel{background:rgba(15,18,24,215);color:#f2f2f2;"
                "border-left:6px solid #e5484d;padding:10px 16px;font-size:20px;"
                "border-radius:4px}"
            )
            lab.setWordWrap(True)
            self.label = lab
        parts = []
        if self.title:
            parts.append(
                f"<span style='font-size:13px;color:#9aa4b2'>{html.escape(self.title)}</span>"
            )
        parts.append(f"<b>{html.escape(caption)}</b>")
        if detail:
            parts.append(
                f"<span style='font-size:16px;color:#c8c8c8'>{html.escape(detail)}</span>"
            )
        parts.append(
            f"<span style='font-size:13px;color:#8a8a8a'>step {n}/{total}</span>"
        )
        lab.setText("<br>".join(parts))
        lab.setFixedWidth(760)
        lab.adjustSize()
        view = mw.findChild(QtWidgets.QMdiArea)
        if view is not None:
            p = view.mapTo(mw, QtCore.QPoint(0, 0))
            lab.move(p.x() + max(24, view.width() - lab.width() - 24), p.y() + 24)
        lab.show()
        lab.raise_()

    # -- steps
    def begin(self):
        """Schedule the first step."""
        self.state = "running"
        QtCore.QTimer.singleShot(300, self._run_step)

    def _run_step(self):
        if self.stopped:
            return
        self.touch()
        if self.i >= len(self.steps):
            self._finish()
            return
        step = self.steps[self.i]
        self.i += 1
        caption = str(step.get("caption", ""))
        detail = str(step.get("detail", "") or "")
        now = time.time()
        if self.t_first is None:
            self.t_first = now
        self.cur = {
            "step": self.i,
            "t": now,
            "caption": caption,
            "detail": detail,
            "t_end": None,
            "actions_s": None,
            "notes": [],
            "errors": [],
        }
        self.log.append(self.cur)
        try:
            self._caption(caption, detail, self.i, len(self.steps))
        except Exception as exc:
            self.error(f"caption: {exc}")
        QtWidgets.QApplication.processEvents()

        q = Queue(self, on_done=self._step_done)
        for action in step.get("actions") or []:
            q.defer(lambda sub, a=action: self._build_action(sub, a))
        q.defer(self._build_after)
        q.start()

    def _step_done(self):
        if self.stopped:
            return
        cur = self.cur
        cur["t_end"] = time.time()
        cur["actions_s"] = round(cur["t_end"] - cur["t"], 2)
        QtCore.QTimer.singleShot(self.hold_ms, self._run_step)

    def _finish(self):
        self.t_done = time.time()
        self.cur = None
        _remove_captions()
        self.label = None
        self.state = "done"

    def abort(self, reason="aborted"):
        """Stop the chain, close popups and remove the caption."""
        self.stopped = True
        if self.state in ("starting", "running"):
            self.state = "aborted"
            self.errors.append(reason)
        self.t_done = self.t_done or time.time()
        _close_popup()
        _remove_captions()
        self.label = None

    # -- actions
    def _build_action(self, q, action):
        if not isinstance(action, dict) or len(action) != 1:
            self.error(f"bad action {action!r}")
            return
        kind, arg = next(iter(action.items()))
        builder = getattr(self, f"_act_{kind}", None)
        if builder is None:
            self.error(f"unknown action {kind!r}")
            return
        builder(q, arg)

    def _act_python(self, q, code):
        def run_code():
            self.ns["doc"] = FreeCAD.ActiveDocument
            try:
                exec(compile(str(code), f"<demo step {self.i}>", "exec"), self.ns)
            except Exception:
                self.error("python: " + traceback.format_exc(limit=3).strip()[-800:])

        q.add(0, run_code).wait(200)

    def _act_command(self, q, name):
        # deferred so a modal dialog the command opens does not stall the chain
        q.add(
            0, lambda: QtCore.QTimer.singleShot(0, lambda: FreeCADGui.runCommand(name))
        )
        q.wait(700)

    def _act_wait(self, q, seconds):
        q.wait(max(0, int(float(seconds) * 1000)))

    def _act_view(self, q, which):
        which = str(which).lower()

        def apply():
            gdoc = FreeCADGui.ActiveDocument
            view = gdoc.activeView() if gdoc else None
            if view is None:
                raise LookupError("no active 3D view")
            if which != "fit":
                meth = _VIEWS.get(which)
                if meth is None:
                    raise ValueError(f"unknown view {which!r}")
                getattr(view, meth)()
            view.fitAll()

        q.add(0, apply).wait(700)

    def _ensure_tab(self, q, names):
        if _tab_needs_click(names):
            q.click(_tab_target(names), what=f"tab {names[0]}").wait(350)

    def _act_click_tree(self, q, ref):
        try:
            obj = _find_object(ref)
        except LookupError:
            self._ensure_tab(q, ("Model", "Tree View", "Tree view"))
            q.click(_tree_item(str(ref)), what=f"tree item {ref}").wait(650)
            return
        self._select_in_tree(q, obj)

    def _select_in_tree(self, q, obj):
        sel = FreeCADGui.Selection.getSelection()
        if len(sel) == 1 and sel[0] is obj:
            return  # clicking a selected item again may start a rename
        self._ensure_tab(q, ("Model", "Tree View", "Tree view"))
        q.click(
            _tree_item(obj.Label),
            what=f"tree item {obj.Label}",
            on_fail=lambda: (
                FreeCADGui.Selection.clearSelection(),
                FreeCADGui.Selection.addSelection(obj),
            ),
        ).wait(650)

    def _act_property(self, q, spec):
        obj = _find_object(spec["object"])
        prop = _find_property(obj, spec["name"])
        label = str(spec["name"])
        value = spec.get("value")
        type_id = obj.getTypeIdOfProperty(prop)
        if type_id == "App::PropertyEnumeration":
            kind = "enum"
            choices = obj.getEnumerationsOfProperty(prop)
            if str(value) not in choices:
                raise ValueError(f"{prop}: {value!r} is not one of {choices}")
        elif type_id == "App::PropertyBool":
            kind = "bool"
        else:
            kind = "text"
        for path, _expr in list(getattr(obj, "ExpressionEngine", []) or []):
            if path == prop or path.split(".")[0] == prop:
                obj.setExpression(path, None)
                self.note(f"cleared expression on {obj.Label}.{prop} before the edit")

        self._select_in_tree(q, obj)
        self._ensure_tab(q, ("Property View", "Model"))
        self._ensure_tab(q, ("Data",))
        cell = _prop_cell(label, prop)

        def fallback():
            _close_popup()
            if not _value_matches(obj, prop, kind, value):
                _set_directly(obj, prop, kind, value)
                self.note(f"{obj.Label}.{prop}: mouse edit missed, set directly")

        if kind == "enum":
            q.click(cell, what=f"{prop} cell").wait(450)

            def open_again(sub):
                if QtWidgets.QApplication.activePopupWidget() is None:
                    sub.click(cell, what=f"{prop} dropdown").wait(450)

            q.defer(open_again)
            q.click(_popup_item(value), glide_ms=380, what=f"{prop} item {value!r}")
            q.wait(500)
        elif kind == "bool":

            def toggle(sub):
                if not _value_matches(obj, prop, kind, value):
                    sub.click(cell, what=f"{prop} checkbox").wait(500)

            q.defer(toggle)
        else:
            text = _with_units(obj, prop, value)
            q.click(cell, what=f"{prop} cell").wait(300)

            def typing(sub):
                # never type into whatever else has the keyboard focus
                fw = QtWidgets.QApplication.focusWidget()
                try:
                    editor = _prop_editor()
                except LookupError:
                    editor = None
                if self.last_click_ok and fw is not None and editor is not None:
                    if editor.isAncestorOf(fw):
                        sub.keys("Control_L", "a").type(text).keys("Return").wait(500)
                        return
                self.note(f"{prop}: no value editor got the focus; not typing")

            q.defer(typing)
            value = text
        q.add(0, fallback)

    def _act_panel_combo(self, q, spec):
        name, item = spec["widget"], str(spec["item"])

        def build(sub):
            w = _named_widget(name)
            tab = _tab_of(w)
            if tab is not None:
                sub.click(tab, what=f"tab of {name}").wait(700)
            sub.click(lambda: _center(w), what=f"combo {name}").wait(450)
            sub.click(_popup_item(item), glide_ms=380, what=f"{name} item {item!r}")
            sub.wait(500)

            def verify():
                _close_popup()
                if isinstance(w, QtWidgets.QComboBox) and w.currentText() != item:
                    idx = w.findText(item)
                    if idx < 0:
                        raise LookupError(f"{name} has no item {item!r}")
                    w.setCurrentIndex(idx)
                    self.note(f"{name}: mouse pick missed, set directly")

            sub.add(0, verify)

        q.defer(build)

    def _act_click_button(self, q, text):
        q.click(_button(str(text)), what=f"button {text}").wait(700)

    def _build_after(self, q):
        """After a step's actions: close popups, deselect, recompute."""
        q.add(0, _close_popup)

        def deselect(sub):
            if FreeCADGui.Selection.getSelection():
                sub.click(_view3d_empty, glide_ms=500, what="empty 3D spot").wait(200)
            sub.add(0, FreeCADGui.Selection.clearSelection)

        q.defer(deselect)

        def recompute():
            doc = FreeCAD.ActiveDocument
            if doc is not None and any("Touched" in o.State for o in doc.Objects):
                doc.recompute()

        q.add(0, recompute)


def _remove_captions():
    mw = _mw()
    for lab in mw.findChildren(QtWidgets.QLabel, CAPTION_NAME):
        if _alive(lab):
            lab.hide()
            lab.setParent(None)
            lab.deleteLater()


def _current():
    return getattr(FreeCADGui, "_mcp_demo", None)


# ---------------------------------------------------------------- entry points
def demo_probe():
    """Display, screen and GUI state, checked before recording starts."""
    display = os.environ.get("DISPLAY")
    ok, reason = display_ok(display)
    app = QtWidgets.QApplication
    run = _current()
    screen = app.primaryScreen().geometry() if app.primaryScreen() else None
    return {
        "display": display,
        "display_ok": ok,
        "reason": reason,
        "pid": os.getpid(),
        "screen": [screen.width(), screen.height()] if screen else None,
        "modal_dialog": bool(app.activeModalWidget()),
        "popup": bool(app.activePopupWidget()),
        "task_panel": bool(FreeCADGui.Control.activeDialog()),
        "running": bool(run is not None and run.state in ("starting", "running")),
        "active_document": FreeCAD.ActiveDocument.Name
        if FreeCAD.ActiveDocument
        else None,
    }


def demo_start(steps, title=None, hold_ms=3500, glide_ms=450):
    """Start a run on QTimers and return at once; poll demo_status()."""
    display = os.environ.get("DISPLAY")
    ok, reason = display_ok(display)
    if not ok:
        raise RuntimeError("refused: " + reason)
    run = _current()
    if (
        run is not None
        and run.state in ("starting", "running")
        and time.time() - run.last < STALE_S
    ):
        raise RuntimeError("a demo run is already in progress")
    if run is not None:
        run.abort("replaced by a new run")
    app = QtWidgets.QApplication
    if app.activeModalWidget() is not None:
        raise RuntimeError("a modal dialog is open; close it first (gui_reset)")
    _close_popup()
    _remove_captions()
    run = DemoRun(list(steps), title, hold_ms, glide_ms, display)
    FreeCADGui._mcp_demo = run
    run.begin()
    return {"started": True, "n": len(run.steps), "t_start": run.t_start}


def demo_status():
    """Status of the current (or last) run."""
    run = _current()
    if run is None:
        return {"state": "none"}
    return run.status()


def demo_abort(reason="aborted by the server"):
    """Abort the current run and clean up its caption."""
    run = _current()
    if run is None:
        _remove_captions()
        return {"state": "none"}
    run.abort(reason)
    return run.status()
