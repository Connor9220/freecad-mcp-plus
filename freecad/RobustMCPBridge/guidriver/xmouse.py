# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

# guidriver.xmouse -- real mouse/keyboard driver for FreeCAD (XTest, X11 only),
# async via QTimer so modal dialogs opened by a click never block the caller.
#   from guidriver import xmouse as xm
#   xm.run(lambda: script())   where script is a generator of xm primitives
#   xm.go(script)              run + pump events until idle (from a bridge call)
#   xm.state -> {"busy", "log", "error"}
import ctypes
import time
import traceback

import FreeCAD
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

_x = ctypes.cdll.LoadLibrary("libX11.so.6")
_t = ctypes.cdll.LoadLibrary("libXtst.so.6")
_x.XOpenDisplay.restype = ctypes.c_void_p
_x.XStringToKeysym.restype = ctypes.c_ulong
_x.XStringToKeysym.argtypes = [ctypes.c_char_p]
_x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
_d = ctypes.c_void_p(_x.XOpenDisplay(None))

PACE = 1.0  # multiplier on pauses
state = {"busy": False, "log": [], "error": None}
_pos = [None, None]

CHARS = {
    " ": ("space", False),
    ".": ("period", False),
    "-": ("minus", False),
    "/": ("slash", False),
    ",": ("comma", False),
    "=": ("equal", False),
    '"': ("quotedbl", True),
    "_": ("underscore", True),
    "*": ("asterisk", True),
    "+": ("plus", True),
    "(": ("parenleft", True),
    ")": ("parenright", True),
}
NAMES = {
    "enter": "Return",
    "return": "Return",
    "tab": "Tab",
    "esc": "Escape",
    "escape": "Escape",
    "del": "Delete",
    "delete": "Delete",
    "bs": "BackSpace",
    "ctrl": "Control_L",
    "shift": "Shift_L",
    "alt": "Alt_L",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
}


def _code(name):
    ks = _x.XStringToKeysym(name.encode())
    if not ks:
        raise ValueError(f"no keysym {name}")
    return _x.XKeysymToKeycode(_d, ks)


def _keyev(name, down):
    _t.XTestFakeKeyEvent(_d, _code(name), bool(down), 0)
    _x.XFlush(_d)


def _move(x, y):
    _t.XTestFakeMotionEvent(_d, -1, int(round(x)), int(round(y)), 0)
    _x.XFlush(_d)
    _pos[:] = [x, y]


def _btn(b, down):
    _t.XTestFakeButtonEvent(_d, b, bool(down), 0)
    _x.XFlush(_d)


# ------------------------------------------------------------------ primitives (generators)
def wait(sec):
    yield sec * PACE


def log(msg):
    state["log"].append(msg)
    yield 0


def glide(x, y, dur=0.35):
    if callable(x):  # lazy target
        x, y = x()
    if _pos[0] is None:
        from PySide import QtGui

        c = QtGui.QCursor.pos()
        _pos[:] = [c.x(), c.y()]
    x0, y0 = _pos
    steps = max(4, int(dur / 0.016))
    for i in range(1, steps + 1):
        s = i / steps
        s = s * s * (3 - 2 * s)
        _move(x0 + (x - x0) * s, y0 + (y - y0) * s)
        yield 0.016


def click(target, button=1, double=False, pause=0.6, mods=()):
    x, y = target() if callable(target) else target
    yield from glide(x, y)
    # tools that just reset only see the pointer on a real motion event
    _move(x + 2, y + 2)
    yield 0.03
    _move(x, y)
    yield 0.12
    for m in mods:
        _keyev(NAMES.get(m, m), True)
    for n in range(2 if double else 1):
        _btn(button, True)
        yield 0.05
        _btn(button, False)
        yield 0.1
    for m in mods:
        _keyev(NAMES.get(m, m), False)
    yield pause * PACE


def key(*names, pause=0.25):
    """key('ctrl', 'a') presses a chord; key('enter')."""
    codes = [NAMES.get(n, n) for n in names]
    for c in codes:
        _keyev(c, True)
        yield 0.02
    for c in reversed(codes):
        _keyev(c, False)
        yield 0.02
    yield pause * PACE


def type_(text, per=0.05, pause=0.3):
    for ch in text:
        if ch in CHARS:
            name, shift = CHARS[ch]
        elif ch.isupper():
            name, shift = ch.lower(), True
        else:
            name, shift = ch, False
        if shift:
            _keyev("Shift_L", True)
        _keyev(name, True)
        _keyev(name, False)
        if shift:
            _keyev("Shift_L", False)
        yield per
    yield pause * PACE


def call(fn):
    """Run a plain function inside the script (e.g. to inspect state)."""
    fn()
    yield 0


# ------------------------------------------------------------------ runner
def run(script, pace=None):
    global PACE
    if pace is not None:
        PACE = pace
    if state["busy"]:
        raise RuntimeError("driver busy")
    state.update(busy=True, error=None, log=[])
    gen = script() if callable(script) else script

    def step():
        try:
            d = next(gen)
        except StopIteration:
            state["busy"] = False
            return
        except Exception:
            state["error"] = traceback.format_exc()
            state["busy"] = False
            for m in ("Shift_L", "Control_L", "Alt_L"):
                _keyev(m, False)
            return
        QtCore.QTimer.singleShot(max(1, int(d * 1000)), step)

    QtCore.QTimer.singleShot(0, step)


def stop():
    state["busy"] = False


# ------------------------------------------------------------------ locators (return global x, y)
def mw():
    return Gui.getMainWindow()


