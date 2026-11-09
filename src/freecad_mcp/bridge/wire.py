# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""How the MCP server and the FreeCAD bridge prove to each other that they know the token.

Every request and every answer carries an HMAC-SHA256 signature keyed with the token, so the
token itself never travels: without it nobody can run code or forge an answer, and a captured
request can't be changed or replayed. A connection to another computer is also TLS with the
token as the pre-shared key (TLS-PSK, Python 3.13 or newer), so nobody on the network can read
it either; without TLS-PSK the bridge listens on this computer only. Both sides also keep the
files they write for the AI in the same private folder (``work_dir``).

The add-on (``freecad_mcp_bridge/wire.py``) and the server (``freecad_mcp/bridge/wire.py``)
carry identical copies of this file; a test keeps them equal.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import ssl
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

# How far apart the two computers' clocks may be
MAX_CLOCK_SKEW_S = 120
PSK_IDENTITY = "mcp-plus"
# First byte of a TLS connection (a handshake record)
TLS_FIRST_BYTE = b"\x16"
# TLS 1.2 suites that use the pre-shared key with an ephemeral key exchange; TLS 1.3 needs none
_PSK_CIPHERS = "kECDHEPSK+CHACHA20:kECDHEPSK"
_SEEN_NONCES_PRUNE_AT = 256


def is_loopback(host: str | None) -> bool:
    """Whether ``host`` (a name or address) is this computer."""
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(address.is_loopback or (mapped and mapped.is_loopback))


def peer_address(peer: Any) -> str:
    """The address of a connection's other end (a ``getpeername`` tuple); IPv4 written plainly."""
    host = str(peer[0]) if isinstance(peer, tuple) and peer else ""
    try:
        address = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return host
    return str(getattr(address, "ipv4_mapped", None) or address)


def is_wildcard(host: str) -> bool:
    """Whether ``host`` means every address at once (0.0.0.0, ::)."""
    try:
        return ipaddress.ip_address(host.strip().split("%")[0]).is_unspecified
    except ValueError:
        return host.strip() == "*"


def psk_available() -> bool:
    """Whether this Python can do TLS-PSK (3.13 or newer, with OpenSSL's PSK support)."""
    return bool(getattr(ssl, "HAS_PSK", False)) and hasattr(
        ssl.SSLContext, "set_psk_server_callback"
    )


def _derive(token: str, purpose: bytes) -> bytes:
    return hmac.new(token.encode("utf-8"), purpose, hashlib.sha256).digest()


def _mac(token: str, *parts: str | bytes) -> str:
    mac = hmac.new(_derive(token, b"mcp-plus sign"), digestmod=hashlib.sha256)
    for part in parts:
        data = part if isinstance(part, bytes) else part.encode("utf-8")
        mac.update(len(data).to_bytes(8, "big"))
        mac.update(data)
    return mac.hexdigest()


def canonical(message: Any) -> bytes:
    """A JSON message as the bytes both sides sign: the same however it was written."""
    reparsed = json.loads(json.dumps(message))
    return json.dumps(reparsed, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_request(token: str, body: bytes) -> dict[str, str]:
    """The ``ts``, ``nonce`` and ``sig`` a request carries."""
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    return {"ts": ts, "nonce": nonce, "sig": _mac(token, "request", ts, nonce, body)}


def sign_response(token: str, nonce: str, body: bytes) -> str:
    """The signature of the answer to the request with ``nonce``."""
    return _mac(token, "response", nonce, body)


def response_ok(token: str, nonce: str, body: bytes, sig: Any) -> bool:
    """Whether ``sig`` shows the answer came from a bridge that knows the token."""
    return isinstance(sig, str) and hmac.compare_digest(
        sig, sign_response(token, nonce, body)
    )


class RequestChecker:
    """Checks signed requests: the signature, their age, and that no nonce is used twice."""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, token: str, auth: Any, body: bytes) -> bool:
        """Whether the request ``body`` with ``auth`` (ts, nonce, sig) may be served."""
        if not token or not isinstance(auth, dict):
            return False
        ts, nonce, sig = auth.get("ts"), auth.get("nonce"), auth.get("sig")
        if not (
            isinstance(ts, str) and isinstance(nonce, str) and isinstance(sig, str)
        ):
            return False
        fresh = ts.isdigit() and abs(time.time() - int(ts)) <= MAX_CLOCK_SKEW_S
        if not fresh or not hmac.compare_digest(
            sig, _mac(token, "request", ts, nonce, body)
        ):
            return False
        now = time.monotonic()
        with self._lock:
            if len(self._seen) > _SEEN_NONCES_PRUNE_AT:
                self._seen = {
                    n: t
                    for n, t in self._seen.items()
                    if now - t < 2 * MAX_CLOCK_SKEW_S
                }
            if nonce in self._seen:
                return False
            self._seen[nonce] = now
        return True


def server_tls_context(get_token: Callable[[], str]) -> ssl.SSLContext:
    """TLS for the bridge: only a client that knows the token completes the handshake."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(_PSK_CIPHERS)

    def key_for(identity: str | None) -> bytes:
        token = get_token()
        # An empty key fails the handshake
        return (
            _derive(token, b"mcp-plus psk")
            if token and identity == PSK_IDENTITY
            else b""
        )

    context.set_psk_server_callback(key_for, PSK_IDENTITY)  # type: ignore[attr-defined]
    return context


def client_tls_context(token: str) -> ssl.SSLContext:
    """TLS for the server's connection; the token, not a certificate, proves the bridge."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(_PSK_CIPHERS)
    key = _derive(token, b"mcp-plus psk")
    context.set_psk_client_callback(lambda _hint: (PSK_IDENTITY, key))  # type: ignore[attr-defined]
    return context


def require_psk_handshake(ssl_object: Any) -> None:
    """Refuse a TLS peer that showed a certificate instead of using the token."""
    if ssl_object is not None and ssl_object.getpeercert(binary_form=True):
        msg = "The other side answered with a certificate, not the MCP+ token"
        raise ssl.SSLError(msg)


def work_dir() -> Path:
    """The private folder for files written for the AI (created, user-only).

    ``FREECAD_MCP_WORK_DIR``, else the user's cache folder; never the shared temp folder.
    """
    override = os.environ.get("FREECAD_MCP_WORK_DIR")
    if override:
        path = Path(override).expanduser()
    elif sys.platform == "win32":
        path = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "mcp-plus"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Caches" / "mcp-plus"
    else:
        path = (
            Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
            / "mcp-plus"
        )
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        path.chmod(0o700)
    return path
