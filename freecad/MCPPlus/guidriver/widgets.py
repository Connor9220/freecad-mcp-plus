# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

# guidriver.widgets -- read and set Qt widgets the way a user would.
#
# Setting goes through the widget's own API so its signals fire and the CAM
# panel's handlers run, instead of writing object properties behind its back.

import importlib

import FreeCAD
from PySide import QtCore, QtWidgets

try:
    import shiboken6 as shiboken
except ImportError:
    import shiboken2 as shiboken

SKIP_PREFIXES = ("qt_", "qt_spinbox")


def alive(w):
    """False for a wrapper whose C++ widget is gone. Walking every widget as
    QWidget leaves such wrappers behind once a dialog closes."""
    return shiboken.isValid(w)


def qt_class(w):
    return w.metaObject().className()


def is_quantity(w):
    return qt_class(w) in (
        "Gui::QuantitySpinBox",
        "Gui::IntSpinBox",
        "Gui::DoubleSpinBox",
    ) or (
        isinstance(w, QtWidgets.QAbstractSpinBox)
        and not isinstance(w, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox))
    )


def read(w, full=False):
    """Return the user-visible value of a widget, or None if it has no value."""
    if isinstance(w, (QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
        return w.isChecked()
    if isinstance(w, QtWidgets.QComboBox):
        if full:
            return {
                "current": w.currentText(),
                "items": [w.itemText(i) for i in range(w.count())],
            }
        return w.currentText()
    if isinstance(w, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
        return w.text() if w.suffix() or w.prefix() else w.value()
    if isinstance(w, QtWidgets.QAbstractSpinBox):
        return w.text()
    if isinstance(w, QtWidgets.QLineEdit):
        return w.text()
    if isinstance(w, QtWidgets.QPlainTextEdit):
        return w.toPlainText()[:200]
    if isinstance(w, QtWidgets.QAbstractButton):
        return (
            ("checked" if w.isChecked() else "unchecked")
            if w.isCheckable()
            else "button"
        )
    if isinstance(w, QtWidgets.QListWidget):
        items = [w.item(i).text() for i in range(w.count())]
        return items if full else f"{len(items)} items"
    if isinstance(w, QtWidgets.QTableWidget):
        return f"{w.rowCount()} rows"
    if isinstance(w, QtWidgets.QAbstractItemView) and w.model() is not None:
        return f"{w.model().rowCount()} rows"
    if isinstance(w, QtWidgets.QLabel):
        return w.text()[:120]
    return None


def _is_tab_page(node):
    p = node.parentWidget()
    return isinstance(p, QtWidgets.QStackedWidget) or (
        p is not None
        and qt_class(p) == "QWidget"
        and isinstance(p.parentWidget(), QtWidgets.QToolBox)
    )


def gated_hidden(w, root):
    """True if w (or an ancestor below root) was hidden by the panel's logic --
    not merely because it sits on a tab/toolbox page that isn't current."""
    node = w
    while node is not None and node is not root:
        if node.isHidden() and not _is_tab_page(node):
            return True
        node = node.parentWidget()
    return False


def tab_of(w, root):
    """Name of the tab/toolbox page holding w, if any."""
    node = w
    while node is not None and node is not root:
        p = node.parentWidget()
        if isinstance(p, QtWidgets.QStackedWidget) and isinstance(
            p.parentWidget(), QtWidgets.QTabWidget
        ):
            tw = p.parentWidget()
            return tw.tabText(tw.indexOf(node))
        if p is not None and isinstance(p.parentWidget(), QtWidgets.QToolBox):
            tb = p.parentWidget()
            i = tb.indexOf(node)
            if i >= 0:
                return tb.itemText(i)
        node = p
    return None


def reveal(w):
    """Bring w's tab/toolbox page to the front, as a user would before using it."""
    node = w
    while node is not None:
        p = node.parentWidget()
        if isinstance(p, QtWidgets.QStackedWidget) and isinstance(
            p.parentWidget(), QtWidgets.QTabWidget
        ):
            tw = p.parentWidget()
            tw.setCurrentIndex(tw.indexOf(node))
        elif p is not None and isinstance(p.parentWidget(), QtWidgets.QToolBox):
            tb = p.parentWidget()
            i = tb.indexOf(node)
            if i >= 0:
                tb.setCurrentIndex(i)
        node = p
    QtWidgets.QApplication.processEvents()


def describe(w, root, full=False, labels=False):
    if isinstance(w, QtWidgets.QLabel) and not labels:
        return None
    v = read(w, full)
    if v is None:
        return None
    d = {
        "name": w.objectName(),
        "type": qt_class(w).replace("QtWidgets.", ""),
        "value": v,
    }
    tab = tab_of(w, root)
    if tab:
        d["tab"] = tab
    if gated_hidden(w, root):
        d["hidden"] = True
    if not w.isEnabled():
        d["disabled"] = True
    if full and w.toolTip():
        d["tooltip"] = w.toolTip()[:200]
    return d


def children(root):
    """Named, value-bearing child widgets of root, in tree order."""
    out = []
    for w in root.findChildren(QtWidgets.QWidget):
        try:
            n = w.objectName()
        except RuntimeError:  # C++ widget deleted (panel rebuilt part of its form)
            continue
        if not n or n.startswith(SKIP_PREFIXES):
            continue
        out.append(w)
    return out


def find(root, name):
    if root.objectName() == name:
        return root
    hits = [w for w in root.findChildren(QtWidgets.QWidget, name)]
    return hits[0] if hits else None


def _pick_combo_index(w, value):
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    s = str(value)
    i = w.findText(s)
    if i < 0:
        i = w.findData(value)
    if i < 0:
        low = s.lower()
        exact = [j for j in range(w.count()) if w.itemText(j).lower() == low]
        part = [j for j in range(w.count()) if low in w.itemText(j).lower()]
        if exact:
            i = exact[0]
        elif len(part) == 1:
            i = part[0]
        elif len(part) > 1:
            raise ValueError(
                f"{w.objectName()}: '{s}' is ambiguous: {[w.itemText(j) for j in part]}"
            )
    if i < 0:
        raise ValueError(
            f"{w.objectName()}: no item '{s}' in {[w.itemText(j) for j in range(w.count())]}"
        )
    return i


def _to_raw(w, value):
    """Quantity text ('0.25 in', '45 deg', '120 in/min') -> internal-unit float."""
    if isinstance(value, (int, float)):
        return float(value)
    return FreeCAD.Units.Quantity(str(value)).Value


def mouse_click(w, pos=None):
    """A real left click (press + release) on a widget that has no click() API."""
    try:
        # QtTest of the binding FreeCAD's PySide wrapper sits on (it has no
        # QtTest of its own); falls back to synthesized events without it.
        binding = QtCore.QObject.__module__.split(".")[0]
        QtTest = importlib.import_module(binding + ".QtTest")
        QtTest.QTest.mouseClick(
            w, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, pos or w.rect().center()
        )
    except ImportError:
        from PySide import QtGui

        p = QtCore.QPointF(pos or w.rect().center())
        g = QtCore.QPointF(w.mapToGlobal(p.toPoint()))
        for t in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease):
            ev = QtGui.QMouseEvent(
                t,
                p,
                g,
                QtCore.Qt.LeftButton,
                QtCore.Qt.LeftButton,
                QtCore.Qt.NoModifier,
            )
            QtWidgets.QApplication.sendEvent(w, ev)
    QtWidgets.QApplication.processEvents()


def expression_of(w):
    if is_quantity(w) and str(w.property("exprSet")).lower() == "true":
        return str(w.property("expression"))
    return None


def clear_expression(w):
    """Click the f(x) icon of an expression-bound spin box and press Discard
    ('Revert to last calculated value') in the formula pop-up."""
    reveal(w)
    icon = next(
        (
            l
            for l in w.findChildren(QtWidgets.QLabel)
            if l.toolTip().startswith("Expression") and l.isVisible()
        ),
        None,
    )
    if icon is None:
        raise RuntimeError(f"{w.objectName()}: no visible expression icon")
    mouse_click(icon)
    box = next(
        (
            t
            for t in QtWidgets.QApplication.topLevelWidgets()
            if alive(t) and t.objectName() == "DlgExpressionInput" and t.isVisible()
        ),
        None,
    )
    if box is None:
        raise RuntimeError(f"{w.objectName()}: formula dialog did not open")
    bb = box.findChild(QtWidgets.QDialogButtonBox)
    bb.button(QtWidgets.QDialogButtonBox.Reset).click()
    for _ in range(5):
        QtWidgets.QApplication.processEvents()
    return expression_of(w)


def set(w, value, typed=False, clear_expression_first=False):
    """Set a widget like a user would. Returns the widget's value afterwards."""
    reveal(w)
    expr = expression_of(w)
    if expr is not None:
        if not clear_expression_first:
            raise RuntimeError(
                f"{w.objectName()} is bound to expression '{expr}' (read-only); pass clear_expression=True"
            )
        left = clear_expression(w)
        if left is not None:
            raise RuntimeError(
                f"{w.objectName()}: expression '{left}' survived Discard"
            )
    if not w.isVisible():
        raise RuntimeError(f"{w.objectName()} is hidden (GUI gating)")
    if not w.isEnabled():
        raise RuntimeError(f"{w.objectName()} is disabled (GUI gating)")
    if isinstance(w, QtWidgets.QComboBox):
        i = _pick_combo_index(w, value)
        w.setCurrentIndex(i)
        w.activated.emit(i)
    elif isinstance(w, (QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
        if w.isChecked() != bool(value):
            w.click()
    elif isinstance(w, QtWidgets.QAbstractButton) and w.isCheckable():
        if w.isChecked() != bool(value):
            w.click()
    elif isinstance(w, QtWidgets.QSpinBox):
        w.setValue(int(value))
        w.editingFinished.emit()
    elif isinstance(w, QtWidgets.QDoubleSpinBox):
        w.setValue(float(value))
        w.editingFinished.emit()
    elif isinstance(w, QtWidgets.QAbstractSpinBox):
        if typed:
            le = w.findChild(QtWidgets.QLineEdit)
            le.selectAll()
            le.setText(str(value))
            w.editingFinished.emit()
        else:
            w.setProperty("rawValue", _to_raw(w, value))
            w.editingFinished.emit()
    elif isinstance(w, QtWidgets.QLineEdit):
        w.setText(str(value))
        w.editingFinished.emit()
        w.returnPressed.emit()
    elif isinstance(w, QtWidgets.QPlainTextEdit):
        w.setPlainText(str(value))
    else:
        raise TypeError(f"don't know how to set {qt_class(w)} {w.objectName()}")
    QtWidgets.QApplication.processEvents()
    return read(w)


def click(w):
    reveal(w)
    if not w.isEnabled():
        raise RuntimeError(f"{w.objectName()} is disabled (GUI gating)")
    if not w.isVisible():
        raise RuntimeError(f"{w.objectName()} is hidden (GUI gating)")
    w.click()
    QtWidgets.QApplication.processEvents()


def select_items(view, labels, clear=True):
    """Select rows in a QListWidget by (partial) text. Returns selected texts."""
    if clear:
        view.clearSelection()
    picked = []
    for lab in labels:
        lab = str(lab)
        hits = []
        for i in range(view.count()):
            it = view.item(i)
            texts = [it.text()]
            iw = view.itemWidget(it)
            if iw is not None:
                texts += [c.text() for c in iw.findChildren(QtWidgets.QLabel)]
            if any(lab == t or lab in t for t in texts):
                hits.append((it, " | ".join(t for t in texts if t)))
        if not hits:
            raise ValueError(f"no item matching '{lab}'")
        hits[0][0].setSelected(True)
        picked.append(hits[0][1])
    QtWidgets.QApplication.processEvents()
    return picked


def select_rows(table, labels, column=0, clear=True, whole_row=False):
    """Select QTableWidget rows whose cell in `column` contains a label.

    Default mimics clicking (ctrl-clicking for more) the cell itself; whole_row
    mimics clicking the row header, which selects every cell in the row."""
    reveal(table)
    if clear:
        table.clearSelection()
    picked = []
    for lab in labels:
        rows = [
            r
            for r in range(table.rowCount())
            if table.item(r, column) and str(lab) in table.item(r, column).text()
        ]
        if not rows:
            raise ValueError(f"no row matching '{lab}'")
        r = rows[0]
        if whole_row:
            mode = table.selectionMode()
            table.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
            try:
                table.selectRow(r)
            finally:
                table.setSelectionMode(mode)
        else:
            table.setCurrentCell(r, column, QtCore.QItemSelectionModel.Select)
        picked.append(table.item(r, column).text())
    QtWidgets.QApplication.processEvents()
    return picked


def table_rows(table):
    return [
        [
            (table.item(r, c).text() if table.item(r, c) else None)
            for c in range(table.columnCount())
        ]
        for r in range(table.rowCount())
    ]


def check_tree_items(view, labels, column=0):
    """Check items (by exact text) anywhere in a QStandardItemModel tree."""
    model = view.model()
    found = []

    def walk(parent):
        for r in range(parent.rowCount()):
            it = parent.child(r, column)
            if it is None:
                continue
            if it.text() in labels and it.isCheckable():
                it.setCheckState(QtCore.Qt.Checked)
                found.append(it.text())
            walk(it)

    walk(model.invisibleRootItem())
    missing = [l for l in labels if l not in found]
    if missing:
        raise ValueError(f"tree items not found/checkable: {missing}")
    return found
