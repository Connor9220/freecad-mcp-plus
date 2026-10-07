# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2025-2026 Sean P. Kane <spkane@gmail.com>
# SPDX-FileNotice: Part of MCP+.

"""MCP tool implementations for FreeCAD.

This package contains all MCP tool definitions for interacting with FreeCAD.
Tools are organized by category:

- execution: Python code execution tools
- documents: Document management tools
- objects: Object creation and manipulation tools
- partdesign: PartDesign workbench tools
- spreadsheet: Spreadsheet workbench tools for parametric design
- draft: Draft workbench tools (ShapeString for 3D text)
- export: Export functionality tools
- macros: Macro management tools
- view: View and screenshot tools
- validation: Object and document validation tools
- cam: CAM review tools (path statistics, A/B runs)
- cam_checks: gouge and coverage checks
- cam_demo: captioned, mouse-driven demo recordings on test displays
- cam_gui: GUI state check and reset
- cam_headless: headless FreeCADCmd scripts and CAM unit tests
- cam_model: geometry selection and CAM test fixtures
- cam_post: headless posting and G-code diff
- gui_driver: drive the GUI through the add-on's guidriver package (commands,
  task panels, modal dialog rules; CAM add-base/check/post)
"""

from collections.abc import Awaitable, Callable
from typing import Any

from freecad_mcp.tools.cam import register_cam_tools
from freecad_mcp.tools.cam_checks import register_check_tools
from freecad_mcp.tools.cam_demo import register_demo_tools
from freecad_mcp.tools.cam_gui import register_gui_tools
from freecad_mcp.tools.cam_headless import register_headless_tools
from freecad_mcp.tools.cam_model import register_model_tools
from freecad_mcp.tools.cam_post import register_post_tools
from freecad_mcp.tools.documents import register_document_tools
from freecad_mcp.tools.draft import register_draft_tools
from freecad_mcp.tools.execution import register_execution_tools
from freecad_mcp.tools.export import register_export_tools
from freecad_mcp.tools.gui_driver import register_guidriver_tools
from freecad_mcp.tools.macros import register_macro_tools
from freecad_mcp.tools.objects import register_object_tools
from freecad_mcp.tools.partdesign import register_partdesign_tools
from freecad_mcp.tools.spreadsheet import register_spreadsheet_tools
from freecad_mcp.tools.validation import register_validation_tools
from freecad_mcp.tools.view import register_view_tools

__all__ = [
    "register_all_tools",
    "register_cam_tools",
    "register_check_tools",
    "register_demo_tools",
    "register_gui_tools",
    "register_headless_tools",
    "register_model_tools",
    "register_post_tools",
    "register_document_tools",
    "register_draft_tools",
    "register_execution_tools",
    "register_export_tools",
    "register_guidriver_tools",
    "register_macro_tools",
    "register_object_tools",
    "register_partdesign_tools",
    "register_spreadsheet_tools",
    "register_validation_tools",
    "register_view_tools",
]


def register_all_tools(mcp: Any, get_bridge_func: Callable[[], Awaitable[Any]]) -> None:
    """Register all FreeCAD tools with the Robust MCP Server.

    Args:
        mcp: The FastMCP (Robust MCP Server) instance (Any due to lack of stubs).
        get_bridge_func: Async function returning the active bridge connection.
    """
    register_execution_tools(mcp, get_bridge_func)
    register_document_tools(mcp, get_bridge_func)
    register_object_tools(mcp, get_bridge_func)
    register_partdesign_tools(mcp, get_bridge_func)
    register_spreadsheet_tools(mcp, get_bridge_func)
    register_draft_tools(mcp, get_bridge_func)
    register_export_tools(mcp, get_bridge_func)
    register_macro_tools(mcp, get_bridge_func)
    register_view_tools(mcp, get_bridge_func)
    register_validation_tools(mcp, get_bridge_func)
    register_cam_tools(mcp, get_bridge_func)
    register_check_tools(mcp, get_bridge_func)
    register_demo_tools(mcp, get_bridge_func)
    register_gui_tools(mcp, get_bridge_func)
    register_headless_tools(mcp, get_bridge_func)
    register_model_tools(mcp, get_bridge_func)
    register_post_tools(mcp, get_bridge_func)
    register_guidriver_tools(mcp, get_bridge_func)
