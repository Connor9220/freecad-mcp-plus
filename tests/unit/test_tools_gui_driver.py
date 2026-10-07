"""Tests for the GuiDriver tools module and the add-on's guidriver package."""

import ast
import sys
import types
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from freecad_mcp.bridge.base import ExecutionResult
from freecad_mcp.tools.gui_driver import guidriver_code

ROOT = Path(__file__).parent.parent.parent
ADDON_DIR = ROOT / "freecad" / "RobustMCPBridge"
GUIDRIVER = ADDON_DIR / "guidriver"

TOOLS = {
    "gui_run_command": "guidriver.mcp",
    "gui_panel": "guidriver.mcp",
    "gui_edit": "guidriver.mcp",
    "cam_gui_add_base": "guidriver.cam.mcp",
    "cam_gui_check": "guidriver.cam.mcp",
    "cam_gui_post": "guidriver.cam.mcp",
}


def _ok(result=None):
    return ExecutionResult(
        success=True,
        result=result or {"ok": True},
        stdout="",
        stderr="",
        execution_time_ms=1.0,
    )


@pytest.fixture
def mock_mcp():
    mcp = MagicMock()
    mcp._registered_tools = {}

    def tool_decorator():
        def wrapper(func):
            mcp._registered_tools[func.__name__] = func
            return func

        return wrapper

    mcp.tool = tool_decorator
    return mcp


@pytest.fixture
def mock_bridge():
    bridge = AsyncMock()
    bridge.execute_python = AsyncMock(return_value=_ok())
    return bridge


@pytest.fixture
def tools(mock_mcp, mock_bridge):
    from freecad_mcp.tools.gui_driver import register_guidriver_tools

    async def get_bridge():
        return mock_bridge

    register_guidriver_tools(mock_mcp, get_bridge)
    return mock_mcp._registered_tools


def _sent(mock_bridge):
    args = mock_bridge.execute_python.call_args.args
    return args[0], args[1]


def _run_sent(code, module):
    """Exec the generated code against a stub guidriver module; return the call."""
    calls = []
    stub = types.ModuleType(module)

    def __getattr__(name):
        def entry(**kwargs):
            calls.append((name, kwargs))
            return {"called": name}

        return entry

    stub.__getattr__ = __getattr__
    parents = module.split(".")
    saved = {
        n: sys.modules.get(n)
        for n in [".".join(parents[: i + 1]) for i in range(len(parents))]
    }
    try:
        for i in range(len(parents) - 1):
            pkg = types.ModuleType(".".join(parents[: i + 1]))
            pkg.__path__ = []
            sys.modules[pkg.__name__] = pkg
        sys.modules[module] = stub
        for i in range(len(parents) - 1):
            setattr(
                sys.modules[".".join(parents[: i + 1])],
                parents[i + 1],
                sys.modules[".".join(parents[: i + 2])],
            )
        ns = {}
        exec(compile(code, "<guidriver>", "exec"), ns)  # noqa: S102
    finally:
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m
    assert ns["_result_"] == {"called": calls[0][0]}
    return calls[0]


class TestRegistration:
    def test_all_tools_registered(self, tools):
        assert set(tools) == set(TOOLS)

    def test_registered_by_register_all_tools(self, mock_mcp):
        from freecad_mcp.tools import register_all_tools

        register_all_tools(mock_mcp, AsyncMock())
        assert set(TOOLS) <= set(mock_mcp._registered_tools)
        # existing GUI tools are not duplicated
        assert "gui_health" in mock_mcp._registered_tools


