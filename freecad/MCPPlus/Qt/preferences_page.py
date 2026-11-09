# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2025-2026 Sean P. Kane <spkane@gmail.com>
# SPDX-FileNotice: Part of MCP+.

"""Preferences page for FreeCAD Preferences dialog integration.

This module provides a QWidget-based preferences page that integrates
with FreeCAD's main Preferences dialog (Edit → Preferences).

NOTE: This is separate from the preferences.py module which handles
the actual preference storage. This module only handles the UI.
"""

from __future__ import annotations

from PySide import QtCore, QtWidgets  # type: ignore[import-not-found]


class MCPBridgePreferencesPage(QtWidgets.QWidget):
    """Preferences page for FreeCAD's Preferences dialog.

    This widget appears in the FreeCAD Preferences dialog sidebar
    when registered via FreeCADGui.addPreferencePage().

    Required methods:
        - loadSettings(): Load preferences into widgets
        - saveSettings(): Save widget values to preferences
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        """Initialize the preferences page widget."""
        super().__init__(parent)
        # Set window title - this appears in the preferences tree under the category
        self.setWindowTitle("General")
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        # The page is taller than the Preferences dialog: scroll it instead of clipping
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        outer.addWidget(scroll)
        content = QtWidgets.QWidget()
        scroll.setWidget(content)
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)

        # Title
        title = QtWidgets.QLabel("<h2>MCP+</h2>")
        layout.addWidget(title)

        description = QtWidgets.QLabel(
            "Configure the MCP+ Bridge for AI assistant integration with FreeCAD."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        layout.addSpacing(10)

        # Startup group
        startup_group = QtWidgets.QGroupBox("Startup")
        startup_layout = QtWidgets.QVBoxLayout(startup_group)

        self.auto_start_cb = QtWidgets.QCheckBox(
            "Auto-start bridge when FreeCAD launches"
        )
        self.auto_start_cb.setToolTip(
            "Automatically start the MCP bridge server when FreeCAD starts.\n"
            "The bridge allows AI assistants like Claude to control FreeCAD."
        )
        startup_layout.addWidget(self.auto_start_cb)

        start_note = QtWidgets.QLabel(
            "While the bridge runs, AI agents connected to it can run code in FreeCAD with "
            "your full permissions (see the AI Permission settings below)."
        )
        start_note.setWordWrap(True)
        startup_layout.addWidget(start_note)

        layout.addWidget(startup_group)

        # Display group
        display_group = QtWidgets.QGroupBox("Display")
        display_layout = QtWidgets.QVBoxLayout(display_group)

        self.status_bar_cb = QtWidgets.QCheckBox("Show status indicator in status bar")
        self.status_bar_cb.setToolTip(
            "Display MCP bridge connection status in FreeCAD's status bar."
        )
        display_layout.addWidget(self.status_bar_cb)

        layout.addWidget(display_group)

        # Network Ports group
        ports_group = QtWidgets.QGroupBox("Network Ports")
        ports_layout = QtWidgets.QFormLayout(ports_group)

        self.xmlrpc_spin = QtWidgets.QSpinBox()
        self.xmlrpc_spin.setRange(1024, 65535)
        self.xmlrpc_spin.setToolTip(
            "Port for XML-RPC connections.\n"
            "Default: 9875\n\n"
            "The MCP server connects to this port to communicate with FreeCAD."
        )
        ports_layout.addRow("XML-RPC Port:", self.xmlrpc_spin)

        self.socket_spin = QtWidgets.QSpinBox()
        self.socket_spin.setRange(1024, 65535)
        self.socket_spin.setToolTip(
            "Port for JSON-RPC socket connections.\n"
            "Default: 9876\n\n"
            "Alternative connection method using raw sockets."
        )
        ports_layout.addRow("Socket Port:", self.socket_spin)

        # Warning about restart
        port_warning = QtWidgets.QLabel(
            "<i>Note: Changing ports requires restarting the bridge.</i>"
        )
        port_warning.setWordWrap(True)
        ports_layout.addRow(port_warning)

        layout.addWidget(ports_group)

        # Security group
        security_group = QtWidgets.QGroupBox("Security")
        security_layout = QtWidgets.QFormLayout(security_group)

        security_intro = QtWidgets.QLabel(
            "Whoever can reach the bridge can run any Python in FreeCAD as you. Every request "
            "and answer is signed with a token that only your user can read; your MCP server "
            "picks it up from the token file by itself."
        )
        security_intro.setWordWrap(True)
        security_layout.addRow(security_intro)

        self.token_file_label = QtWidgets.QLabel()
        self.token_file_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.token_file_label.setWordWrap(True)
        security_layout.addRow("Token file:", self.token_file_label)

        token_buttons = QtWidgets.QHBoxLayout()
        copy_button = QtWidgets.QPushButton("Copy Token")
        copy_button.setToolTip(
            "Copy the token, to give an MCP server on another computer\n"
            "(set it there as FREECAD_AUTH_TOKEN)."
        )
        copy_button.clicked.connect(self._copy_token)
        new_button = QtWidgets.QPushButton("New Token")
        new_button.setToolTip(
            "Replace the token, e.g. if it was shared by mistake.\n"
            "Takes effect at once; MCP servers on this computer pick it up by themselves."
        )
        new_button.clicked.connect(self._new_token)
        token_buttons.addWidget(copy_button)
        token_buttons.addWidget(new_button)
        token_buttons.addStretch()
        security_layout.addRow(token_buttons)

        self.bind_host_edit = QtWidgets.QLineEdit()
        self.bind_host_edit.setToolTip(
            "Addresses the bridge listens on, separated by commas, such as\n"
            "localhost, 192.168.0.10. localhost (the default) keeps it to this computer.\n"
            "One other address is allowed. It needs FreeCAD with Python 3.13 or newer: connections from other\n"
            "computers are encrypted with the token (TLS-PSK), and the MCP server there needs\n"
            "FREECAD_AUTH_TOKEN. The first time another computer connects, FreeCAD asks you\n"
            "whether to let it in. Every address at once (0.0.0.0) isn't allowed. Restart the\n"
            "bridge after changing it."
        )
        security_layout.addRow("Listen on:", self.bind_host_edit)

        computers_row = QtWidgets.QHBoxLayout()
        self.computers_label = QtWidgets.QLabel()
        self.computers_label.setWordWrap(True)
        self.computers_label.setToolTip(
            "Other computers you chose to always let connect. FreeCAD asks again about\n"
            "each one after Forget All."
        )
        forget_button = QtWidgets.QPushButton("Forget All")
        forget_button.clicked.connect(self._forget_computers)
        computers_row.addWidget(self.computers_label, 1)
        computers_row.addWidget(forget_button)
        security_layout.addRow("Allowed computers:", computers_row)

        permission_intro = QtWidgets.QLabel(
            "Not every task has its own MCP tool, so the AI agent sometimes needs to execute "
            "Python code it writes itself (execute_python, execute_python_file, safe_execute, "
            "run_macro, fc_eval), much like typing into FreeCAD's Python console or running a "
            "macro. That code runs with your full permissions, so these settings control when "
            "it may run. Without it, the AI can still use the regular tools (modeling, sketches, "
            "CAM and so on), but may not manage every task."
        )
        permission_intro.setWordWrap(True)
        security_layout.addRow(permission_intro)

        self.code_approval_combo = QtWidgets.QComboBox()
        self.code_approval_combo.addItem("Ask every time", "ask")
        self.code_approval_combo.addItem("Allow for this FreeCAD session", "session")
        self.code_approval_combo.addItem("Always allow (not recommended)", "always")
        self.code_approval_combo.setToolTip(
            "Whether FreeCAD shows each piece of Python the AI wants to run and runs it only\n"
            "when you click Run. Allowed code runs at once, with your full permissions.\n"
            "Documents and files the AI reads can carry instructions planted by someone else."
        )
        security_layout.addRow("GUI AI Permission:", self.code_approval_combo)

        self.headless_combo = QtWidgets.QComboBox()
        self.headless_combo.addItem("Normal tools only", False)
        self.headless_combo.addItem("Allow the advanced tools (not recommended)", True)
        self.headless_combo.setToolTip(
            "Headless FreeCAD (FreeCADCmd) has no window to ask in, so it can't ask first.\n"
            "Normal tools only: it refuses the AI's Python. Allow: it runs it without asking.\n"
            "Headless FreeCAD reads this setting too."
        )
        security_layout.addRow("Headless AI Permission:", self.headless_combo)

        layout.addWidget(security_group)

        # Server Configuration info
        server_group = QtWidgets.QGroupBox("MCP Server Configuration")
        server_layout = QtWidgets.QVBoxLayout(server_group)

        server_intro = QtWidgets.QLabel(
            "The external MCP server (used by Claude Code, etc.) is configured "
            "separately using environment variables:"
        )
        server_intro.setWordWrap(True)
        server_layout.addWidget(server_intro)

        server_layout.addSpacing(5)

        # Environment variables as a form layout for better alignment
        env_layout = QtWidgets.QFormLayout()
        env_layout.setLabelAlignment(QtCore.Qt.AlignRight)

        env_vars = [
            ("FREECAD_XMLRPC_PORT", "XML-RPC port (default: 9875)"),
            ("FREECAD_SOCKET_PORT", "JSON-RPC socket port (default: 9876)"),
            ("FREECAD_MODE", "Connection mode: xmlrpc, socket, or embedded"),
            ("FREECAD_SOCKET_HOST", "Server hostname (default: localhost)"),
            ("FREECAD_AUTH_TOKEN", "Token, only for a bridge on another computer"),
        ]

        for var_name, description in env_vars:
            var_label = QtWidgets.QLabel(f"<code>{var_name}</code>")
            var_label.setTextFormat(QtCore.Qt.RichText)
            desc_label = QtWidgets.QLabel(description)
            env_layout.addRow(var_label, desc_label)

        server_layout.addLayout(env_layout)

        server_layout.addSpacing(5)

        server_note = QtWidgets.QLabel(
            "<i>Ensure these match the ports configured above.</i>"
        )
        server_note.setTextFormat(QtCore.Qt.RichText)
        server_layout.addWidget(server_note)

        layout.addWidget(server_group)

        # Add stretch to push everything to the top
        layout.addStretch()

    def loadSettings(self) -> None:
        """Load settings from FreeCAD preferences into widgets.

        This method is called by FreeCAD when the Preferences dialog opens.
        """
        # Import here to avoid circular imports and ensure module is available
        from ..preferences import (
            get_auto_start,
            get_socket_port,
            get_status_bar_enabled,
            get_xmlrpc_port,
        )

        self.auto_start_cb.setChecked(get_auto_start())
        self.status_bar_cb.setChecked(get_status_bar_enabled())
        self.xmlrpc_spin.setValue(get_xmlrpc_port())
        self.socket_spin.setValue(get_socket_port())

        from ..freecad_mcp_bridge.auth import token_file
        from ..preferences import get_bind_host

        self.bind_host_edit.setText(get_bind_host())
        self.token_file_label.setText(str(token_file()))
        self._show_computers()

        from ..freecad_mcp_bridge.approval import headless_allowed, standing_permission

        index = self.code_approval_combo.findData(standing_permission() or "ask")
        self.code_approval_combo.setCurrentIndex(max(index, 0))
        self.headless_combo.setCurrentIndex(1 if headless_allowed() else 0)

    def saveSettings(self) -> None:
        """Save settings from widgets to FreeCAD preferences.

        This method is called by FreeCAD when OK or Apply is clicked.
        """
        from ..preferences import (
            get_socket_port,
            get_xmlrpc_port,
            set_auto_start,
            set_socket_port,
            set_status_bar_enabled,
            set_xmlrpc_port,
        )

        # Track if ports changed for potential restart
        old_xmlrpc = get_xmlrpc_port()
        old_socket = get_socket_port()

        # Save all preferences
        set_auto_start(self.auto_start_cb.isChecked())
        set_status_bar_enabled(self.status_bar_cb.isChecked())
        set_xmlrpc_port(self.xmlrpc_spin.value())
        set_socket_port(self.socket_spin.value())

        from ..freecad_mcp_bridge.approval import (
            set_headless_allowed,
            set_standing_permission,
            standing_permission,
        )
        from ..preferences import get_bind_host, set_bind_host

        scope = self.code_approval_combo.currentData()
        if scope != (standing_permission() or "ask"):
            set_standing_permission(scope)
        set_headless_allowed(bool(self.headless_combo.currentData()))

        old_host = get_bind_host()
        new_host = self._allowed_listen(self.bind_host_edit.text())
        self.bind_host_edit.setText(new_host)
        set_bind_host(new_host)
        if get_bind_host() != old_host:
            import FreeCAD

            FreeCAD.Console.PrintMessage(
                "MCP+ Bridge security settings changed. "
                "If the bridge is running, restart it for changes to take effect.\n"
            )

        # Check if ports changed and notify about restart if needed
        new_xmlrpc = self.xmlrpc_spin.value()
        new_socket = self.socket_spin.value()

        if old_xmlrpc != new_xmlrpc or old_socket != new_socket:
            # Import FreeCAD here to avoid issues at module load time
            import FreeCAD

            FreeCAD.Console.PrintMessage(
                "MCP+ Bridge ports changed. "
                "If the bridge is running, restart it for changes to take effect.\n"
            )

    def _copy_token(self) -> None:
        """Put the token on the clipboard (creating it if there is none yet)."""
        from ..freecad_mcp_bridge.auth import load_or_create_token

        QtWidgets.QApplication.clipboard().setText(load_or_create_token())
        QtWidgets.QMessageBox.information(
            self,
            "MCP+",
            "Token copied. Treat it like a password: whoever has it can run code in FreeCAD.",
        )

    def _allowed_listen(self, setting: str) -> str:
        """``setting`` without the addresses the bridge may not listen on (says why)."""
        from ..freecad_mcp_bridge.server import listen_hosts

        hosts, refused = listen_hosts(setting)
        if refused:
            QtWidgets.QMessageBox.warning(
                self, "MCP+", "Removed from Listen on:\n" + "\n".join(refused)
            )
        return ", ".join(hosts)

    def _show_computers(self) -> None:
        """Show the other computers always allowed to connect."""
        from ..freecad_mcp_bridge.approval import always_allowed_computers

        self.computers_label.setText(", ".join(always_allowed_computers()) or "None")

    def _forget_computers(self) -> None:
        """Ask again about every other computer before it may connect."""
        from ..freecad_mcp_bridge.approval import forget_computers

        forget_computers()
        self._show_computers()

    def _new_token(self) -> None:
        """Replace the token file with a new token."""
        from ..freecad_mcp_bridge.auth import load_or_create_token, token_file

        answer = QtWidgets.QMessageBox.question(
            self,
            "MCP+",
            "Replace the token? MCP servers on other computers that were given the old "
            "token will need the new one.",
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        token_file().unlink(missing_ok=True)
        load_or_create_token()
        QtWidgets.QMessageBox.information(self, "MCP+", "New token created.")
