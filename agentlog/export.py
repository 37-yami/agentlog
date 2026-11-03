"""Rendering and writing of conversation exports."""
import json
import os

from . import config
from .timeutil import fmt, path_tail


def render(agent, cwd, sid, start, msgs, include_thinking=False, output_format="txt"):
    """Render messages to the requested format ("txt", "md", or "json")."""
    if output_format == "md":
        return render_markdown(agent, cwd, sid, start, msgs, include_thinking)
    if output_format == "json":
        return render_json(agent, cwd, sid, start, msgs, include_thinking)
    return render_txt(agent, cwd, sid, start, msgs, include_thinking)


def render_txt(agent, cwd, sid, start, msgs, include_thinking=False):
    """Render messages in plain text format."""
    out = []
    out.append(f"# Agent: {agent}")
    out.append(f"# Path: {cwd or '(unknown)'}")
    if sid:
        out.append(f"# Session: {sid}")
    out.append(f"# Start (Beijing): {fmt(start)}")
    out.append(f"# Messages: {len(msgs)}")
    out.append("")
    role_label = {"user": "User", "assistant": "Assistant",
                  "system": "System", "tool": "Tool"}
    for dt, role, text, thinking in msgs:
        label = role_label.get(role, role.capitalize() if role else "?")
        out.append(f"[{fmt(dt)}] {label}:")
        if include_thinking and thinking:
            out.append(f"  [Thinking]:")
            for line in thinking.split("\n"):
                out.append(f"    {line}")
        if text:
            out.append(text)
        out.append("")
    return "\n".join(out) + "\n"


def render_markdown(agent, cwd, sid, start, msgs, include_thinking=False):
    """Render messages in Markdown format."""
    out = []
    out.append(f"# Agent Conversation Log")
    out.append("")
    out.append(f"- **Agent:** {agent}")
    out.append(f"- **Path:** `{cwd or '(unknown)'}`")
    if sid:
        out.append(f"- **Session:** `{sid}`")
    out.append(f"- **Start (Beijing):** {fmt(start)}")
    out.append(f"- **Messages:** {len(msgs)}")
    out.append("")
    out.append("---")
    out.append("")

    role_label = {"user": "User", "assistant": "Assistant",
                  "system": "System", "tool": "Tool"}

    for dt, role, text, thinking in msgs:
        label = role_label.get(role, role.capitalize() if role else "?")
        out.append(f"## [{fmt(dt)}] {label}")
        out.append("")

        # Include thinking content if enabled
        if include_thinking and thinking:
            out.append("### Thinking")
            out.append("")
            out.append("```")
            out.append(thinking)
            out.append("```")
            out.append("")

        # Include main text
        if text:
            out.append("### Response")
            out.append("")
            out.append(text)
            out.append("")

        out.append("---")
        out.append("")

    return "\n".join(out) + "\n"


def render_json(agent, cwd, sid, start, msgs, include_thinking=False):
    """Render messages in JSON format."""
    data = {
        "agent": agent,
        "path": cwd or "(unknown)",
        "session": sid,
        "start_beijing": fmt(start),
        "messages": []
    }
    for dt, role, text, thinking in msgs:
        msg = {
            "time": fmt(dt),
            "role": role,
            "text": text
        }
        if include_thinking and thinking:
            msg["thinking"] = thinking
        data["messages"].append(msg)
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def exports_fallback(agent):
    d = os.path.join(config.HERE, "exports", agent)
    os.makedirs(d, exist_ok=True)
    return d


def write_export(agent, cwd, sid, start, msgs, key, mtime, size, state,
                 include_thinking=False, output_formats=None, title=None):
    """Dedupe + render + write one conversation export. Shared by the file
    parser and the opencode SQLite ingestion. `title` (already cleaned) is
    appended to the filename so each conversation group of one project gets
    its own file."""
    if output_formats is None:
        output_formats = config.OUTPUT_FORMATS

    prev = state.get(key)
    if prev and prev.get("mtime") == mtime and prev.get("size") == size:
        return False  # unchanged

    if not msgs:
        state[key] = {"mtime": mtime, "size": size, "out": None}
        return False

    # Determine output directory: thinking files go in subfolder
    if include_thinking:
        out_dir = os.path.join(config.OUTPUT_ROOT, agent, config.THINKING_DIR)
    else:
        out_dir = os.path.join(config.OUTPUT_ROOT, agent)
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError:
        out_dir = exports_fallback(agent)

    # Generate files for each format
    ext_map = {"txt": ".txt", "md": ".md", "json": ".json"}
    base_name = path_tail(cwd)
    # Conversation-group title names the file (one file per conversation of
    # one project); the short session id keeps legacy per-session exports
    # unique (remove ses_ prefix).
    if title:
        base_name = f"{base_name}-{title}"
    if sid:
        # Remove ses_ prefix if present
        sid_clean = sid[4:] if sid.startswith("ses_") else sid
        sid_short = sid_clean[:8] if len(sid_clean) > 8 else sid_clean
        base_name = f"{base_name}-{sid_short}"
    written_paths = []

    for fmt_key in output_formats:
        ext = ext_map.get(fmt_key, ".txt")
        fname = base_name + ext
        out_path = os.path.join(out_dir, fname)
        text = render(agent, cwd, sid, start, msgs, include_thinking, fmt_key)
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(text)
            written_paths.append(out_path)
        except OSError:
            # Fallback directory
            fallback_dir = exports_fallback(agent)
            if include_thinking:
                fallback_dir = os.path.join(fallback_dir, config.THINKING_DIR)
                os.makedirs(fallback_dir, exist_ok=True)
            out_path = os.path.join(fallback_dir, fname)
            try:
                with open(out_path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                written_paths.append(out_path)
            except OSError as e3:
                from .daemon import log
                log(f"[write-error] {out_path}: {e3}")

    if written_paths:
        state[key] = {"mtime": mtime, "size": size, "out": written_paths[0]}
        tag = "(thinking)" if include_thinking else ""
        from .daemon import log
        log(f"[write] {agent} {tag} -> {', '.join(os.path.basename(p) for p in written_paths)} ({len(msgs)} msgs)")
        return True
    return False


_EXPORT_EXTS = ("txt", "md", "json")


def cleanup_exports(agent, cwd, keep_stems, log=None):
    """Remove this project's export files whose stem is not in keep_stems
    (old per-session files, previous untitled merges, or everything when the
    proxy capture covers the project and keep_stems is empty)."""
    adir = os.path.join(config.OUTPUT_ROOT, agent)
    prefix = path_tail(cwd)
    for sub in ("", config.THINKING_DIR):
        d = os.path.join(adir, sub) if sub else adir
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            stem, dot, ext = fn.rpartition(".")
            if not dot or ext not in _EXPORT_EXTS:
                continue
            if stem in keep_stems:
                continue
            if stem != prefix and not stem.startswith(prefix + "-"):
                continue  # belongs to a different project
            try:
                os.remove(os.path.join(d, fn))
                if log:
                    log("[cleanup] removed old export " + agent + "/" + fn)
            except OSError:
                pass
