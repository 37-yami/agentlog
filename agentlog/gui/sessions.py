"""Session-list data collection (opencode SQLite + JSONL agents).

Pure data layer, no tkinter - directly unit-testable.
"""
import os
import sqlite3
from datetime import datetime

from .. import extract as _extract
from ..opencode_db import FORK_SUFFIX_RE, opencode_db_path
from ..parsers import parse_claude, parse_codebuddy, parse_pi
from ..timeutil import parse_ts

# (agent, project, title, sid_short, start_time, update_time, msg_count, full_sid, cwd)
Session = tuple


def collect_sessions(sel_agent="全部", search_key=""):
    """Return all known sessions across agents, newest first."""
    sessions = []
    agents_set = set()

    # 1. opencode sessions from the SQLite database
    db_path = opencode_db_path()
    if db_path and os.path.exists(db_path):
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = con.cursor()
            # sid -> (directory, title, tcreate, tupdate, source); v2 wins
            db_sessions = {}
            try:
                cur.execute("""
                    SELECT id, directory, title, time_created, time_updated
                    FROM session_v2
                    WHERE directory IS NOT NULL AND parent_id IS NULL
                """)
                for sid, directory, title, tcreate, tupdate in cur.fetchall():
                    db_sessions[sid] = (directory, title, tcreate, tupdate, "v2")
            except sqlite3.OperationalError:
                pass
            try:
                cur.execute("""
                    SELECT id, directory, title, time_created, time_updated
                    FROM session
                    WHERE directory IS NOT NULL AND parent_id IS NULL
                """)
                for sid, directory, title, tcreate, tupdate in cur.fetchall():
                    if sid not in db_sessions:
                        db_sessions[sid] = (directory, title, tcreate, tupdate, "legacy")
            except sqlite3.OperationalError:
                pass

            for sid, (directory, title, tcreate, tupdate, src) in \
                    sorted(db_sessions.items(), key=lambda kv: -(kv[1][3] or kv[1][2] or 0)):
                if not directory:
                    continue
                sid_clean = sid[4:] if sid.startswith("ses_") else sid
                sid_short = sid_clean[:8] if len(sid_clean) > 8 else sid_clean
                conv_title = _extract.clean_title(
                    FORK_SUFFIX_RE.sub("", (title or "").strip()))

                start_dt = parse_ts(tcreate)
                update_dt = parse_ts(tupdate)
                start_time = start_dt.strftime("%Y-%m-%d %H:%M") if start_dt else "unknown"
                update_time = update_dt.strftime("%Y-%m-%d %H:%M") if update_dt else "unknown"

                cur2 = con.cursor()
                if src == "v2":
                    cur2.execute("SELECT COUNT(*) FROM session_message "
                                 "WHERE session_id=? AND type IN ('user','assistant')", (sid,))
                else:
                    cur2.execute("SELECT COUNT(*) FROM message WHERE session_id=?", (sid,))
                msg_count = cur2.fetchone()[0]
                if msg_count == 0:
                    continue

                agent = "opencode"
                agents_set.add(agent)
                if sel_agent != "全部" and agent != sel_agent:
                    continue
                if search_key:
                    if (search_key not in directory.lower()
                            and search_key not in (conv_title or "").lower()
                            and search_key not in sid_short.lower()
                            and search_key not in agent.lower()):
                        continue
                sessions.append((agent, directory, conv_title, sid_short,
                                 start_time, update_time, msg_count, sid, directory))
            con.close()
        except Exception:
            pass

    # 2. JSONL agents (codebuddy, pi, claude)
    jsonl_agents = [
        ("codebuddy", os.path.expanduser("~/.codebuddy/projects"),
         parse_codebuddy),
        ("pi", os.path.expanduser("~/.pi/agent/sessions"), parse_pi),
        ("claude", os.path.expanduser("~/.claude/projects"), parse_claude),
    ]
    from ..config import IGNORE_DIR_PARTS
    for agent_name, base_dir, parser in jsonl_agents:
        if not os.path.isdir(base_dir):
            continue
        for root, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIR_PARTS]
            for fn in files:
                if not fn.endswith(".jsonl"):
                    continue
                full_path = os.path.join(root, fn)
                try:
                    cwd, sid, title, start_dt, msgs = parser(full_path)
                except Exception:
                    continue
                if not msgs:
                    continue
                if not sid:
                    sid = fn[:-6]  # strip ".jsonl"
                sid_clean = sid[4:] if sid.startswith("ses_") else sid
                sid_short = sid_clean[:8] if len(sid_clean) > 8 else sid_clean
                conv_title = _extract.clean_title(title) or _extract.first_user_title(msgs)
                try:
                    mtime = os.stat(full_path).st_mtime
                except OSError:
                    continue
                project = cwd or "unknown"
                start_time = start_dt.strftime("%Y-%m-%d %H:%M") if start_dt else "unknown"
                update_time = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                agents_set.add(agent_name)
                if sel_agent != "全部" and agent_name != sel_agent:
                    continue
                if search_key:
                    if (search_key not in project.lower()
                            and search_key not in (conv_title or "").lower()
                            and search_key not in sid_short.lower()
                            and search_key not in agent_name.lower()):
                        continue
                sessions.append((agent_name, project, conv_title, sid_short,
                                 start_time, update_time, len(msgs), sid,
                                 cwd or ""))

    sessions.sort(key=lambda x: x[5], reverse=True)
    return sessions, sorted(agents_set)
