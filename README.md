<img src="Resources/Icons/Logo.svg" alt="MCP+ robot" width="96" align="right">

# MCP+

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

MCP+ lets AI assistants (Claude Code and other [MCP](https://modelcontextprotocol.io/) clients) work in
your live [FreeCAD](https://www.freecad.org/) session: build and inspect models, run Python, drive the
GUI the way a user would, and review CAM jobs. It works with FreeCAD 1.1 and newer development builds.

MCP+ is built on [spkane's MCP+ server](https://github.com/spkane/freecad-addon-robust-mcp-server)
and adds:

- **CAM tools**: path statistics, A/B comparisons between two code trees, geometry finders, test
  fixtures, posting and G-code diffs, gouge and coverage checks, headless test runs, demo recording.
- **GuiDriver**: run toolbar commands, fill in task panels, answer modal dialogs by rule
  (see [GuiDriver](#guidriver---drive-the-gui-like-a-user-6-tools)).
- **Runs that outlast their timeout keep going**: the answer carries a `job_token`, and
  `get_output_page` reads the rest of the output and the result page by page
  (after [CREATeNG/freecad-mcp-bridge](https://github.com/CREATeNG/freecad-mcp-bridge)).
- **`bridge_status`**: what FreeCAD is busy with, or which modal dialog is blocking it, answered even
  while every other call would wait (after [neka-nat/freecad-mcp](https://github.com/neka-nat/freecad-mcp)).
- **`execute_python_file`**: run a script with `__file__` set and `__name__ == "__main__"`.
- **Safer bridge**: every request must carry a per-user token, and requests from foreign web pages
  are refused (see [Security and your data](#security-and-your-data)).
- **Fixes**: Addon Manager crash on install, Qt imports through FreeCAD's `PySide` wrapper,
  PartDesign/Sketcher tools on FreeCAD 1.x, screenshots, `.env` handling.

Install it with FreeCAD's Addon Manager or with git (see [Installation](#installation)). The MCP
server comes with the add-on; your AI client runs it from the add-on folder with
[uv](https://docs.astral.sh/uv/).

**Platforms.** MCP+ runs on Linux, Windows and macOS. The exceptions are the tools that move a real
mouse and keyboard: `cam_record_demo` and GuiDriver's `xmouse` / `xkeys_external` helpers use X11
(XTest) and work only on Linux with X11. The other GuiDriver tools work everywhere.

The rest of this README is adapted from the original server's documentation.

## Security and your data

**What MCP+ allows.** MCP+ lets an AI assistant run any Python code in your FreeCAD session, with
your user's permissions, the same as a macro you run yourself. There is no sandbox: the code can
read and write your files, start programs and reach the network. Your MCP client is the guardrail:
use one you trust, and have it ask for your approval before it runs tool calls.

**Who can reach the bridge.** The bridge only runs while you start it (auto-start is off unless you
turn it on in **Edit → Preferences → MCP+**). It listens on `localhost` (ports 9875 and 9876 by
default), so other computers can't reach it. Every request must also carry a token:

- On its first start the bridge creates a random token in a file only your user can read:
  `~/.config/mcp-plus/token` on Linux, `~/Library/Application Support/mcp-plus/token` on macOS,
  `%APPDATA%\mcp-plus\token` on Windows. The MCP server reads the same file, so nothing needs
  configuring, while other users' programs on the same computer can't get in.
- **Edit → Preferences → MCP+ → Security** shows the token file, copies the token, or replaces it
  (takes effect at once). Turning the token off is possible for localhost only, and not recommended.
- Requests sent by web pages in your browser (an `Origin` other than localhost) are refused.
- To use FreeCAD from another computer, prefer an ssh tunnel to its port. If you do set
  **Listen on** to another address, the MCP server there needs the token in `FREECAD_AUTH_TOKEN`;
  the bridge never listens beyond localhost without one.

**Where your data goes.** The add-on and the MCP server collect nothing and send nothing to anyone;
there is no telemetry. But whatever the assistant asks FreeCAD for becomes part of its conversation:
object names and properties, file names and paths, geometry, the output of code it runs, and
screenshots of your 3D view. Your MCP client sends that to your AI provider, under that provider's
terms. Don't use MCP+ on designs you aren't allowed to share with your AI provider. Code the assistant
runs can also reach the network, as any macro can.

**Reporting a security problem.** Please use GitHub's private vulnerability reporting (the
repository's **Security** tab) rather than a public issue.

## Table of Contents

<!--TOC-->

- [MCP+](#mcp)
  - [Security and your data](#security-and-your-data)
  - [Table of Contents](#table-of-contents)
  - [Features](#features)
  - [Installation Requirements / Dependencies](#installation-requirements--dependencies)
  - [For Users](#for-users)
    - [Quick Links](#quick-links)
  - [MCP+ Server](#mcp-server)
    - [Installation](#installation)
      - [Addon Manager (recommended)](#addon-manager-recommended)
      - [Git](#git)
      - [Finding the add-on folder](#finding-the-add-on-folder)
    - [Configuration](#configuration)
      - [MCP Client Configuration](#mcp-client-configuration)
      - [Environment Variables](#environment-variables)
      - [Connection Modes](#connection-modes)
    - [Usage](#usage)
      - [Starting the MCP Bridge in FreeCAD](#starting-the-mcp-bridge-in-freecad)
      - [Updating](#updating)
      - [Uninstalling](#uninstalling)
    - [Available Tools](#available-tools)
      - [Execution & Debugging (8 tools)](#execution--debugging-8-tools)
      - [Document Management (7 tools)](#document-management-7-tools)
      - [Object Creation - Primitives (8 tools)](#object-creation---primitives-8-tools)
      - [Object Management (12 tools)](#object-management-12-tools)
      - [PartDesign - Sketching (14 tools)](#partdesign---sketching-14-tools)
      - [PartDesign - Patterns & Edges (5 tools)](#partdesign---patterns--edges-5-tools)
      - [View & Display (11 tools)](#view--display-11-tools)
      - [Undo/Redo (3 tools)](#undoredo-3-tools)
      - [Export/Import (7 tools)](#exportimport-7-tools)
      - [Macro Management (6 tools)](#macro-management-6-tools)
      - [Parts Library (2 tools)](#parts-library-2-tools)
      - [CAM review and testing (11 tools)](#cam-review-and-testing-11-tools)
      - [GUI health (2 tools)](#gui-health-2-tools)
      - [GuiDriver - drive the GUI like a user (6 tools)](#guidriver---drive-the-gui-like-a-user-6-tools)
  - [For Developers](#for-developers)
  - [MCP+ Server Development](#mcp-server-development)
    - [Prerequisites](#prerequisites)
    - [Initial Setup](#initial-setup)
    - [Running Tests](#running-tests)
    - [Code Quality](#code-quality)
  - [Architecture](#architecture)
  - [Acknowledgements](#acknowledgements)
    - [Related Projects](#related-projects)
  - [License](#license)

<!--TOC-->

**FreeCAD Forum:** [addon discussion post](https://forum.freecad.org/viewtopic.php?p=866012)

## Features

- **150+ MCP Tools**: Comprehensive CAD operations including primitives, PartDesign, booleans, export
- **Multiple Connection Modes**: XML-RPC (recommended), JSON-RPC socket, or embedded
- **GUI & Headless Support**: Full modeling in headless mode, plus screenshots/colors in GUI mode
- **Macro Development**: Create, edit, run, and template FreeCAD macros via MCP

## Installation Requirements / Dependencies

- [FreeCAD](https://www.freecad.org/) 1.1 or newer
- [uv](https://docs.astral.sh/uv/getting-started/installation/), which runs the MCP server and sets
  up its Python environment on first start

---

## For Users

This section covers installation and usage for end users who want to use the MCP+ server with AI assistants.

### Quick Links

| Resource                                                                       | Description                                       |
| ------------------------------------------------------------------------------ | ------------------------------------------------- |
| [**Documentation**](https://spkane.github.io/freecad-addon-robust-mcp-server/) | Original server's documentation and API reference |
| [GitHub Releases](https://github.com/Connor9220/freecad-mcp-plus/releases)     | MCP+ releases and changelogs                      |
| [Issues](https://github.com/Connor9220/freecad-mcp-plus/issues)                | Bug reports and feature requests                  |

## MCP+ Server

### Installation

MCP+ is one download: the FreeCAD add-on and the MCP server live in the same folder,
`freecad-mcp-plus` inside FreeCAD's `Mod` folder. Install it one of two ways.

#### Addon Manager (recommended)

1. **Edit → Preferences → Addon Manager → Custom repositories → +**, add
   `https://github.com/Connor9220/freecad-mcp-plus` with branch `main`.
1. **Tools → Addon Manager**, search for "MCP+", install it and restart FreeCAD.

#### Git

Clone the repository into FreeCAD's `Mod` folder and restart FreeCAD:

```bash
git clone https://github.com/Connor9220/freecad-mcp-plus.git <Mod folder>/freecad-mcp-plus
```

#### Finding the add-on folder

Your MCP client needs the add-on folder's path. Run this in FreeCAD's Python console:

```python
import os; print(os.path.join(App.getUserAppDataDir(), "Mod", "freecad-mcp-plus"))
```

On Linux with FreeCAD 1.1 this is usually `~/.local/share/FreeCAD/v1-1/Mod/freecad-mcp-plus`.

### Configuration

#### MCP Client Configuration

Add something like the following to your MCP client settings, with `--project` pointing at the
add-on folder. For Claude Code, this is `~/.claude.json` (or `claude mcp add`) or a project
`.mcp.json` file:

```json
{
  "mcpServers": {
    "freecad": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/FreeCAD/Mod/freecad-mcp-plus", "freecad-mcp"],
      "env": {
        "FREECAD_MODE": "xmlrpc"
      }
    }
  }
}
```

#### Environment Variables

Set these in the `env` block above.

| Variable              | Description                                          | Default     |
| --------------------- | ---------------------------------------------------- | ----------- |
| `FREECAD_MODE`        | Connection mode: `xmlrpc`, `socket`, or `embedded`   | `xmlrpc`    |
| `FREECAD_PATH`        | Path to FreeCAD's lib directory (embedded mode only) | Auto-detect |
| `FREECAD_SOCKET_HOST` | Socket/XML-RPC server hostname                       | `localhost` |
| `FREECAD_SOCKET_PORT` | JSON-RPC socket server port                          | `9876`      |
| `FREECAD_XMLRPC_PORT` | XML-RPC server port                                  | `9875`      |
| `FREECAD_TIMEOUT_MS`  | Execution timeout in ms                              | `30000`     |

#### Connection Modes

| Mode       | Description                                 | Platform Support                  |
| ---------- | ------------------------------------------- | --------------------------------- |
| `xmlrpc`   | Connects to FreeCAD via XML-RPC (port 9875) | **All platforms** (recommended)   |
| `socket`   | Connects via JSON-RPC socket (port 9876)    | **All platforms**                 |
| `embedded` | Imports FreeCAD directly into process       | **Linux only** (crashes on macOS) |

**Note:** Embedded mode crashes on macOS because FreeCAD's `FreeCAD.so` links to `@rpath/libpython3.11.dylib`, which conflicts with external Python interpreters. Use `xmlrpc` or `socket` mode on macOS and Windows.

### Usage

#### Starting the MCP Bridge in FreeCAD

Before your AI assistant can connect, you need to start the MCP bridge inside FreeCAD:

1. Switch to the MCP+ workbench.
1. Click the **Start MCP Bridge** button in the toolbar, or use the menu **MCP Bridge -> Start Bridge**.
   To start it with FreeCAD every time, turn on auto-start in **Edit → Preferences → MCP+**.
1. You should see in the FreeCAD console:

   ```text
   MCP Bridge started!
     - XML-RPC: localhost:9875
     - Socket: localhost:9876
   ```

After starting the bridge, start/restart your MCP client (Claude Code, etc.) - it will connect automatically.

#### Updating

- Addon Manager: **Tools → Addon Manager → Check for updates**, then update MCP+.
- Git: run `git pull` in the add-on folder.

Restart FreeCAD and your MCP client afterwards.

#### Uninstalling

- Addon Manager: **Tools → Addon Manager**, select MCP+, click **Uninstall**, restart FreeCAD.
- Git: delete the `freecad-mcp-plus` folder from FreeCAD's `Mod` folder and restart FreeCAD.

Remove the `freecad` entry from your MCP client settings as well.

### Available Tools

The MCP+ server provides **150+ tools** organized into categories. Tools marked with **GUI** require FreeCAD to be running in GUI mode; they will return an error in headless mode.

#### Execution & Debugging (8 tools)

| Tool                         | Description                                                   | Mode |
| ---------------------------- | ------------------------------------------------------------- | ---- |
| `execute_python`             | Execute arbitrary Python code in FreeCAD's context            | All  |
| `execute_python_file`        | Run a .py file as `__main__`, with `__file__` set             | All  |
| `get_output_page`            | Read a run that outlasted its timeout, page by page           | All  |
| `bridge_status`              | What FreeCAD is busy with; answers even when FreeCAD is stuck | All  |
| `get_freecad_version`        | Get FreeCAD version, build date, and Python version           | All  |
| `get_connection_status`      | Check MCP bridge connection status and latency                | All  |
| `get_console_output`         | Get recent FreeCAD console output (up to N lines)             | All  |
| `get_mcp_server_environment` | Get MCP+ server environment (OS, hostname, instance_id)       | All  |

#### Document Management (7 tools)

| Tool                  | Description                               | Mode |
| --------------------- | ----------------------------------------- | ---- |
| `list_documents`      | List all open documents with metadata     | All  |
| `get_active_document` | Get information about the active document | All  |
| `create_document`     | Create a new FreeCAD document             | All  |
| `open_document`       | Open an existing .FCStd file              | All  |
| `save_document`       | Save a document to disk                   | All  |
| `close_document`      | Close a document (with optional save)     | All  |
| `recompute_document`  | Force recomputation of all objects        | All  |

#### Object Creation - Primitives (8 tools)

| Tool              | Description                                        | Mode |
| ----------------- | -------------------------------------------------- | ---- |
| `create_object`   | Create a generic FreeCAD object by type ID         | All  |
| `create_box`      | Create a Part::Box with length, width, height      | All  |
| `create_cylinder` | Create a Part::Cylinder with radius, height, angle | All  |
| `create_sphere`   | Create a Part::Sphere with radius                  | All  |
| `create_cone`     | Create a Part::Cone with two radii and height      | All  |
| `create_torus`    | Create a Part::Torus (donut) with radii and angles | All  |
| `create_wedge`    | Create a Part::Wedge (tapered box)                 | All  |
| `create_helix`    | Create a Part::Helix curve for sweeps and threads  | All  |

#### Object Management (12 tools)

| Tool                | Description                                        | Mode |
| ------------------- | -------------------------------------------------- | ---- |
| `list_objects`      | List all objects in a document                     | All  |
| `inspect_object`    | Get detailed object info (properties, shape, etc.) | All  |
| `edit_object`       | Modify properties of an existing object            | All  |
| `delete_object`     | Delete an object from a document                   | All  |
| `set_placement`     | Set object position and rotation                   | All  |
| `scale_object`      | Scale an object uniformly or non-uniformly         | All  |
| `rotate_object`     | Rotate an object around an axis                    | All  |
| `copy_object`       | Create a copy of an object                         | All  |
| `mirror_object`     | Mirror an object across a plane (XY, XZ, YZ)       | All  |
| `boolean_operation` | Fuse, cut, or intersect objects                    | All  |
| `get_selection`     | Get currently selected objects                     | GUI  |
| `set_selection`     | Select specific objects by name                    | GUI  |
| `clear_selection`   | Clear all selections                               | GUI  |

#### PartDesign - Sketching (14 tools)

| Tool                     | Description                                     | Mode |
| ------------------------ | ----------------------------------------------- | ---- |
| `create_partdesign_body` | Create a PartDesign::Body container             | All  |
| `create_sketch`          | Create a sketch on a plane or face              | All  |
| `add_sketch_rectangle`   | Add a rectangle to a sketch                     | All  |
| `add_sketch_circle`      | Add a circle to a sketch                        | All  |
| `add_sketch_line`        | Add a line (with optional construction flag)    | All  |
| `add_sketch_arc`         | Add an arc by center, radius, and angles        | All  |
| `add_sketch_point`       | Add a point (useful for hole centers)           | All  |
| `pad_sketch`             | Extrude a sketch (additive)                     | All  |
| `pocket_sketch`          | Cut into solid using a sketch (subtractive)     | All  |
| `revolution_sketch`      | Revolve a sketch around an axis (additive)      | All  |
| `groove_sketch`          | Revolve a sketch around an axis (subtractive)   | All  |
| `create_hole`            | Create parametric holes with optional threading | All  |
| `loft_sketches`          | Create a loft through multiple sketches         | All  |
| `sweep_sketch`           | Sweep a profile along a spine path              | All  |

#### PartDesign - Patterns & Edges (5 tools)

| Tool               | Description                                | Mode |
| ------------------ | ------------------------------------------ | ---- |
| `linear_pattern`   | Create linear pattern of a feature         | All  |
| `polar_pattern`    | Create polar/circular pattern of a feature | All  |
| `mirrored_feature` | Mirror a feature across a plane            | All  |
| `fillet_edges`     | Add fillets (rounded edges)                | All  |
| `chamfer_edges`    | Add chamfers (beveled edges)               | All  |

#### View & Display (11 tools)

| Tool                    | Description                                     | Mode |
| ----------------------- | ----------------------------------------------- | ---- |
| `get_screenshot`        | Capture a screenshot of the 3D view             | GUI  |
| `set_view_angle`        | Set camera to standard views (Front, Top, etc.) | GUI  |
| `fit_all`               | Zoom to fit all objects in view                 | GUI  |
| `zoom_in`               | Zoom in by a factor                             | GUI  |
| `zoom_out`              | Zoom out by a factor                            | GUI  |
| `set_camera_position`   | Set camera position and look-at point           | GUI  |
| `set_object_visibility` | Show/hide objects                               | GUI  |
| `set_display_mode`      | Set display mode (Shaded, Wireframe, etc.)      | GUI  |
| `set_object_color`      | Set object color as RGB values                  | GUI  |
| `list_workbenches`      | List available FreeCAD workbenches              | All  |
| `activate_workbench`    | Switch to a different workbench                 | All  |

#### Undo/Redo (3 tools)

| Tool                   | Description                        | Mode |
| ---------------------- | ---------------------------------- | ---- |
| `undo`                 | Undo the last operation            | All  |
| `redo`                 | Redo a previously undone operation | All  |
| `get_undo_redo_status` | Get available undo/redo operations | All  |

#### Export/Import (7 tools)

| Tool          | Description                                | Mode |
| ------------- | ------------------------------------------ | ---- |
| `export_step` | Export to STEP format (ISO CAD exchange)   | All  |
| `export_stl`  | Export to STL format (3D printing)         | All  |
| `export_3mf`  | Export to 3MF format (modern 3D printing)  | All  |
| `export_obj`  | Export to OBJ format (Wavefront)           | All  |
| `export_iges` | Export to IGES format (older CAD exchange) | All  |
| `import_step` | Import a STEP file                         | All  |
| `import_stl`  | Import an STL file as mesh                 | All  |

#### Macro Management (6 tools)

| Tool                         | Description                                    | Mode |
| ---------------------------- | ---------------------------------------------- | ---- |
| `list_macros`                | List all available FreeCAD macros              | All  |
| `run_macro`                  | Execute a macro by name                        | All  |
| `create_macro`               | Create a new macro file                        | All  |
| `read_macro`                 | Read macro source code                         | All  |
| `delete_macro`               | Delete a user macro                            | All  |
| `create_macro_from_template` | Create macro from template (basic, part, etc.) | All  |

#### Parts Library (2 tools)

| Tool                       | Description                           | Mode |
| -------------------------- | ------------------------------------- | ---- |
| `list_parts_library`       | List parts in FreeCAD's parts library | All  |
| `insert_part_from_library` | Insert a part from the library        | All  |

#### CAM review and testing (11 tools)

Tools for reviewing and testing CAM (Path) changes. "Local" tools run on the machine of the MCP
server (headless FreeCADCmd, file comparisons) and don't need the bridge.

| Tool                | Description                                                              | Mode  |
| ------------------- | ------------------------------------------------------------------------ | ----- |
| `cam_path_stats`    | Counts, cut/rapid length, Z levels, link heights, upward feeds of an op  | All   |
| `cam_ab`            | Run a probe against two git refs of the CAM code and compare the results | Local |
| `cam_find_geometry` | Find faces and edges by normal, height or radius                         | All   |
| `cam_fixture`       | Build a test Job with stock, tool controller and operation               | All   |
| `cam_post`          | Post a job; upload, username and remote dialogs are cancelled            | All   |
| `cam_gcode_diff`    | Compare two G-code outputs                                               | Local |
| `cam_gouge_check`   | Sweep the tool along link moves and report gouges                        | All   |
| `cam_coverage`      | Grid check of the material an operation leaves uncut                     | All   |
| `cam_tests`         | Run CAM unit tests, optionally against a baseline tree                   | Local |
| `fc_eval`           | Run a script in headless FreeCADCmd and return its real exit code        | Local |
| `cam_record_demo`   | Record a captioned GUI demo on an off-screen display                     | GUI   |

#### GUI health (2 tools)

| Tool         | Description                                                                   | Mode |
| ------------ | ----------------------------------------------------------------------------- | ---- |
| `gui_health` | Open panels, stray dialogs, active document, new Report-view errors           | GUI  |
| `gui_reset`  | Close stray panels and dialogs; gets in even while a run is stuck on a dialog | GUI  |

#### GuiDriver - drive the GUI like a user (6 tools)

The add-on ships `guidriver` (in `freecad/MCPPlus/guidriver/`, importable inside
FreeCAD as `freecad.MCPPlus.guidriver`; CAM parts in `guidriver.cam`). It runs real GUI commands,
reads and sets task panel widgets through their own Qt API (GUI gating included), presses
the task view's OK/Cancel and answers modal dialogs by rule; a dialog no rule matches is
rejected after 3 s and reported. See its [README](freecad/MCPPlus/guidriver/README.md).
These six tools work on every platform; only the real mouse/keyboard helpers (`xmouse`,
`xkeys_external`) need Linux with X11.

| Tool               | Description                                                      | Mode |
| ------------------ | ---------------------------------------------------------------- | ---- |
| `gui_run_command`  | Run a command like a toolbar click, with modal-dialog rules      | GUI  |
| `gui_panel`        | Dump / set / click / OK / Cancel the open task panel             | GUI  |
| `gui_edit`         | Open an object's editor like a tree double-click                 | GUI  |
| `cam_gui_add_base` | Base Geometry "Add" on the open CAM operation panel              | GUI  |
| `cam_gui_check`    | Pre-post warnings (and summary) for a CAM job                    | GUI  |
| `cam_gui_post`     | Post a job, answering the post's dialogs by rule (never uploads) | GUI  |

---

## For Developers

This section covers development setup, contributing, and working with the codebase.

## MCP+ Server Development

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [FreeCAD](https://www.freecad.org/) 1.1 or newer

### Initial Setup

Clone the repository into FreeCAD's `Mod` folder (see [Finding the add-on folder](#finding-the-add-on-folder)),
so FreeCAD loads the add-on from your working copy, then install the development tools:

```bash
git clone https://github.com/Connor9220/freecad-mcp-plus.git <Mod folder>/freecad-mcp-plus
cd <Mod folder>/freecad-mcp-plus
uv sync --extra dev
uv run pre-commit install
```

Point your MCP client at the clone as in [MCP Client Configuration](#mcp-client-configuration).

### Running Tests

```bash
# Unit tests (no FreeCAD required)
uv run pytest tests/unit -o addopts=""

# Integration tests (requires FreeCAD running with the bridge started)
uv run pytest tests/integration -o addopts=""
```

### Code Quality

The project uses strict code quality checks via pre-commit:

- **Ruff** - Linting and formatting
- **MyPy** - Type checking
- **Bandit** - Security scanning
- **Codespell** - Spell checking
- **Secrets scanning** - Gitleaks, detect-secrets, TruffleHog

```bash
uv run ruff check freecad src             # Lint
uv run mypy src                           # Type check
uv run pre-commit run --all-files         # All hooks
```

---

## Architecture

See the [detailed architecture document](docs/development/architecture-detailed.md) for design documentation covering:

- Module structure
- Bridge communication protocols
- Tool registration patterns
- FreeCAD plugin architecture

---

## Acknowledgements

MCP+ is a copy of [spkane/freecad-addon-robust-mcp-server](https://github.com/spkane/freecad-addon-robust-mcp-server)
by Sean P. Kane, extended by Billy Huddleston. It also takes ideas from
[neka-nat/freecad-mcp](https://github.com/neka-nat/freecad-mcp) (bridge status, version check) and
[CREATeNG/freecad-mcp-bridge](https://github.com/CREATeNG/freecad-mcp-bridge) (paged output for long
runs, Origin check, running files). All three are MIT licensed; their notices are in [LICENSE](LICENSE).

spkane's original acknowledgements:

This project was developed after analyzing several existing FreeCAD MCP implementations. We are grateful to these projects for their pioneering work and the ideas they contributed to the FreeCAD + AI ecosystem:

### Related Projects

- **[neka-nat/freecad-mcp](https://github.com/neka-nat/freecad-mcp)** (MIT License) - The queue-based thread safety pattern and XML-RPC protocol design (port 9875) were directly inspired by this project. Our implementation maintains protocol compatibility while being a complete rewrite with additional features.

- **[jango-blockchained/mcp-freecad](https://github.com/jango-blockchained/mcp-freecad)** - Inspired our connection recovery mechanisms and multi-mode architecture approach.

- **[contextform/freecad-mcp](https://github.com/contextform/freecad-mcp)** - Informed our comprehensive PartDesign and Part workbench tool coverage.

- **[ATOI-Ming/FreeCAD-MCP](https://github.com/ATOI-Ming/FreeCAD-MCP)** - Inspired our macro development toolkit including templates, validation, and automatic imports.

- **[bonninr/freecad_mcp](https://github.com/bonninr/freecad_mcp)** - Influenced our simple socket-based communication approach.

See [docs/COMPARISON.md](docs/COMPARISON.md) for a detailed analysis of these implementations and the design decisions they informed.

---

## License

MIT License - see [LICENSE](LICENSE) for details.
