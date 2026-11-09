# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""One undo step per run, and files for the AI kept out of the shared temp folder."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from freecad_mcp.bridge import undo
from freecad_mcp.bridge.embedded import EmbeddedBridge
from freecad_mcp.bridge.wire import work_dir

_BRIDGE = (
    Path(__file__).resolve().parents[2] / "freecad" / "MCPPlus" / "freecad_mcp_bridge"
)


def test_both_copies_are_identical():
    assert (_BRIDGE / "undo.py").read_bytes() == Path(undo.__file__).read_bytes()


class _Doc:
    """A document that records its transactions."""

    def __init__(self, undo_mode=1):
        self.UndoMode = undo_mode
        self.HasPendingTransaction = False
        self.calls = []

    def openTransaction(self, name):
        self.HasPendingTransaction = True
        self.calls.append(("open", name))

    def commitTransaction(self):
        self.HasPendingTransaction = False
        self.calls.append(("commit",))


def _freecad(docs, active=None):
    freecad = MagicMock()
    freecad.getActiveTransaction.return_value = None
    freecad.listDocuments.return_value = {str(i): d for i, d in enumerate(docs)}
    freecad.ActiveDocument = active or (docs[0] if docs else None)
    return freecad


class TestUndoStep:
    def test_each_document_gets_one_step(self):
        a, b, no_undo = _Doc(), _Doc(), _Doc(undo_mode=0)
        freecad = _freecad([a, b, no_undo])
        docs = undo.open_step(freecad)
        undo.close_step(freecad, docs)
        for doc in (a, b):
            assert doc.calls == [("open", undo.UNDO_NAME), ("commit",)]
        assert no_undo.calls == []

    def test_open_transaction_is_left_alone(self):
        """A task panel's (or a command's) transaction is not ours to open or close."""
        doc = _Doc()
        freecad = _freecad([doc])
        freecad.getActiveTransaction.return_value = ("Edit Job", 7)
        assert undo.open_step(freecad) == []
        undo.close_step(freecad, [])
        assert doc.calls == []

    def test_panel_opened_by_the_run_keeps_its_transaction(self):
        doc, other = _Doc(), _Doc()
        freecad = _freecad([doc, other])
        docs = undo.open_step(freecad)
        freecad.getActiveTransaction.return_value = ("Edit Job", 9)
        undo.close_step(freecad, docs)
        assert doc.calls == [("open", undo.UNDO_NAME)]
        assert other.calls[-1] == ("commit",)

    @pytest.mark.parametrize("code", ["App.ActiveDocument.Label = 'x'", "1 / 0"])
    def test_embedded_mode_too(self, code):
        """Embedded mode runs in the server's own FreeCAD: one step there too, also on failure."""
        doc = _Doc()
        bridge = EmbeddedBridge()
        bridge._fc_module = _freecad([doc])
        bridge._execute_code(code)
        assert doc.calls == [("open", undo.UNDO_NAME), ("commit",)]

    def test_closed_document_is_skipped(self):
        doc = _Doc()
        freecad = _freecad([doc])
        docs = undo.open_step(freecad)
        type(doc).HasPendingTransaction = property(
            lambda _self: (_ for _ in ()).throw(ReferenceError("closed"))
        )
        try:
            undo.close_step(freecad, docs)
        finally:
            del type(doc).HasPendingTransaction


class TestWorkDir:
    def test_private_and_not_in_temp(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FREECAD_MCP_WORK_DIR")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
        path = work_dir()
        assert path.is_dir()
        assert path.name == "mcp-plus"
        if os.name == "posix" and sys.platform != "darwin":
            assert path.parent == tmp_path / "cache"
            assert stat.S_IMODE(path.stat().st_mode) == 0o700

    @pytest.mark.asyncio
    async def test_screenshot_code_writes_to_the_private_folder(self):
        from freecad_mcp.bridge.xmlrpc import XmlRpcBridge

        bridge = XmlRpcBridge.__new__(XmlRpcBridge)
        sent = []

        async def fake_execute(code, *_args, **_kwargs):
            sent.append(code)
            return MagicMock(success=True, result={"success": False, "error": "x"})

        bridge.execute_python = fake_execute
        with pytest.raises(Exception):
            await bridge.get_screenshot()
        assert sent
        # Worked out on FreeCAD's computer, not the server's
        assert "freecad_mcp_bridge.wire import work_dir" in sent[0]
        assert "dir=work_dir()" in sent[0]
