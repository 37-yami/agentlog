"""Best-effort parser for any JSONL transcript that uses a
message/role/content shape (e.g. codex, mimo, or unknown agents)."""
import json
import os

from ..extract import extract_text, resolve_cwd
from ..timeutil import parse_ts


def parse_generic(path):
    """Returns (cwd, session_id, title, start_dt, [(dt, role, text, thinking), ...])."""
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
                text, thinking = extract_text(msg.get("content") if msg is not o else o.get("content"))
                if not text and not thinking:
                    continue
                msgs.append((parse_ts(o.get("timestamp") or msg.get("timestamp")), role, text, thinking))
    return resolve_cwd(raw_cwd, folder), sid, None, start, msgs
