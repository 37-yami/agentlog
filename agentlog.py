#!/usr/bin/env python3
# agentlog - capture CLI agent conversations into the project directory.
#
# Works by watching each agent's own transcript files (JSONL) and rendering a
# clean .txt per conversation window into the project's working directory.
# Thinking/reasoning content is dropped. A tiny background daemon (pythonw,
# started at logon via a Startup-folder shortcut) does this automatically for
# every agent - no per-session toggle needed.
#
# Usage:
#   agentlog.py run            # foreground watch loop (used by scheduler)
#   agentlog.py once           # single scan (good for testing)
#   agentlog.py start          # launch background daemon now
#   agentlog.py stop           # stop background daemon
#   agentlog.py status         # is it running?
#   agentlog.py install        # register logon task + start now
#   agentlog.py uninstall      # stop + remove logon task

import os
import re
import sys
import json
import time
import glob
import uuid
import ctypes
import argparse
import subprocess
import sqlite3
import ctypes.wintypes as wt
from datetime import datetime, timezone, timedelta

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
# All conversation exports go under <repo>/agent_logs/<agent>/...
OUTPUT_ROOT = os.path.join(HERE, "agent_logs")
# Optional icon (built from a source image, see build_icon()). The GUI and the
# launcher shortcut use it when present; otherwise a default system icon is used.
ICON_PATH = os.path.join(HERE, "agentlog.ico")
POLL_SECONDS = 5
# Drop assistant lines that are just narration right before a tool call
# (these are the "thinking out loud" bits the user does not want kept).
DROP_PRE_TOOL_ASSISTANT = True
# Directory components to ignore entirely (sub-agent transcripts are not
# standalone conversations and contain a lot of internal chatter).
IGNORE_DIR_PARTS = {"subagents"}
STATE_FILE = os.path.join(HERE, "agentlog.state.json")
LOG_FILE = os.path.join(HERE, "agentlog.log")
PID_FILE = os.path.join(HERE, "agentlog.pid")

BJ = timedelta(hours=8)  # Beijing time is fixed UTC+8

# Block types we never want in the conversation text.
THINK_TYPES = {"thinking", "reasoning", "thought", "reasoning_content"}
SKIP_TYPES = THINK_TYPES | {
    "tool_use", "tool_result", "function_call", "function_result",
    "input_json", "annotation", "image", "file", "diff", "redacted_thinking",
}
# Block types that carry user/assistant prose.
TEXT_TYPES = {"text", "input_text", "output_text", "markdown", "plain_text"}


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------
def parse_ts(v):
    """Accept ms-epoch int/float or ISO-8601 string -> Beijing datetime."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            s = v / 1000.0 if v > 1e12 else v
            return datetime.fromtimestamp(s, tz=timezone.utc) + BJ
        except (OSError, ValueError, OverflowError):
            return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            try:
                return parse_ts(float(s))
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt + BJ
    return None


def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "?"


def fmt_file(dt):
    return dt.strftime("%Y%m%d_%H%M%S") if dt else "unknown"


def path_tail(cwd, n=3):
    """Last n path components (drive stripped), joined by '-', filename-safe."""
    if not cwd:
        return "unknown"
    parts = [p for p in re.split(r"[\\/]", cwd) if p]
    if parts and len(parts[0]) == 2 and parts[0].endswith(":"):
        parts = parts[1:]  # drop drive letter, e.g. "E:"
    tail = parts[-n:] if len(parts) >= n else parts
    s = "-".join(tail)
    s = re.sub(r'[\\/:*?"<>|]', "-", s)  # sanitize
    return s or "unknown"


# --------------------------------------------------------------------------
# Content extraction
# --------------------------------------------------------------------------
def extract_text(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
                continue
            if not isinstance(b, dict):
                continue
            bt = (b.get("type") or "").lower()
            if bt in SKIP_TYPES:
                continue
            txt = b.get("text") or b.get("content") or ""
            if isinstance(txt, list):  # nested
                txt = extract_text(txt)
            if txt:
                parts.append(str(txt).strip())
        return "\n".join(p for p in parts if p).strip()
    return ""


def looks_internal(path):
    """Avoid writing exports into the agents' own config dirs."""
    low = path.replace("/", "\\").lower()
    for tok in (".codebuddy", ".claude", ".pi", ".config\\opencode",
                ".local\\share\\opencode", "appdata\\roaming\\npm"):
        if tok in low:
            return True
    return False


