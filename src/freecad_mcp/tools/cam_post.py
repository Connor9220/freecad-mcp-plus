# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""CAM post-processing review tools for FreeCAD Robust MCP Server.

- cam_post: post-process a CAM job (or some of its operations) in the running
  FreeCAD without any dialog, write the file, and summarise the G-code.
- cam_gcode_diff: section-aware, tolerance-aware diff of two G-code files.
"""

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from freecad_mcp.cam.gcode_diff import gcode_diff

_POST_SRC = (Path(__file__).parent.parent / "cam" / "fc_post.py").read_text()


def register_post_tools(mcp: Any, get_bridge: Callable[[], Awaitable[Any]]) -> None:
    """Register CAM post-processing review tools with the Robust MCP Server."""

    @mcp.tool()
    async def cam_post(
        job: str | None = None,
        operations: list[str] | None = None,
        postprocessor: str | None = None,
        args: str = "",
        output: str | None = None,
        doc_name: str | None = None,
        return_gcode: bool = False,
        max_lines: int = 400,
        dialogs: str = "accept",
        timeout_ms: int = 120000,
    ) -> dict[str, Any]:
        """Post-process a CAM job in the running FreeCAD, without any dialog.

        Uses the same machinery as the Post Process command (PostProcessorFactory;
        export2() for a job with a Machine, export() otherwise) but never opens the
        post chooser, the output-file dialog, the unified post dialog or the G-code
        editor. The stored operation paths are posted as they are: recompute first
        (e.g. cam_path_stats) if they may be stale.

        Args:
            job: Job Name or Label. Optional when the document has one job.
            operations: Op Names/Labels to post, in this order ("Post Process
                Selected"). Default: every op of the job.
            postprocessor: Post name, e.g. "linuxcnc", "grbl", "linuxcnc_legacy".
                None: the job's own post (Machine's post, else Job.PostProcessor,
                else the preference default); fails clearly if there is none.
            args: Post arguments, e.g. "--no-header --inches". With an explicit
                postprocessor they are used exactly ("" = none); with the job's
                own post, "" means Job.PostProcessorArgs. Machine-based posts take
                a JSON dict of property overrides instead. The job is left
                unchanged afterwards.
            output: File to write (always the full G-code). Default: a temp file.
                Several output sections (SplitOutput) are written as extra files.
            doc_name: Document name. Uses the active document if None.
            return_gcode: Also return the first max_lines lines of G-code.
            max_lines: Line limit for return_gcode.
            dialogs: How modal dialogs the post itself opens (e.g. option
                checkboxes) are answered while posting in the GUI: "accept"
                (OK/Yes/default button, checkbox states kept), "reject" (Cancel)
                or "fail" (Cancel, then return an error naming the dialog).
                Safety: dialogs that look like an upload/remote/username step are
                always cancelled and the post's remote_post() hook is disabled,
                because some posts send G-code to a live machine. Posts whose name
                contains an entry of FREECAD_CAM_POST_DENY (comma-separated, server
                environment) are refused outright.
            timeout_ms: Execution timeout inside FreeCAD.

        Returns:
            job, postprocessor, flow, args, output, files, lines, bytes,
            operations_posted, per_operation [{label, first_line, lines,
            tool_at_start, digest}], digest {tool_changes, spindle, feeds, rotary
            axes/codes (A/B/C, G68.2/G53.1/G43.4...), units, motion counts},
            dialogs_answered [{class, title, labels, checkboxes, buttons, answer,
            forced_cancel}], warnings, and gcode when asked.
        """
        kwargs = {
            "doc_name": doc_name,
            "job": job,
            "operations": list(operations) if operations else None,
            "postprocessor": postprocessor,
            "args": args,
            "output": output,
            "return_gcode": bool(return_gcode),
            "max_lines": int(max_lines),
            "dialogs": dialogs,
            "deny": [
                d.strip()
                for d in os.environ.get("FREECAD_CAM_POST_DENY", "").split(",")
                if d.strip()
            ],
        }
        call = f"_result_ = entry(**{kwargs!r})"
        bridge = await get_bridge()
        result = await bridge.execute_python(_POST_SRC + "\n" + call, timeout_ms)
        if result.success:
            return result.result
        raise ValueError(
            result.error_traceback
            or f"cam_post failed: {result.error_type}: {getattr(result, 'error_message', None) or result.stderr}"
        )

    @mcp.tool()
    async def cam_gcode_diff(
        a: str,
        b: str,
        ignore_comments: bool = True,
        ignore_header: bool = True,
        tolerance: float = 1e-4,
        context: int = 2,
        max_hunks: int = 50,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Diff two posted G-code files, section by section, numbers within tolerance.

        Normalises each line: comments dropped (ignore_comments), FreeCAD header
        lines dropped (Exported by / Post Processor / Output Time / Project File /
        Cam File / Document; ignore_header), whitespace collapsed, N line numbers
        ignored, words parsed so X1.0 == X1.00001 within tolerance and G01 == G1.
        Sections come from the post's "(Begin preamble)", "(Begin operation: X)"
        and "(Begin postamble)" comments.

        Args:
            a: Path of the first G-code file.
            b: Path of the second G-code file.
            ignore_comments: Drop comments before comparing.
            ignore_header: Drop FreeCAD header lines (timestamps, file names).
            tolerance: Absolute tolerance for numeric words.
            context: Context lines per hunk.
            max_hunks: Maximum hunks returned (counts are always complete).
            labels: Op labels to split on when the post writes no "Begin
                operation" markers (machine-based posts write only "(Label)");
                e.g. operations_posted from cam_post.

        Returns:
            verdict (IDENTICAL/DIFFERENT), a_lines, b_lines, section_order_same,
            sections [{name, status identical/different/only_in_a/only_in_b,
            a_lines, b_lines, changed, removed, added, hunks}], hunks [{section,
            header, lines}], hunks_total, hunks_truncated.
        """
        return await asyncio.to_thread(
            lambda: gcode_diff(
                a, b, ignore_comments=ignore_comments, ignore_header=ignore_header,
                tolerance=tolerance, context=context, max_hunks=max_hunks, labels=labels,
            )
        )  # fmt: skip