class TestGeneratedCode:
    def test_code_imports_guidriver_with_clear_error(self):
        code = guidriver_code("run_command", {"command": "Part_Box"})
        assert "import guidriver.mcp as _gd" in code
        assert "update the MCP+ add-on" in code
        assert "_result_ = _gd.run_command(**{'command': 'Part_Box'})" in code
        compile(code, "<test>", "exec")

    def test_code_quotes_strings_safely(self):
        code = guidriver_code("run_command", {"command": "x'); import os; ('"})
        _name, kwargs = _run_sent(code, "guidriver.mcp")
        assert kwargs["command"] == "x'); import os; ('"

    @pytest.mark.asyncio
    async def test_gui_run_command(self, tools, mock_bridge):
        rules = ["input_int", {"rule": "message_box", "args": ["Yes"]}]
        result = await tools["gui_run_command"](
            command="CAM_Profile", modal_rules=rules, select=[["Body", "Face1"]]
        )
        assert result == {"ok": True}
        code, timeout = _sent(mock_bridge)
        assert timeout == 60000
        name, kwargs = _run_sent(code, "guidriver.mcp")
        assert name == "run_command"
        assert kwargs == {
            "command": "CAM_Profile",
            "modal": rules,
            "select": [["Body", "Face1"]],
            "doc_name": None,
        }

    @pytest.mark.asyncio
    async def test_gui_panel_set_many(self, tools, mock_bridge):
        await tools["gui_panel"](
            action="set", values={"stepDown": "0.25 in", "stepOver": 30}, page="Heights"
        )
        name, kwargs = _run_sent(_sent(mock_bridge)[0], "guidriver.mcp")
        assert name == "panel"
        assert kwargs["action"] == "set"
        assert kwargs["values"] == {"stepDown": "0.25 in", "stepOver": 30}
        assert kwargs["page"] == "Heights"
        assert kwargs["modal"] is None

    @pytest.mark.asyncio
    async def test_gui_panel_ok_timeout(self, tools, mock_bridge):
        await tools["gui_panel"](action="ok", timeout_ms=5000)
        code, timeout = _sent(mock_bridge)
        assert timeout == 5000
        assert _run_sent(code, "guidriver.mcp")[1]["action"] == "ok"

    @pytest.mark.asyncio
    async def test_gui_edit(self, tools, mock_bridge):
        await tools["gui_edit"](object_name="Profile", doc_name="Doc")
        name, kwargs = _run_sent(_sent(mock_bridge)[0], "guidriver.mcp")
        assert name == "edit"
        assert kwargs == {"object_name": "Profile", "modal": None, "doc_name": "Doc"}

    @pytest.mark.asyncio
    async def test_cam_gui_add_base(self, tools, mock_bridge):
        await tools["cam_gui_add_base"](
            selections=[["Clone", ["Face1", "Face2"]]], clear=True
        )
        name, kwargs = _run_sent(_sent(mock_bridge)[0], "guidriver.cam.mcp")
        assert name == "add_base"
        assert kwargs["selections"] == [["Clone", ["Face1", "Face2"]]]
        assert kwargs["clear"] is True

    @pytest.mark.asyncio
    async def test_cam_gui_check(self, tools, mock_bridge):
        await tools["cam_gui_check"](job="Job", include_summary=True)
        name, kwargs = _run_sent(_sent(mock_bridge)[0], "guidriver.cam.mcp")
        assert name == "check"
        assert kwargs == {
            "job_name": "Job",
            "tabs": False,
            "summary": True,
            "doc_name": None,
        }

    @pytest.mark.asyncio
    async def test_cam_gui_post_passes_deny_list(self, tools, mock_bridge, monkeypatch):
        monkeypatch.setenv("FREECAD_CAM_POST_DENY", " nibbler , ,live")
        await tools["cam_gui_post"](
            job="Job", postprocessor="linuxcnc", dust=[True, False]
        )
        code, timeout = _sent(mock_bridge)
        assert timeout == 120000
        name, kwargs = _run_sent(code, "guidriver.cam.mcp")
        assert name == "post"
        assert kwargs["deny"] == ["nibbler", "live"]
        assert kwargs["postname"] == "linuxcnc"
        assert kwargs["dust"] == [True, False]
        assert kwargs["show_editor"] is False

    @pytest.mark.asyncio
    async def test_failure_raises_with_traceback(self, tools, mock_bridge):
        mock_bridge.execute_python = AsyncMock(
            return_value=ExecutionResult(
                success=False,
                result=None,
                stdout="",
                stderr="",
                execution_time_ms=1.0,
                error_type="RuntimeError",
                error_traceback="Traceback: no task panel is open",
            )
        )
        with pytest.raises(ValueError, match="no task panel is open"):
            await tools["gui_panel"](action="ok")


class TestAddonPackage:
    def test_package_xml_subdirectory_holds_guidriver(self):
        """FreeCAD puts the workbench <subdirectory> on sys.path -> `import guidriver`."""
        ns = {"p": "https://wiki.freecad.org/Package_Metadata"}
        root = ET.parse(ROOT / "package.xml").getroot()  # noqa: S314
        sub = root.find("p:content/p:workbench/p:subdirectory", ns).text
        assert (ROOT / sub / "guidriver" / "__init__.py").exists()

    @pytest.mark.parametrize(
        "rel",
        [
            "__init__.py",
            "widgets.py",
            "modal.py",
            "taskpanel.py",
            "mcp.py",
            "xmouse.py",
            "xkeys_external.py",
            "cam/__init__.py",
            "cam/modal.py",
            "cam/taskpanel.py",
            "cam/inspect.py",
            "cam/mcp.py",
            "cam/recipes/__init__.py",
            "cam/recipes/class_tray.py",
        ],
    )
    def test_layout(self, rel):
        assert (GUIDRIVER / rel).exists()

    @pytest.mark.parametrize(
        "path", sorted(GUIDRIVER.rglob("*.py")), ids=lambda p: p.name
    )
    def test_python311_syntax_and_qt_imports(self, path):
        """FreeCAD 1.1 ships Python 3.11; Qt only via FreeCAD's PySide wrapper."""
        tree = ast.parse(path.read_text(), feature_version=(3, 11))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                assert not n.startswith(("PySide2", "PySide6")), f"{path}: imports {n}"
                assert "camdriver" not in n, f"{path}: imports {n}"

    def test_generic_part_has_no_cam_rules(self):
        src = (GUIDRIVER / "modal.py").read_text()
        for cam_rule in ("def tc_chooser", "def job_create", "def nibblerbot_post"):
            assert cam_rule not in src
        assert "def tc_chooser" in (GUIDRIVER / "cam" / "modal.py").read_text()

    def test_xmouse_not_imported_eagerly(self):
        """xmouse opens the X display at import; the package must not import it."""
        src = (GUIDRIVER / "__init__.py").read_text()
        assert "xmouse" not in [
            a.name
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.ImportFrom)
            for a in n.names
        ]