# --------------------------------------------------------------------------
# Project (cwd) resolution
# --------------------------------------------------------------------------
def decode_folder_cwd(folder):
    """Decode an agent's encoded project folder name back to an absolute path.

    Handles two common encodings:
      - pi style:              '--E--other-project--'          -> E:\\other\\project
      - codebuddy/claude style: 'e-lab-Group_meeting-202608'   -> e:\\lab\\Group_meeting\\202608
    Returns None when it cannot be decoded confidently.
    """
    if not folder:
        return None
    if "--" in folder:
        parts = [p for p in folder.split("--") if p]
        if not parts:
            return None
        drive, rest = parts[0], parts[1:]
        if len(drive) == 1 and drive.isalpha():
            return drive.upper() + ":" + ("\\" + "\\".join(rest) if rest else "")
        return None
    if "-" in folder and not folder.startswith("-") and len(folder.split("-")[0]) == 1:
        parts = folder.split("-")
        drive, rest = parts[0], parts[1:]
        if drive.isalpha():
            return drive.upper() + ":" + ("\\" + "\\".join(rest) if rest else "")
    return None


def resolve_cwd(raw, folder):
    """Best-effort absolute project path: prefer an explicit cwd, else decode
    the folder name. Falls back to None when nothing reliable is found."""
    if raw and isinstance(raw, str) and (":" in raw or raw.startswith("\\") or raw.startswith("/")):
        return os.path.normpath(raw)
    dec = decode_folder_cwd(folder)
    if dec:
        return os.path.normpath(dec)
    return None


# --------------------------------------------------------------------------
# Per-agent parsers
# Each returns (cwd, session_id, start_dt, [(beijing_dt, role, text), ...])
# --------------------------------------------------------------------------
def parse_codebuddy(path):
    folder = os.path.basename(os.path.dirname(path))
    raw_cwd = None
    sid = None
    start = None
    raw = []  # (kind, text, dt)  kind in {user, assistant, tool}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = o.get("type")
            if t == "reasoning":          # thinking -> skip
                continue
            if t == "message":
                role = o.get("role")
                if role == "user":
                    raw.append(("user", extract_text(o.get("content")),
                                parse_ts(o.get("timestamp"))))
                elif role == "assistant":
                    raw.append(("assistant", extract_text(o.get("content")),
                                parse_ts(o.get("timestamp"))))
                else:
                    continue
                raw_cwd = raw_cwd or o.get("cwd")
                sid = sid or o.get("sessionId")
                start = start or parse_ts(o.get("timestamp"))
            elif t in ("function_call", "function_call_result",
                       "tool_use", "tool_result"):
                raw.append(("tool", "", None))
            else:
                # ai-title, file-history-snapshot, etc.
                start = start or parse_ts(o.get("timestamp"))
                raw_cwd = raw_cwd or o.get("cwd")
    msgs = []
    n = len(raw)
    for i, (kind, text, dt) in enumerate(raw):
        if kind == "tool":
            continue
        if kind == "assistant" and DROP_PRE_TOOL_ASSISTANT:
            nxt = raw[i + 1][0] if i + 1 < n else None
            if nxt == "tool":          # planning aloud before acting -> drop
                continue
        if not text:
            continue
        msgs.append((dt, kind, text))
    return resolve_cwd(raw_cwd, folder), sid, start, msgs


def parse_pi(path):
    folder = os.path.basename(os.path.dirname(path))
    raw_cwd = None
    sid = None
    start = None
    msgs = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = o.get("type")
            if t == "session":
                raw_cwd = o.get("cwd")
                sid = o.get("id")
                start = parse_ts(o.get("timestamp"))
                continue
            if t in ("thinking_level_change", "model_change"):
                continue
            if t == "message":
                m = o.get("message") or {}
                role = m.get("role")
                # pi stores tool outputs as role "toolResult"; keep only the
                # actual conversation (user questions + assistant answers).
                if role not in ("user", "assistant"):
                    continue
                text = extract_text(m.get("content"))
                if not text:
                    continue
                msgs.append((parse_ts(o.get("timestamp")), role, text))
    return resolve_cwd(raw_cwd, folder), sid, start, msgs


def parse_claude(path):
    # Claude Code transcripts carry cwd / sessionId on every event line.
    folder = os.path.basename(os.path.dirname(path))
    raw_cwd = None
    sid = None
    start = None
    msgs = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = o.get("type")
            raw_cwd = raw_cwd or o.get("cwd")
            sid = sid or o.get("sessionId")
            start = start or parse_ts(o.get("timestamp"))
            if t not in ("user", "assistant"):
                continue
            msg = o.get("message") or {}
            role = msg.get("role") or o.get("role")
            if role not in ("user", "assistant"):
                continue
            text = extract_text(msg.get("content"))
            if not text:
                continue
            msgs.append((parse_ts(o.get("timestamp")), role, text))
    return resolve_cwd(raw_cwd, folder), sid, start, msgs


