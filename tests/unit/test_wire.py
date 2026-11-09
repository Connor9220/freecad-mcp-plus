# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Signed requests and answers both ways, and TLS-PSK, between a real bridge and the clients."""

from __future__ import annotations

import http.client
import json
import os
import socket
import sys
import threading
import time
import xmlrpc.client
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from freecad_mcp.bridge import wire
from freecad_mcp.bridge.socket import SocketBridge
from freecad_mcp.bridge.xmlrpc import signed_proxy

TOKEN = "test-token-" + "x" * 32
ROOT = Path(__file__).resolve().parents[2]
needs_psk = pytest.mark.skipif(
    not wire.psk_available(), reason="TLS-PSK needs Python 3.13+"
)


def test_both_copies_are_identical():
    addon = ROOT / "freecad" / "MCPPlus" / "freecad_mcp_bridge" / "wire.py"
    server = ROOT / "src" / "freecad_mcp" / "bridge" / "wire.py"
    assert addon.read_bytes() == server.read_bytes()


class TestRequestChecker:
    def test_good_request_once(self):
        checker = wire.RequestChecker()
        auth = wire.sign_request(TOKEN, b"body")
        assert checker.check(TOKEN, auth, b"body")
        assert not checker.check(TOKEN, auth, b"body"), "a replay is refused"

    def test_changed_body_wrong_token_and_old_requests_are_refused(self):
        checker = wire.RequestChecker()
        assert not checker.check(TOKEN, wire.sign_request(TOKEN, b"a"), b"b")
        assert not checker.check(TOKEN, wire.sign_request("other", b"a"), b"a")
        old = wire.sign_request(TOKEN, b"a")
        with patch.object(wire.time, "time", return_value=time.time() + 10_000):
            assert not checker.check(TOKEN, old, b"a")
        assert not checker.check(TOKEN, TOKEN, b"a"), (
            "the bare token is no longer enough"
        )

    def test_response_signature(self):
        sig = wire.sign_response(TOKEN, "n1", b"answer")
        assert wire.response_ok(TOKEN, "n1", b"answer", sig)
        assert not wire.response_ok(TOKEN, "n2", b"answer", sig)
        assert not wire.response_ok(TOKEN, "n1", b"changed", sig)
        assert not wire.response_ok(TOKEN, "n1", b"answer", None)

    def test_loopback(self):
        assert all(
            map(wire.is_loopback, ["localhost", "127.0.0.1", "::1", "::ffff:127.0.0.1"])
        )
        assert not any(
            map(wire.is_loopback, ["192.168.1.5", "0.0.0.0", "example.com", ""])  # noqa: S104
        )


class _Params:
    def GetString(self, _name, default=""):
        return default

    def GetBool(self, _name, default=False):
        return default

    def GetInt(self, _name, default=0):
        return default


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    """A real bridge (headless, FreeCAD faked) on free ports, with a known token."""
    freecad = MagicMock()
    freecad.GuiUp = False
    freecad.ParamGet.return_value = _Params()
    monkeypatch.setenv("FREECAD_MCP_TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setenv("FREECAD_MCP_TESTING", "1")
    monkeypatch.delenv("FREECAD_MCP_TLS", raising=False)
    with patch.dict(sys.modules, {"FreeCAD": freecad, "FreeCADGui": MagicMock()}):
        for name in list(sys.modules):
            if "freecad_mcp_bridge" in name:
                del sys.modules[name]
        from freecad.MCPPlus.freecad_mcp_bridge import server

        plugin = server.FreecadMCPPlugin(
            host="localhost",
            port=_free_port(),
            xmlrpc_port=_free_port(),
            enable_xmlrpc=True,
            auth_token=TOKEN,
        )
        plugin.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("localhost", plugin.xmlrpc_port), 0.2).close()
                socket.create_connection(("localhost", plugin.socket_port), 0.2).close()
                break
            except OSError:
                time.sleep(0.05)
        yield plugin
        plugin.stop()
        for name in list(sys.modules):
            if "freecad_mcp_bridge" in name:
                del sys.modules[name]


