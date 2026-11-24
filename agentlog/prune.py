"""Manual prune: remove exports of tiny stale conversations on demand.

Nothing is deleted automatically - the GUI button / `agentlog.py prune`
collects the candidates first (dry run), then removes them after the user
confirms. Pruned groups get a state marker with the same (mtime, size) the
export ingestion would compute, so the next scan's dedupe skips them and
they are NOT regenerated. Orphan files (whose source transcripts are gone
already, e.g. a deleted project) have no source left and can never come
back either.
"""
import os
import re

from . import config
from .export import _EXPORT_EXTS
from .extract import should_prune
from .parsers import AGENTS
from .timeutil import path_tail

_MSG_RE = re.compile(r"Messages:\**\s*(\d+)")


def _read_header_msg_count(path):
    """Messages count from an export header ('?' when unknown)."""
    try:
        with open(path, encoding="utf-8") as fh:
            for _ in range(12):
                line = fh.readline()
                if not line:
                    break
                m = _MSG_RE.search(line)
                if m:
                    return int(m.group(1))
    except OSError:
        pass
    return None


def _iter_export_files(agent):
    """Yield (dir, filename) for every export file of one agent."""
    adir = os.path.join(config.OUTPUT_ROOT, agent)
    for sub in ("", config.THINKING_DIR):
        d = os.path.join(adir, sub) if sub else adir
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            stem, dot, ext = fn.rpartition(".")
            if dot and ext in _EXPORT_EXTS:
                yield d, fn, stem


def _collect_source_groups(agents=None, include_db=True,
                           include_capture=True):
    """All conversation groups from every source, as
    {(agent, stem): (key, mtime, size, msgs)} plus per-agent stem sets."""
    from .daemon import collect_jsonl_groups, log
    from .capture import collect_capture_groups
    from .opencode_db import collect_opencode_groups

    groups = {}
    live_stems = {}   # agent -> set of stems backed by live source data
    for watch in (agents if agents is not None else AGENTS):
        agent = watch["agent"]
        if watch.get("source") == "opencode_db":
            if not include_db:
                continue
            try:
                collected = collect_opencode_groups()
            except Exception as e:
                log(f"[prune] opencode collect failed: {e}")
                continue
            for (cwd, gtitle), g in collected.items():
                stem = f"{path_tail(cwd)}-{gtitle}"
                groups[(agent, stem)] = (f"opencode-db:grp:{cwd}|{gtitle}",
                                         g["tupdate"] / 1000.0,
                                         len(g["msgs"]), g["msgs"])
                live_stems.setdefault(agent, set()).add(stem)
        else:
            try:
                collected = collect_jsonl_groups(watch)
            except Exception as e:
                log(f"[prune] {agent} collect failed: {e}")
                continue
            for (cwd, title), g in collected.items():
                stem = f"{path_tail(cwd)}-{title}"
                groups[(agent, stem)] = (f"{agent}:grp:{cwd}|{title}",
                                         g["mtime"], len(g["msgs"]),
                                         g["msgs"])
                live_stems.setdefault(agent, set()).add(stem)
    if include_capture:
        try:
            capture_groups = list(collect_capture_groups().items())
        except Exception as e:
            from .daemon import log as _log
            _log(f"[prune] capture collect failed: {e}")
            capture_groups = []
    else:
        capture_groups = []
    for ck, p in capture_groups:
        stem = path_tail(p["cwd"])
        groups[(p["agent"], stem)] = ("capture:" + ck, p["mtime"],
                                      p["n"], p["msgs"])
        live_stems.setdefault(p["agent"], set()).add(stem)
    return groups, live_stems


def prune_exports(state, min_messages=None, hours=None, dry_run=False,
                  agents=None, include_db=True, include_capture=True):
    """Collect (dry_run=True) or delete (dry_run=False) exports of tiny
    stale conversations. Returns the list of affected file paths. `agents`
    and the include_* flags exist so tests can point the collection at
    fixture sources."""
    from datetime import datetime, timedelta
    from .daemon import log

    groups, live_stems = _collect_source_groups(
        agents, include_db=include_db, include_capture=include_capture)

    # 1) Source-driven: live groups matching the prune rule.
    targets = {}   # (agent, stem) -> state marker for regeneration guard
    for (agent, stem), (key, mtime, size, msgs) in groups.items():
        if should_prune(msgs, min_messages, hours):
            targets[(agent, stem)] = (key, mtime, size)

    # 2) Orphan fallback: export files whose source is gone entirely.
    #    Decided on the file itself (header message count + file age), so
    #    leftovers like `lab-proj.md` from removed projects are caught too.
    cutoff = datetime.now().timestamp() - timedelta(hours=(
        hours if hours is not None else config.PRUNE_AFTER_HOURS)).total_seconds()
    if min_messages is None:
        min_messages = config.MIN_MESSAGES
    orphans = []
    if os.path.isdir(config.OUTPUT_ROOT):
        for agent in os.listdir(config.OUTPUT_ROOT):
            stems = live_stems.get(agent, set())
            for d, fn, stem in _iter_export_files(agent):
                if stem in stems or (agent, stem) in targets:
                    continue
                full = os.path.join(d, fn)
                try:
                    age_ok = os.path.getmtime(full) < cutoff
                except OSError:
                    continue
                if not age_ok:
                    continue
                n = _read_header_msg_count(full)
                if n is None or n > min_messages:
                    continue
                orphans.append(full)

    # Build the source-driven file list (all formats + thinking copies).
    src_files = []
    for (agent, stem) in targets:
        for d, fn, s in _iter_export_files(agent):
            if s == stem:
                src_files.append(os.path.join(d, fn))

    removed = []
    if not dry_run:
        for f in src_files:
            try:
                os.remove(f)
                removed.append(f)
            except OSError:
                pass
        for f in orphans:
            try:
                os.remove(f)
                removed.append(f)
            except OSError:
                pass
        # Regeneration guard: mark pruned groups with the exact (mtime, size)
        # the ingestion dedupe compares, so later scans skip re-exporting.
        for (agent, stem), (key, mtime, size) in targets.items():
            state[key] = {"mtime": mtime, "size": size, "out": None,
                          "pruned": True}
            log(f"[prune] {agent} {stem} "
                f"(<={min_messages} msgs and older than "
                f"{config.PRUNE_AFTER_HOURS:g}h)")
        for f in orphans:
            log(f"[prune] orphan export removed: {os.path.basename(f)}")
    return sorted(set(src_files) | set(orphans)) if dry_run else removed