def parse_opencode(path):
    folder = os.path.basename(os.path.dirname(path))
    raw_cwd = None
    sid = None
    start = None
    msgs = []
    name = os.path.basename(path)
    sid = name[:-6] if name.endswith(".jsonl") else name
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = o.get("type")
            raw_cwd = raw_cwd or o.get("cwd") or o.get("workingDirectory")
            sid = sid or o.get("id") or o.get("sessionId")
            start = start or parse_ts(o.get("timestamp") or o.get("createdAt"))
            if t == "session" or t == "conversation":
                continue
            msg = o.get("message") or {}
            role = o.get("role") or msg.get("role")
            if role in ("thinking", "reasoning", "system"):
                continue
            if role in ("user", "assistant"):
                text = extract_text(msg.get("content") if msg else o.get("content"))
                if not text:
                    continue
                msgs.append((parse_ts(o.get("timestamp")), role, text))
    return resolve_cwd(raw_cwd, folder), sid, start, msgs


def parse_generic(path):
    """Best-effort parser for any JSONL transcript that uses a
    message/role/content shape (e.g. codex, mimo, or unknown agents)."""
    folder = os.path.basename(os.path.dirname(path))
    raw_cwd = None
    sid = None
    start = None
    msgs = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(o, dict):
                continue
            msg = o.get("message") if isinstance(o.get("message"), dict) else o
            role = msg.get("role") or o.get("role")
            raw_cwd = raw_cwd or o.get("cwd") or msg.get("cwd")
            sid = sid or o.get("sessionId") or o.get("id") or o.get("session_id")
            start = start or parse_ts(o.get("timestamp") or o.get("createdAt") or msg.get("timestamp"))
            if role in ("thinking", "reasoning", "system", "tool", "toolResult", "function"):
                continue
            if role in ("user", "assistant"):
                text = extract_text(msg.get("content") if msg is not o else o.get("content"))
                if not text:
                    continue
                msgs.append((parse_ts(o.get("timestamp") or msg.get("timestamp")), role, text))
    return resolve_cwd(raw_cwd, folder), sid, start, msgs


# --------------------------------------------------------------------------
# Agent registry  (edit here or via agents.json to add new agents)
# --------------------------------------------------------------------------
PARSERS = {
    "codebuddy": parse_codebuddy,
    "pi": parse_pi,
    "claude": parse_claude,
    "opencode": parse_opencode,
    "generic": parse_generic,
}

DEFAULT_AGENTS = [
    {"name": "codebuddy", "parser": "codebuddy",
     "globs": [r"~/.codebuddy/projects/**/*.jsonl"]},
    {"name": "pi", "parser": "pi",
     "globs": [r"~/.pi/agent/sessions/**/*.jsonl"]},
    {"name": "claude", "parser": "claude",
     "globs": [r"~/.claude/projects/**/*.jsonl"]},
    {"name": "opencode", "parser": "opencode",
     "source": "opencode_db",
     "globs": [r"~/.config/opencode/**/session/*.jsonl",
               r"~/.local/share/opencode/**/*.jsonl"]},
    # Best-effort stubs for future agents (adjust globs/parser as needed):
    {"name": "codex", "parser": "generic",
     "globs": [r"~/.codex/**/*.jsonl", r"~/.config/codex/**/*.jsonl"]},
    {"name": "mimo", "parser": "generic",
     "globs": [r"~/.mimo/**/*.jsonl", r"~/.config/mimo/**/*.jsonl"]},
]


def load_agents():
    """Load agent specs, allowing overrides/additions from agents.json."""
    agents = {a["name"]: dict(a) for a in DEFAULT_AGENTS}
    cfg = os.path.join(HERE, "agents.json")
    if os.path.exists(cfg):
        try:
            with open(cfg, encoding="utf-8") as fh:
                extra = json.load(fh)
            for a in extra.get("agents", []):
                agents[a["name"]] = a
        except Exception as e:
            print(f"[config-error] agents.json: {e}", file=sys.stderr)
    result = []
    for a in agents.values():
        parser = PARSERS.get(a.get("parser"), parse_generic)
        globs = [os.path.expanduser(g) for g in a.get("globs", [])]
        result.append({"agent": a["name"], "parser": parser, "globs": globs})
    return result


