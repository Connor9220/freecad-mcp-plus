# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Record a captioned, mouse-driven FreeCAD demo video (server side).

The steps run INSIDE FreeCAD (fc_demo.py, sent through the bridge) on QTimers;
this module checks the display, records it with ffmpeg (x11grab), polls the run
with short bridge calls, stops ffmpeg cleanly, then trims the lead-in and the
idle tail using the run's step log and writes the final H.264 video plus a
chapters JSON next to it.

Only nested test displays (":2" and up) are recorded or driven; the user's real
screen (":0", ":1", unset) is refused. Every subprocess and bridge call has a
bounded timeout.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    # execute(code, timeout_ms) -> the code's _result_ (raises on failure)
    Execute = Callable[[str, int], Awaitable[Any]]

FC_DEMO_SRC = (Path(__file__).parent / "fc_demo.py").read_text()

ACTIONS = (
    "python",
    "command",
    "view",
    "wait",
    "click_tree",
    "property",
    "panel_combo",
    "click_button",
)
VIEWS = ("iso", "isometric", "front", "top", "right", "left", "rear", "bottom", "fit")

STATUS_CODE = (
    "_d = getattr(FreeCADGui, '_mcp_demo', None)\n"
    "_result_ = _d.status() if _d is not None else {'state': 'none'}\n"
)

LEAD_S = 0.25  # kept before the first caption appears
TAIL_S = 0.4  # kept after the last step's hold
MAX_WIDTH = 1600

_DISPLAY_RE = re.compile(r"^(?:localhost|unix)?:(\d+)(?:\.\d+)?$")


class DemoRefusedError(RuntimeError):
    """The demo was refused before anything was recorded or driven."""


def display_ok(display: str | None) -> tuple[bool, str]:
    """(ok, reason): only nested test displays (":2" and up) may be recorded."""
    if not display:
        return False, "FreeCAD's DISPLAY is not set"
    m = _DISPLAY_RE.match(str(display).strip())
    if not m:
        return False, f"DISPLAY {display!r} is not a local X display"
    if int(m.group(1)) < 2:
        return False, (
            f"DISPLAY {display!r} looks like the user's real screen; demos are only "
            "recorded on nested test displays (:2 or higher)"
        )
    return True, ""


def validate_steps(steps: Any) -> list[dict[str, Any]]:
    """Check the step list before anything starts; return a normalized copy."""
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list")
    out = []
    for n, step in enumerate(steps, 1):
        if not isinstance(step, dict) or not str(step.get("caption", "")).strip():
            raise ValueError(f"step {n}: needs a 'caption'")
        actions = step.get("actions") or []
        if not isinstance(actions, list):
            raise ValueError(f"step {n}: 'actions' must be a list")
        for a in actions:
            if not isinstance(a, dict) or len(a) != 1:
                raise ValueError(f"step {n}: each action is a one-key dict, got {a!r}")
            kind, arg = next(iter(a.items()))
            if kind not in ACTIONS:
                raise ValueError(f"step {n}: unknown action {kind!r}; known: {ACTIONS}")
            if kind == "view" and str(arg).lower() not in VIEWS:
                raise ValueError(f"step {n}: view must be one of {VIEWS}")
            if kind == "wait" and not 0 <= float(arg) <= 120:
                raise ValueError(f"step {n}: wait must be 0..120 s")
            if kind == "property" and not (
                isinstance(arg, dict) and {"object", "name", "value"} <= set(arg)
            ):
                raise ValueError(f"step {n}: property needs object, name and value")
            if kind == "panel_combo" and not (
                isinstance(arg, dict) and {"widget", "item"} <= set(arg)
            ):
                raise ValueError(f"step {n}: panel_combo needs widget and item")
        out.append(
            {
                "caption": str(step["caption"]),
                "detail": str(step.get("detail") or ""),
                "actions": [{str(k): v for k, v in a.items()} for a in actions],
            }
        )
    return out


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed tool, no shell
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )


def display_size(display: str) -> tuple[int, int]:
    """Current size of an X display, read with xdpyinfo."""
    r = _run(["xdpyinfo", "-display", display], 10)
    m = re.search(r"dimensions:\s+(\d+)x(\d+) pixels", r.stdout)
    if r.returncode != 0 or not m:
        raise RuntimeError(f"xdpyinfo -display {display} failed: {r.stderr.strip()}")
    return int(m.group(1)), int(m.group(2))


def probe_duration(path: Path) -> float:
    """Container duration in seconds (ffprobe)."""
    r = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        30,
    )
    try:
        return float(r.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"ffprobe {path}: {r.stderr.strip()}") from exc


