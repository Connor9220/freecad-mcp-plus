# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Billy Huddleston <billy@ivdc.com>
# SPDX-FileNotice: Part of MCP+.

"""Post-process a CAM job without any GUI dialog.

This module runs INSIDE FreeCAD (GUI through the bridge, or headless FreeCADCmd).
It must not import anything from freecad_mcp. The MCP tool sends its source and
calls :func:`post_job` with plain arguments; the result is XML-RPC safe (string
dict keys only).

How posting works in this FreeCAD version (mirrors Path/Post/Command.py):

* ``PostProcessorFactory.get_post_processor(job, name)`` loads ``<name>_post.py``
  from the post search paths. A module with a class ``Name.title()`` gives a
  class-based (refactored / machine) post; a module with only ``export()`` is a
  legacy script wrapped in ``WrapperPost``.
* Job with a ``Machine`` (new flow): the post comes from the machine config and
  ``export2()`` is called (configuration bundle from the machine plus
  ``Job.PostProcessorPropertyOverrides``).
* Job without a Machine (legacy flow): the post is ``Job.PostProcessor`` (else the
  preference default) and ``export()`` is called; its argument string is read
  from ``Job.PostProcessorArgs``.

Dialogs avoided: the post/job chooser (callers name the post and the job), the
unified PostProcessDialog and file-name dialog (not used; the file is written
here), the G-code editor (``Path.Post.Utils.GCodeEditorDialog`` is swapped for a
no-op while posting) and ``pre_processing_dialog`` (marked as handled).
"""

import contextlib
import os
import re
import tempfile

import FreeCAD