AGENTS = load_agents()


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def render(agent, cwd, sid, start, msgs):
    out = []
    out.append(f"# Agent: {agent}")
    out.append(f"# Path: {cwd or '(unknown)'}")
    if sid:
        out.append(f"# Session: {sid}")
    out.append(f"# Start (Beijing): {fmt(start)}")
    out.append(f"# Messages: {len(msgs)}")
    out.append("")
    role_label = {"user": "User", "assistant": "Assistant",
                  "system": "System", "tool": "Tool"}
    for dt, role, text in msgs:
        label = role_label.get(role, role.capitalize() if role else "?")
        out.append(f"[{fmt(dt)}] {label}:")
        out.append(text)
        out.append("")
    return "\n".join(out) + "\n"


def exports_fallback(agent):
    d = os.path.join(HERE, "exports", agent)
    os.makedirs(d, exist_ok=True)
    return d


def write_export(agent, cwd, sid, start, msgs, key, mtime, size, state):
    """Dedupe + render + write one conversation export. Shared by the file
    parser and the opencode SQLite ingestion."""
    prev = state.get(key)
    if prev and prev.get("mtime") == mtime and prev.get("size") == size:
        return False  # unchanged

    if not msgs:
        state[key] = {"mtime": mtime, "size": size, "out": None}
        return False

    out_dir = os.path.join(OUTPUT_ROOT, agent)
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError:
        out_dir = exports_fallback(agent)
    # Filename: just the bottom-three path components (agent is implied by
    # the containing folder). Same project -> same file (latest wins).
    fname = path_tail(cwd) + ".txt"
    out_path = os.path.join(out_dir, fname)
    text = render(agent, cwd, sid, start, msgs)
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as e2:
        out_dir = exports_fallback(agent)
        out_path = os.path.join(out_dir, fname)
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as e3:
            log(f"[write-error] {out_path}: {e3}")
            return False

    state[key] = {"mtime": mtime, "size": size, "out": out_path}
    log(f"[write] {agent} -> {out_path} ({len(msgs)} msgs)")
    return True


def process_file(watch, path, state):
    try:
        st = os.stat(path)
    except OSError:
        return
    key = os.path.abspath(path)
    try:
        cwd, sid, start, msgs = watch["parser"](path)
    except Exception as e:
        log(f"[parse-error] {path}: {e}")
        state[key] = {"mtime": st.st_mtime, "size": st.st_size, "out": None}
        return
    write_export(watch["agent"], cwd, sid, start, msgs,
                 key, st.st_mtime, st.st_size, state)


def _opencode_db_path():
    for p in (os.path.expanduser("~/.local/share/opencode/opencode.db"),
              os.path.expanduser("~/.config/opencode/opencode.db")):
        if os.path.exists(p):
            return p
    return None


def ingest_opencode_db(watch, state):
    """opencode (newer versions) stores sessions in a SQLite database rather
    than JSONL. Each `session` has a `directory` (the project cwd); its
    messages live in `message` and the actual text in `part` rows. We keep
    `text` parts (user/assistant content) and drop `reasoning` / `tool` /
    `step-*` parts, matching the other parsers."""
    dbp = _opencode_db_path()
    if not dbp:
        return
    agent = watch["agent"]
    try:
        con = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
    except Exception as e:
        log(f"[opencode-db] cannot open {dbp}: {e}")
        return
    try:
        cur = con.cursor()
        # Newest session per project wins (scanned last -> overwrites file).
        cur.execute("SELECT id, directory, time_created, time_updated "
                    "FROM session ORDER BY time_updated DESC")
        for sid, directory, tcreate, tupdate in cur.fetchall():
            cwd = directory
            if not cwd:
                continue
            msgs = []
            cur2 = con.cursor()
            cur2.execute("SELECT id, data, time_created FROM message "
                         "WHERE session_id=? ORDER BY time_created ASC", (sid,))
            for mid, mdata, mtc in cur2.fetchall():
                try:
                    m = json.loads(mdata)
                except Exception:
                    continue
                role = m.get("role")
                if role not in ("user", "assistant"):
                    continue
                cur3 = con.cursor()
                cur3.execute("SELECT data FROM part WHERE message_id=? "
                             "ORDER BY time_created ASC", (mid,))
                chunks = []
                for (pdata,) in cur3.fetchall():
                    try:
                        p = json.loads(pdata)
                    except Exception:
                        continue
                    if p.get("type") == "text":
                        t = p.get("text")
                        if t:
                            chunks.append(t)
                text = "\n".join(chunks).strip()
                if not text:
                    continue
                ts = (m.get("time") or {}).get("created") or mtc
                msgs.append((parse_ts(ts), role, text))
            key = f"opencode-db:{sid}"
            mtime = (tupdate or tcreate or 0) / 1000.0
            write_export(agent, cwd, sid, parse_ts(tcreate), msgs,
                         key, mtime, len(msgs), state)
    finally:
        con.close()


