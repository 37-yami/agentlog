"""Claude Code transcript parser."""
import json
import os

from ..extract import extract_text, resolve_cwd
from ..timeutil import parse_ts


def parse_claude(path):
    """Returns (cwd, session_id, title, start_dt, [(dt, role, text, thinking), ...]).

    Claude Code transcripts carry cwd / sessionId on every event line and no
    conversation title; the grouping layer falls back to the first real user
    message (see agentlog.extract.first_user_title)."""
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
            # isMeta marks system-injected user lines (<local-command-caveat>,
            # <ide_opened_file> etc.) - not real conversation turns.
            if o.get("isMeta"):
                continue
            msg = o.get("message") or {}
            role = msg.get("role") or o.get("role")
            if role not in ("user", "assistant"):
                continue
            text, thinking = extract_text(msg.get("content"))
            if not text and not thinking:
                continue
            msgs.append((parse_ts(o.get("timestamp")), role, text, thinking))
    return resolve_cwd(raw_cwd, folder), sid, None, start, msgs