_COMMENT_RE = re.compile(r"\(([^)]*)\)|;(.*)$")
_WORD_RE = re.compile(r"([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
_ROTARY_CODES = ("G68.2", "G53.1", "G43.4", "G68", "G69", "G93", "G7", "G8")
_BEGIN_RE = re.compile(
    r"^\s*(?:begin operation|start operation|operation start|operation initiali[sz]e)"
    r"\s*:\s*(.+?)\s*$",
    re.IGNORECASE,
)
_POSTAMBLE_RE = re.compile(r"^\s*begin postamble\b", re.IGNORECASE)
_FINISH_RE = re.compile(
    r"^\s*(?:finish operation|operation done|operation finali[sz]ed)\s*:\s*(.+?)\s*$",
    re.IGNORECASE,
)


def _num(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in {"", "-0"} else "0"


def _comments(line):
    return [
        (m.group(1) if m.group(1) is not None else m.group(2))
        for m in _COMMENT_RE.finditer(line)
    ]


def _words(line):
    code = _COMMENT_RE.sub(" ", line).upper()
    return [(m.group(1), float(m.group(2))) for m in _WORD_RE.finditer(code)]


def digest(lines, first_line=1):
    """Tool changes, spindle, feeds and rotary words of a block of G-code lines."""
    tool_changes = []
    speeds, feeds = set(), set()
    m_counts = {"M3": 0, "M4": 0, "M5": 0}
    motion = {"G0": 0, "G1": 0, "G2": 0, "G3": 0}
    axes, codes, units = set(), set(), set()
    for i, line in enumerate(lines):
        words = _words(line)
        if not words:
            continue
        tool = None
        m6 = False
        for letter, value in words:
            if letter == "T":
                tool = int(value)
            elif letter == "S":
                speeds.add(value)
            elif letter == "F":
                feeds.add(value)
            elif letter in "ABC":
                axes.add(letter)
            elif letter == "M":
                key = "M" + _num(value)
                if key in m_counts:
                    m_counts[key] += 1
                m6 = m6 or value == 6
            elif letter == "G":
                key = "G" + _num(value)
                if key in motion:
                    motion[key] += 1
                if key in _ROTARY_CODES:
                    codes.add(key)
                if key in {"G20", "G21"}:
                    units.add(key)
        if m6 and len(tool_changes) < 200:
            tool_changes.append(
                {"line": first_line + i, "tool": tool, "text": line.strip()[:80]}
            )
    fsorted = sorted(feeds)
    return {
        "tool_changes": tool_changes,
        "spindle": {
            "speeds": [_num(s) for s in sorted(speeds)[:20]],
            "m3": m_counts["M3"],
            "m4": m_counts["M4"],
            "m5": m_counts["M5"],
        },
        "feeds": {
            "distinct": len(fsorted),
            "min": _num(fsorted[0]) if fsorted else None,
            "max": _num(fsorted[-1]) if fsorted else None,
            "values": [_num(f) for f in fsorted[:20]],
        },
        "rotary": {"axes": sorted(axes), "codes": sorted(codes)},
        "units": sorted(units),
        "explicit_motion_words": motion,
    }


def _find_job(doc, job):
    jobs = [o for o in doc.Objects if hasattr(o, "Operations") and hasattr(o, "Tools")]
    if job:
        for o in jobs:
            if job in (o.Name, o.Label):
                return o
        raise ValueError(f"Job {job!r} not found; jobs: {[o.Label for o in jobs]}")
    if len(jobs) == 1:
        return jobs[0]
    if not jobs:
        raise ValueError(f"Document {doc.Name!r} has no CAM Job")
    raise ValueError(f"Several jobs, pass job=: {[o.Label for o in jobs]}")


def _find_ops(doc, job, names):
    group = list(job.Operations.Group)
    out = []
    for n in names:
        hit = next((o for o in group if n in (o.Name, o.Label)), None)
        if hit is None:
            hit = doc.getObject(n) or next(iter(doc.getObjectsByLabel(n)), None)
        if hit is None or not hasattr(hit, "Path"):
            raise ValueError(
                f"Operation {n!r} not found in job; ops: {[o.Label for o in group]}"
            )
        out.append(hit)
    return out


class _NoDialogEditor:
    """Stand-in for GCodeEditorDialog: never shows, keeps the text unchanged."""

    class _Text:
        def __init__(self, text):
            self._text = text

        def setPlainText(self, text):
            self._text = text

        def toPlainText(self):
            return self._text

    class _Buttons:
        def button(self, *_a):
            return self

        def setDisabled(self, *_a):
            pass

    def __init__(self, text="", parent=None, refactored=False):
        self.editor = self._Text(text)
        self.buttons = self._Buttons()
        self._refactored = refactored

    def exec_(self):
        # refactored: 2 = "Save Without Changes"; legacy: falsy = keep original
        return 2 if self._refactored else 0

    exec = exec_


DEFAULT_DENY = ()
# Dialogs that look like a remote-post / upload step are ALWAYS cancelled,
# whatever `dialogs` says: accepting them can send G-code to a live machine.
_UPLOAD_RE = re.compile(
    r"user|upload|remote|server|file ?manager|filemanagerdialog", re.IGNORECASE
)


class _DialogWatcher:
    """Answers modal dialogs that a post opens while it runs (GUI only).

    A repeating QTimer fires inside the dialog's nested event loop. Each new
    modal widget is recorded and answered once ("accept", "reject" or "fail" =
    reject and report); one that is still open on later ticks is closed, then
    hidden, so the post can never hang the GUI thread.
    """

    def __init__(self, mode):
        from PySide import QtCore, QtWidgets

        self.mode = mode
        self.answered = []
        self.failed = None
        self._qw = QtWidgets
        self._app = QtWidgets.QApplication.instance()
        self._baseline = self._app.activeModalWidget()  # already open: not ours
        self._timer = QtCore.QTimer()
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def stop(self):
        with contextlib.suppress(Exception):
            self._timer.stop()

    @staticmethod
    def _valid(w):
        try:
            import shiboken6

            return shiboken6.isValid(w)
        except ImportError:
            try:
                import shiboken2

                return shiboken2.isValid(w)
            except ImportError:
                return True

    def _describe(self, w):
        qw = self._qw
        info = {
            "class": type(w).__name__,
            "qt_class": w.metaObject().className(),
            "title": w.windowTitle(),
        }
        if isinstance(w, qw.QMessageBox):
            info["text"] = w.text()
            if w.informativeText():
                info["informative_text"] = w.informativeText()
        labels = [lb.text()[:200] for lb in w.findChildren(qw.QLabel) if lb.text()]
        if labels:
            info["labels"] = labels[:10]
        boxes = {}
        for i, cb in enumerate(w.findChildren(qw.QCheckBox)):
            boxes[cb.text() or cb.objectName() or f"checkbox{i}"] = cb.isChecked()
        if boxes:
            info["checkboxes"] = boxes
        info["buttons"] = [
            b.text() for b in w.findChildren(qw.QPushButton) if b.isVisible()
        ]
        return info

    def _button(self, w, accept):
        qw = self._qw
        box_cls = (
            qw.QMessageBox if isinstance(w, qw.QMessageBox) else qw.QDialogButtonBox
        )
        roles = (
            (box_cls.AcceptRole, box_cls.YesRole, box_cls.ApplyRole)
            if accept
            else (box_cls.RejectRole, box_cls.NoRole)
        )
        if isinstance(w, qw.QMessageBox):
            pref = w.defaultButton() if accept else w.escapeButton()
            if pref is not None:
                return pref
            buttons = [(b, w.buttonRole(b)) for b in w.buttons()]
        else:
            buttons = [
                (b, bb.buttonRole(b))
                for bb in w.findChildren(qw.QDialogButtonBox)
                for b in bb.buttons()
            ]
        for role in roles:
            for b, r in buttons:
                if r == role and b.isEnabled():
                    return b
        if accept:
            for b in w.findChildren(qw.QPushButton):
                if b.isDefault() and b.isEnabled():
                    return b
        return None

    def _haystack(self, w, info):
        qw = self._qw
        parts = [info.get("class", ""), info.get("qt_class", ""), info.get("title", "")]
        parts += [info.get("text", ""), info.get("informative_text", "")]
        parts += info.get("labels", []) + info.get("buttons", [])
        parts += list(info.get("checkboxes", {}))
        for cb in w.findChildren(qw.QComboBox):
            parts += [cb.itemText(i) for i in range(min(cb.count(), 200))]
        for lw in w.findChildren(qw.QListWidget):
            parts += [lw.item(i).text() for i in range(min(lw.count(), 200))]
        return "\n".join(str(p) for p in parts if p)

    def _tick(self):
        try:
            w = self._app.activeModalWidget()
            if w is None or not self._valid(w) or w is self._baseline:
                return
            tries = w.property("_mcp_post_answered") or 0
            w.setProperty("_mcp_post_answered", tries + 1)
            if tries == 0:
                info = self._describe(w)
                m = _UPLOAD_RE.search(self._haystack(w, info))
                if m:
                    info["forced_cancel"] = True
                    info["note"] = f"upload prompt cancelled (matched {m.group(0)!r})"
                accept = self.mode == "accept" and not m
                btn = self._button(w, accept)
                if btn is not None:
                    info["answer"] = f"clicked {btn.text()!r}"
                    btn.click()
                elif hasattr(w, "accept") and accept:
                    info["answer"] = "accept()"
                    w.accept()
                elif hasattr(w, "reject") and not accept:
                    info["answer"] = "reject()"
                    w.reject()
                else:
                    info["answer"] = "close()"
                    w.close()
                self.answered.append(info)
                if self.mode == "fail" and self.failed is None and not m:
                    self.failed = info
            elif tries == 1:  # still open after being answered
                self.answered[-1]["forced"] = "close()"
                w.close()
            else:
                if self.answered:
                    self.answered[-1]["forced"] = "done(0)/hide()"
                if hasattr(w, "done"):
                    w.done(0)
                w.hide()
        except Exception as e:  # never let the watcher raise in the event loop
            self.answered.append({"watcher_error": str(e)})


def _gui_watcher(mode):
    if not FreeCAD.GuiUp:
        return None
    try:
        from PySide import QtWidgets

        if QtWidgets.QApplication.instance() is None:
            return None
    except ImportError:
        return None
    return _DialogWatcher(mode)


def _write(path, gcode):
    # same end-of-line handling as Path/Post/Command.py _write_file
    if len(gcode) > 1 and gcode[0:2] == "\n\n":
        newline, gcode = "", gcode[2:]
    elif "\r" in gcode:
        newline = ""
    else:
        newline = None
    with open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(gcode)
    return gcode


def _locate_ops(lines, labels):
    """Start/end line indices of each posted operation, from the post's comments."""
    comments = [[c.strip() for c in _comments(line)] for line in lines]
    begins = {}  # label -> [idx] via explicit begin markers
    any_begin = []
    ends = {}
    postamble = None
    for i, cs in enumerate(comments):
        for c in cs:
            m = _BEGIN_RE.match(c)
            if m:
                any_begin.append(i)
                begins.setdefault(m.group(1), []).append(i)
            m = _FINISH_RE.match(c)
            if m:
                ends.setdefault(m.group(1), []).append(i)
            if postamble is None and _POSTAMBLE_RE.match(c):
                postamble = i
    starts = {}
    for lab in labels:
        if begins.get(lab):
            starts[lab] = (begins[lab], "begin")
        else:  # machine-flow posts: the op's own "(Label)" comment
            idx = [i for i, cs in enumerate(comments) if lab in cs]
            if idx:
                starts[lab] = (idx, "label")
    boundaries = sorted(
        set(any_begin)
        | {i for v, _k in starts.values() for i in v}
        | ({postamble} if postamble is not None else set())
    )
    out = {}
    for lab, (idxs, kind) in starts.items():
        ranges = []
        for s in idxs:
            e = next((b for b in boundaries if b > s), len(lines))
            fin = [f for f in ends.get(lab, []) if s < f < e]
            if fin:
                e = fin[0] + 1
            ranges.append((s, e))
        out[lab] = (ranges, kind)
    return out


def post_job(
    doc_name=None,
    *,
    job=None,
    operations=None,
    postprocessor=None,
    args="",
    output=None,
    return_gcode=False,
    max_lines=400,
    dialogs="accept",
    deny=None,
):
    """Post-process a job (or some of its operations) and write the G-code file.

    ``deny``: post names refused outright (case-insensitive substrings; the tool
    passes FREECAD_CAM_POST_DENY, default none) for posts that must never run.
    ``dialogs``: how modal dialogs opened by the post are answered in the GUI:
    "accept", "reject" or "fail" (reject, then raise naming the dialog). Dialogs
    that look like an upload / remote / username step are always cancelled, and
    the post's remote_post() hook is disabled, so nothing is sent anywhere.
    """
    if dialogs not in {"accept", "reject", "fail"}:
        raise ValueError(f"dialogs must be accept, reject or fail, not {dialogs!r}")
    import Path
    import Path.Post.Utils as PostUtils
    from Path.Post.Processor import PostProcessor, PostProcessorFactory, WrapperPost

    doc = FreeCAD.getDocument(doc_name) if doc_name else FreeCAD.ActiveDocument
    if doc is None:
        raise ValueError("No document (pass doc_name)")
    jobobj = _find_job(doc, job)
    warnings = []

    machine_name = getattr(jobobj, "Machine", "") or ""
    source = "argument"
    if not postprocessor:
        if machine_name:
            from Machine.models.machine import MachineFactory

            machine = MachineFactory.get_machine(machine_name)
            postprocessor = getattr(machine, "postprocessor_file_name", None)
            source = f"machine {machine_name!r}"
            if not postprocessor:
                raise ValueError(
                    f"Machine {machine_name!r} does not specify a postprocessor"
                )
        elif jobobj.PostProcessor:
            postprocessor, source = jobobj.PostProcessor, "Job.PostProcessor"
        elif Path.Preferences.defaultPostProcessor():
            postprocessor = Path.Preferences.defaultPostProcessor()
            source = "preference default"
        else:
            raise ValueError(
                "Job has no Machine, no PostProcessor and there is no default post "
                "(the GUI would open a chooser dialog). Pass postprocessor=, one of: "
                f"{Path.Preferences.allAvailablePostProcessors()}"
            )
        if not args:
            args = None  # use the job's own PostProcessorArgs
    postprocessor = postprocessor.removesuffix("_post").removesuffix(".py")
    deny_list = [
        d.strip().lower() for d in (DEFAULT_DENY if deny is None else deny) if d.strip()
    ]
    hit = next((d for d in deny_list if d in postprocessor.lower()), None)
    if hit:
        raise PermissionError(
            f"Post processor {postprocessor!r} (from {source}) is on the deny list "
            f"({hit!r}): some posts upload G-code to a live machine, so it is never "
            "run from here. Pass postprocessor= with a plain file-writing post."
        )
    if not PostProcessor.exists(postprocessor):
        raise ValueError(
            f"Post processor {postprocessor!r} not found; available: "
            f"{Path.Preferences.allAvailablePostProcessors()}"
        )

    ops = _find_ops(doc, jobobj, operations) if operations else None

    real_editor = PostUtils.GCodeEditorDialog
    old_args = jobobj.PostProcessorArgs
    args_used = old_args if args is None else args
    changed_args = False
    PostUtils.GCodeEditorDialog = _NoDialogEditor
    watcher = _gui_watcher(dialogs)
    try:
        pp = PostProcessorFactory.get_post_processor(jobobj, postprocessor)
        if pp is None or isinstance(pp, Exception):
            raise ValueError(f"Could not load post processor {postprocessor!r}: {pp}")
        # review tool: never upload / send anything (export2 stage 6 hook)
        pp.remote_post = lambda *_a, **_k: None
        if ops is not None:
            # the factory's {"job", "operations"} form drops the Machine, so
            # construct with the job and narrow the operation list afterwards
            pp._operations = ops
        use_new_flow = (
            bool(machine_name)
            and not isinstance(pp, WrapperPost)
            and getattr(pp, "_machine", None) is not None
        )
        pp._dialog_handled = True
        if use_new_flow:
            flow = "export2 (machine)"
            if args:
                import json

                try:
                    overrides = json.loads(args)
                except ValueError:
                    overrides = None
                if isinstance(overrides, dict):
                    pp.apply_configuration_bundle(overrides=overrides)
                    pp._bundle_applied = True
                    args_used = args
                else:
                    warnings.append(
                        "args ignored: machine-flow posts take a JSON dict of property overrides"
                    )
                    args_used = ""
            else:
                args_used = ""
            sections = pp.export2()
        else:
            flow = (
                "export (legacy script)"
                if isinstance(pp, WrapperPost)
                else "export (class, no machine)"
            )
            if args_used != old_args:
                jobobj.PostProcessorArgs = args_used
                changed_args = True
            sections = pp.export()
    finally:
        if watcher is not None:
            watcher.stop()
        PostUtils.GCodeEditorDialog = real_editor
        if changed_args:
            jobobj.PostProcessorArgs = old_args

    answered = watcher.answered if watcher is not None else []
    if watcher is not None and watcher.failed is not None:
        f = watcher.failed
        raise RuntimeError(
            f"Post {postprocessor!r} opened a dialog ({f.get('class')} {f.get('title')!r}, "
            f"answered: {f.get('answer')}) and dialogs='fail'. Dialogs: {answered}"
        )

    if not sections:
        raise ValueError(
            f"Post processor {postprocessor!r} returned no output (argument error, "
            f"or a dialog was rejected). Dialogs answered: {answered}"
        )

    ext = None
    try:
        ext = (
            pp.get_file_extension() if use_new_flow else pp.values.get("FILE_EXTENSION")
        )
    except Exception:
        ext = None
    ext = (ext or "nc").lstrip(".")
    if not output:
        fd, output = tempfile.mkstemp(
            prefix=f"{doc.Name}_{jobobj.Name}_{postprocessor}_", suffix=f".{ext}"
        )
        os.close(fd)
    output = os.path.abspath(os.path.expanduser(output))
    os.makedirs(os.path.dirname(output), exist_ok=True)

    files = []
    all_lines = []
    real_sections = [(n, g) for n, g in sections if g is not None]
    for k, (name, gcode) in enumerate(real_sections):
        if len(real_sections) == 1 or k == 0:
            path = output
        else:
            base, e = os.path.splitext(output)
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name)) or str(k)
            path = f"{base}-{k}-{safe}{e}"
        text = _write(path, gcode if isinstance(gcode, str) else str(gcode))
        lines = text.splitlines()
        files.append(
            {
                "section": str(name),
                "path": path,
                "lines": len(lines),
                "bytes": os.path.getsize(path),
            }
        )
        all_lines.extend(lines)
    if len(files) > 1:
        warnings.append(
            f"{len(files)} output sections (SplitOutput/ordering); written as separate files"
        )

    posted = [o for o in (pp._operations or []) if getattr(o, "Active", True)]
    skipped = [
        o.Label for o in (pp._operations or []) if not getattr(o, "Active", True)
    ]
    for o in posted:
        state = set(getattr(o, "State", []))
        if state & {"Invalid", "Error", "Touched"}:
            warnings.append(f"{o.Label} is {sorted(state)}: its stored path was posted")
    labels = [o.Label for o in posted]
    located = _locate_ops(all_lines, labels)
    whole = digest(all_lines)
    per_op = []
    for lab in labels:
        if lab not in located:
            per_op.append(
                {"label": lab, "first_line": None, "lines": None, "marker": None}
            )
            continue
        ranges, kind = located[lab]
        block = [ln for s, e in ranges for ln in all_lines[s:e]]
        per_op.append(
            {
                "label": lab,
                "first_line": ranges[0][0] + 1,
                "lines": len(block),
                "occurrences": len(ranges),
                "marker": kind,
                # last M6 before the op starts (tool changes are posted in the
                # tool controller's own section, not inside the op)
                "tool_at_start": next(
                    (
                        t["tool"]
                        for t in reversed(whole["tool_changes"])
                        if t["line"] <= ranges[0][0] + 1
                    ),
                    None,
                ),
                "digest": digest(block, ranges[0][0] + 1),
            }
        )

    result = {
        "document": doc.Name,
        "job": jobobj.Label,
        "postprocessor": postprocessor,
        "postprocessor_source": source,
        "flow": flow,
        "args": args_used,
        "output": files[0]["path"] if files else output,
        "files": files,
        "lines": len(all_lines),
        "bytes": sum(f["bytes"] for f in files),
        "operations_posted": labels,
        "operations_skipped_inactive": skipped,
        "per_operation": per_op,
        "digest": whole,
        "dialogs_answered": answered,
        "warnings": warnings,
    }
    if return_gcode:
        n = max(0, int(max_lines))
        result["gcode"] = "\n".join(all_lines[:n])
        result["gcode_truncated"] = len(all_lines) > n
    return result


def entry(**kwargs):
    """Tool entry point (the bridge returns any exception's traceback)."""
    return post_job(**kwargs)