def scan_once(state):
    for watch in AGENTS:
        if watch.get("source") == "opencode_db":
            try:
                ingest_opencode_db(watch, state)
            except Exception as e:
                log(f"[opencode-db error] {e}")
        for pattern in watch["globs"]:
            for path in glob.glob(pattern, recursive=True):
                parts = path.replace("\\", "/").split("/")
                if any(p in IGNORE_DIR_PARTS for p in parts):
                    continue
                try:
                    process_file(watch, path, state)
                except Exception as e:
                    log(f"[error] {path}: {e}")


# --------------------------------------------------------------------------
# Logging / state
# --------------------------------------------------------------------------
def log(msg):
    line = f"{fmt(datetime.now(timezone.utc) + BJ)} {msg}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Daemon loop
# --------------------------------------------------------------------------
def run_loop():
    log("daemon started")
    state = load_state()
    try:
        while True:
            scan_once(state)
            save_state(state)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        save_state(state)
        log("daemon stopped")


# --------------------------------------------------------------------------
# Control: start / stop / status / install / uninstall
# --------------------------------------------------------------------------
# Hide the console window for child console processes (e.g. when the GUI runs
# under pythonw and spawns tasklist / powershell / taskkill). 0 on non-Windows.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _detach_streams():
    """When launched by pythonw (no console), stdout/stderr/stdin are None.
    Redirect them so print()/input() never crash and messages land in the log."""
    if sys.stdout is None:
        try:
            sys.stdout = open(LOG_FILE, "a", encoding="utf-8")
        except Exception:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        try:
            sys.stderr = open(LOG_FILE, "a", encoding="utf-8")
        except Exception:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")


def run_cmd(args):
    """Run a command, decode output safely (Windows consoles are GBK)."""
    try:
        r = subprocess.run(args, capture_output=True,
                           creationflags=CREATE_NO_WINDOW)
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        return r.returncode, out, err
    except Exception as e:  # pragma: no cover
        return -1, "", str(e)


def pythonw_exe():
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    return exe


def write_pid(pid):
    try:
        with open(PID_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(pid))
    except OSError:
        pass


_inst_handle = None  # named-mutex handle, kept open for the process lifetime


def _acquire_run_lock():
    """Guarantee a single daemon via a named mutex (Windows). Returns True if
    we own the instance, False if another agentlog daemon is already running.

    This is a hard guarantee independent of the pid file, so concurrent
    launches (e.g. the Startup shortcut firing while `start` runs) can never
    spin up two daemons, and stale pid files can't cause duplicates either."""
    global _inst_handle
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool,
                                          ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        mutex = kernel32.CreateMutexW(None, False,
                                      "Local\\agentlog.single.instance")
        if not mutex:
            return True  # cannot lock -> allow (best effort)
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(mutex)
            return False
        _inst_handle = mutex  # released automatically when the process exits
        return True
    except Exception:
        return True


def read_pid():
    try:
        with open(PID_FILE, encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def is_running(pid):
    """True if a process with this pid is still alive. Uses a lightweight
    ctypes check (no external process) so the GUI's status poll every few
    seconds does NOT spawn a visible console window."""
    if not pid:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD,
                                         ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
        kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE,
                                                 ctypes.wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = ctypes.wintypes.DWORD
        kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
        # PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE
        h = kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)
        if not h:
            return False
        try:
            # WAIT_TIMEOUT (258) => still running; WAIT_OBJECT_0 (0) => exited
            return kernel32.WaitForSingleObject(h, 0) == 0x00000102
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        # Fallback: ask tasklist (rarely hit on Windows).
        try:
            rc, out, _ = run_cmd(["tasklist", "/FI", f"PID eq {pid}"])
            return rc == 0 and str(pid) in out
        except Exception:
            return False


