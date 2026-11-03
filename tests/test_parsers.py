"""Regression tests for the per-agent JSONL parsers."""
import json
import os
import tempfile
import unittest

from agentlog.parsers import parse_claude, parse_codebuddy


def write_jsonl(rows):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


class TestParseClaude(unittest.TestCase):
    def test_basic_and_ismeta_filter(self):
        p = write_jsonl([
            {"type": "user", "isMeta": True, "cwd": "E:\\p", "sessionId": "s1",
             "timestamp": 1760000000000,
             "message": {"role": "user", "content": "<local-command-caveat>x"}},
            {"type": "user", "cwd": "E:\\p", "sessionId": "s1",
             "timestamp": 1760000000000,
             "message": {"role": "user", "content": "你好"}},
            {"type": "assistant", "cwd": "E:\\p", "sessionId": "s1",
             "timestamp": 1760000001000,
             "message": {"role": "assistant",
                         "content": [{"type": "text", "text": "回答"}]}},
        ])
        try:
            cwd, sid, title, start, msgs = parse_claude(p)
            self.assertEqual(cwd, "E:\\p")
            self.assertEqual(sid, "s1")
            self.assertIsNone(title)
            self.assertEqual(len(msgs), 2)
            self.assertEqual(msgs[0][2], "你好")
        finally:
            os.remove(p)


class TestParseCodebuddy(unittest.TestCase):
    def test_ai_title(self):
        p = write_jsonl([
            {"type": "ai-title", "aiTitle": "Analyze project", "cwd": "E:\\p",
             "sessionId": "c1", "timestamp": 1760000000000},
            {"type": "message", "role": "user", "content": "hi",
             "cwd": "E:\\p", "sessionId": "c1", "timestamp": 1760000000000},
            {"type": "message", "role": "assistant", "content": "ok",
             "cwd": "E:\\p", "sessionId": "c1", "timestamp": 1760000001000},
        ])
        try:
            cwd, sid, title, start, msgs = parse_codebuddy(p)
            self.assertEqual(title, "Analyze project")
            self.assertEqual(len(msgs), 2)
        finally:
            os.remove(p)


if __name__ == "__main__":
    unittest.main()
