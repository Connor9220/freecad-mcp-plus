# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2025-2026 Sean P. Kane <spkane@gmail.com>
# SPDX-FileNotice: Part of MCP+.

"""FreeCAD MCP+ - Bundled server module for the workbench addon.

This module provides the MCP bridge server that runs inside FreeCAD.
It is bundled with the workbench addon for self-contained installation.
"""

from .server import FreecadMCPPlugin

__version__ = "0.6.2"  # Updated by release workflow
__all__ = ["FreecadMCPPlugin", "__version__"]
