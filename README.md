# agentlog

Capture CLI agent conversations into the project directory they ran in.

When you run coding agents (CodeBuddy, pi, Claude Code, OpenCode, …) from many
different working directories, it is easy to forget *where* a conversation
happened. Also, many agents print their “thinking”/reasoning inline, which
clutters the scrollback and pollutes any transcript you keep.

`agentlog` watches each agent’s own transcript files (JSONL) in the background
and renders a clean **`.txt`** per conversation window, placed under the
project folder it belongs to. It:

- keeps **only the user/assistant conversation** (questions & answers),
- **drops thinking/reasoning** and tool internals,
- puts every message on **Beijing time** (`UTC+8`),
- runs as a tiny **background daemon** (no console window) that starts at logon,
  so you never toggle it per session or per agent.

## Output layout

```
agent_logs/<agent>/<bottom-3-path-components>.txt
```

The filename is just the last three path components of the project directory
(joined with `-`), because the agent is implied by the containing folder and
you are unlikely to run two conversations in the same project at once.
Example: a session run in `E:\lab\Group_meeting\202608` becomes
`claude/lab-Group_meeting-202608.txt`.

Each file starts with metadata, then the conversation:

```
# Agent: claude
# Path: E:\lab\Group_meeting\202608
# Session: 0181fb85...
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
git clone <your-fork> agentlog
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

## How thinking is dropped

Each parser keeps only `user`/`assistant` messages. It additionally:

- skips `reasoning`/`thinking` events entirely,
- skips `tool_use` / `tool_result` (and pi’s `toolResult`) blocks,
- drops assistant text that is just narration immediately before a tool call
  (the “thinking out loud” lines),
- ignores `subagents/` directories (those are internal chatter, not standalone
  conversations).

## Files

- `agentlog.py` — the tool (daemon + CLI + per-agent parsers).
- `agents.example.json` — optional config template.
- `agent_logs/` — generated exports (git-ignored; safe to delete).

## License

MIT — see [LICENSE](LICENSE).
