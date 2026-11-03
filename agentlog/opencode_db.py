"""opencode SQLite ingestion (newer builds store sessions in a database)."""
import json
import os
import re
import sqlite3

from . import config
from .capture import covered_key, norm_cwd
from .config import DROP_PRE_TOOL_ASSISTANT
from .export import cleanup_exports, write_export
from .extract import clean_title, first_user_title, is_probe
from .timeutil import parse_ts, path_tail

FORK_SUFFIX_RE = re.compile(r"\s*\(fork\s*#\d+\)\s*$")


def log(msg):
    """Lazy forward to daemon.log (daemon imports this module at top level,
    so importing daemon back here at import time would be circular)."""
    from .daemon import log as _log
    return _log(msg)


def opencode_db_path():
    for p in (os.path.expanduser("~/.local/share/opencode/opencode.db"),
              os.path.expanduser("~/.config/opencode/opencode.db")):
        if os.path.exists(p):
            return p
    return None


def _append_db_message(msgs, m, role, chunks, other_parts, has_tool, mtc):
    text = "\n".join(chunks).strip()
    # opencode keeps "narrating while acting" texts in the same
    # assistant message as the tool parts; the final answer comes
    # as a plain text message without tools. Mirror the
    # DROP_PRE_TOOL_ASSISTANT rule here.
    if role == "assistant" and DROP_PRE_TOOL_ASSISTANT and has_tool:
        text = ""
    thinking = "\n".join(other_parts).strip()
    if not text:
        return
    ts = (m.get("time") or {}).get("created") or mtc
    msgs.append((parse_ts(ts), role, text, thinking))


def _read_legacy_session_messages(con, sid):
    """Messages from the legacy `message`/`part` tables."""
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
        chunks = []
        other_parts = []
        has_tool = False
        cur3 = con.cursor()
        cur3.execute("SELECT data FROM part WHERE message_id=? "
                     "ORDER BY time_created ASC", (mid,))
        for (pdata,) in cur3.fetchall():
            try:
                p = json.loads(pdata)
            except Exception:
                continue
            ptype = p.get("type")
            if ptype == "text":
                t = p.get("text")
                if t:
                    chunks.append(t)
            else:
                if ptype and ptype.startswith("tool"):
                    has_tool = True
                # All non-text parts go to thinking (reasoning, tool, step-start, etc.)
                t = p.get("text") or p.get("reasoning") or p.get("tool") or ""
                if t:
                    if isinstance(t, dict):
                        t = json.dumps(t, ensure_ascii=False)
                    other_parts.append(f"[{ptype}] {t}")
        _append_db_message(msgs, m, role, chunks, other_parts, has_tool, mtc)
    return msgs


def _read_session_v2_messages(con, sid):
    """Messages from the newer event-sourced `session_message` table, where
    user text and assistant content parts are embedded in the message JSON."""
    msgs = []
    cur2 = con.cursor()
    cur2.execute("SELECT type, data, time_created FROM session_message "
                 "WHERE session_id=? ORDER BY seq ASC", (sid,))
    for mtype, mdata, mtc in cur2.fetchall():
        if mtype not in ("user", "assistant"):
            continue
        try:
            m = json.loads(mdata)
        except Exception:
            continue
        chunks = []
        other_parts = []
        has_tool = False
        if mtype == "user":
            t = m.get("text")
            if t:
                chunks.append(t)
        else:
            for p in m.get("content") or []:
                if not isinstance(p, dict):
                    continue
                ptype = p.get("type")
                if ptype == "text":
                    t = p.get("text")
                    if t:
                        chunks.append(t)
                    continue
                if ptype == "tool":
                    has_tool = True
                    st = p.get("state") or {}
                    t = st.get("input") or ""
                else:
                    t = p.get("text") or p.get("reasoning") or ""
                if t:
                    if isinstance(t, (dict, list)):
                        t = json.dumps(t, ensure_ascii=False)
                    other_parts.append(f"[{ptype}] {t}")
        _append_db_message(msgs, m, mtype, chunks, other_parts, has_tool, mtc)
    return msgs