def _find_agentlog_pids():
    """Return pids of python/pythonw processes running the *daemon*
    (agentlog.py). Explicitly excludes the GUI (agentlog_gui.py) so that
    stopping/restarting the daemon never kills the GUI itself. Uses PowerShell
    (hidden window) and falls back to the pid-file pid when PowerShell is
    unavailable."""
    pids = []
    try:
        rc, out, _ = run_cmd([
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "Get-CimInstance Win32_Process -Filter \"name='pythonw.exe' or "
            "name='python.exe'\" | Where-Object { $_.CommandLine -like "
            "'*agentlog.py*' -and $_.CommandLine -notlike '*agentlog_gui.py*' } "
            "| ForEach-Object { $_.ProcessId }"
        ])
        if rc == 0:
            for tok in out.split():
                tok = tok.strip()
                if tok.isdigit():
                    pids.append(int(tok))
    except Exception:
        pass
    if not pids:
        pid = read_pid()
        if pid and is_running(pid):
            pids.append(pid)
    return pids


def _kill_all_agentlog():
    """Kill every python/pythonw process whose command line runs agentlog.py
    (handles strays / duplicates). Falls back to the pid-file pid."""
    pids = []
    for pid in _find_agentlog_pids():
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
        pids.append(str(pid))
    if not pids:
        pid = read_pid()
        if pid and is_running(pid):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
            pids.append(str(pid))
    return pids


def cmd_start():
    pid = read_pid()
    if is_running(pid):
        print(f"agentlog is already running (pid {pid}).")
        if sys.stdin.isatty():
            try:
                while True:
                    r = input(
                        "A daemon is already running. "
                        "Restart it (kill the old process and start a fresh one) "
                        "[r], or cancel this run [c]? "
                    ).strip().lower()
                    if r in ("r", "restart"):
                        _kill_all_agentlog()
                        time.sleep(1)
                        break
                    if r in ("c", "cancel"):
                        print("cancelled; the existing daemon keeps running.")
                        return
                    print("Please enter 'r' to restart or 'c' to cancel.")
            except (EOFError, KeyboardInterrupt):
                print("\ncancelled; the existing daemon keeps running.")
                return
        else:
            print("Non-interactive shell: leaving the existing daemon running. "
                  "Re-run in a terminal to restart, or use `stop` then `start`.")
            return
    exe = pythonw_exe()
    script = os.path.abspath(__file__)
    log("launching background daemon")
    subprocess.Popen([exe, script, "run"],
                     creationflags=0x00000008)  # DETACHED_PROCESS
    # give it a moment, then record pid via tasklist? We can't easily get pid,
    # so we rely on the run loop writing the pidfile itself.
    time.sleep(1)
    print("agentlog started in background.")


def cmd_stop():
    pids = _kill_all_agentlog()
    if pids:
        print(f"stopped pid(s): {', '.join(pids)}.")
    else:
        print("no agentlog daemon process found.")
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def cmd_status():
    pid = read_pid()
    running = is_running(pid)
    print(f"running: {running}" + (f" (pid {pid})" if pid else ""))
    print(f"log: {LOG_FILE}")
    print(f"state: {STATE_FILE}")


def run_key():
    return r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_folder():
    """User's per-user Startup folder (no admin needed, survives on this box)."""
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                        "Start Menu", "Programs", "Startup")


def startup_lnk_path():
    return os.path.join(startup_folder(), "agentlog.lnk")


# --------------------------------------------------------------------------
# .lnk shortcut creation via ctypes/IShellLink (stdlib only, no pywin32).
# --------------------------------------------------------------------------
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]
    def __init__(self, s):
        u = uuid.UUID(s)
        super().__init__()
        self.Data1 = u.time_low
        self.Data2 = u.time_mid
        self.Data3 = u.time_hi_version
        self.Data4 = (ctypes.c_ubyte * 8)(*u.bytes[8:])


_CLSID_ShellLink = _GUID("{00021401-0000-0000-C000-000000000046}")
_IID_IShellLinkW = _GUID("{000214F9-0000-0000-C000-000000000046}")
_IID_IPersistFile = _GUID("{0000010B-0000-0000-C000-000000000046}")
_CLSCTX_INPROC_SERVER = 0x1


