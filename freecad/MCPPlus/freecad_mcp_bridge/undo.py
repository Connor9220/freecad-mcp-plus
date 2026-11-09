# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""One undo step for each run the AI sends, so Edit > Undo reverts the whole run.

Each open document gets its own step, named UNDO_NAME. FreeCAD commands the run calls that open
their own undo step split it there. A run that leaves a transaction open (a task panel waiting
for OK or Cancel) keeps it: that document's step is left to the panel.

The add-on and the server (for embedded mode) carry identical copies of this file; a test keeps
them equal.
"""

from __future__ import annotations

import contextlib
from typing import Any

UNDO_NAME = "MCP+ AI change"


def open_step(freecad: Any) -> list[Any]:
    """Open the step in each open document; [] when a transaction is already open."""
    if freecad.getActiveTransaction():
        return []
    docs = [doc for doc in freecad.listDocuments().values() if doc.UndoMode]
    for doc in docs:
        doc.openTransaction(UNDO_NAME)
    return docs


def close_step(freecad: Any, docs: list[Any]) -> None:
    """Commit the step in each document, except where the run left its own one open."""
    if not docs:
        return
    left_open = freecad.getActiveTransaction()
    for doc in docs:
        # A document the run closed raises here
        with contextlib.suppress(Exception):
            if not doc.HasPendingTransaction:
                continue
            if (
                left_open
                and left_open[0] != UNDO_NAME
                and doc is freecad.ActiveDocument
            ):
                continue
            doc.commitTransaction()
