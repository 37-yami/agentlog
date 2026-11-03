"""codebuddy transcript parser."""
import json
import os

from ..config import DROP_PRE_TOOL_ASSISTANT
from ..extract import extract_text, resolve_cwd
from ..timeutil import parse_ts


def parse_codebuddy(path):
    """Returns (cwd, session_id, title, start_dt, [(dt, role, text, thinking), ...])."""
    folder = os.path.basename(os.path.dirname(path))
    raw_cwd = None
    sid = None
    title = None
    start = None
    raw = []  # (kind, text, thinking, dt)  kind in {user, assistant, tool}
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
            if t == "ai-title":           # agent-generated conversation title
                title = title or o.get("aiTitle")
                continue
            if t == "message":
                role = o.get("role")
                if role == "user":
                    text, thinking = extract_text(o.get("content"))
                    raw.append(("user", text, thinking,
                                parse_ts(o.get("timestamp"))))
                elif role == "assistant":
                    text, thinking = extract_text(o.get("content"))
                    raw.append(("assistant", text, thinking,
                                parse_ts(o.get("timestamp"))))
                else:
                    continue
                raw_cwd = raw_cwd or o.get("cwd")
                sid = sid or o.get("sessionId")
                start = start or parse_ts(o.get("timestamp"))
            elif t in ("function_call", "function_call_result",
                       "tool_use", "tool_result"):
                raw.append(("tool", "", "", None))
            else:
                # file-history-snapshot, etc.
                start = start or parse_ts(o.get("timestamp"))
                raw_cwd = raw_cwd or o.get("cwd")
    msgs = []
    n = len(raw)
    for i, (kind, text, thinking, dt) in enumerate(raw):
        if kind == "tool":
            continue
        if kind == "assistant" and DROP_PRE_TOOL_ASSISTANT:
            nxt = raw[i + 1][0] if i + 1 < n else None
            if nxt == "tool":          # planning aloud before acting -> drop
                continue
        if not text and not thinking:
            continue
        msgs.append((dt, kind, text, thinking))
    return resolve_cwd(raw_cwd, folder), sid, title, start, msgs
