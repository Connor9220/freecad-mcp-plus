# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Finding the token the FreeCAD bridge asks for.

The bridge creates a per-user token file on its first start (see the add-on's
``freecad_mcp_bridge/auth.py``; both must agree on the location). The server reads it on every
call, so a token the bridge recreated is picked up without a restart. An explicit token
(``FREECAD_AUTH_TOKEN``) wins, for a bridge on another machine.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from freecad_mcp.bridge.wire import client_tls_context, is_loopback, psk_available

if TYPE_CHECKING:
    import ssl


def token_file() -> Path:
    """Where the bridge keeps the token: ``FREECAD_MCP_TOKEN_FILE``, else the user's config folder."""
    override = os.environ.get("FREECAD_MCP_TOKEN_FILE")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "mcp-plus" / "token"


def current_token(explicit: str | None = None) -> str | None:
    """The token to send: the explicit one, else the bridge's token file, else None."""
    if explicit:
        return explicit
    try:
        return token_file().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def env_flag(name: str) -> bool:
    """Whether the environment variable ``name`` is set to 1, true, yes or on."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def required_token(explicit: str | None = None) -> str:
    """The token to sign with; ConnectionError when there is none."""
    token = current_token(explicit)
    if not token:
        msg = (
            f"No MCP+ token: start the bridge in FreeCAD once (it creates {token_file()}), "
            "or set FREECAD_AUTH_TOKEN for a bridge on another computer"
        )
        raise ConnectionError(msg)
    return token


def tls_context_for(host: str, token: str) -> ssl.SSLContext | None:
    """TLS-PSK for a bridge on another computer (or with FREECAD_MCP_TLS=1), else None."""
    if is_loopback(host) and not env_flag("FREECAD_MCP_TLS"):
        return None
    if not psk_available():
        msg = (
            f"Reaching FreeCAD at {host} needs an encrypted connection (TLS-PSK), which needs "
            "Python 3.13 or newer for the MCP server"
        )
        raise ConnectionError(msg)
    return client_tls_context(token)