class TestXmlRpc:
    def test_signed_call_and_signed_answer(self, bridge):
        proxy = signed_proxy("localhost", bridge.xmlrpc_port, TOKEN)
        result = proxy.execute("_result_ = 6 * 7")
        assert result["result"] == 42

    def test_unsigned_and_wrong_token_are_refused(self, bridge):
        plain = xmlrpc.client.ServerProxy(f"http://localhost:{bridge.xmlrpc_port}")
        with pytest.raises(xmlrpc.client.ProtocolError, match="403"):
            plain.ping()
        with pytest.raises(xmlrpc.client.ProtocolError, match="403"):
            signed_proxy("localhost", bridge.xmlrpc_port, "wrong-token").ping()

    def test_get_and_foreign_host_are_refused(self, bridge):
        """No banner without a token, and nothing for a web page whose name points here."""
        port = bridge.xmlrpc_port

        def status(method, host):
            body = xmlrpc.client.dumps((), "ping").encode()
            auth = wire.sign_request(TOKEN, body)
            conn = http.client.HTTPConnection("localhost", port, timeout=5)
            conn.putrequest(method, "/", skip_host=True)
            conn.putheader("Host", host)
            for header, key in (
                ("X-MCP-Time", "ts"),
                ("X-MCP-Nonce", "nonce"),
                ("X-MCP-Sig", "sig"),
            ):
                conn.putheader(header, auth[key])
            conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            try:
                return conn.getresponse().status
            finally:
                conn.close()

        assert status("GET", f"localhost:{port}") == 403
        assert status("POST", f"evil.example:{port}") == 403
        assert status("POST", f"localhost:{port}") == 200
        assert status("POST", f"[::1]:{port}") == 200

    @needs_psk
    def test_tls_psk(self, bridge, monkeypatch):
        monkeypatch.setenv("FREECAD_MCP_TLS", "1")
        proxy = signed_proxy("localhost", bridge.xmlrpc_port, TOKEN)
        assert proxy.execute("_result_ = 'over tls'")["result"] == "over tls"
        with pytest.raises((OSError, xmlrpc.client.ProtocolError)):
            signed_proxy("localhost", bridge.xmlrpc_port, "wrong-token").ping()


@needs_psk
@pytest.mark.asyncio
async def test_another_computer_is_let_in_only_when_allowed(bridge, monkeypatch):
    """Both listeners ask about another computer once its TLS-PSK handshake succeeded."""
    monkeypatch.setenv("FREECAD_MCP_TLS", "1")
    server = sys.modules["freecad.MCPPlus.freecad_mcp_bridge.server"]
    monkeypatch.setattr(server.wire, "peer_address", lambda _peer: "10.0.0.60")
    # Headless, and not always allowed: turned away
    with pytest.raises((OSError, xmlrpc.client.ProtocolError)):
        signed_proxy("localhost", bridge.xmlrpc_port, TOKEN).ping()
    with pytest.raises((ConnectionError, OSError)):
        await SocketBridge("localhost", bridge.socket_port, auth_token=TOKEN).connect()
    monkeypatch.setattr(server.approval, "computer_allowed", lambda _address: True)
    assert signed_proxy("localhost", bridge.xmlrpc_port, TOKEN).ping()
    client = SocketBridge("localhost", bridge.socket_port, auth_token=TOKEN)
    await client.connect()
    await client.disconnect()


