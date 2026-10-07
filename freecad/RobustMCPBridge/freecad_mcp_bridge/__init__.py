# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2025-2026 Sean P. Kane <spkane@gmail.com>
# SPDX-FileNotice: Part of MCP+.

"""FreeCAD MCP+ - Bundled server module for the workbench addon.

This module provides the MCP bridge server that runs inside FreeCAD.
It is bundled with the workbench addon for self-contained installation.
"""

from typing import Any

__version__ = "0.6.2"  # Updated by release workflow
__all__ = ["FreecadMCPPlugin", "__version__"]


def __getattr__(name: str) -> Any:
    # Load the server only when asked for: it imports FreeCADGui, and this package is
    # imported while Init.py files run, before FreeCAD's GUI exists
    if name == "FreecadMCPPlugin":
        from .server import FreecadMCPPlugin

        return FreecadMCPPlugin
    raise AttributeError(name)