class _IShellLinkWVtbl(ctypes.Structure):
    _fields_ = [("QueryInterface", ctypes.c_void_p), ("AddRef", ctypes.c_void_p),
                ("Release", ctypes.c_void_p), ("GetPath", ctypes.c_void_p),
                ("GetIDList", ctypes.c_void_p), ("SetIDList", ctypes.c_void_p),
                ("GetDescription", ctypes.c_void_p), ("SetDescription", ctypes.c_void_p),
                ("GetWorkingDirectory", ctypes.c_void_p), ("SetWorkingDirectory", ctypes.c_void_p),
                ("GetArguments", ctypes.c_void_p), ("SetArguments", ctypes.c_void_p),
                ("GetHotkey", ctypes.c_void_p), ("SetHotkey", ctypes.c_void_p),
                ("GetShowCmd", ctypes.c_void_p), ("SetShowCmd", ctypes.c_void_p),
                ("GetIconLocation", ctypes.c_void_p), ("SetIconLocation", ctypes.c_void_p),
                ("SetRelativePath", ctypes.c_void_p), ("Resolve", ctypes.c_void_p),
                ("SetPath", ctypes.c_void_p)]


class _IShellLinkW(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(_IShellLinkWVtbl))]


class _IPersistFileVtbl(ctypes.Structure):
    _fields_ = [("QueryInterface", ctypes.c_void_p), ("AddRef", ctypes.c_void_p),
                ("Release", ctypes.c_void_p), ("GetClassID", ctypes.c_void_p),
                ("IsDirty", ctypes.c_void_p), ("Load", ctypes.c_void_p),
                ("Save", ctypes.c_void_p), ("SaveCompleted", ctypes.c_void_p),
                ("GetCurFile", ctypes.c_void_p)]


class _IPersistFile(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(_IPersistFileVtbl))]


def create_shortcut(lnk_path, target, args="", workdir="", icon=""):
    """Create a .lnk shortcut. Windows-only, stdlib-only (ctypes COM)."""
    ole32 = ctypes.OleDLL("ole32")
    ole32.CoInitialize(None)
    try:
        shell_link = ctypes.POINTER(_IShellLinkW)()
        hr = ole32.CoCreateInstance(ctypes.byref(_CLSID_ShellLink), None,
                                    _CLSCTX_INPROC_SERVER,
                                    ctypes.byref(_IID_IShellLinkW),
                                    ctypes.byref(shell_link))
        if hr != 0:
            raise ctypes.WinError(hr)
        vtbl = shell_link.contents.lpVtbl
        def m(idx):
            return ctypes.cast(getattr(vtbl.contents, idx), ctypes.WINFUNCTYPE(
                wt.LONG, ctypes.POINTER(_IShellLinkW), ctypes.c_wchar_p))
        m("SetPath")(shell_link, target)
        if args:
            m("SetArguments")(shell_link, args)
        if workdir:
            m("SetWorkingDirectory")(shell_link, workdir)
        if icon:
            # SetIconLocation(LPCWSTR pszIconPath, int iIcon)
            set_icon = ctypes.cast(vtbl.contents.SetIconLocation,
                                   ctypes.WINFUNCTYPE(wt.LONG,
                                                     ctypes.POINTER(_IShellLinkW),
                                                     ctypes.c_wchar_p, wt.INT))
            set_icon(shell_link, icon, 0)
        ppv = ctypes.c_void_p()
        QI = ctypes.cast(vtbl.contents.QueryInterface, ctypes.WINFUNCTYPE(
            wt.LONG, ctypes.POINTER(_IShellLinkW), ctypes.POINTER(_GUID),
            ctypes.POINTER(ctypes.c_void_p)))
        hr = QI(shell_link, ctypes.byref(_IID_IPersistFile), ctypes.byref(ppv))
        if hr != 0:
            raise ctypes.WinError(hr)
        pf = ctypes.cast(ppv, ctypes.POINTER(_IPersistFile))
        pf_vtbl = pf.contents.lpVtbl
        Save = ctypes.cast(pf_vtbl.contents.Save, ctypes.WINFUNCTYPE(
            wt.LONG, ctypes.POINTER(_IPersistFile), ctypes.c_wchar_p, wt.BOOL))
        hr = Save(pf, lnk_path, True)
        if hr != 0:
            raise ctypes.WinError(hr)
        Release = ctypes.cast(pf_vtbl.contents.Release,
                              ctypes.WINFUNCTYPE(wt.ULONG, ctypes.POINTER(_IPersistFile)))
        Release(pf)
        Release2 = ctypes.cast(vtbl.contents.Release,
                               ctypes.WINFUNCTYPE(wt.ULONG, ctypes.POINTER(_IShellLinkW)))
        Release2(shell_link)
    finally:
        ole32.CoUninitialize()


