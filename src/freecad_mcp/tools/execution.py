# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2025-2026 Sean P. Kane <spkane@gmail.com>
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Execution tools for FreeCAD Robust MCP Server.

This module provides tools for executing Python code in FreeCAD's context,
getting version information, and accessing the console.
"""

import os
import platform
import socket
from collections.abc import Awaitable, Callable
from typing import Any

from freecad_mcp.bridge.base import ExecutionResult
from freecad_mcp.server import get_instance_id

# The bridge protocol this server is written for (2: jobs, paging, status, auth)
EXPECTED_BRIDGE_PROTOCOL = 2

# Runs a file like ``python file.py`` would, inside FreeCAD
_RUN_FILE_CODE = """
_path = {path!r}
with open(_path, encoding="utf-8") as _f:
    _source = _f.read()
_ns = {{
    "__name__": "__main__",
    "__file__": _path,
    "__builtins__": __builtins__,
    "FreeCAD": FreeCAD,
    "App": App,
    "FreeCADGui": FreeCADGui,
    "Gui": Gui,
}}
exec(compile(_source, _path, "exec"), _ns)
_result_ = _ns.get("_result_")
"""


def _execution_dict(result: ExecutionResult) -> dict[str, Any]:
    """An ExecutionResult as the dictionary the execution tools return."""
    answer = {
        "success": result.success,
        "result": result.result,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "execution_time_ms": result.execution_time_ms,
        "error_type": result.error_type,
        "error_traceback": result.error_traceback,
    }
    if result.job_token:
        answer["job_token"] = result.job_token
        answer["still_running"] = True
    return answer


def register_execution_tools(
    mcp: Any, get_bridge: Callable[[], Awaitable[Any]]
) -> None:
    """Register execution-related tools with the Robust MCP Server.

    Args:
        mcp: The FastMCP (Robust MCP Server) instance.
        get_bridge: Async function to get the active bridge.
    """

    @mcp.tool()
    async def execute_python(
        code: str,
        timeout_ms: int = 30000,
        echo: bool = False,
        while_busy: bool = False,
    ) -> dict[str, Any]:
        """Execute Python code in FreeCAD's Python console context.

        This tool allows you to run arbitrary Python code within FreeCAD's
        environment, with access to all FreeCAD modules and the active document.

        Args:
            code: Python code to execute. Use `_result_ = value` to return data
                to the caller. The code has access to FreeCAD, App, FreeCADGui,
                and Gui modules.
            timeout_ms: How long to wait for the run, in milliseconds. Defaults to
                30000. A run that takes longer is NOT stopped: it keeps going, and the
                answer carries a ``job_token``; read its output and final result with
                ``get_output_page(job_token, page_no)``, page 0, 1, ... while
                ``has_more`` is true.
            echo: Also print the run's output to FreeCAD's Report view as it happens,
                so the user can watch it.
            while_busy: Run even while another run is stuck inside a modal dialog
                (bridge_status shows it), to inspect or close that dialog. Ordinary
                runs wait their turn.

        Returns:
            Dictionary containing execution results:
                - success: Whether execution completed without errors
                - result: The value assigned to `_result_` variable
                - stdout: Captured standard output
                - stderr: Captured standard error
                - execution_time_ms: Time taken in milliseconds
                - error_type: Type of exception if failed (None if success)
                - error_traceback: Full traceback if failed (None if success)
                - job_token: Only when the run is still going (see timeout_ms)

        Example:
            Create a simple box and return its volume::

                execute_python('''
                import Part
                box = Part.makeBox(10, 20, 30)
                _result_ = {"volume": box.Volume, "area": box.Area}
                ''')

            List all objects in the active document::

                execute_python('''
                doc = FreeCAD.ActiveDocument
                if doc:
                    _result_ = [obj.Name for obj in doc.Objects]
                else:
                    _result_ = []
                ''')
        """
        bridge = await get_bridge()
        result = await bridge.execute_python(code, timeout_ms, echo, while_busy)
        return _execution_dict(result)

    @mcp.tool()
    async def execute_python_file(
        file_path: str,
        timeout_ms: int = 30000,
        echo: bool = False,
    ) -> dict[str, Any]:
        """Run a Python file in FreeCAD, as if it were run directly.

        The file is read on the machine running FreeCAD and runs with ``__file__``
        set to its path and ``__name__ == "__main__"``, so an
        ``if __name__ == "__main__":`` block runs and tracebacks name the file's lines.
        FreeCAD, App, FreeCADGui and Gui are available; assign ``_result_`` to return
        a value.

        Args:
            file_path: Absolute path of the .py file.
            timeout_ms: How long to wait, as for execute_python; a longer run keeps
                going and answers with a ``job_token`` for get_output_page.
            echo: Also print the run's output to FreeCAD's Report view.

        Returns:
            Same dictionary as execute_python.
        """
        bridge = await get_bridge()
        code = _RUN_FILE_CODE.format(path=file_path)
        result = await bridge.execute_python(code, timeout_ms, echo)
        return _execution_dict(result)

    @mcp.tool()
    async def get_output_page(
        job_token: str,
        page_no: int = 0,
        wait_ms: int = 15000,
    ) -> dict[str, Any]:
        """Read the output of a run that outlasted its timeout, one page at a time.

        execute_python and execute_python_file answer with a ``job_token`` when the
        run is still going. Ask for page 0, then 1, 2, ... while ``has_more`` is true.
        A page comes back as soon as there is output, or after ``wait_ms`` with an
        empty, unnumbered page if the run printed nothing new; ask for the same
        page_no again then. Pages can be fetched again by number.

        Args:
            job_token: Token from the execute answer.
            page_no: 0-based page number.
            wait_ms: How long to wait for new output (at most 60000).

        Returns:
            Dictionary with:
                - page: List of {stream: "stdout"|"stderr", text}
                - page_no: Number of this page (absent on an empty wait)
                - has_more: Whether more pages will follow
                - success, result, execution_time_ms, error_type, error_message,
                  error_traceback: On the final page, the run's outcome
                - error: "unknown or expired job_token" or "page_no out of range"
        """
        bridge = await get_bridge()
        return await bridge.get_output_page(job_token, page_no, wait_ms)

    @mcp.tool()
    async def bridge_status() -> dict[str, Any]:
        """What the FreeCAD bridge is doing, answered without waiting on FreeCAD.

        Works even while FreeCAD is busy running code or stuck on a modal dialog,
        when every other tool would wait. Use it to find out why calls hang.

        Returns:
            Dictionary with:
                - protocol / addon_version: The bridge's protocol level and add-on
                  version (the server expects protocol >= 2)
                - busy: The run occupying FreeCAD's main thread (job_token,
                  running_s, code_head), or None
                - modal_dialog: Title of the modal dialog that is open, or None
                - queue_depth: Runs waiting their turn
                - last_tick_age_s: Seconds since the main thread last checked in
                - jobs_running: Timed-out runs still going
                - auth_required, host, gui_up, instance_id
        """
        bridge = await get_bridge()
        status = await bridge.bridge_status()
        status["server_expects_protocol"] = EXPECTED_BRIDGE_PROTOCOL
        if (status.get("protocol") or 0) < EXPECTED_BRIDGE_PROTOCOL:
            status["warning"] = (
                "The FreeCAD add-on is older than this MCP server; update it."
            )
        return status

    @mcp.tool()
    async def get_freecad_version() -> dict[str, Any]:
        """Get FreeCAD version and build information.

        Returns:
            Dictionary containing version information:
                - version: Version string (e.g., "0.21.2")
                - version_tuple: Version as list of integers
                - build_date: Build date string
                - python_version: Embedded Python version
                - gui_available: Whether GUI is available
        """
        bridge = await get_bridge()
        return await bridge.get_freecad_version()

    @mcp.tool()
    async def get_connection_status() -> dict[str, Any]:
        """Get the current FreeCAD connection status.

        Returns:
            Dictionary containing connection information:
                - connected: Whether bridge is connected
                - mode: Connection mode (embedded, xmlrpc, socket)
                - freecad_version: FreeCAD version string
                - gui_available: Whether GUI is available
                - last_ping_ms: Last ping latency in milliseconds
                - error: Error message if not connected
        """
        bridge = await get_bridge()
        status = await bridge.get_status()
        return {
            "connected": status.connected,
            "mode": status.mode,
            "freecad_version": status.freecad_version,
            "gui_available": status.gui_available,
            "last_ping_ms": status.last_ping_ms,
            "error": status.error,
        }

    @mcp.tool()
    async def get_console_output(lines: int = 100) -> list[str]:
        """Get recent FreeCAD console output.

        Args:
            lines: Maximum number of lines to return. Defaults to 100.

        Returns:
            List of console output lines, most recent last.
        """
        bridge = await get_bridge()
        return await bridge.get_console_output(lines)

    @mcp.tool()
    async def get_mcp_server_environment() -> dict[str, Any]:
        """Get environment info about the MCP Server and FreeCAD connection.

        This tool returns information about the environment where the MCP Server
        is running and the FreeCAD connection state, which is useful for debugging,
        verifying which MCP Server instance you are connected to, and determining
        if GUI features are available.

        Returns:
            Dictionary containing environment information:
                - instance_id: Unique UUID for this server instance (generated at
                    startup). Use this to verify you're connected to the expected
                    server instance in tests and automation.
                - hostname: Machine hostname
                - os_name: Operating system name (Linux, Darwin, Windows)
                - os_version: Operating system version
                - platform: Platform identifier string
                - python_version: Python version running the MCP Server
                - freecad: FreeCAD connection information:
                    - connected: Whether bridge is connected to FreeCAD
                    - mode: Connection mode (embedded, xmlrpc, socket)
                    - version: FreeCAD version string
                    - gui_available: Whether FreeCAD GUI is available (False in
                        headless mode). Use this to skip GUI-only tests.
                    - is_headless: Convenience boolean, True when GUI is NOT
                        available (opposite of gui_available)
                - env_vars: Selected environment variables for debugging:
                    - FREECAD_MODE: Connection mode
                    - FREECAD_SOCKET_HOST: Socket host
                    - FREECAD_SOCKET_PORT: Socket port
                    - FREECAD_XMLRPC_PORT: XML-RPC port

        Example:
            Verify you're connected to the expected server instance::

                env = get_mcp_server_environment()
                expected_id = "abc123..."  # Captured from server startup output
                assert env["instance_id"] == expected_id

            Skip GUI-only tests in headless mode::

                env = get_mcp_server_environment()
                if env["freecad"]["is_headless"]:
                    pytest.skip("Test requires GUI mode")
        """
        # Get FreeCAD connection status
        bridge = await get_bridge()
        status = await bridge.get_status()

        return {
            "instance_id": get_instance_id(),
            "hostname": socket.gethostname(),
            "os_name": platform.system(),
            "os_version": platform.release(),
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "freecad": {
                "connected": status.connected,
                "mode": status.mode,
                "version": status.freecad_version,
                "gui_available": status.gui_available,
                "is_headless": not status.gui_available,
            },
            "env_vars": {
                "FREECAD_MODE": os.environ.get("FREECAD_MODE", ""),
                "FREECAD_SOCKET_HOST": os.environ.get("FREECAD_SOCKET_HOST", ""),
                "FREECAD_SOCKET_PORT": os.environ.get("FREECAD_SOCKET_PORT", ""),
                "FREECAD_XMLRPC_PORT": os.environ.get("FREECAD_XMLRPC_PORT", ""),
            },
        }
