"""Content extraction: message blocks, cwd decoding and title helpers."""
import os
import re

from .config import SKIP_TYPES, THINK_TYPES


def extract_text(content):
    """Extract prose text and thinking/reasoning text from a content value.

    Thinking blocks are always collected into the returned `thinking` string;
    callers decide at render time (include_thinking flag in render_txt etc.)
    whether to keep it.

    Returns:
        Tuple of (text, thinking) where thinking is content from THINK_TYPES
    """
    if content is None:
        return "", ""
    if isinstance(content, str):
        return content.strip(), ""
    if isinstance(content, list):
        parts = []
        thinking_parts = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
                continue
            if not isinstance(b, dict):
                continue
            bt = (b.get("type") or "").lower()
            # Skip non-text block types (tool calls, etc.)
            if bt in SKIP_TYPES and bt not in THINK_TYPES:
                continue
            txt = (b.get("text") or b.get("content") or b.get("thinking")
                   or b.get("reasoning") or b.get("reasoning_content") or "")
            nested_thinking = ""
            if isinstance(txt, list):  # nested
                txt, nested_thinking = extract_text(txt)
            if nested_thinking:
                thinking_parts.append(nested_thinking)
            if txt:
                if bt in THINK_TYPES:
                    thinking_parts.append(str(txt).strip())
                else:
                    parts.append(str(txt).strip())
        text = "\n".join(p for p in parts if p).strip()
        thinking = "\n".join(p for p in thinking_parts if p).strip()
        return text, thinking
    return "", ""


def clean_title(s, limit=40):
    """Make a conversation title safe (and short enough) for a filename."""
    if not s:
        return ""
    s = re.sub(r"\s+", " ", str(s)).strip()
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", s).strip()
    return s[:limit].rstrip(" -.")


def first_user_title(msgs, limit=40):
    """Fallback group title: the beginning of the first real user message.
    Forked/resumed sessions copy the history, so their first user message
    matches the original and they merge into one group. Lines starting with
    '<' are agent/system injections (<local-command-caveat>, attachments),
    not user prose."""
    for _dt, role, text, _th in msgs:
        if role == "user" and text and not text.lstrip().startswith("<"):
            return clean_title(text, limit)
    return ""


# Connectivity probes (superpowers' "Reply with exactly: OK" sessions) are
# the only known 1-question/1-tiny-reply noise; matching the exact phrase
# keeps genuinely short real conversations (Q + short A) safe.
PROBE_USER_PREFIX = "reply with exactly"


def is_probe(msgs):
    """True for connectivity-probe conversations (1 question + 1 reply where
    the question starts with the known probe phrase)."""
    if len(msgs) != 2 or msgs[0][1] != "user" or msgs[1][1] != "assistant":
        return False
    return msgs[0][2].strip().lower().startswith(PROBE_USER_PREFIX)


def looks_internal(path):
    """Avoid writing exports into the agents' own config dirs."""
    low = path.replace("/", "\\").lower()
    for tok in (".codebuddy", ".claude", ".pi", ".config\\opencode",
                ".local\\share\\opencode", "appdata\\roaming\\npm"):
        if tok in low:
            return True
    return False


def decode_folder_cwd(folder):
    """Decode an agent's encoded project folder name back to an absolute path.

    Handles two common encodings (both are lossy: a '-' inside a real path
    component cannot be told apart from an encoded separator):
      - pi style:               '--E--other--project--' -> E:\\other\\project
        ("--" separates components, so '--E--other-project--' decodes to
        E:\\other-project - one single directory named "other-project")
      - codebuddy/claude style: 'e-lab-Group_meeting-202608' -> E:\\lab\\Group_meeting\\202608
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