class TestSocket:
    @pytest.mark.asyncio
    async def test_signed_call_and_signed_answer(self, bridge):
        client = SocketBridge("localhost", bridge.socket_port, auth_token=TOKEN)
        await client.connect()
        result = await client.execute_python("_result_ = {'a': [1, 2.5, None]}")
        await client.disconnect()
        assert result.result == {"a": [1, 2.5, None]}

    @pytest.mark.asyncio
    async def test_wrong_token_is_refused(self, bridge):
        client = SocketBridge("localhost", bridge.socket_port, auth_token="wrong-token")  # noqa: S106
        with pytest.raises(ConnectionError):
            await client.connect()

    @needs_psk
    @pytest.mark.asyncio
    async def test_tls_psk(self, bridge, monkeypatch):
        monkeypatch.setenv("FREECAD_MCP_TLS", "1")
        client = SocketBridge("localhost", bridge.socket_port, auth_token=TOKEN)
        await client.connect()
        result = await client.execute_python("_result_ = 'over tls'")
        await client.disconnect()
        assert result.result == "over tls"

    @pytest.mark.parametrize(
        "line",
        [
            b"POST / HTTP/1.1\r\n",
            b'{"id": 1, "method": "ping", "params": {}}\n',
            b"[1, 2]\n",
        ],
    )
    def test_anything_else_gets_an_error_then_a_hang_up(self, bridge, line):
        with socket.create_connection(("localhost", bridge.socket_port), 5) as raw:
            raw.sendall(line)
            stream = raw.makefile("rb")
            assert "error" in json.loads(stream.readline())
            assert stream.readline() == b"", "the bridge closed the connection"

    def test_plain_from_another_computer_is_refused(self, bridge):
        plugin = bridge
        assert plugin._connection_kind(b"{", ("192.168.1.20", 5000)) is None
        assert plugin._connection_kind(b"{", ("127.0.0.1", 5000)) == "plain"
        expected = "tls" if wire.psk_available() else None
        assert (
            plugin._connection_kind(wire.TLS_FIRST_BYTE, ("192.168.1.20", 5000))
            == expected
        )


class _StoredParams(_Params):
    def __init__(self):
        self.strings = {}

    def GetString(self, name, default=""):
        return self.strings.get(name, default)

    def SetString(self, name, value):
        self.strings[name] = value


@pytest.fixture
def bridge_modules(monkeypatch, tmp_path):
    """The add-on's server and approval modules, FreeCAD faked with stored parameters."""
    freecad = MagicMock()
    freecad.GuiUp = False
    freecad.ParamGet.return_value = params = _StoredParams()
    dialog_module = MagicMock()
    monkeypatch.setenv("FREECAD_MCP_TOKEN_FILE", str(tmp_path / "token"))
    with patch.dict(
        sys.modules,
        {
            "FreeCAD": freecad,
            "FreeCADGui": MagicMock(),
            "freecad.MCPPlus.Qt.approval_dialog": dialog_module,
        },
    ):
        for name in list(sys.modules):
            if "freecad_mcp_bridge" in name:
                del sys.modules[name]
        from freecad.MCPPlus.freecad_mcp_bridge import approval, server
        from freecad.MCPPlus.freecad_mcp_bridge import wire as bridge_wire

        yield SimpleNamespace(
            server=server,
            approval=approval,
            wire=bridge_wire,
            freecad=freecad,
            params=params,
            dialog=dialog_module.ask_connection,
        )
        for name in list(sys.modules):
            if "freecad_mcp_bridge" in name:
                del sys.modules[name]


@pytest.mark.parametrize(
    ("host", "psk", "listens_on"),
    [
        ("localhost", False, ["localhost"]),
        ("127.0.0.1", False, ["127.0.0.1"]),
        ("0.0.0.0", True, ["localhost"]),  # noqa: S104
        ("::", True, ["localhost"]),
        ("192.168.1.20", False, ["localhost"]),
        ("192.168.1.20", True, ["192.168.1.20"]),
        ("localhost, 192.168.1.20", True, ["localhost", "192.168.1.20"]),
        ("localhost, 192.168.1.20", False, ["localhost"]),
        ("192.168.1.20, 10.0.0.5, localhost", True, ["192.168.1.20", "localhost"]),
        ("localhost, 0.0.0.0", True, ["localhost"]),
    ],
)
def test_where_the_bridge_listens(bridge_modules, host, psk, listens_on):
    """Localhost and at most one other address, never all at once, and only with TLS-PSK."""
    with patch.object(bridge_modules.wire, "psk_available", return_value=psk):
        plugin = bridge_modules.server.FreecadMCPPlugin(host=host, auth_token=TOKEN)
    assert plugin._hosts == listens_on


