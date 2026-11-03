"""Time parsing/formatting and path helpers."""
import re
from datetime import datetime, timezone

from .config import BJ


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
