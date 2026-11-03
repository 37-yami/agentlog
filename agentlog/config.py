"""Configuration: paths, polling knobs and mutable run-time options."""
import os
from datetime import timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# All conversation exports go under <repo>/agent_logs/<agent>/...
OUTPUT_ROOT = os.path.join(HERE, "agent_logs")
# Optional icon (built from a source image, see build_icon()). The GUI and the
# launcher shortcut use it when present; otherwise a default system icon is used.
ICON_PATH = os.path.join(HERE, "agentlog.ico")
POLL_SECONDS = 5
# Drop assistant lines that are just narration right before a tool call
# (these are the "thinking out loud" bits the user does not want kept).
DROP_PRE_TOOL_ASSISTANT = True
# Directory components to ignore entirely (sub-agent transcripts are not
# standalone conversations and contain a lot of internal chatter).
IGNORE_DIR_PARTS = {"subagents"}
STATE_FILE = os.path.join(HERE, "agentlog.state.json")
LOG_FILE = os.path.join(HERE, "agentlog.log")
PID_FILE = os.path.join(HERE, "agentlog.pid")
# Output formats: list of formats to generate (e.g. ["txt", "md", "json"]).
# Mutable run-time option - read it as config.OUTPUT_FORMATS (module
# attribute), never bind it with `from config import OUTPUT_FORMATS`.
OUTPUT_FORMATS = ["md"]
# Include thinking/reasoning content in output (default: False). Mutable
# run-time option, same access rule as OUTPUT_FORMATS.
INCLUDE_THINKING = False
# Subfolder for files with thinking content
THINKING_DIR = "_thinking"

BJ = timedelta(hours=8)  # Beijing time is fixed UTC+8

# Block types we never want in the conversation text.
THINK_TYPES = {"thinking", "reasoning", "thought", "reasoning_content"}
SKIP_TYPES = THINK_TYPES | {
    "tool_use", "tool_result", "function_call", "function_result",
    "input_json", "annotation", "image", "file", "diff", "redacted_thinking",
}
# Block types that carry user/assistant prose.
TEXT_TYPES = {"text", "input_text", "output_text", "markdown", "plain_text"}


def set_options(include_thinking=None, formats=None):
    """Update the mutable run-time options (CLI flags / GUI checkboxes)."""
    global INCLUDE_THINKING, OUTPUT_FORMATS
    if include_thinking is not None:
        INCLUDE_THINKING = include_thinking
    if formats is not None:
        OUTPUT_FORMATS = formats
