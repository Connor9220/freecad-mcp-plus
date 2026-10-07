# GuiDriver

Drive the FreeCAD GUI from Python (via the MCP+ bridge's `execute_python`, or the
`gui_*` / `cam_gui_*` MCP tools), so objects are built through the real commands,
task panels and dialogs -- GUI gating included. Ships inside the MCP+ add-on; FreeCAD
puts `freecad/RobustMCPBridge/` on `sys.path`, so it imports as `guidriver`.

```python
import guidriver as gd; gd = gd.reload()           # reload after editing
r = gd.run("PartDesign_Pad", modal=[gd.modal.message_box("OK")])   # toolbar command
p = gd.panel()                                     # the open task panel
p.dump(hidden=False)                               # pages -> widgets: value, tab, hidden, disabled
p.set("Length", "10 mm")
p.ok()                                             # task view's real OK button
gd.edit(obj)                                       # double-click equivalent

import guidriver.cam as cd                         # CAM side (drop-in for the old camdriver)
r = cd.run("CAM_Adaptive", modal=[cd.modal.tc_chooser(70)])
p = cd.panel()                                     # CamPanel = Panel + add_base()
p.add_base([(clone, "Face19")])                    # select + Base Geometry "Add"
p.set("stepDown", "0.25 in", page="Heights", clear_expression=True)
p.set("toolController", "Down Cut", page="Tool Controller")
p.ok()
cd.check(); cd.summary(); cd.post(modal=cd.modal.nibblerbot_post()); cd.snapshot(path)
```

Generic modules: `widgets` (read/set Qt like a user; reveals tabs; refuses
hidden/disabled/expression-bound), `modal` (watcher answering modal dialogs by rule;
unmatched ones rejected after 3 s and logged; rules `message_box`, `input_int`),
`taskpanel` (panel lookup, pages, OK/Cancel), `xmouse` (real XTest pointer/keys, X11
only, imported on demand), `xkeys_external.py` (XTest typing from outside FreeCAD),
`mcp` (JSON-safe entry points the MCP tools call).

CAM modules (`guidriver.cam`): `modal` (CAM dialog rules, plus the generic ones
re-exported), `taskpanel` (`CamPanel.add_base`), `inspect` (summary/check/post/snapshot),
`mcp`, `recipes/class_tray.py` (full GUI rebuild of the CAM 101 Lesson 3 tray; regression
replay), `recipes/twp_block.py`, `recipes/twp_block2.py`, `recipes/flat_job.py`.

Recipes:
- `class_tray.run(doc, copy_dressups=True, machine=None, stop_before_post=False)` on a fresh copy of
  `class-tray.FCStd`. `copy_dressups=False`: the finish pass copies only the Profile op and gets its own
  Lead In/Out. `machine="NibblerBOT"`: picks the Job machine, so posting goes through the machine post.
- `twp_block.run(doc, upto=N)` on a fresh copy of `twp-test-block.FCStd`: Job on "NibblerBOT AC Trunnion",
  stock, tools with presets, four work planes, then the first N operations. Saves after every step.
Both need the NibblerBOT job template, tool library and machine files (see the kit's `assets/`).

Ready-made CAM dialog rules: `job_create`, `tc_chooser`, `toolbit_selector`, `input_int`,
`stock_material`, `tc_editor` (+ F&S wizard), `feeds_speeds`, `message_box`, `nibblerbot_post`.

Gotchas learned:
- Never `FreeCADGui.Control.closeDialog()` a CAM Job panel: it skips reject()/cleanup and leaves
  its Selection observer registered. Close it with the panel's Cancel/OK (`cd.panel().cancel()`).
- New ops inherit the previous op's TC; only the first op shows the TC chooser.
- A job must keep one TC: add tools first, then delete the template default.
- Expression-bound spin boxes are read-only; `clear_expression=True` uses the f(x) -> Discard popup.
- Profile "Set Start Point" reads the 3D selection's picked point (addSelection with x,y,z).
- Panel values can lag a moment behind the object (e.g. final depth after adding base).
- Pass `timeout_ms` per call for long steps. A blocked GUI thread (a modal nobody answers) still stalls it.
- Walking widgets as `QWidget` leaves Python wrappers that can outlive their C++ widget. A later
  panel may then get a plain `QWidget`/`QWidgetItem` for a spinbox or combo, or "already deleted";
  touching a dead dialog segfaults FreeCAD. Guards: `widgets.alive()`, the modal watcher tracks
  `destroyed`, recipes call processEvents + `gc.collect()` between steps. Avoid `p.dump()` in long runs.
- FreeCAD's own SIGSEGV handler replaces Python's, so `PYTHONFAULTHANDLER` prints nothing: call
  `faulthandler.enable(open(path, "w"), all_threads=True)` from inside the session.
- Changing the work plane on the Heights page refreshes the depth fields a moment later; set depths
  after that (a separate call, or processEvents first), or the refresh overwrites them.
- A new op adopts the previous op's work plane (dressups skipped), like its tool controller.
- `CAM_Engrave` with a non-model shape selected asks "Choose a CAM Job" (QInputDialog): answer it with
  a rule that `setTextValue()`s the job label.
- `setEdit` only opens a panel for the document in the active 3D view; activate its MDI window first.