def _center(w, r=None):
    r = r if r is not None else w.rect()
    p = w.mapToGlobal(r.center())
    return p.x(), p.y()


def tool(cmd):
    """Visible toolbar button for a command name, e.g. 'PartDesign_Pad'."""

    def f():
        for b in mw().findChildren(QtWidgets.QToolButton):
            a = b.defaultAction()
            if b.isVisible() and a is not None and a.objectName() == cmd:
                return _center(b)
        raise LookupError(cmd)

    return f


def widget(name, cls=QtWidgets.QWidget, parent=None):
    def f():
        for w in (parent or mw()).findChildren(cls, name):
            if w.isVisible():
                return _center(w)
        raise LookupError(name)

    return f


def button(text):
    def f():
        for b in QtWidgets.QApplication.instance().allWidgets():
            if (
                isinstance(b, QtWidgets.QAbstractButton)
                and b.isVisible()
                and b.text().replace("&", "") == text
            ):
                return _center(b)
        raise LookupError(text)

    return f


def tree_item(label, dx=40):
    def f():
        for tw in mw().findChildren(QtWidgets.QTreeWidget):
            if not tw.isVisible():
                continue
            it = QtWidgets.QTreeWidgetItemIterator(tw)
            while it.value():
                i = it.value()
                if i.text(0) == label:
                    tw.scrollToItem(i)
                    r = tw.visualItemRect(i)
                    p = tw.viewport().mapToGlobal(
                        QtCore.QPoint(r.left() + dx, r.center().y())
                    )
                    return p.x(), p.y()
                it += 1
        raise LookupError(label)

    return f


def _viewer_widget():
    v = Gui.ActiveDocument.ActiveView
    viewer = v.getViewer()
    sz = viewer.getSoRenderManager().getViewportRegion().getWindowSize()
    w, h = sz.getValue()
    # the GL widget: largest visible widget with exactly this size inside the active MDI view
    for cand in mw().findChildren(QtWidgets.QWidget):
        if (
            cand.isVisible()
            and cand.width() == w
            and cand.height() == h
            and cand.metaObject().className().endswith("GLWidget")
        ):
            return cand, h
    for cand in mw().findChildren(QtWidgets.QWidget):
        if cand.isVisible() and cand.width() == w and cand.height() == h:
            return cand, h
    raise LookupError("GL widget")


def at3d(vec):
    """Screen position of a 3D world point in the active view."""

    def f():
        v = Gui.ActiveDocument.ActiveView
        px, py = v.getPointOnViewport(
            FreeCAD.Vector(*vec) if not isinstance(vec, FreeCAD.Vector) else vec
        )
        w, h = _viewer_widget()
        p = w.mapToGlobal(QtCore.QPoint(int(round(px)), int(round(h - py))))
        return p.x(), p.y()

    return f


def at_sketch(sk, x, y):
    """Screen position of sketch-local (x, y) in mm."""
    return lambda: at3d(sk.getGlobalPlacement().multVec(FreeCAD.Vector(x, y, 0)))()


def view_center():
    def f():
        w, h = _viewer_widget()
        return _center(w)

    return f


def popup_item(text):
    def f():
        pw = QtWidgets.QApplication.activePopupWidget()
        if isinstance(pw, QtWidgets.QMenu):
            for a in pw.actions():
                if a.text().replace("&", "") == text:
                    return _center(pw, pw.actionGeometry(a))
        if pw is not None:
            for v in pw.findChildren(QtWidgets.QAbstractItemView):
                m = v.model()
                for r in range(m.rowCount()):
                    if m.index(r, 0).data() == text:
                        return _center(v.viewport(), v.visualRect(m.index(r, 0)))
        raise LookupError(text)

    return f


def menubar(text):
    def f():
        mb = mw().menuBar()
        for a in mb.actions():
            if a.text().replace("&", "") == text:
                return _center(mb, mb.actionGeometry(a))
        raise LookupError(text)

    return f


def shot(path):
    scr = mw().screen()
    g = mw().frameGeometry()
    pm = scr.grabWindow(
        0, g.x() - scr.geometry().x(), g.y() - scr.geometry().y(), g.width(), g.height()
    )
    pm.save(path)
    return path


def tab(tooltip_or_text):
    """Tab in any visible QTabBar by text or tooltip."""

    def f():
        for t in QtWidgets.QApplication.instance().allWidgets():
            if isinstance(t, QtWidgets.QTabBar) and t.isVisible():
                for i in range(t.count()):
                    if tooltip_or_text in (
                        t.tabText(i).replace("&", ""),
                        t.tabToolTip(i),
                    ):
                        return _center(t, t.tabRect(i))
        raise LookupError(tooltip_or_text)

    return f


def list_item(text):
    """Row in any visible item view (QListWidget/QTreeView...)."""

    def f():
        for v in QtWidgets.QApplication.instance().allWidgets():
            if isinstance(v, QtWidgets.QAbstractItemView) and v.isVisible():
                m = v.model()
                if m is None:
                    continue
                for r in range(m.rowCount()):
                    ix = m.index(r, 0)
                    if ix.data() == text:
                        v.scrollTo(ix)
                        return _center(v.viewport(), v.visualRect(ix))
        raise LookupError(text)

    return f


S = "/tmp"  # screenshot dir; override per session


def wait_idle(limit=60):
    """Call from a bridge request: pump events until the script finishes."""
    t = time.time()
    while state["busy"] and time.time() - t < limit:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)
    return dict(state)


def go(script, limit=60, pace=None):
    run(script, pace)
    return wait_idle(limit)