def _set_autostart(value):
    """Write the Run-key value via winreg (avoids reg.exe quote quirks)."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key(),
                        0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "agentlog", 0, winreg.REG_SZ, value)


def _del_autostart():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key(),
                        0, winreg.KEY_SET_VALUE) as k:
        winreg.DeleteValue(k, "agentlog")


def cmd_install():
    exe = pythonw_exe()
    script = os.path.abspath(__file__)
    # A .lnk in the per-user Startup folder is the primary autostart channel:
    # it needs no admin and (unlike the Run key) is not swept by startup
    # managers such as Lenovo's. The shortcut launches `start` (idempotent).
    lnk = startup_lnk_path()
    try:
        create_shortcut(lnk, exe, f'"{script}" start', os.path.dirname(script))
        print(f"autostart enabled via Startup shortcut: {lnk}")
    except Exception as e:
        print("could not create Startup shortcut:", e)
        # Fall back to the registry Run key for machines where the Startup
        # folder is read-only.
        try:
            _set_autostart(f'"{exe}" "{script}" start')
            print("autostart enabled via registry Run key (no admin needed).")
        except Exception as e2:
            print("could not set autostart:", e2)
    cmd_start()


def cmd_uninstall():
    cmd_stop()
    try:
        os.remove(startup_lnk_path())
        print("Startup shortcut removed.")
    except OSError:
        pass
    try:
        _del_autostart()
        print("registry autostart removed.")
    except FileNotFoundError:
        pass


def gui_exe_and_script():
    exe = pythonw_exe()
    script = os.path.join(HERE, "agentlog_gui.py")
    return exe, script


def cmd_gui_shortcut():
    """Create a double-click launcher for the GUI: a .lnk that runs
    `pythonw agentlog_gui.py` (no console window) with the agentlog icon.
    Places one next to this repo and one on the Desktop."""
    exe, script = gui_exe_and_script()
    icon = ICON_PATH if os.path.exists(ICON_PATH) else ""
    targets = [os.path.join(HERE, "agentlog-gui.lnk")]
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        targets.append(os.path.join(desktop, "agentlog 界面.lnk"))
    created = []
    for lnk in targets:
        try:
            create_shortcut(lnk, exe, f'"{script}"', HERE, icon)
            created.append(lnk)
        except Exception as e:
            print(f"could not create shortcut {lnk}: {e}")
    if created:
        print("GUI launcher created (double-click to run, no console):")
        for p in created:
            print("  ", p)
    else:
        print("no GUI launcher created.")


def build_icon(src, dest=ICON_PATH, sizes=(16, 32, 48, 64, 128, 256)):
    """Render a source image (any format Pillow reads) into a multi-size
    .ico for the tray / window / shortcut. Needs Pillow; this is a build-time
    helper, not required at runtime (the .ico is shipped)."""
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is required to build the icon: pip install Pillow")
        return False
    im = Image.open(src).convert("RGBA")
    # center-crop to a square, then fit the largest size
    w, h = im.size
    s = min(w, h)
    left = (w - s) // 2
    top = (h - s) // 2
    im = im.crop((left, top, left + s, top + s)).resize((max(sizes),) * 2,
                                                         Image.LANCZOS)
    im.save(dest, format="ICO", sizes=[(sz, sz) for sz in sizes])
    print("icon written:", dest)
    return True


def cmd_build_icon(src):
    if not src:
        print("usage: python agentlog.py build-icon <path-to-image>")
        return
    if not os.path.exists(src):
        print("image not found:", src)
        return
    build_icon(src)


def main():
    _detach_streams()
    ap = argparse.ArgumentParser(description="Capture CLI agent conversations.")
    ap.add_argument("action", nargs="?", default="once",
                    choices=["run", "once", "start", "stop",
                             "status", "install", "uninstall",
                             "gui-shortcut", "build-icon"])
    ap.add_argument("path", nargs="?", default="")
    args = ap.parse_args()

    if args.action == "run":
        if not _acquire_run_lock():
            print("another agentlog daemon is already running; exiting.")
            sys.exit(0)
        write_pid(os.getpid())
        run_loop()
    elif args.action == "once":
        scan_once(load_state())
        print("scan complete. see log:", LOG_FILE)
    elif args.action == "start":
        cmd_start()
    elif args.action == "stop":
        cmd_stop()
    elif args.action == "status":
        cmd_status()
    elif args.action == "install":
        cmd_install()
    elif args.action == "uninstall":
        cmd_uninstall()
    elif args.action == "gui-shortcut":
        cmd_gui_shortcut()
    elif args.action == "build-icon":
        cmd_build_icon(args.path)


if __name__ == "__main__":
    main()
