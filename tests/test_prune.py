"""Regression tests for the manual prune feature (tiny stale conversations)."""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from agentlog import config, daemon
from agentlog.config import BJ
from agentlog.extract import should_prune
from agentlog.parsers import parse_claude
from agentlog.prune import prune_exports


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


class TestManualPrune(unittest.TestCase):
    """Exports are NOT pruned automatically; the explicit prune_exports()
    call collects candidates, removes them after confirmation, and blocks
    regeneration via the state marker."""

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

    def _ingest(self, state):
        watch = {"agent": "claude", "parser": parse_claude,
                 "globs": [os.path.join(self.home, ".claude", "projects",
                                        "**", "*.jsonl")]}
        daemon.ingest_jsonl_agent(watch, state)

    def test_manual_prune_flow_and_no_regeneration(self):
        self._write("old.jsonl", "old", "旧小会话", "短", minutes_ago=60 * 48)
        self._write("new.jsonl", "new", "新小会话", "短", minutes_ago=60)
        watch = {"agent": "claude", "parser": parse_claude,
                 "globs": [os.path.join(self.home, ".claude", "projects",
                                        "**", "*.jsonl")]}
        pruner = dict(agents=[watch], include_db=False, include_capture=False)
        # 大会话: 同一会话 6 条消息
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

        state = {}
        self._ingest(state)   # 自动扫描: 全部照常导出(不自动删)
        cdir = os.path.join(config.OUTPUT_ROOT, "claude")
        self.assertEqual(sorted(os.listdir(cdir)),
                         sorted(["lab-proj-大会话第0问.md",
                                 "lab-proj-新小会话.md",
                                 "lab-proj-旧小会话.md"]))

        # 手动 prune: dry run 只列旧小会话(新会话受 24h 保护,大会话超条数)
        targets = prune_exports(state, dry_run=True, **pruner)
        self.assertEqual([os.path.basename(f) for f in targets],
                         ["lab-proj-旧小会话.md"])

        removed = prune_exports(state, dry_run=False, **pruner)
        self.assertEqual(len(removed), 1)
        self.assertEqual(sorted(os.listdir(cdir)),
                         sorted(["lab-proj-大会话第0问.md",
                                 "lab-proj-新小会话.md"]))

        # 防重生: 再扫描两轮不再生成
        self._ingest(state)
        self._ingest(state)
        self.assertEqual(sorted(os.listdir(cdir)),
                         sorted(["lab-proj-大会话第0问.md",
                                 "lab-proj-新小会话.md"]))
        k = "claude:grp:E:\\lab\\proj|旧小会话"
        self.assertTrue(state[k].get("pruned"))

    def test_orphan_files_caught(self):
        # 孤儿: 源数据不存在的导出文件(如项目已删除), 头部 4 条 + 文件超 24h
        cdir = os.path.join(config.OUTPUT_ROOT, "claude")
        os.makedirs(cdir)
        orphan = os.path.join(cdir, "lab-proj.md")
        with open(orphan, "w", encoding="utf-8") as fh:
            fh.write("# Agent Conversation Log\n\n"
                     "- **Agent:** claude\n"
                     "- **Path:** `E:\\lab\\proj`\n"
                     "- **Start (Beijing):** 2026-10-31 18:00:00\n"
                     "- **Messages:** 4\n")
        old = datetime.now().timestamp() - 40 * 3600
        os.utime(orphan, (old, old))

        state = {}
        targets = prune_exports(state, dry_run=True)
        self.assertIn(orphan, targets)
        removed = prune_exports(state, dry_run=False)
        self.assertEqual(removed, [orphan])
        self.assertFalse(os.path.exists(orphan))

        # 新鲜的孤儿(24h 内)不删
        fresh = os.path.join(cdir, "fresh-orphan.md")
        shutil.copyfile(orphan if os.path.exists(orphan) else orphan, fresh) \
            if False else None
        with open(fresh, "w", encoding="utf-8") as fh:
            fh.write("- **Messages:** 4\n")
        self.assertEqual(prune_exports(state, dry_run=True), [])


if __name__ == "__main__":
    unittest.main()
