"""Agent registry: parser mapping and agent discovery globs.

Adjust via agents.json (overrides/additions) without touching code.
"""
import json
import os

from ..config import HERE
from .codebuddy import parse_codebuddy
from .pi import parse_pi
from .claude import parse_claude
from .opencode import parse_opencode
from .generic import parse_generic

PARSERS = {
    "codebuddy": parse_codebuddy,
    "pi": parse_pi,
    "claude": parse_claude,
    "opencode": parse_opencode,
    "generic": parse_generic,
}

DEFAULT_AGENTS = [
    {"name": "codebuddy", "parser": "codebuddy",
     "globs": [r"~/.codebuddy/projects/**/*.jsonl"]},
    {"name": "pi", "parser": "pi",
     "globs": [r"~/.pi/agent/sessions/**/*.jsonl"]},
    {"name": "claude", "parser": "claude",
     "globs": [r"~/.claude/projects/**/*.jsonl"]},
    {"name": "opencode", "parser": "opencode",
     "source": "opencode_db",
     "globs": [r"~/.config/opencode/**/session/*.jsonl",
               r"~/.local/share/opencode/**/*.jsonl"]},
    # Best-effort stubs for future agents (adjust globs/parser as needed):
    {"name": "codex", "parser": "generic",
     "globs": [r"~/.codex/**/*.jsonl", r"~/.config/codex/**/*.jsonl"]},
    {"name": "mimo", "parser": "generic",
     "globs": [r"~/.mimo/**/*.jsonl", r"~/.config/mimo/**/*.jsonl"]},
]


def load_agents():
    """Load agent specs, allowing overrides/additions from agents.json."""
    agents = {a["name"]: dict(a) for a in DEFAULT_AGENTS}
    cfg = os.path.join(HERE, "agents.json")
    if os.path.exists(cfg):
        try:
            with open(cfg, encoding="utf-8") as fh:
                extra = json.load(fh)
            for a in extra.get("agents", []):
                agents[a["name"]] = a
        except Exception as e:
            import sys
            print(f"[config-error] agents.json: {e}", file=sys.stderr)
    result = []
    for a in agents.values():
        parser = PARSERS.get(a.get("parser"), parse_generic)
        globs = [os.path.expanduser(g) for g in a.get("globs", [])]
        entry = {"agent": a["name"], "parser": parser, "globs": globs}
        if a.get("source"):
            entry["source"] = a["source"]
        result.append(entry)
    return result


AGENTS = load_agents()
