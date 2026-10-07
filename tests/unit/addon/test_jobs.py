"""Tests for the bridge's paged job output (freecad_mcp_bridge/jobs.py)."""

import importlib.util
import threading
import time
from pathlib import Path

# Loaded by path: importing the add-on package itself needs FreeCAD
_JOBS_PATH = (
    Path(__file__).parents[3]
    / "freecad"
    / "RobustMCPBridge"
    / "freecad_mcp_bridge"
    / "jobs.py"
)
_spec = importlib.util.spec_from_file_location("mcp_bridge_jobs", _JOBS_PATH)
jobs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jobs)
Job, JobRegistry, JobStream = jobs.Job, jobs.JobRegistry, jobs.JobStream


def _run(
    job: Job, outputs: list[tuple[str, str]], delay: float = 0.0
) -> threading.Thread:
    """Write ``outputs`` into ``job`` from another thread, then finish it."""

    def produce() -> None:
        for stream, text in outputs:
            job.add_output(stream, text)
            time.sleep(delay)
        job.finish({"success": True, "result": 42, "stdout": "x", "stderr": ""})

    thread = threading.Thread(target=produce)
    thread.start()
    return thread


def _read_all(job: Job, page_chars: int = 65536) -> list[dict]:
    pages, page_no = [], 0
    while True:
        page = job.page(page_no, wait_ms=2000, page_chars=page_chars)
        if "page_no" in page:
            pages.append(page)
            page_no += 1
        if not page["has_more"]:
            return pages


class TestJob:
    """Paging of one job's output."""

    def test_pages_carry_output_in_order_and_result_last(self):
        """All output arrives in order, and only the final page has the result."""
        job = Job("code")
        thread = _run(
            job, [("stdout", "a"), ("stderr", "b"), ("stdout", "c")], delay=0.05
        )
        pages = _read_all(job)
        thread.join()
        text = "".join(chunk["text"] for page in pages for chunk in page["page"])
        assert text == "abc"
        assert pages[-1]["result"] == 42
        assert pages[-1]["has_more"] is False
        assert "stdout" not in pages[-1]
        assert all("result" not in page for page in pages[:-1])

    def test_adjacent_chunks_of_one_stream_are_merged(self):
        """Consecutive writes to the same stream come back as one chunk."""
        job = Job("code")
        job.add_output("stdout", "a")
        job.add_output("stdout", "b")
        job.add_output("stderr", "c")
        job.finish({"success": True})
        page = job.page(0, wait_ms=0)
        assert page["page"] == [
            {"stream": "stdout", "text": "ab"},
            {"stream": "stderr", "text": "c"},
        ]

    def test_oversized_output_is_split_across_pages(self):
        """A chunk longer than the page size continues on the next page."""
        job = Job("code")
        job.add_output("stdout", "x" * 2500)
        job.finish({"success": True})
        pages = _read_all(job, page_chars=1000)
        assert [len(p["page"][0]["text"]) for p in pages if p["page"]] == [
            1000,
            1000,
            500,
        ]

    def test_quiet_wait_returns_an_unnumbered_empty_page(self):
        """With no new output, the wait ends with an empty page that is not stored."""
        job = Job("code")
        page = job.page(0, wait_ms=50)
        assert page["page"] == []
        assert page["has_more"] is True
        assert "page_no" not in page
        job.finish({"success": True})
        assert job.page(0, wait_ms=0)["page_no"] == 0

    def test_pages_can_be_fetched_again(self):
        """A stored page comes back unchanged."""
        job = Job("code")
        job.add_output("stdout", "hello")
        first = job.page(0, wait_ms=0)
        assert job.page(0, wait_ms=0) is first

    def test_page_out_of_range(self):
        """Skipping ahead is an error."""
        job = Job("code")
        assert job.page(3, wait_ms=0)["error"] == "page_no out of range"

    def test_text_keeps_everything_written(self):
        """The whole output stays available for the classic result."""
        job = Job("code")
        stream = JobStream(job, "stdout")
        stream.write("a")
        stream.write("b")
        assert job.text("stdout") == "ab"

    def test_echo_failure_does_not_break_the_run(self):
        """An echo target that raises is ignored."""
        job = Job("code")

        def broken(_text: str) -> None:
            raise RuntimeError

        JobStream(job, "stdout", broken).write("still recorded")
        assert job.text("stdout") == "still recorded"


class TestJobRegistry:
    """Keeping and forgetting jobs."""

    def test_unknown_token(self):
        """An unknown token is reported, not raised."""
        assert JobRegistry().page("nope", 0)["error"] == "unknown or expired job_token"

    def test_finished_job_expires_after_its_final_page(self):
        """Retention starts when the final page is handed out."""
        registry = JobRegistry(retention_s=0.05)
        job = Job("code")
        registry.keep(job)
        job.finish({"success": True})
        assert registry.page(job.token, 0, wait_ms=0)["has_more"] is False
        time.sleep(0.1)
        assert registry.get(job.token) is None

    def test_running_lists_unfinished_jobs(self):
        """Only jobs still going count as running."""
        registry = JobRegistry()
        busy, done = Job("a"), Job("b")
        done.finish({"success": True})
        registry.keep(busy)
        registry.keep(done)
        assert registry.running() == [busy]
