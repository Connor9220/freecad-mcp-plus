# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Dialog that shows the AI's code and asks whether to run it."""

from __future__ import annotations

from PySide import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

from ..freecad_mcp_bridge.approval import ALWAYS, ONCE, SESSION

WARNING = (
    "The code runs with your full permissions: besides your documents, it can read, change "
    "or delete your files and start programs. Documents and files the AI has read can carry "
    "instructions planted by someone else, so check what the code does before you run it."
)


class ApprovalDialog(QtWidgets.QDialog):
    """Shows the code; Run with a scope (once, this session, always) or Cancel."""

    def __init__(
        self,
        tool: str,
        code: str,
        detail: str = "",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("MCP+: Run Code From the AI?")
        self.setMinimumSize(680, 480)
        layout = QtWidgets.QVBoxLayout(self)

        header = QtWidgets.QLabel(
            f"The AI wants to run Python code in FreeCAD ({tool})."
        )
        header.setTextFormat(QtCore.Qt.PlainText)
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        layout.addWidget(header)
        if detail:
            detail_label = QtWidgets.QLabel(detail)
            detail_label.setTextFormat(QtCore.Qt.PlainText)
            detail_label.setWordWrap(True)
            detail_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            layout.addWidget(detail_label)
        warning = QtWidgets.QLabel(WARNING)
        warning.setWordWrap(True)
        layout.addWidget(warning)

        code_view = QtWidgets.QPlainTextEdit(code)
        code_view.setReadOnly(True)
        code_view.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        code_view.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        layout.addWidget(code_view, 1)

        self._choices = {
            ONCE: QtWidgets.QRadioButton("Only this time"),
            SESSION: QtWidgets.QRadioButton("For the rest of this FreeCAD session"),
            ALWAYS: QtWidgets.QRadioButton(
                "Always (turn it off again in Edit > Preferences > MCP+)"
            ),
        }
        self._choices[ONCE].setChecked(True)
        allow_box = QtWidgets.QGroupBox("Allow")
        allow_layout = QtWidgets.QVBoxLayout(allow_box)
        for button in self._choices.values():
            allow_layout.addWidget(button)
        layout.addWidget(allow_box)

        buttons = QtWidgets.QDialogButtonBox()
        run = buttons.addButton("Run", QtWidgets.QDialogButtonBox.AcceptRole)
        cancel = buttons.addButton(QtWidgets.QDialogButtonBox.Cancel)
        # Enter cancels: running takes a deliberate click
        run.setAutoDefault(False)
        cancel.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @classmethod
    def ask(cls, tool: str, code: str, detail: str, timeout_s: float) -> str | None:
        """Show the dialog and wait; the chosen scope, or None for Cancel or no answer."""
        import FreeCADGui

        parent = FreeCADGui.getMainWindow()
        dialog = cls(tool, code, detail, parent)
        _close_after(dialog, timeout_s)
        QtWidgets.QApplication.alert(parent)
        dialog.raise_()
        dialog.activateWindow()
        accepted = dialog.exec() == QtWidgets.QDialog.Accepted
        scope = (
            next(key for key, button in dialog._choices.items() if button.isChecked())
            if accepted
            else None
        )
        dialog.deleteLater()
        return scope


def _close_after(dialog: QtWidgets.QDialog, timeout_s: float) -> QtCore.QTimer:
    """Reject ``dialog`` after ``timeout_s``; the timer goes away with the dialog."""
    timer = QtCore.QTimer(dialog)
    timer.setSingleShot(True)
    timer.timeout.connect(dialog.reject)
    timer.start(int(timeout_s * 1000))
    return timer


def ask_connection(address: str, timeout_s: float) -> str | None:
    """Ask whether another computer may connect: SESSION, ALWAYS, "cancel", or None (no answer)."""
    import FreeCADGui

    parent = FreeCADGui.getMainWindow()
    box = QtWidgets.QMessageBox(parent)
    box.setIcon(QtWidgets.QMessageBox.Warning)
    box.setWindowTitle("MCP+: Another Computer Wants to Connect")
    box.setTextFormat(QtCore.Qt.PlainText)
    box.setText(f"{address} wants to connect to FreeCAD.")
    box.setInformativeText(
        "It has this computer's MCP+ token. Once connected, the AI agent on it can use "
        "FreeCAD's tools, and run code as GUI AI Permission allows. If you don't know "
        "that computer, click Cancel."
    )
    session = box.addButton("Allow for This Session", QtWidgets.QMessageBox.AcceptRole)
    always = box.addButton("Always Allow", QtWidgets.QMessageBox.AcceptRole)
    cancel = box.addButton(QtWidgets.QMessageBox.Cancel)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    timer = _close_after(box, timeout_s)
    QtWidgets.QApplication.alert(parent)
    box.exec()
    timed_out = not timer.isActive()
    clicked = box.clickedButton()
    box.deleteLater()
    if clicked is session:
        return SESSION
    if clicked is always:
        return ALWAYS
    return None if timed_out else "cancel"
