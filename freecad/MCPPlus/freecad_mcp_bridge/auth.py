# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""The per-user token every bridge request must carry.

The bridge creates the token on its first start, in a file only the user can read; the MCP server
(``freecad_mcp.bridge.auth``) reads the same file, so a local setup needs no configuration while
other users and processes without access to the user's files can't run code in FreeCAD.
The file's location must match ``freecad_mcp.bridge.auth.token_file``.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path


def token_file() -> Path:
    """Where the token lives: ``FREECAD_MCP_TOKEN_FILE``, else the user's config folder."""
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


def read_token(path: Path | None = None) -> str:
    """The token in the file, or "" when there is none."""
    try:
        return (path or token_file()).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def load_or_create_token() -> str:
    """The user's token, creating it (readable by the user only) on first use."""
    path = token_file()
    token = read_token(path)
    if token:
        return token
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = secrets.token_urlsafe(32)
    try:
        # O_EXCL: when two FreeCADs start at once, only one writes and both use its token
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return read_token(path)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    return token
