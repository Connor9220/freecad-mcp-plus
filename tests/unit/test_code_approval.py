# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""The user approves the AI's code before it runs (server side and bridge side)."""

from __future__ import annotations

import os
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from freecad_mcp.bridge.base import ExecutionResult
from freecad_mcp.tools import approval

CODE = "_result_ = 1"


@pytest.fixture(autouse=True)
def _ask_every_time(ask_before_running):
    """Approval on."""


def _bridge(answer=None, error=None):
    """A bridge whose request_approval answers ``answer`` (or raises ``error``)."""
    bridge = AsyncMock()
    bridge.request_approval = AsyncMock(return_value=answer, side_effect=error)
    return bridge


def _getter(bridge):
    async def get_bridge():
        return bridge

    return get_bridge


class TestRequireApproval:
    """require_approval: FreeCAD decides, and anything but "allow" refuses."""

    @pytest.mark.asyncio
    async def test_freecad_allows(self):
        bridge = _bridge({"decision": "allow", "scope": "once"})
        await approval.require_approval(_getter(bridge), "execute_python", CODE, "d")
        bridge.request_approval.assert_called_once_with("execute_python", CODE, "d")

    @pytest.mark.asyncio
    async def test_freecad_denies(self):
        bridge = _bridge({"decision": "deny"})
        with pytest.raises(PermissionError, match="declined"):
            await approval.require_approval(_getter(bridge), "execute_python", CODE)

    @pytest.mark.asyncio
    async def test_headless_off_says_how_to_allow(self):
        bridge = _bridge({"decision": "headless_off"})
        with pytest.raises(PermissionError, match="headless") as raised:
            await approval.require_approval(_getter(bridge), "execute_python", CODE)
        assert "declined" not in str(raised.value)

    @pytest.mark.asyncio
    async def test_old_addon_refuses(self):
        bridge = _bridge(error=RuntimeError('method "approve" is not supported'))
        with pytest.raises(PermissionError, match="too old"):
            await approval.require_approval(_getter(bridge), "execute_python", CODE)

    @pytest.mark.asyncio
    async def test_other_bridge_errors_pass_through(self):
        bridge = _bridge(error=TimeoutError("no reply"))
        with pytest.raises(TimeoutError):
            await approval.require_approval(_getter(bridge), "execute_python", CODE)

    @pytest.mark.asyncio
    async def test_unknown_answer_is_not_approval(self):
        bridge = _bridge(MagicMock())
        with pytest.raises(PermissionError):
            await approval.require_approval(_getter(bridge), "execute_python", CODE)

    @pytest.mark.asyncio
    async def test_without_freecad(self):
        """No FreeCAD: tools that need it fail; fc_eval and cam_ab refuse."""

        async def no_bridge():
            msg = "FreeCAD isn't running"
            raise ConnectionError(msg)

        with pytest.raises(ConnectionError):
            await approval.require_approval(no_bridge, "execute_python", CODE)
        with pytest.raises(PermissionError, match="no FreeCAD is running"):
            await approval.require_approval(
                no_bridge, "fc_eval", CODE, runs_in_freecad=False
            )


class TestToolsAsk:
    """Each code-running tool asks first, and runs nothing when declined."""

    @pytest.fixture
    def tools_and_bridge(self):
        from freecad_mcp.tools.execution import register_execution_tools
        from freecad_mcp.tools.validation import register_validation_tools

        tools = {}
        mcp = MagicMock()
        mcp.tool = lambda: lambda func: tools.setdefault(func.__name__, func)
        bridge = _bridge({"decision": "deny"})
        bridge.execute_python = AsyncMock(
            return_value=ExecutionResult(
                success=True, result=None, stdout="", stderr="", execution_time_ms=1
            )
        )
        register_execution_tools(mcp, _getter(bridge))
        register_validation_tools(mcp, _getter(bridge))
        return tools, bridge

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool", ["execute_python", "safe_execute"])
    async def test_declined_code_never_runs(self, tools_and_bridge, tool):
        tools, bridge = tools_and_bridge
        with pytest.raises(PermissionError):
            await tools[tool](code=CODE)
        bridge.request_approval.assert_called_once()
        assert bridge.request_approval.call_args.args[:2] == (tool, CODE)
        bridge.execute_python.assert_not_called()

    @pytest.mark.asyncio
    async def test_fc_eval_asks_before_starting_freecadcmd(self):
        from freecad_mcp.tools import cam_headless

        tools = {}
        mcp = MagicMock()
        mcp.tool = lambda: lambda func: tools.setdefault(func.__name__, func)
        bridge = _bridge({"decision": "deny"})
        cam_headless.register_headless_tools(mcp, _getter(bridge))
        with (
            patch.object(cam_headless.headless, "fc_eval") as run,
            pytest.raises(PermissionError),
        ):
            await tools["fc_eval"](code=CODE)
        run.assert_not_called()


