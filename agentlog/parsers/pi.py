"""pi transcript parser."""
import json
import os

from ..extract import extract_text, resolve_cwd
from ..timeutil import parse_ts


def parse_pi(path):
    """Returns (cwd, session_id, title, start_dt, [(dt, role, text, thinking), ...])."""
    folder = os.path.basename(os.path.dirname(path))
    raw_cwd = None
    sid = None
    title = None
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
                title = title or o.get("title")
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
                text, thinking = extract_text(m.get("content"))
                if not text and not thinking:
                    continue
                msgs.append((parse_ts(o.get("timestamp")), role, text, thinking))
    return resolve_cwd(raw_cwd, folder), sid, title, start, msgs