class TestOtherComputers:
    """Another computer connects only once the user lets it in."""

    def _ask(self, plugin, peer):
        """Ask from a server thread while this (main) thread answers the dialogs."""
        answers = []
        thread = threading.Thread(
            target=lambda: answers.append(plugin._computer_allowed(peer))
        )
        thread.start()
        while thread.is_alive():
            plugin._run_approvals()
            thread.join(0.01)
        return answers[0]

    def _gui_plugin(self, modules):
        modules.freecad.GuiUp = True
        plugin = modules.server.FreecadMCPPlugin(auth_token=TOKEN)
        plugin._headless = False
        return plugin

    def test_this_computer_never_asks(self, bridge_modules):
        plugin = self._gui_plugin(bridge_modules)
        assert plugin._computer_allowed(("127.0.0.1", 5000))
        assert plugin._computer_allowed(("::ffff:127.0.0.1", 5000, 0, 0))
        # This computer dialing the listen address comes from that address
        plugin._hosts = ["192.168.1.20"]
        assert plugin._computer_allowed(("192.168.1.20", 5000))
        bridge_modules.dialog.assert_not_called()

    def test_session_then_always_then_forget(self, bridge_modules):
        modules = bridge_modules
        plugin = self._gui_plugin(modules)
        approval = modules.approval
        modules.dialog.return_value = approval.SESSION
        assert self._ask(plugin, ("::ffff:10.0.0.37", 5000, 0, 0))
        modules.dialog.assert_called_once_with("10.0.0.37", approval.ANSWER_TIMEOUT_S)
        assert self._ask(plugin, ("10.0.0.37", 5001)), "asked once per session"
        assert modules.dialog.call_count == 1
        assert approval.always_allowed_computers() == []

        modules.dialog.return_value = approval.ALWAYS
        assert self._ask(plugin, ("10.0.0.38", 5000))
        assert approval.always_allowed_computers() == ["10.0.0.38"]

        approval.forget_computers()
        assert approval.computer_allowed("10.0.0.37") is None
        assert approval.always_allowed_computers() == []

    def test_cancel_blocks_until_restart_and_no_answer_asks_again(self, bridge_modules):
        modules = bridge_modules
        plugin = self._gui_plugin(modules)
        modules.dialog.return_value = None
        assert not self._ask(plugin, ("10.0.0.40", 5000))
        assert modules.approval.computer_allowed("10.0.0.40") is None, "ask again"
        modules.dialog.return_value = "cancel"
        assert not self._ask(plugin, ("10.0.0.40", 5000))
        assert not self._ask(plugin, ("10.0.0.40", 5001))
        assert modules.dialog.call_count == 2, "turned away quietly after Cancel"

    def test_many_connections_one_question(self, bridge_modules):
        modules = bridge_modules
        plugin = self._gui_plugin(modules)
        modules.dialog.return_value = modules.approval.SESSION
        answers = []
        threads = [
            threading.Thread(
                target=lambda: answers.append(
                    plugin._computer_allowed(("10.0.0.41", 1))
                )
            )
            for _ in range(5)
        ]
        for thread in threads:
            thread.start()
        while any(thread.is_alive() for thread in threads):
            plugin._run_approvals()
            time.sleep(0.01)
        assert answers == [True] * 5
        assert modules.dialog.call_count == 1

    def test_headless_lets_in_only_always_allowed(self, bridge_modules):
        modules = bridge_modules
        plugin = modules.server.FreecadMCPPlugin(auth_token=TOKEN)
        assert not plugin._computer_allowed(("10.0.0.50", 1))
        modules.params.SetString(modules.approval.COMPUTERS_PARAM, "10.0.0.50")
        assert plugin._computer_allowed(("10.0.0.50", 1))
        modules.dialog.assert_not_called()


def test_wildcard():
    assert all(map(wire.is_wildcard, ["0.0.0.0", "::", " 0.0.0.0 ", "*"]))  # noqa: S104
    assert not any(
        map(wire.is_wildcard, ["localhost", "192.168.1.20", "box.local", ""])
    )


def test_client_refuses_unencrypted_remote(monkeypatch):
    from freecad_mcp.bridge import auth

    monkeypatch.setattr(auth, "psk_available", lambda: False)
    with pytest.raises(ConnectionError, match=r"3\.13"):
        auth.tls_context_for("192.168.1.20", TOKEN)
    assert auth.tls_context_for("localhost", TOKEN) is None
    assert os.environ.get("FREECAD_MCP_TLS") is None
