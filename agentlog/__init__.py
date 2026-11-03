"""agentlog - capture CLI agent conversations into the project directory.

Watches each agent's own transcript storage (JSONL files / SQLite databases)
and a local routing proxy, and renders clean exports per project and
conversation group into the repo's agent_logs/ directory.
"""

__version__ = "1.1.0"

# Convenience re-exports so `import agentlog as core` keeps working for the
# GUI, user scripts and the thin top-level entry shims.
from .config import (HERE, OUTPUT_ROOT, ICON_PATH, POLL_SECONDS,
                     DROP_PRE_TOOL_ASSISTANT, IGNORE_DIR_PARTS, STATE_FILE,
                     LOG_FILE, PID_FILE, OUTPUT_FORMATS, INCLUDE_THINKING,
                     THINKING_DIR, BJ, THINK_TYPES, SKIP_TYPES, TEXT_TYPES)
from .timeutil import parse_ts, fmt, fmt_file, path_tail
from .extract import (extract_text, looks_internal, decode_folder_cwd,
                      resolve_cwd, clean_title, first_user_title)
from .parsers import (PARSERS, DEFAULT_AGENTS, load_agents, AGENTS,
                      parse_codebuddy, parse_pi, parse_claude, parse_opencode,
                      parse_generic)
from .export import (render, render_txt, render_markdown, render_json,
                     exports_fallback, write_export, cleanup_exports)
from .opencode_db import (opencode_db_path, ingest_opencode_db,
                          FORK_SUFFIX_RE)
from .capture import (CAPTURE_DIR, PROXY_PORT, norm_cwd, covered_key,
                      proxy_alive, ingest_capture)
from .daemon import (scan_once, run_loop, log, load_state, save_state,
                     ingest_jsonl_agent, start_proxy_thread)
from .winapi import (run_cmd, pythonw_exe, read_pid, is_running,
                     write_pid, detach_streams, acquire_run_lock,
                     find_agentlog_pids, kill_all_agentlog, create_shortcut,
                     startup_lnk_path, autostart_blocked,
                     set_startup_approved, systemctl_user,
                     SYSTEMD_UNIT_PATH)
from .cli import (main, cmd_start, cmd_stop, cmd_status, cmd_install,
                  cmd_uninstall, cmd_gui_shortcut, cmd_route, build_icon)

# Underscore-prefixed names kept for backward compatibility with the GUI /
# older scripts that imported the single-file module.
opencode_db_path.__doc__ = opencode_db_path.__doc__ or ""
_opencode_db_path = opencode_db_path
_FORK_SUFFIX_RE = FORK_SUFFIX_RE
_detach_streams = detach_streams
_systemctl_user = systemctl_user
_acquire_run_lock = acquire_run_lock
