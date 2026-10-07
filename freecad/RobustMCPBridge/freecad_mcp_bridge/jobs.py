# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Paged output for code runs that outlive a single request.

Every execution gets a Job that collects what the code prints, chunk by chunk. When the code
finishes inside the caller's timeout the Job is dropped and the caller gets the usual result. When
it does not, the Job stays registered under a token, the code keeps running on FreeCAD's main
thread, and the caller reads its output page by page with ``get_output_page`` until the final page
arrives with the result.

The paging follows CREATeNG's freecad-mcp-bridge (MIT): a page closes when it reaches the page size,
when the wait for more output runs out, or when the code finishes; every page with content and the
final page are stored, so a page can be fetched again by number; finished jobs are kept for a while
after their final page and then forgotten.
"""

from __future__ import annotations

import contextlib
import threading
import time
import uuid
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

DEFAULT_PAGE_CHARS = 65536
DEFAULT_PAGE_WAIT_MS = 15000
DEFAULT_RETENTION_S = 300.0

_FINISHED = object()


class JobStream:
    """File-like writer that feeds a job's output, optionally echoing it elsewhere."""

    def __init__(
        self, job: Job, stream: str, echo: Callable[[str], Any] | None = None
    ) -> None:
        """Write into ``job`` as ``stream`` ("stdout" or "stderr"), echoing to ``echo``."""
        self._job = job
        self._stream = stream
        self._echo = echo

    def write(self, text: str) -> int:
        """Record ``text`` as one output chunk."""
        if text:
            self._job.add_output(self._stream, text)
            if self._echo is not None:
                # Echoing (to the Report view) must never break the run
                with contextlib.suppress(Exception):
                    self._echo(text)
        return len(text)

    def flush(self) -> None:
        """Nothing is buffered."""

    def isatty(self) -> bool:
        """Not a terminal."""
        return False


class Job:
    """One code run: its pending output, captured text, stored pages and final result."""

    def __init__(self, code: str) -> None:
        """Create a job for ``code`` with a fresh token."""
        self.token = uuid.uuid4().hex
        self.code = code
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.result: dict[str, Any] | None = None
        self.done = threading.Event()
        self._chunks: deque[Any] = deque()
        self._ready = threading.Condition()
        self._text: dict[str, list[str]] = {"stdout": [], "stderr": []}
        self._pages: list[dict[str, Any]] = []
        self._final_page_at: float | None = None
        self._lock = threading.Lock()

    # --- producer side (FreeCAD's main thread) ---

    def add_output(self, stream: str, text: str) -> None:
        """Append a chunk of output from ``stream``."""
        self._text[stream].append(text)
        with self._ready:
            self._chunks.append((stream, text))
            self._ready.notify_all()

    def text(self, stream: str) -> str:
        """All output written to ``stream`` so far."""
        return "".join(self._text[stream])

    def finish(self, result: dict[str, Any]) -> None:
        """Mark the run finished with its result dictionary."""
        self.result = result
        self.finished_at = time.monotonic()
        with self._ready:
            self._chunks.append(_FINISHED)
            self._ready.notify_all()
        self.done.set()

    # --- consumer side (request threads) ---

    def page(
        self,
        page_no: int,
        wait_ms: int = DEFAULT_PAGE_WAIT_MS,
        page_chars: int = DEFAULT_PAGE_CHARS,
    ) -> dict[str, Any]:
        """Return page ``page_no``, building it from new output when it is the next one."""
        with self._lock:
            if 0 <= page_no < len(self._pages):
                return self._pages[page_no]
            if page_no != len(self._pages):
                return self._error("page_no out of range")
            return self._next_page(wait_ms, page_chars)

    def _next_page(self, wait_ms: int, page_chars: int) -> dict[str, Any]:
        chunks: list[dict[str, str]] = []
        size = 0
        finished = False
        deadline = time.monotonic() + max(wait_ms, 0) / 1000
        while size < page_chars:
            with self._ready:
                self._ready.wait_for(
                    lambda: self._chunks, timeout=max(deadline - time.monotonic(), 0)
                )
                if not self._chunks:
                    break
                item = self._chunks.popleft()
                if item is _FINISHED:
                    finished = True
                    break
                stream, text = item
                room = page_chars - size
                if len(text) > room:
                    # Split an oversized chunk across pages, keeping the rest at the head
                    self._chunks.appendleft((stream, text[room:]))
                    text = text[:room]
            if chunks and chunks[-1]["stream"] == stream:
                chunks[-1]["text"] += text
            else:
                chunks.append({"stream": stream, "text": text})
            size += len(text)
            # Once something has arrived, only take what is already waiting
            deadline = min(deadline, time.monotonic())

        response: dict[str, Any] = {
            "job_token": self.token,
            "page": chunks,
            "has_more": not finished,
        }
        if chunks or finished:
            response["page_no"] = len(self._pages)
            if finished:
                response.update(self._result_fields())
                self._final_page_at = time.monotonic()
            self._pages.append(response)
        return response

    def _result_fields(self) -> dict[str, Any]:
        result = dict(self.result or {})
        # The pages already carry the output
        result.pop("stdout", None)
        result.pop("stderr", None)
        return result

    def _error(self, message: str) -> dict[str, Any]:
        return {
            "job_token": self.token,
            "page": [],
            "has_more": False,
            "error": message,
        }

    def expired(self, now: float, retention_s: float) -> bool:
        """Whether the final page was handed out more than ``retention_s`` ago."""
        return (
            self._final_page_at is not None and now - self._final_page_at > retention_s
        )


class JobRegistry:
    """Jobs whose output is still being read, by token."""

    def __init__(self, retention_s: float = DEFAULT_RETENTION_S) -> None:
        """Keep finished jobs for ``retention_s`` seconds after their final page."""
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self.retention_s = retention_s

    def keep(self, job: Job) -> None:
        """Register ``job`` so its output can be paged."""
        with self._lock:
            self._jobs[job.token] = job

    def get(self, token: str) -> Job | None:
        """The live job for ``token``, if any."""
        self.purge()
        with self._lock:
            return self._jobs.get(token)

    def running(self) -> list[Job]:
        """Registered jobs that have not finished."""
        with self._lock:
            return [job for job in self._jobs.values() if not job.done.is_set()]

    def purge(self) -> None:
        """Forget jobs whose retention has run out."""
        now = time.monotonic()
        with self._lock:
            for token in [
                t for t, j in self._jobs.items() if j.expired(now, self.retention_s)
            ]:
                del self._jobs[token]

    def clear(self) -> None:
        """Forget every job."""
        with self._lock:
            self._jobs.clear()

    def page(
        self,
        token: str,
        page_no: int,
        wait_ms: int = DEFAULT_PAGE_WAIT_MS,
        page_chars: int = DEFAULT_PAGE_CHARS,
    ) -> dict[str, Any]:
        """Page ``page_no`` of the job named by ``token``."""
        job = self.get(token)
        if job is None:
            return {
                "page": [],
                "has_more": False,
                "error": "unknown or expired job_token",
            }
        return job.page(page_no, wait_ms, page_chars)
