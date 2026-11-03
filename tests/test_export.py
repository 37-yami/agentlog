"""Regression tests for export naming, dedupe and grouping behaviour."""
import json
import os
import shutil
import tempfile
import unittest

from agentlog import config, daemon
from agentlog.capture import covered_key
from agentlog.export import write_export
from agentlog.timeutil import parse_ts


class ExportBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agtest_")
        self.old_root = config.OUTPUT_ROOT
        config.OUTPUT_ROOT = os.path.join(self.tmp, "out")

    def tearDown(self):
        config.OUTPUT_ROOT = self.old_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def msgs(self):
        return [(parse_ts(1760000000000), "user", "q", ""),
                (parse_ts(1760000001000), "assistant", "a", "")]


class TestWriteExport(ExportBase):
    def test_title_names_file(self):
        state = {}
        write_export("claude", "E:\\lab\\proj", None, None, self.msgs(),
                     "k", 1.0, 2, state, title="我的对话",
                     output_formats=["md"])
        self.assertTrue(os.path.exists(
            os.path.join(config.OUTPUT_ROOT, "claude", "lab-proj-我的对话.md")))

    def test_sid_suffix(self):
        state = {}
        write_export("claude", "E:\\lab\\proj", "ses_abcdef12", None,
                     self.msgs(), "k", 1.0, 2, state, output_formats=["md"])
        self.assertTrue(os.path.exists(
            os.path.join(config.OUTPUT_ROOT, "claude",
                         "lab-proj-abcdef12.md")))

    def test_dedupe_on_mtime_size(self):
        state = {}
        self.assertTrue(write_export("claude", "E:\\x", None, None,
                                     self.msgs(), "k", 1.0, 2, state,
                                     output_formats=["md"]))
        self.assertFalse(write_export("claude", "E:\\x", None, None,
                                      self.msgs(), "k", 1.0, 2, state,
                                      output_formats=["md"]))
        self.assertTrue(write_export("claude", "E:\\x", None, None,
                                     self.msgs(), "k", 2.0, 2, state,
                                     output_formats=["md"]))


class TestGrouping(ExportBase):
    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")
        self.proj = os.path.join(self.home, ".claude", "projects", "e-lab-proj")
        os.makedirs(self.proj)

    def _session(self, name, rows):
        with open(os.path.join(self.proj, name), "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    def claude_rows(self, sid, q, a, ts=1760000000000):
        return [
            {"type": "user", "cwd": "E:\\lab\\proj", "sessionId": sid,
             "timestamp": ts, "message": {"role": "user", "content": q}},
            {"type": "assistant", "cwd": "E:\\lab\\proj", "sessionId": sid,
             "timestamp": ts + 1000,
             "message": {"role": "assistant",
                         "content": [{"type": "text", "text": a}]}},
        ]

    def test_same_title_merges_probe_skipped(self):
        # fork 复制历史(同首条 user、同时间戳) + 新对话(不同首条) -> 两组文件
        self._session("aaa.jsonl", self.claude_rows("aaa", "帮我分析", "好的"))
        self._session("bbb.jsonl", self.claude_rows("bbb", "帮我分析", "好的"))
        self._session("ccc.jsonl", self.claude_rows("ccc", "另一个问题", "另答"))
        # 探测会话: "Reply with exactly: ..." 模式 -> 跳过
        self._session("ddd.jsonl", self.claude_rows("ddd", "Reply with exactly: OK", "OK"))
        from agentlog.parsers import parse_claude
        watch = {"agent": "claude", "parser": parse_claude,
                 "globs": [os.path.join(self.home, ".claude", "projects",
                                        "**", "*.jsonl")]}
        state = {}
        daemon.ingest_jsonl_agent(watch, state)
        cdir = os.path.join(config.OUTPUT_ROOT, "claude")
        files = sorted(os.listdir(cdir))
        # 一组: 帮我分析(aaa+bbb 合并), 一组: 另一个问题; 探测无文件
        self.assertEqual(files, ["lab-proj-另一个问题.md", "lab-proj-帮我分析.md"])
        merged = open(os.path.join(cdir, "lab-proj-帮我分析.md"),
                      encoding="utf-8").read()
        self.assertEqual(merged.count("User"), 1)  # fork 去重

    def test_capture_covered_suppresses(self):
        self._session("aaa.jsonl", self.claude_rows("aaa", "帮我分析", "好的"))
        from agentlog.parsers import parse_claude
        watch = {"agent": "claude", "parser": parse_claude,
                 "globs": [os.path.join(self.home, ".claude", "projects",
                                        "**", "*.jsonl")]}
        state = {"_capture_covered": {covered_key("claude", "E:\\lab\\proj"): True}}
        daemon.ingest_jsonl_agent(watch, state)
        cdir = os.path.join(config.OUTPUT_ROOT, "claude")
        self.assertFalse(os.path.isdir(cdir))  # 不导出


if __name__ == "__main__":
    unittest.main()
