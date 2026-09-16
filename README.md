# agentlog

> **English** | [中文](README_zh.md)

Capture CLI agent conversations into the project directory they ran in.

When you run coding agents (CodeBuddy, pi, Claude Code, OpenCode, …) from many
different working directories, it is easy to forget *where* a conversation
happened. Also, many agents print their "thinking"/reasoning inline, which
clutters the scrollback and pollutes any transcript you keep.

`agentlog` watches each agent's own transcript files (JSONL) in the background
and renders a clean file per conversation window, placed under the project
folder it belongs to. It:

- keeps **only the user/assistant conversation** (questions & answers),
- **optionally keeps thinking/reasoning** (dropped by default),
- supports multiple output formats (**txt**, **Markdown**, **JSON**),
- puts every message on **Beijing time** (`UTC+8`),
- runs as a tiny **background daemon** (no console window) that starts at logon,
  so you never toggle it per session or per agent.

## Output layout

```
agent_logs/<agent>/<bottom-3-path-components>.<format>
```

The filename is just the last three path components of the project directory
(joined with `-`), because the agent is implied by the containing folder and
you are unlikely to run two conversations in the same project at once.

Example: a session run in `/path/to/your/project/v2` becomes
`claude/your-project-v2.txt`.

### Output formats

| Format | Extension | Description |
|--------|-----------|-------------|
| txt | `.txt` | Plain text, compact and simple |
| Markdown | `.md` | Markdown format, well-structured for reading |
| JSON | `.json` | JSON format, easy for programmatic processing |

### Thinking content

By default, thinking/reasoning content is not included. When enabled, files
are saved to the `_thinking` subfolder:

```
agent_logs/<agent>/<file>.txt          # Without thinking
agent_logs/<agent>/_thinking/<file>.txt # With thinking
```

Each file starts with metadata, then the conversation:

```
# Agent: claude
# Path: /path/to/your/project/v2
# Session: <session-id>
# Start (Beijing): 2026-08-17 11:50:03
# Messages: 12

[2026-08-17 11:50:15] User:
How do I ...

[2026-08-17 11:50:31] Assistant:
You can ...
```

## Requirements

- Python 3.8+ (tested on 3.13), **standard library only** — no `pip install`.
- Windows. Auto-start uses a shortcut in your personal **Startup folder**
  (no admin needed).

## Install & run

```bash
# 1. clone / copy this folder anywhere
git clone <repository-url> agentlog
cd agentlog

# 2. one-time scan (good for a quick test)
python agentlog.py once

# 3. start the background daemon + enable logon auto-start
python agentlog.py install
```

That is it. The daemon polls every 5 seconds; idle CPU is ~0.

