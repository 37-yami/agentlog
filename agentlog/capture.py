"""Local routing proxy capture: normalized Q/A turns written by agentlog_proxy."""
import os

from . import config
from .config import HERE
from .export import cleanup_exports, write_export
from .timeutil import parse_ts, path_tail

CAPTURE_DIR = os.path.join(HERE, "capture")
PROXY_PORT = int(os.environ.get("AGENTLOG_PROXY_PORT", "8765"))

# Parsed daily capture files, keyed by path -> (size, mtime, groups).
_capture_cache = {}


def norm_cwd(s):
    """Normalize a working-directory string so the same project always maps
    to the same key regardless of separator style ('E:/a/b' vs 'E:\\a\\b')
    or a trailing slash. Platform-aware: POSIX paths keep forward slashes.
    Keep agentlog_proxy.norm_cwd in sync."""
    if not s:
        return s
    if os.name == "nt":
        s = s.replace("/", "\\")
        s = os.path.normpath(s)
        return s.rstrip("\\")
    s = os.path.normpath(s)
    return s


def covered_key(agent, cwd):
    """State key deciding whether the proxy capture suppresses the legacy
    scrape for one (agent, project). Case-insensitive: Windows paths."""
    return (agent + "|" + norm_cwd(cwd or "")).lower()


def proxy_alive(timeout=0.5):
    """True when the local routing proxy answers on its port."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", PROXY_PORT), timeout=timeout):
            return True
    except OSError:
        return False


def collect_capture_groups():
    """Parse the proxy capture store into groups: covered_key ->
    {"agent", "cwd", "msgs": [chronological], "n", "mtime"}. Shared by the
    export ingestion and the manual prune."""
    import json
    agg = {}
    if not os.path.isdir(CAPTURE_DIR):
        return agg
    # Parse every daily file per agent (cached), aggregating ALL days of the
    # same (agent, cwd) together - a conversation spanning several days must
    # end up in ONE export, not overwrite itself day by day.
    for agent_name in sorted(os.listdir(CAPTURE_DIR)):
        adir = os.path.join(CAPTURE_DIR, agent_name)
        if not os.path.isdir(adir):
            continue
        for fn in sorted(os.listdir(adir)):
            if not fn.endswith(".jsonl"):
                continue
            full = os.path.join(adir, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            cached = _capture_cache.get(full)
            if cached and cached[0] == st.st_size and cached[1] == st.st_mtime:
                groups = cached[2]
            else:
                groups = {}
                try:
                    with open(full, encoding="utf-8") as fh:
                        for line in fh:
                            try:
                                ev = json.loads(line)
                            except Exception:
                                continue
                            if ev.get("minor"):
                                continue
                            cwd = norm_cwd(ev.get("cwd")) or "(unknown)"
                            ts = parse_ts(ev.get("ts"))
                            if ts is None:
                                continue
                            g = groups.setdefault(cwd, {"msgs": [], "n": 0})
                            u = (ev.get("user") or "").strip()
                            a = (ev.get("assistant") or "").strip()
                            if u:
                                g["msgs"].append((ts, "user", u, ""))
                            if a:
                                g["msgs"].append((ts, "assistant", a, ""))
                            g["n"] += 1
                except OSError:
                    continue
                _capture_cache[full] = (st.st_size, st.st_mtime, groups)
            for cwd, g in groups.items():
                if not g["msgs"]:
                    continue
                ck = covered_key(agent_name, cwd)
                p = agg.setdefault(ck, {"agent": agent_name, "cwd": cwd,
                                        "msgs": [], "n": 0, "mtime": 0.0})
                p["msgs"].extend(g["msgs"])
                p["n"] += g["n"]
                p["mtime"] = max(p["mtime"], st.st_mtime)
    for p in agg.values():
        p["msgs"] = sorted(p["msgs"], key=lambda m: (m[0] is None, m[0]))
    return agg


def ingest_capture(state):
    if not os.path.isdir(CAPTURE_DIR):
        # Capture data gone entirely -> no project is covered any more and
        # the legacy scrape resumes everywhere.
        state.pop("_capture_covered", None)
        return
    agg = collect_capture_groups()
    # Rebuild the covered set from what capture data actually shows (stale
    # markers disappear automatically once data is deleted).
    state["_capture_covered"] = {ck: True for ck in agg}
    # One merged export per (agent, cwd).
    for ck, p in agg.items():
        if not p["msgs"] or p["n"] == 0:
            continue
        write_export(p["agent"], p["cwd"], None, p["msgs"][0][0], p["msgs"],
                     "capture:" + ck, p["mtime"], p["n"], state,
                     include_thinking=config.INCLUDE_THINKING,
                     output_formats=config.OUTPUT_FORMATS)
