# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

# guidriver.cam.mcp -- JSON-safe entry points for the MCP+ cam_gui_* tools.

from .. import mcp as G
from . import inspect, taskpanel

# Dialogs whose title looks like an upload / remote / username step are always
# cancelled by cam_gui_post: some posts send G-code to a live machine.
UPLOAD_WORDS = ("user", "upload", "remote", "server", "file manager")


def job(doc, name=None):
    """Job by Name/Label, or the document's only job."""
    if name:
        return G.obj(doc, name)
    return inspect._job(None)


def check(job_name=None, tabs=False, summary=False, doc_name=None):
    """inspect.check (+ inspect.summary) for a job."""
    doc = G.activate(doc_name)
    j = job(doc, job_name)
    out = {"job": j.Name, "warnings": inspect.check(j, tabs=tabs)}
    if summary:
        out["summary"] = inspect.summary(j)
    return G.jsonable(out)


def add_base(selections, clear=False, modal=None, doc_name=None):
    """Select sub-elements and press the open op panel's Base Geometry Add."""
    doc = G.activate(doc_name)
    p = taskpanel.current()
    if p is None:
        raise RuntimeError("no task panel is open")
    return G.jsonable(
        p.add_base(G.selections(doc, selections), clear=clear, modal=G.rules(modal))
    )


def no_upload_rules():
    return [
        {"name": f"no_upload({w})", "match": {"title": w}, "do": "reject"}
        for w in UPLOAD_WORDS
    ]


def post(
    job_name=None,
    outdir=None,
    postname=None,
    args=None,
    modal=None,
    dust=None,
    show_editor=False,
    deny=(),
    doc_name=None,
):
    """inspect.post with upload dialogs always cancelled and remote_post() off.

    deny: post names (case-insensitive substrings) refused outright."""
    from . import modal as CM

    doc = G.activate(doc_name)
    j = job(doc, job_name)
    names = [postname or "", j.PostProcessor or "", getattr(j, "Machine", "") or ""]
    for d in deny or ():
        d = d.strip().lower()
        hit = d and next((n for n in names if d in str(n).lower()), None)
        if hit:
            raise PermissionError(
                f"post {hit!r} is on the deny list ({d!r}); it is never run from here"
            )
    rules = (
        no_upload_rules()
        + CM.nibblerbot_post(dust=dust, show_editor=show_editor, upload=False)
        + G.rules(modal)
    )
    r = inspect.post(
        j, outdir=outdir, postname=postname, args=args, modal=rules, no_remote=True
    )
    r["job"] = j.Name
    return G.jsonable(r)