class Recorder:
    """ffmpeg x11grab of one display into a raw mp4, stopped with 'q' on stdin."""

    def __init__(
        self, display: str, size: tuple[int, int], fps: int, path: Path
    ) -> None:
        self.display, self.size, self.fps, self.path = display, size, fps, path
        self.proc: subprocess.Popen[bytes] | None = None
        fd, name = tempfile.mkstemp(prefix="demo-ffmpeg-", suffix=".log")
        os.close(fd)
        self.log = Path(name)
        self.t_popen = 0.0

    def start(self, ready_timeout: float = 8.0) -> None:
        """Start ffmpeg and wait until it is capturing frames."""
        w, h = self.size
        cmd = [
            "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info", "-y",
            "-f", "x11grab", "-draw_mouse", "1", "-framerate", str(self.fps),
            "-video_size", f"{w}x{h}", "-i", self.display,
            "-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16",
            "-pix_fmt", "yuv420p", str(self.path),
        ]  # fmt: skip
        with self.log.open("wb") as err:
            self.proc = subprocess.Popen(  # noqa: S603 - fixed tool, no shell
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err
            )
        self.t_popen = time.time()
        deadline = time.time() + ready_timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("ffmpeg exited at start:\n" + self.log_tail())
            if self.path.exists() and self.path.stat().st_size > 0:
                time.sleep(0.3)  # a few frames in
                return
            time.sleep(0.1)
        self.kill()
        raise RuntimeError("ffmpeg did not start capturing:\n" + self.log_tail())

    def stop(self, timeout: float = 20.0) -> float:
        """Stop cleanly ('q', then SIGINT, then kill). Returns the stop time."""
        t_stop = time.time()
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return t_stop
        try:
            assert proc.stdin is not None  # noqa: S101 - opened with PIPE
            proc.stdin.write(b"q")
            proc.stdin.flush()
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.send_signal(2)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.kill()
        return t_stop

    def kill(self) -> None:
        """Kill ffmpeg (last resort)."""
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=5)

    def log_tail(self, n: int = 15) -> str:
        """Last lines of the ffmpeg log."""
        try:
            return "\n".join(self.log.read_text(errors="replace").splitlines()[-n:])
        except OSError:
            return ""


def plan_cut(
    t_rec0: float,
    status: dict[str, Any],
    raw_duration: float,
    trim: bool,
) -> tuple[float, float, list[dict[str, Any]]]:
    """Plan the cut: (start, end, chapters) in raw-video seconds.

    Chapter times are relative to the cut's start.
    """
    log = status.get("log") or []
    start, end = 0.0, raw_duration
    if trim:
        t_first = status.get("t_first") or (log[0]["t"] if log else None)
        if t_first:
            start = min(max(0.0, t_first - t_rec0 - LEAD_S), raw_duration)
        t_done = status.get("t_done")
        if t_done:
            end = min(raw_duration, t_done - t_rec0 + TAIL_S)
        if end - start < 0.5:
            start, end = 0.0, raw_duration
    chapters = [
        {
            "step": e["step"],
            "t": round(max(0.0, e["t"] - t_rec0 - start), 2),
            "caption": e.get("caption", ""),
            "detail": e.get("detail", ""),
        }
        for e in log
    ]
    return start, end, chapters


