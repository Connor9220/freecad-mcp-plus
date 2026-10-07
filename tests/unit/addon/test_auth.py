# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Tests for the per-user bridge token (bridge side and server side)."""

import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

from freecad_mcp.bridge.auth import current_token
from freecad_mcp.bridge.auth import token_file as server_token_file

# Loaded by path: importing the add-on package itself needs FreeCAD
_AUTH_PATH = (
    Path(__file__).parents[3]
    / "freecad"
    / "RobustMCPBridge"
    / "freecad_mcp_bridge"
    / "auth.py"
)
_spec = importlib.util.spec_from_file_location("mcp_bridge_auth", _AUTH_PATH)
bridge_auth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge_auth)


class TestBridgeToken:
    """The bridge creates and reuses the token file."""

    def test_created_once_and_reused(self, tmp_path, monkeypatch):
        """The first call writes a token; later calls return the same one."""
        path = tmp_path / "sub" / "token"
        monkeypatch.setenv("FREECAD_MCP_TOKEN_FILE", str(path))
        first = bridge_auth.load_or_create_token()
        assert len(first) >= 32
        assert bridge_auth.load_or_create_token() == first
        assert path.read_text().strip() == first

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
    def test_only_the_user_can_read_it(self, tmp_path, monkeypatch):
        """The token file is 0600 inside a 0700 folder."""
        path = tmp_path / "fresh" / "token"
        monkeypatch.setenv("FREECAD_MCP_TOKEN_FILE", str(path))
        bridge_auth.load_or_create_token()
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700

    def test_default_location_is_the_user_config_folder(self, tmp_path, monkeypatch):
        """Without an override the file lives in the user's config folder."""
        monkeypatch.delenv("FREECAD_MCP_TOKEN_FILE", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        if sys.platform not in ("win32", "darwin"):
            assert bridge_auth.token_file() == tmp_path / "mcp-plus" / "token"

    def test_bridge_and_server_agree_on_the_location(self, tmp_path, monkeypatch):
        """Both sides compute the same path, override or not."""
        monkeypatch.delenv("FREECAD_MCP_TOKEN_FILE", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert bridge_auth.token_file() == server_token_file()
        monkeypatch.setenv("FREECAD_MCP_TOKEN_FILE", str(tmp_path / "x"))
        assert bridge_auth.token_file() == server_token_file()


class TestServerToken:
    """The server picks the token to send."""

    def test_explicit_token_wins(self, tmp_path, monkeypatch):
        """FREECAD_AUTH_TOKEN (passed in) beats the file."""
        path = tmp_path / "token"
        path.write_text("from-file\n")
        monkeypatch.setenv("FREECAD_MCP_TOKEN_FILE", str(path))
        assert current_token("explicit") == "explicit"

    def test_reads_the_bridge_file(self, tmp_path, monkeypatch):
        """Without an explicit token the bridge's file is used."""
        path = tmp_path / "token"
        path.write_text("from-file\n")
        monkeypatch.setenv("FREECAD_MCP_TOKEN_FILE", str(path))
        assert current_token(None) == "from-file"

    def test_no_file_means_no_token(self, tmp_path, monkeypatch):
        """An old bridge (no file) gets requests without a token."""
        monkeypatch.setenv("FREECAD_MCP_TOKEN_FILE", str(tmp_path / "missing"))
        assert current_token(None) is None
