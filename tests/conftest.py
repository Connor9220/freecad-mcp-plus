"""Pytest configuration and shared fixtures."""

import pytest


@pytest.fixture
def sample_python_code():
    """Sample Python code for execution tests."""
    return """
x = 1 + 1
_result_ = {"value": x, "type": type(x).__name__}
"""


@pytest.fixture
def sample_freecad_code():
    """Sample FreeCAD Python code."""
    return """
import Part
box = Part.makeBox(10, 20, 30)
_result_ = {
    "volume": box.Volume,
    "area": box.Area,
    "is_valid": box.isValid(),
}
"""


@pytest.fixture
def mock_execution_result():
    """Mock execution result for testing."""
    from freecad_mcp.bridge.base import ExecutionResult

    return ExecutionResult(
        success=True,
        result={"value": 42},
        stdout="",
        stderr="",
        execution_time_ms=10.5,
    )


@pytest.fixture
def mock_document_info():
    """Mock document info for testing."""
    from freecad_mcp.bridge.base import DocumentInfo

    return DocumentInfo(
        name="TestDoc",
        path="/tmp/test.FCStd",
        objects=["Box", "Cylinder"],
        is_modified=False,
        label="Test Document",
    )


@pytest.fixture
def mock_object_info():
    """Mock object info for testing."""
    from freecad_mcp.bridge.base import ObjectInfo

    return ObjectInfo(
        name="Box",
        label="My Box",
        type_id="Part::Box",
        properties={"Length": 10.0, "Width": 20.0, "Height": 30.0},
        shape_info={
            "type": "Solid",
            "volume": 6000.0,
            "area": 2200.0,
            "is_valid": True,
        },
        children=[],
    )


@pytest.fixture(autouse=True)
def _isolated_token_file(tmp_path, monkeypatch):
    """Keep the bridge's auth token file out of the real config folder during tests."""
    monkeypatch.setenv("FREECAD_MCP_TOKEN_FILE", str(tmp_path / "mcp-plus-token"))


# The tool modules that ask the user before running the AI's code
ASKING_TOOLS = ("cam", "cam_demo", "cam_headless", "execution", "macros", "validation")


@pytest.fixture(autouse=True)
def _run_code_without_asking(monkeypatch):
    """Tool tests run the AI's code without the approval question (approval tests ask)."""

    async def allow(*_args, **_kwargs):
        return None

    for name in ASKING_TOOLS:
        monkeypatch.setattr(f"freecad_mcp.tools.{name}.require_approval", allow)


@pytest.fixture
def ask_before_running(monkeypatch):
    """The tools ask the user again (undoes _run_code_without_asking)."""
    from freecad_mcp.tools.approval import require_approval

    for name in ASKING_TOOLS:
        monkeypatch.setattr(
            f"freecad_mcp.tools.{name}.require_approval", require_approval
        )


@pytest.fixture(autouse=True)
def _private_work_dir(monkeypatch, tmp_path):
    """Files written for the AI go to a test folder, not the user's cache."""
    monkeypatch.setenv("FREECAD_MCP_WORK_DIR", str(tmp_path / "mcp-plus-work"))
