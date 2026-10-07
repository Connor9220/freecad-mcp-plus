# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Section-aware, tolerance-aware diff of two posted G-code files.

Pure Python (runs in the MCP server, no FreeCAD). Lines are normalised before
comparing: comments and FreeCAD header lines can be dropped, whitespace is
collapsed, leading N line numbers are ignored, and every address word is parsed
so numbers compare within a tolerance (``X1.0`` equals ``X1.00001`` at 1e-4,
``G01`` equals ``G1``). Word order within a line is kept.

Files are split into sections at the markers FreeCAD posts write:
``(Begin preamble)``, ``(Begin operation: <label>)``, ``(Begin postamble)``
(also the lower-case and ``operation start:`` spellings of older scripts, and
``;`` comments). Machine-based posts only emit each op's own ``(<label>)``
comment; pass those labels to split on them.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

_COMMENT_RE = re.compile(r"\(([^)]*)\)|;(.*)$")
_WORD_RE = re.compile(r"([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
_LINENO_RE = re.compile(r"^N\s*\d+\s*")
_WS_RE = re.compile(r"\s+")
_HEADER_RE = re.compile(
    r"^\s*(?:exported by\b|post processor\s*:|output time\b|project file\b|"
    r"cam file\s*:|document\s*:)",
    re.IGNORECASE,
)
_BEGIN_OP_RE = re.compile(
    r"^\s*(?:begin operation|start operation|operation start|operation initiali[sz]e)"
    r"\s*:\s*(.+?)\s*$",
    re.IGNORECASE,
)
_BLOCK_RE = re.compile(r"^\s*begin (preamble|postamble)\s*$", re.IGNORECASE)
_MAX_HUNK_LINES = 80


class _Line:
    """One compared line: its number, raw text, and parsed tokens."""

    __slots__ = ("key", "no", "raw", "tokens")

    def __init__(self, no: int, raw: str, tokens: tuple, key: tuple) -> None:
        self.no = no
        self.raw = raw
        self.tokens = tokens
        self.key = key


def _tokens(code: str) -> list[tuple[str, Any]]:
    """Address words of a comment-free line; unparsed text is kept as a token."""
    out: list[tuple[str, Any]] = []
    pos = 0
    for m in _WORD_RE.finditer(code):
        gap = code[pos : m.start()].strip()
        if gap:
            out.append(("", gap))
        out.append((m.group(1), float(m.group(2))))
        pos = m.end()
    gap = code[pos:].strip()
    if gap:
        out.append(("", gap))
    return out


def _quantise(tokens: list[tuple[str, Any]], tolerance: float) -> tuple:
    if tolerance <= 0:
        return tuple(tokens)
    return tuple(
        (k, round(v / tolerance)) if isinstance(v, float) else (k, v) for k, v in tokens
    )


def _within(a: _Line, b: _Line, tolerance: float) -> bool:
    if len(a.tokens) != len(b.tokens):
        return False
    for (ka, va), (kb, vb) in zip(a.tokens, b.tokens, strict=True):
        if ka != kb:
            return False
        if isinstance(va, float) and isinstance(vb, float):
            if abs(va - vb) > tolerance:
                return False
        elif va != vb:
            return False
    return True


def parse_sections(
    text: str,
    ignore_comments: bool = True,
    ignore_header: bool = True,
    tolerance: float = 1e-4,
    labels: list[str] | None = None,
) -> list[tuple[str, list[_Line]]]:
    """Split G-code into named sections of normalised lines."""
    label_set = set(labels or [])
    sections: list[tuple[str, list[_Line]]] = [("header", [])]
    seen: dict[str, int] = {"header": 1}

    def start(name: str) -> None:
        seen[name] = seen.get(name, 0) + 1
        sections.append((name if seen[name] == 1 else f"{name}#{seen[name]}", []))

    for no, raw_line in enumerate(text.splitlines(), 1):
        raw = raw_line.strip()
        if not raw:
            continue
        comments = [
            _WS_RE.sub(
                " ", (m.group(1) if m.group(1) is not None else m.group(2)).strip()
            )
            for m in _COMMENT_RE.finditer(raw)
        ]
        code = _WS_RE.sub(" ", _COMMENT_RE.sub(" ", raw)).strip().upper()
        code = _LINENO_RE.sub("", code)
        for c in comments:
            m = _BEGIN_OP_RE.match(c)
            b = _BLOCK_RE.match(c)
            if m:
                start(m.group(1))
            elif b:
                start(b.group(1).lower())
            elif c in label_set and not code:
                start(c)
        if ignore_header and not code and comments and _HEADER_RE.match(comments[0]):
            continue
        tokens = _tokens(code)
        if not ignore_comments:
            tokens += [("(", c) for c in comments if c]
        if not tokens:
            continue
        sections[-1][1].append(
            _Line(no, raw, tuple(tokens), _quantise(tokens, tolerance))
        )
    if not sections[0][1] and len(sections) > 1:
        sections.pop(0)
    return sections


def _compare(
    a: list[_Line], b: list[_Line], tolerance: float, context: int
) -> tuple[dict[str, int], list[list[str]]]:
    """Counts and rendered hunks for one section."""
    sm = difflib.SequenceMatcher(
        None, [x.key for x in a], [x.key for x in b], autojunk=False
    )
    counts = {"changed": 0, "removed": 0, "added": 0}
    hunks: list[list[str]] = []
    for group in sm.get_grouped_opcodes(context):
        body: list[str] = []
        real = False
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                body += [f" {x.raw}" for x in a[i1:i2]]
                continue
            n = min(i2 - i1, j2 - j1) if tag == "replace" else 0
            for k in range(n):
                la, lb = a[i1 + k], b[j1 + k]
                if _within(la, lb, tolerance):
                    body.append(f" {la.raw}")
                else:
                    real = True
                    counts["changed"] += 1
                    body += [f"-{la.raw}", f"+{lb.raw}"]
            for x in a[i1 + n : i2]:
                real = True
                counts["removed"] += 1
                body.append(f"-{x.raw}")
            for x in b[j1 + n : j2]:
                real = True
                counts["added"] += 1
                body.append(f"+{x.raw}")
        if not real:
            continue
        _t, i1, _i2, j1, _j2 = group[0]
        _t, _i1, i2, _j1, j2 = group[-1]
        a_no = a[i1].no if i1 < len(a) else (a[-1].no + 1 if a else 1)
        b_no = b[j1].no if j1 < len(b) else (b[-1].no + 1 if b else 1)
        head = f"@@ -{a_no},{i2 - i1} +{b_no},{j2 - j1} @@"
        if len(body) > _MAX_HUNK_LINES:
            body = [
                *body[:_MAX_HUNK_LINES],
                f"... {len(body) - _MAX_HUNK_LINES} more lines",
            ]
        hunks.append([head, *body])
    return counts, hunks


def gcode_diff(
    a: str,
    b: str,
    *,
    ignore_comments: bool = True,
    ignore_header: bool = True,
    tolerance: float = 1e-4,
    context: int = 2,
    max_hunks: int = 50,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    """Diff two G-code files section by section (see module docstring)."""
    texts = []
    for p in (a, b):
        path = Path(p).expanduser()
        if not path.is_file():
            raise ValueError(f"No such file: {path}")
        texts.append(path.read_text(encoding="utf-8", errors="replace"))
    opts = {
        "ignore_comments": ignore_comments,
        "ignore_header": ignore_header,
        "tolerance": tolerance,
        "labels": labels,
    }
    sec_a = parse_sections(texts[0], **opts)
    sec_b = parse_sections(texts[1], **opts)
    map_a, map_b = dict(sec_a), dict(sec_b)
    names = [n for n, _ in sec_a] + [n for n, _ in sec_b if n not in map_a]

    sections: list[dict[str, Any]] = []
    hunks: list[dict[str, Any]] = []
    total_hunks = 0
    for name in names:
        la, lb = map_a.get(name), map_b.get(name)
        entry: dict[str, Any] = {
            "name": name,
            "a_lines": None if la is None else len(la),
            "b_lines": None if lb is None else len(lb),
        }
        if la is None or lb is None:
            entry["status"] = "only_in_b" if la is None else "only_in_a"
            sections.append(entry)
            continue
        counts, sec_hunks = _compare(la, lb, tolerance, max(0, int(context)))
        entry.update(counts)
        entry["status"] = "different" if sec_hunks else "identical"
        entry["hunks"] = len(sec_hunks)
        sections.append(entry)
        total_hunks += len(sec_hunks)
        for h in sec_hunks:
            if len(hunks) < max_hunks:
                hunks.append({"section": name, "header": h[0], "lines": h[1:]})

    common = [n for n, _ in sec_a if n in map_b]
    order_b = [n for n, _ in sec_b if n in map_a]
    order_same = common == order_b
    differs = not order_same or any(s["status"] != "identical" for s in sections)
    return {
        "verdict": "DIFFERENT" if differs else "IDENTICAL",
        "a": str(Path(a).expanduser()),
        "b": str(Path(b).expanduser()),
        "a_lines": len(texts[0].splitlines()),
        "b_lines": len(texts[1].splitlines()),
        "a_compared": sum(len(v) for _, v in sec_a),
        "b_compared": sum(len(v) for _, v in sec_b),
        "section_order_same": order_same,
        "sections": sections,
        "hunks": hunks,
        "hunks_total": total_hunks,
        "hunks_truncated": total_hunks > len(hunks),
        "options": {**opts, "context": context, "max_hunks": max_hunks},
    }