def ingest_opencode_db(watch, state):
    """opencode (newer versions) stores sessions in a SQLite database rather
    than JSONL. Each session row has a `directory` (the project cwd). Two
    schema generations exist: the legacy `session`/`message`/`part` tables
    and (since late-2026 builds) the event-sourced `session_v2` +
    `session_message` tables. We read both; on duplicate session ids
    session_v2 wins since it holds the migrated history plus all newer
    messages. Sessions are merged per (project, conversation title).
    Sub-agent sessions (parent_id) and connectivity probes are skipped."""
    dbp = opencode_db_path()
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
        # Sub-agent sessions (parent_id set, e.g. @explore tasks) are internal
        # chatter - the task prompt shows up as a fake "user" message - so only
        # top-level sessions are exported. sid -> [directory, title, tcreate, tupdate, src]
        sessions = {}
        try:
            cur.execute("SELECT id, directory, title, time_created, time_updated "
                        "FROM session WHERE parent_id IS NULL")
            for sid, directory, title, tcreate, tupdate in cur.fetchall():
                sessions[sid] = [directory, title, tcreate, tupdate, "legacy"]
        except sqlite3.OperationalError as e:
            log(f"[opencode-db] legacy session table unavailable: {e}")
        try:
            cur.execute("SELECT id, directory, title, time_created, time_updated "
                        "FROM session_v2 WHERE parent_id IS NULL")
            for sid, directory, title, tcreate, tupdate in cur.fetchall():
                sessions[sid] = [directory, title, tcreate, tupdate, "v2"]
        except sqlite3.OperationalError:
            pass
        # Aggregate per (project, conversation): opencode's UI lets one project
        # run several parallel conversations (browser-like tabs), each with its
        # own title, while forks/compactions clone the history under a new
        # session id with the SAME title (plus a "(fork #N)" suffix). Grouping
        # by the suffix-stripped title therefore merges exactly one
        # conversation and keeps parallel tabs as separate exports.
        groups = {}
        for sid, (cwd, title, tcreate, tupdate, src) in sessions.items():
            if not cwd:
                continue
            cwd = norm_cwd(cwd)  # 'E:/a' vs 'E:\\a' must aggregate together
            if src == "v2":
                msgs = _read_session_v2_messages(con, sid)
            else:
                msgs = _read_legacy_session_messages(con, sid)
            if not msgs:
                continue
            # Skip connectivity probes (e.g. skills that send "Reply with
            # exactly: OK"): the exact probe phrase, not just any short chat.
            if is_probe(msgs):
                continue
            base_title = FORK_SUFFIX_RE.sub("", (title or "").strip())
            gtitle = clean_title(base_title) or first_user_title(msgs)
            if not gtitle:
                continue
            g = groups.setdefault((cwd, gtitle),
                                  {"msgs": [], "tupdate": 0, "tcreate": None})
            g["msgs"].extend(msgs)
            g["tupdate"] = max(g["tupdate"], tupdate or tcreate or 0)
            if tcreate and (g["tcreate"] is None or tcreate < g["tcreate"]):
                g["tcreate"] = tcreate
        keep = {}
        for (cwd, gtitle), g in groups.items():
            # proxy capture takes precedence for covered projects
            if covered_key(agent, cwd) in state.get("_capture_covered", {}):
                continue
            # Dedupe messages copied into forked/compacted sessions, then
            # order chronologically.
            seen = set()
            uniq = []
            for ts, role, text, thinking in g["msgs"]:
                k = (ts, role, text)
                if k in seen:
                    continue
                seen.add(k)
                uniq.append((ts, role, text, thinking))
            uniq.sort(key=lambda m: (m[0] is None, m[0]))
            key = f"opencode-db:grp:{cwd}|{gtitle}"
            mtime = g["tupdate"] / 1000.0
            write_export(agent, cwd, None, parse_ts(g["tcreate"]), uniq,
                         key, mtime, len(uniq), state, title=gtitle,
                         include_thinking=config.INCLUDE_THINKING,
                         output_formats=config.OUTPUT_FORMATS)
            keep.setdefault(cwd, set()).add(f"{path_tail(cwd)}-{gtitle}")
        for cwd in {c for (c, _t) in groups}:
            cleanup_exports(agent, cwd, keep.get(cwd, set()), log=log)
    finally:
        con.close()