> **Why a Startup-folder shortcut and not the registry `Run` key?**
> Some machines run a startup manager (e.g. Lenovo's "smart" services) that
> silently moves unknown `Run`-key entries into a disabled list within seconds.
> A `.lnk` in `shell:startup` is not swept that way. `install` falls back to
> the registry `Run` key only if the Startup folder is not writable.

## Commands

| Command | Description |
| --- | --- |
| `python agentlog.py run` | Foreground watch loop (used internally by the daemon) |
| `python agentlog.py once` | Single scan, then exit (useful for testing) |
| `python agentlog.py start` | Launch the background daemon now |
| `python agentlog.py stop` | Stop the background daemon |
| `python agentlog.py status` | Is the daemon running? |
| `python agentlog.py install` | Enable logon auto-start (Startup shortcut) + start now |
| `python agentlog.py uninstall` | Stop + remove the Startup shortcut |
| `python agentlog.py gui-shortcut` | Create a double-click GUI launcher (repo dir + Desktop) |
| `python agentlog.py build-icon <img>` | Regenerate `agentlog.ico` (tray/window/shortcut) from an image |

### CLI arguments

```bash
# Generate multiple formats at once
python agentlog.py once -f txt md json

# Include thinking content
python agentlog.py once -t

# Combine options
python agentlog.py once -f txt md -t
```

| Argument | Description |
|----------|-------------|
| `-f, --format` | Output format(s), can be multiple: `txt` `md` `json` (default: txt) |
| `-t, --include-thinking` | Include thinking/reasoning content (default: excluded) |

## GUI

Prefer a window over the command line? There is a tiny, zero-dependency UI
(`agentlog_gui.py`, built only on `tkinter` + `ctypes` — nothing to `pip install`):

```bash
python agentlog_gui.py
```

### Features

- **Status display** (running / stopped and pid), plus **Start / Stop /
  Restart / Scan now** buttons;
- **Auto-start toggle** (maps to the `.lnk` in the Startup folder);
- **Dual-tab interface**: File list + Session list;

### File List Tab

- **Format selection**: checkboxes for `txt`, `md`, `json` (can select multiple);
- **Thinking toggle**: checkbox to include thinking content;
- **Exported file list**: grouped by agent / project, supports multiple formats;
- **File update time**: shows last modification time for each file;
- **Column sorting**: click column headers to sort by path, agent, filename, or update time;
- **Filters**:
  - Agent filter: filter by agent name;
  - Format filter: filter by file format (txt/md/json);
  - Thinking filter: filter by presence of thinking content;
  - Search: search by filename or agent name;
- **Selection operations**:
  - Select all checkbox: select all files in current filter;
  - Deselect all button: clear selection;
  - Delete selected button: delete selected files (with confirmation);

### Session List Tab

- **All agents support**: opencode (SQLite) + codebuddy/pi/claude (JSONL);
- **Session info**: Agent, project path, Session ID, start time, last update, message count;
- **Auto-filter**: hides empty sessions with 0 messages;
- **Agent filter**: filter by agent name;
- **Search**: search by project path, Session ID, or agent name;
- **Column sorting**: click column headers to sort by any column;
- **Double-click**: opens the exported file for that session;

### GUI layout

```
[Status: Running] [Start] [Stop] [Restart] [Scan Now]    [Auto-start]

[File List] [Session List]
┌──────────────────────────────────────────────────────────────────────┐
│ Output: ☑ txt  ☑ md  ☐ json    Include thinking ☐    [Refresh]     │
│ Agent: [All ▼]  Format: [All ▼]  Thinking: [All ▼]  Search: [__] [Search] │
├──────────────────────────────────────────────────────────────────────┤
│ Path (Project)     │ Agent    │ File                 │ Updated ↓     │
├──────────────────────────────────────────────────────────────────────┤
│ ...                                                                 │
├──────────────────────────────────────────────────────────────────────┤
│ ☑ Select All  [Deselect All]  [Delete Selected]  Selected 3/12     │
│                                              [Open Export Folder]   │
└──────────────────────────────────────────────────────────────────────┘
                                [Minimize to Tray]                   [Quit]
```

Click column headers to sort. Click again to toggle ascending/descending order.

### Single instance GUI

The GUI ensures only one instance runs at a time. If an instance is already
running (including minimized to tray), launching again will automatically
restore the existing window.

### Double-click to run

Prefer not to open a terminal? Generate a launcher you can just double-click —
it runs via `pythonw`, so there is **no black console window**:

```bash
python agentlog.py gui-shortcut
```

This creates `agentlog-gui.lnk` next to the repo and `agentlog GUI.lnk` on the
Desktop. Double-click either to launch the GUI. The icon comes from
`agentlog.ico` shipped in the repo.

### Changing the icon

The tray, window title bar, and shortcut all use `agentlog.ico` in the repo.
To use your own image (e.g. an anime avatar), regenerate it with Pillow
(only needed at build time — not at runtime):

```bash
python agentlog.py build-icon /path/to/your-image.jpg
```

Then re-run `gui-shortcut` so the launcher picks up the new icon.

## Single instance

`agentlog` guarantees exactly one daemon at a time. If one is already running
when you run `start` or `install`, you are prompted to either **restart** it
(kill the old process and start a fresh one) or **cancel** this run. The
interactive prompt is skipped in non-interactive shells (the existing daemon is
left running). Under the hood a Windows named mutex prevents two daemons from
ever running at once, so even concurrent triggers (e.g. the Startup shortcut
and a manual `start`) cannot spin up duplicates.

## Adding / tweaking agents

No code edit needed. Copy the example and adjust globs or the parser:

```bash
cp agents.example.json agents.json   # optional; edit to taste
```

`agents.json` overrides or adds entries from the built-in `DEFAULT_AGENTS`.
Each entry:

```json
{
  "name": "codex",
  "parser": "generic",
  "globs": ["~/.codex/**/*.jsonl", "~/.config/codex/**/*.jsonl"]
}
```

`parser` is one of `codebuddy`, `pi`, `claude`, `opencode`, or `generic`
(the last is a best-effort fallback for any `message/role/content` JSONL, e.g.
future agents like codex or mimo).

> **opencode note**: recent opencode versions store sessions in a SQLite
> database (default `~/.local/share/opencode/opencode.db`) instead of JSONL.
> `agentlog` reads that database directly — no configuration needed. Only
> `text` parts (user / assistant answers) are kept; `reasoning` (thinking) and
> `tool` (tool calls) parts are dropped.

## How thinking is dropped

Each parser keeps only `user`/`assistant` messages. It additionally:

- skips `reasoning`/`thinking` events entirely,
- skips `tool_use` / `tool_result` (and pi's `toolResult`) blocks,
- drops assistant text that is just narration immediately before a tool call
  (the "thinking out loud" lines),
- ignores `subagents/` directories (those are internal chatter, not standalone
  conversations).

## Files

- `agentlog.py` — the tool (daemon + CLI + per-agent parsers).
- `agentlog_gui.py` — optional GUI (window + system tray), reusing the above logic.
- `agentlog.ico` — icon file (tray / window title bar / shortcut).
- `agents.example.json` — optional config template.
- `agent_logs/` — generated exports

## License

MIT — see [LICENSE](LICENSE).