class _Params:
    """Stand-in for a FreeCAD parameter group."""

    def __init__(self):
        self.values = {}

    def GetBool(self, name, default=False):
        return self.values.get(name, default)

    def SetBool(self, name, value):
        self.values[name] = value

    def GetString(self, _name, default=""):
        return default


class TestBridgeApproval:
    """The bridge answers from standing permission, or asks on the main thread."""

    @pytest.fixture
    def bridge_env(self, tmp_path):
        params = _Params()
        freecad = MagicMock()
        freecad.GuiUp = True
        freecad.ParamGet.return_value = params
        # The dialog module needs FreeCAD's PySide; tests answer for the dialog instead
        dialog_module = SimpleNamespace(ApprovalDialog=MagicMock())
        with (
            patch.dict(
                sys.modules,
                {
                    "FreeCAD": freecad,
                    "FreeCADGui": MagicMock(),
                    "freecad.MCPPlus.Qt.approval_dialog": dialog_module,
                },
            ),
            patch.dict(
                os.environ,
                {
                    "FREECAD_MCP_TESTING": "1",
                    "FREECAD_MCP_TOKEN_FILE": str(tmp_path / "token"),
                },
            ),
        ):
            for name in list(sys.modules):
                if "freecad_mcp_bridge" in name:
                    del sys.modules[name]
            from freecad.MCPPlus.freecad_mcp_bridge import approval as bridge_approval
            from freecad.MCPPlus.freecad_mcp_bridge import server

            yield server, bridge_approval, params, dialog_module.ApprovalDialog
            for name in list(sys.modules):
                if "freecad_mcp_bridge" in name:
                    del sys.modules[name]

    def test_headless_follows_its_own_setting(self, bridge_env):
        server, bridge_approval, params, dialog = bridge_env
        plugin = server.FreecadMCPPlugin()
        plugin._headless = True
        # The GUI's "always" doesn't reach headless FreeCAD
        params.values[bridge_approval.ALWAYS_PARAM] = True
        assert plugin.request_approval("execute_python", CODE) == {
            "decision": "headless_off"
        }
        assert plugin.bridge_status()["code_approval"] == "headless_off"
        params.values[bridge_approval.HEADLESS_PARAM] = True
        assert plugin.request_approval("execute_python", CODE) == {
            "decision": "allow",
            "scope": "headless",
        }
        dialog.ask.assert_not_called()

    def test_always_allowed_skips_the_dialog(self, bridge_env):
        server, bridge_approval, params, dialog = bridge_env
        params.values[bridge_approval.ALWAYS_PARAM] = True
        plugin = server.FreecadMCPPlugin()
        answer = plugin.request_approval("execute_python", CODE)
        assert answer == {"decision": "allow", "scope": "always"}
        assert plugin._approval_queue.empty()
        dialog.ask.assert_not_called()

    def _ask_on_main_thread(self, plugin, tool="execute_python"):
        """Ask from a server thread while this (main) thread runs the queue."""
        answers = []
        thread = threading.Thread(
            target=lambda: answers.append(plugin.request_approval(tool, CODE, "d"))
        )
        thread.start()
        while thread.is_alive():
            plugin._run_approvals()
            thread.join(0.01)
        return answers[0]

    def test_dialog_answer_and_session_scope(self, bridge_env):
        server, bridge_approval, _, dialog = bridge_env
        plugin = server.FreecadMCPPlugin()
        dialog.ask.return_value = bridge_approval.SESSION
        answer = self._ask_on_main_thread(plugin)
        assert answer == {"decision": "allow", "scope": "session"}
        dialog.ask.assert_called_once_with(
            "execute_python", CODE, "d", bridge_approval.ANSWER_TIMEOUT_S
        )
        # The session allowance answers the next request without a dialog
        assert plugin.request_approval("execute_python", CODE) == {
            "decision": "allow",
            "scope": "session",
        }
        # "Ask every time" in the preferences ends the session's allowance
        bridge_approval.set_standing_permission("ask")
        assert bridge_approval.standing_permission() is None
        bridge_approval.set_standing_permission(bridge_approval.ALWAYS)
        assert bridge_approval.standing_permission() == bridge_approval.ALWAYS

    def test_cancel_and_dialog_errors_deny(self, bridge_env):
        server, _, _, dialog = bridge_env
        plugin = server.FreecadMCPPlugin()
        dialog.ask.return_value = None
        assert self._ask_on_main_thread(plugin) == {"decision": "deny"}
        dialog.ask.side_effect = RuntimeError("no display")
        answer = self._ask_on_main_thread(plugin)
        assert answer["decision"] == "deny"
        assert "no display" in answer["error"]

    def test_status_shows_the_pending_question(self, bridge_env):
        server, _, _, _ = bridge_env
        plugin = server.FreecadMCPPlugin()
        status = plugin.bridge_status()
        assert status["code_approval"] == "ask"
        assert status["approval_pending"] is None
        assert status["protocol"] >= 3