def encode(raw: Path, out: Path, start: float, end: float, timeout: float) -> None:
    """Cut [start, end) and encode H.264 (faststart, at most MAX_WIDTH wide)."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-i", str(raw), "-t", f"{end - start:.3f}",
        "-vf", f"scale='min({MAX_WIDTH},iw)':-2:flags=lanczos",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(out),
    ]  # fmt: skip
    r = _run(cmd, timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg encode failed: {r.stderr.strip()[-800:]}")


def _step_lines(status: dict[str, Any]) -> list[str]:
    lines = []
    for e in status.get("log") or []:
        s = f"step {e['step']} {e.get('caption', '')!r}: actions {e.get('actions_s')} s"
        if e.get("notes"):
            s += " | notes: " + "; ".join(e["notes"])
        if e.get("errors"):
            s += " | ERRORS: " + "; ".join(e["errors"])
        lines.append(s)
    lines.extend(f"run error: {err}" for err in status.get("errors") or [])
    return lines


async def record_demo(  # noqa: PLR0912 - one linear procedure
    execute: Execute,
    steps: list[dict[str, Any]],
    output: str,
    title: str | None = None,
    hold_s: float = 3.5,
    glide_ms: int = 450,
    fps: int = 30,
    trim: bool = True,
    timeout_s: int = 900,
) -> dict[str, Any]:
    """Run the steps in FreeCAD while recording its display. See cam_record_demo."""
    steps = validate_steps(steps)
    if not 0 <= hold_s <= 30:
        raise ValueError("hold_s must be 0..30")
    if not 50 <= glide_ms <= 3000:
        raise ValueError("glide_ms must be 50..3000")
    if not 5 <= fps <= 60:
        raise ValueError("fps must be 5..60")
    timeout_s = max(30, min(int(timeout_s), 3600))
    out = Path(output).expanduser().absolute()
    if out.suffix.lower() != ".mp4":
        raise ValueError("output must be an .mp4 path")
    for tool in ("ffmpeg", "ffprobe", "xdpyinfo"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"{tool} is not installed")

    probe = await execute(FC_DEMO_SRC + "\n_result_ = demo_probe()\n", 20000)
    display = probe.get("display")
    ok, reason = display_ok(display)
    if not ok:
        raise DemoRefusedError("refused: " + reason)
    if probe.get("running"):
        raise RuntimeError("a demo run is already in progress in this FreeCAD")
    if probe.get("modal_dialog"):
        raise RuntimeError("a modal dialog is open in FreeCAD; close it (gui_reset)")

    warnings: list[str] = []
    size = await asyncio.to_thread(display_size, display)
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = out.with_name(out.stem + ".raw.mp4")
    rec = Recorder(display, size, fps, raw)
    await asyncio.to_thread(rec.start)

    status: dict[str, Any] = {}
    call = (
        f"_result_ = demo_start({steps!r}, title={title!r}, "
        f"hold_ms={int(hold_s * 1000)}, glide_ms={int(glide_ms)})\n"
    )
    try:
        await execute(FC_DEMO_SRC + "\n" + call, 20000)
    except BaseException:
        await asyncio.to_thread(rec.stop)
        raw.unlink(missing_ok=True)
        rec.log.unlink(missing_ok=True)
        raise
    try:
        deadline = time.time() + timeout_s
        failures = 0
        while True:
            await asyncio.sleep(0.5)
            try:
                status = await execute(STATUS_CODE, 10000)
                failures = 0
            except Exception as exc:
                failures += 1
                if failures >= 3:
                    warnings.append(f"FreeCAD stopped answering status calls: {exc}")
                    break
                continue
            if status.get("state") in ("done", "aborted", "none"):
                break
            if time.time() > deadline:
                warnings.append(f"timed out after {timeout_s} s; run aborted")
                break
    finally:
        t_stop = await asyncio.to_thread(rec.stop)
        if status.get("state") not in ("done", "aborted"):
            try:
                status = await execute(
                    FC_DEMO_SRC + "\n_result_ = demo_abort('stopped by the server')\n",
                    10000,
                )
            except Exception as exc:
                warnings.append(f"could not abort the run in FreeCAD: {exc}")

    if status.get("state") != "done":
        warnings.append(f"run ended in state {status.get('state')!r}")
    for e in status.get("log") or []:
        warnings.extend(f"step {e['step']}: {err}" for err in e.get("errors") or [])
    warnings.extend(f"run: {err}" for err in status.get("errors") or [])

    raw_duration = await asyncio.to_thread(probe_duration, raw)
    t_rec0 = t_stop - raw_duration
    if t_rec0 < rec.t_popen - 1.0:  # dropped frames: fall back to the start time
        warnings.append("recording shorter than wall time; chapter times approximate")
        t_rec0 = rec.t_popen
    start, end, chapters = plan_cut(t_rec0, status, raw_duration, trim)
    try:
        await asyncio.to_thread(
            encode, raw, out, start, end, max(120.0, 4 * (end - start))
        )
        duration = await asyncio.to_thread(probe_duration, out)
        raw.unlink(missing_ok=True)
        rec.log.unlink(missing_ok=True)
    except Exception as exc:
        warnings.append(f"final encode failed, raw capture kept: {exc}")
        out, duration = raw, raw_duration
        chapters = plan_cut(t_rec0, status, raw_duration, False)[2]

    log = _step_lines(status)
    chapters_path = out.with_name(out.name.removesuffix(".mp4") + ".chapters.json")
    chapters_path.write_text(
        json.dumps(
            {
                "video": out.name,
                "title": title,
                "duration_s": round(duration, 2),
                "chapters": chapters,
                "log": log,
            },
            indent=2,
        )
    )
    return {
        "output": str(out),
        "duration_s": round(duration, 2),
        "chapters": chapters,
        "chapters_json": str(chapters_path),
        "log": log,
        "warnings": warnings,
        "display": display,
        "size": list(size),
        "cut": [round(start, 2), round(end, 2)],
    }
