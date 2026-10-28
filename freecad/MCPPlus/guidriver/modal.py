# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

# guidriver.modal -- answer modal dialogs that pop up while the GUI is driven.
#
# A modal dialog runs a nested event loop, so a QTimer started before the
# triggering call keeps firing inside it. The watcher polls for the active
# modal widget, matches it against rules, and acts. Every dialog seen is
# logged -- an unexpected dialog is itself a test result. Unmatched dialogs
# are rejected after a grace period so a driver call can never hang the
# bridge.

import time

from PySide import QtCore, QtWidgets

from . import widgets as W

UNHANDLED_GRACE_S = 3.0

# Rules that persist across driver calls (added with expect()).
RULES = []


def info(w):
    d = {
        "class": type(w).__name__,
        "qt": W.qt_class(w),
        "object": w.objectName(),
        "title": w.windowTitle(),
    }
    if isinstance(w, QtWidgets.QMessageBox):
        d["text"] = w.text()[:300]
        d["buttons"] = [b.text() for b in w.buttons()]
    elif isinstance(w, QtWidgets.QInputDialog):
        d["label"] = w.labelText()
    else:
        d["widgets"] = [c.objectName() for c in W.children(w)][:40]
    return d


def _matches(m, d, w):
    for key, want in m.items():
        if key == "has":
            if W.find(w, want) is None:
                return False
        elif key == "class":
            if want not in (d["class"], d["qt"]):
                return False
        else:
            if str(want).lower() not in str(d.get(key, "")).lower():
                return False
    return True


def find_button(w, text):
    for b in w.findChildren(QtWidgets.QAbstractButton):
        if b.text().replace("&", "").lower() == text.lower() and b.isVisible():
            return b
    raise ValueError(
        f"no button '{text}' in {[b.text() for b in w.findChildren(QtWidgets.QAbstractButton)]}"
    )


def perform(w, action):
    """Run a rule action against dialog w."""
    if callable(action):
        return action(w)
    if action == "accept":
        return w.accept()
    if action == "reject":
        return w.reject()
    if isinstance(action, dict):
        for name, value in action.get("set", {}).items():
            W.set(W.find(w, name), value)
        if "button" in action:
            return find_button(w, action["button"]).click()
        then = action.get("then", "accept")
        return perform(w, then)
    raise ValueError(f"bad modal action {action!r}")


class Watcher(QtCore.QObject):
    def __init__(self, rules):
        super().__init__()
        self.rules = list(rules) + list(RULES)
        self.log = []
        self._busy = set()
        self._first_seen = {}
        self.timer = QtCore.QTimer()
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)

    def __enter__(self):
        self.timer.start()
        return self

    def __exit__(self, *exc):
        self.timer.stop()
        return False

    def _tick(self):
        w = QtWidgets.QApplication.activeModalWidget()
        if w is None or id(w) in self._busy or not W.alive(w):
            return
        d = info(w)
        rule = next((r for r in self.rules if _matches(r.get("match", {}), d, w)), None)
        if rule is None:
            t0 = self._first_seen.setdefault(id(w), time.monotonic())
            if time.monotonic() - t0 < UNHANDLED_GRACE_S:
                return
            d["action"] = "UNHANDLED -> rejected"
            self.log.append(d)
            w.reject()
            return
        self._busy.add(id(w))
        d["action"] = rule.get("name", str(rule.get("do"))[:60])
        self.log.append(d)
        if rule.get("once"):
            self.rules.remove(rule)
            if rule in RULES:
                RULES.remove(rule)
        # Act outside this timer slot: a handler that opens a nested modal
        # (Edit -> F&S wizard) blocks in that modal's event loop, and the
        # watcher timer must stay free to service it.
        # The dialog may be gone before the deferred handler runs; a Python
        # wrapper does not know its C++ object was deleted, and touching it
        # segfaults. Track destruction through Qt itself.
        state = {"alive": True}
        w.destroyed.connect(lambda *_: state.update(alive=False))
        QtCore.QTimer.singleShot(0, lambda: self._act(w, rule, d, state))

    def _act(self, w, rule, d, state=None):
        if state is not None and not state["alive"]:
            self._busy.discard(id(w))
            d["error"] = "dialog closed before its handler ran"
            return
        try:
            perform(w, rule["do"])
        except Exception as e:  # never leave a modal up on a failed handler
            d["error"] = f"{type(e).__name__}: {e}"
            try:
                if w.isVisible():
                    w.reject()
            except RuntimeError:
                pass
        finally:
            self._busy.discard(id(w))


def expect(rule):
    """Add a persistent rule (dict with 'match', 'do', optional 'name'/'once')."""
    RULES.append(rule)
    return rule


def clear():
    RULES.clear()


# ---- generic ready-made rules ---------------------------------------------


def message_box(button, text=None, title=None):
    m = {"class": "QMessageBox"}
    if text:
        m["text"] = text
    if title:
        m["title"] = title
    return {"name": f"message_box({button})", "match": m, "do": {"button": button}}


def input_int(value=None):
    """QInputDialog (e.g. tool number prompt). value None = accept default."""

    def do(w):
        if value is not None:
            w.setIntValue(int(value))
        w.accept()

    return {"name": f"input_int({value})", "match": {"class": "QInputDialog"}, "do": do}


# Rule factories callable by name from the MCP tools (guidriver.mcp.rules).
RULE_FACTORIES = {"message_box": message_box, "input_int": input_int}
