# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2025-2026 Sean P. Kane <spkane@gmail.com>
# SPDX-FileNotice: Part of MCP+.

"""Qt/PySide UI components for the MCP+ workbench.

This module contains Qt-based user interface components:
- status_widget: Status bar widget showing bridge connection state
- preferences_page: Preferences dialog for configuring the bridge
"""

from .preferences_page import MCPBridgePreferencesPage
from .status_widget import MCPStatusWidget

__all__ = [
    "MCPBridgePreferencesPage",
    "MCPStatusWidget",
]
