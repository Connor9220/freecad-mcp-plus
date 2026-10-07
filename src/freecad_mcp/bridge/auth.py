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
