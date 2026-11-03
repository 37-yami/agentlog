"""Daemon: scan orchestration, JSONL ingestion and the watch loop."""
import glob
import json
import os
import time
from datetime import datetime, timezone

from . import config
from .capture import CAPTURE_DIR, PROXY_PORT, covered_key, ingest_capture, norm_cwd
from .config import (IGNORE_DIR_PARTS, LOG_FILE, POLL_SECONDS,
                     STATE_FILE)
from .export import cleanup_exports, write_export
from .extract import clean_title, first_user_title, is_probe
from .opencode_db import ingest_opencode_db
from .parsers import AGENTS
from .timeutil import fmt, path_tail


def log(msg):
    line = f"{fmt(datetime.now(timezone.utc) + config.BJ)} {msg}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except OSError:
        pass


def ingest_jsonl_agent(watch, state):
    """Scrape one agent's JSONL transcripts, merged per conversation group.

    Sessions are aggregated by (project, group title): agent-generated titles
    (codebuddy ai-title / pi session title) group directly; otherwise the
    first user message groups them, which also folds forked/resumed sessions
    (they copy the history, so their first user message matches)."""
    agent = watch["agent"]
    groups = {}   # (cwd, title) -> {"msgs", "start", "mtime"}
    for pattern in watch["globs"]:
        for path in glob.glob(pattern, recursive=True):
            parts = path.replace("\\", "/").split("/")
            if any(p in IGNORE_DIR_PARTS for p in parts):
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            try:
                cwd, sid, title, start, msgs = watch["parser"](path)
            except Exception as e:
                log(f"[parse-error] {path}: {e}")
                continue
            cwd = norm_cwd(cwd)
            if not cwd or not msgs:
                continue
            # Skip connectivity probes (same rule as the opencode ingestion).
            if is_probe(msgs):
                continue
            title = clean_title(title) or first_user_title(msgs)
            if not title:
                continue
            g = groups.setdefault((cwd, title),
                                  {"msgs": [], "start": None, "mtime": 0.0})
            g["msgs"].extend(msgs)
            if g["start"] is None:
                g["start"] = start
            g["mtime"] = max(g["mtime"], st.st_mtime)
    # Export one merged file per conversation group.
    keep = {}   # cwd -> set of stems still valid
    for (cwd, title), g in groups.items():
        # The proxy capture is authoritative for covered projects.
        if covered_key(agent, cwd) in state.get("_capture_covered", {}):
            continue
        seen = set()
        uniq = []
        for m in g["msgs"]:
            k = (m[0], m[1], m[2])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(m)
        uniq.sort(key=lambda m: (m[0] is None, m[0]))
        key = f"{agent}:grp:{cwd}|{title}"
        stem = f"{path_tail(cwd)}-{title}"
        write_export(agent, cwd, None, g["start"], uniq, key,
                     g["mtime"], len(uniq), state, title=title,
                     include_thinking=config.INCLUDE_THINKING,
                     output_formats=config.OUTPUT_FORMATS)
        keep.setdefault(cwd, set()).add(stem)
    # Drop superseded files (per-session exports, older merges, or everything
    # when the capture covers the project).
    for cwd in {c for (c, _t) in groups}:
        cleanup_exports(agent, cwd, keep.get(cwd, set()), log=log)


def start_proxy_thread():
    """Local routing proxy: agents point their API endpoint at
    http://127.0.0.1:<PROXY_PORT>/<agent>[/<upstream-host>]/ and every Q/A
    turn is captured at request time into capture/<agent>/<YYYYMMDD>.jsonl."""
    import threading
    if os.environ.get("AGENTLOG_PROXY", "1") == "0":
        return
    try:
        import agentlog_proxy
    except Exception as e:
        log(f"[proxy] import failed: {e}")
        return
    def _run():
        try:
            agentlog_proxy.serve()
        except Exception as e:
            log(f"[proxy] stopped: {e}")
    threading.Thread(target=_run, daemon=True).start()
    log(f"[proxy] listening on http://127.0.0.1:{PROXY_PORT}")


def scan_once(state):
    try:
        ingest_capture(state)
    except Exception as e:
        log(f"[capture error] {e}")
    for watch in AGENTS:
        if watch.get("source") == "opencode_db":
            try:
                ingest_opencode_db(watch, state)
            except Exception as e:
                log(f"[opencode-db error] {e}")
            continue
        try:
            ingest_jsonl_agent(watch, state)
        except Exception as e:
            log(f"[error] {watch['agent']}: {e}")


def run_loop():
    log("daemon started")
    start_proxy_thread()
    state = load_state()
    try:
        while True:
            scan_once(state)
            save_state(state)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        save_state(state)
        log("daemon stopped")
