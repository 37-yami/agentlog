"""Regression tests for the auto-prune feature (tiny stale conversations)."""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from agentlog import config
from agentlog.extract import should_prune
from agentlog.parsers import parse_claude
from agentlog import daemon
from agentlog.config import BJ

NOW_MS = None  # filled per-test for relative timestamps


def _msg(ts_min_ago, role="user", text="x"):
    # Beijing-style aware datetime (what parse_ts produces everywhere else)
    dt = datetime.now(timezone.utc) + BJ - timedelta(minutes=ts_min_ago)
    return (dt, role, text, "")


class TestShouldPrune(unittest.TestCase):
    def test_at_most_n_pruned(self):
        # <= 5 messages (exactly 5 counts as prunable)
        self.assertTrue(should_prune([_msg(60 * 48)] * 5, min_messages=5,
                                     hours=24))
        self.assertFalse(should_prune([_msg(60 * 48)] * 6, min_messages=5,
                                      hours=24))

    def test_age_guard(self):
        old = [_msg(60 * 48)]  # 2 days ago
        fresh = [_msg(30)]      # 30 min ago
        self.assertTrue(should_prune(old, min_messages=5, hours=24))
        self.assertFalse(should_prune(fresh, min_messages=5, hours=24))

    def test_boundary_just_under_24h(self):
        self.assertFalse(should_prune([_msg(60 * 23)], min_messages=5, hours=24))
        self.assertTrue(should_prune([_msg(60 * 25)], min_messages=5, hours=24))

    def test_no_timestamps_never_pruned(self):
        self.assertFalse(should_prune([(None, "user", "x", "")] * 2))

    def test_defaults_from_config(self):
        self.assertEqual(config.MIN_MESSAGES, 5)


class TestPruneIntegration(unittest.TestCase):
    """ingest_jsonl_agent prunes stale tiny groups, keeps fresh ones, and the
    old export file is removed + never regenerated."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agprune_")
        self.old_root = config.OUTPUT_ROOT
        config.OUTPUT_ROOT = os.path.join(self.tmp, "out")
        self.home = os.path.join(self.tmp, "home")
        self.proj = os.path.join(self.home, ".claude", "projects", "e-lab-proj")
        os.makedirs(self.proj)
        self.old_min = config.MIN_MESSAGES
        config.MIN_MESSAGES = 5

    def tearDown(self):
        config.OUTPUT_ROOT = self.old_root
        config.MIN_MESSAGES = self.old_min
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, sid, q, a, minutes_ago):
        ts = (datetime.now(timezone.utc)
              - timedelta(minutes=minutes_ago)).timestamp() * 1000
        with open(os.path.join(self.proj, name), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "user", "cwd": "E:\\lab\\proj",
                                 "sessionId": sid, "timestamp": ts,
                                 "message": {"role": "user", "content": q}},
                                ensure_ascii=False) + "\n")
            fh.write(json.dumps({"type": "assistant", "cwd": "E:\\lab\\proj",
                                 "sessionId": sid, "timestamp": ts + 1000,
                                 "message": {"role": "assistant",
                                             "content": [{"type": "text",
                                                          "text": a}]}},
                                ensure_ascii=False) + "\n")

    def _run(self, state):
        watch = {"agent": "claude", "parser": parse_claude,
                 "globs": [os.path.join(self.home, ".claude", "projects",
                                        "**", "*.jsonl")]}
        daemon.ingest_jsonl_agent(watch, state)

    def test_prune_flow(self):
        # 旧的小会话(2 条, 2 天前) -> 删除; 新的小会话(2 条, 1 小时前) -> 保留
        self._write("old.jsonl", "old", "旧小会话", "短", minutes_ago=60 * 48)
        self._write("new.jsonl", "new", "新小会话", "短", minutes_ago=60)
        # 大会话(同一会话 6 条消息, 很旧) -> 保留
        big_ts = ((datetime.now(timezone.utc) - timedelta(minutes=60 * 48))
                  .timestamp() * 1000)
        with open(os.path.join(self.proj, "big.jsonl"), "w",
                  encoding="utf-8") as fh:
            for i in range(3):
                fh.write(json.dumps({"type": "user", "cwd": "E:\\lab\\proj",
                                     "sessionId": "big",
                                     "timestamp": big_ts + i * 2000,
                                     "message": {"role": "user",
                                                 "content": f"大会话第{i}问"}},
                                    ensure_ascii=False) + "\n")
                fh.write(json.dumps({"type": "assistant", "cwd": "E:\\lab\\proj",
                                     "sessionId": "big",
                                     "timestamp": big_ts + i * 2000 + 1000,
                                     "message": {"role": "assistant",
                                                 "content": [{"type": "text",
                                                              "text": "长回答"}]}},
                                    ensure_ascii=False) + "\n")
        cdir = os.path.join(config.OUTPUT_ROOT, "claude")
        os.makedirs(cdir)
        stale = os.path.join(cdir, "lab-proj-旧小会话.md")
        open(stale, "w", encoding="utf-8").write("stale export")

        state = {}
        self._run(state)
        files = sorted(os.listdir(cdir))
        self.assertEqual(files, sorted(["lab-proj-大会话第0问.md",
                                        "lab-proj-新小会话.md"]))
        self.assertFalse(os.path.exists(stale))          # 旧导出被清理
        # 防重生: 再扫两轮, 仍不生成
        self._run(state)
        self._run(state)
        self.assertNotIn("lab-proj-旧小会话.md", os.listdir(cdir))
        # state 标记(日志去重用)
        k = "claude:grp:E:\\lab\\proj|旧小会话"
        self.assertTrue(state[k].get("pruned"))

    def test_exactly_five_old_is_pruned(self):
        # 恰好 5 条且超过 24h -> 删除 (<= 语义)
        ts = (datetime.now(timezone.utc)
              - timedelta(hours=25)).timestamp() * 1000
        with open(os.path.join(self.proj, "five.jsonl"), "w",
                  encoding="utf-8") as fh:
            for i in range(5):
                fh.write(json.dumps({"type": "user", "cwd": "E:\\lab\\proj",
                                     "sessionId": "five", "timestamp": ts + i,
                                     "message": {"role": "user",
                                                 "content": f"q{i}"}},
                                    ensure_ascii=False) + "\n")
        state = {}
        self._run(state)
        cdir = os.path.join(config.OUTPUT_ROOT, "claude")
        self.assertFalse(os.path.isdir(cdir))


if __name__ == "__main__":
    unittest.main()
