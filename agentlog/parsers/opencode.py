"""opencode JSONL transcript parser (older storage; newer builds use SQLite)."""
import json
import os

from ..extract import extract_text, resolve_cwd
from ..timeutil import parse_ts


def parse_opencode(path):
    """Returns (cwd, session_id, title, start_dt, [(dt, role, text, thinking), ...])."""
    folder = os.path.basename(os.path.dirname(path))
    raw_cwd = None
    sid = None
    title = None
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
                title = title or o.get("title")
                continue
            msg = o.get("message") or {}
            role = o.get("role") or msg.get("role")
            if role in ("thinking", "reasoning", "system"):
                continue
            if role in ("user", "assistant"):
                text, thinking = extract_text(msg.get("content") if msg else o.get("content"))
                if not text and not thinking:
                    continue
                msgs.append((parse_ts(o.get("timestamp")), role, text, thinking))
    return resolve_cwd(raw_cwd, folder), sid, title, start, msgs
