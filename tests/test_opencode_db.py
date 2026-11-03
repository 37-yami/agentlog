"""Regression tests for opencode SQLite ingestion (in-memory fake DB)."""
import json
import os
import shutil
import sqlite3
import tempfile
import unittest

from agentlog import config
from agentlog.capture import covered_key
from agentlog.opencode_db import ingest_opencode_db

CWD = "E:/lab/proj"  # forward slashes on purpose: must normalize


def _make_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, title TEXT,
                              time_created INTEGER, time_updated INTEGER,
                              parent_id TEXT);
        CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT,
                              time_created INTEGER, time_updated INTEGER,
                              data TEXT);
        CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
                           time_created INTEGER, time_updated INTEGER, data TEXT);
    """)
    return con


def _add_message(con, sid, mid, role, text, ts, tool=False):
    con.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                (mid, sid, ts, ts, json.dumps({"role": role})))
    if tool:
        con.execute("INSERT INTO part VALUES (?,?,?,?,?,?)",
                    ("p_t_" + mid, mid, sid, ts, ts,
                     json.dumps({"type": "tool", "tool": {"name": "bash"}})))
    if text is not None:
        con.execute("INSERT INTO part VALUES (?,?,?,?,?,?)",
                    ("p_x_" + mid, mid, sid, ts, ts,
                     json.dumps({"type": "text", "text": text})))


class TestIngestOpencodeDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agoc_")
        self.db = os.path.join(self.tmp, "oc.db")
        self.old_root = config.OUTPUT_ROOT
        config.OUTPUT_ROOT = os.path.join(self.tmp, "out")
        import agentlog.opencode_db as odb
        self.old_path = odb.opencode_db_path
        odb.opencode_db_path = lambda: self.db
        self.odb = odb

    def tearDown(self):
        self.odb.opencode_db_path = self.old_path
        config.OUTPUT_ROOT = self.old_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_forks_merge_tabs_split_probes_skip(self):
        con = _make_db(self.db)
        # 主会话 + 两个 fork (同 title, 带 fork 后缀) -> 合并
        con.execute("INSERT INTO session VALUES (?,?,?,?,?,?)",
                    ("ses_aaaa1", CWD, "分析任务", 1000, 5000, None))
        _add_message(con, "ses_aaaa1", "m1", "user", "问题", 1000)
        _add_message(con, "ses_aaaa1", "m2", "assistant", "回答", 1100)
        con.execute("INSERT INTO session VALUES (?,?,?,?,?,?)",
                    ("ses_bbbb1", CWD, "分析任务 (fork #1)", 1200, 9000, None))
        _add_message(con, "ses_bbbb1", "m3", "user", "问题", 1000)  # 克隆历史
        _add_message(con, "ses_bbbb1", "m4", "assistant", "回答", 1100)
        _add_message(con, "ses_bbbb1", "m5", "assistant", "fork 后的新回答", 1300)
        # 同项目另一个 tab (不同 title) -> 独立文件
        con.execute("INSERT INTO session VALUES (?,?,?,?,?,?)",
                    ("ses_cccc1", CWD, "另一个任务", 2000, 6000, None))
        _add_message(con, "ses_cccc1", "m6", "user", "tab2 问题", 2000)
        _add_message(con, "ses_cccc1", "m7", "assistant", "tab2 回答", 2100)
        # 子代理 (parent_id) -> 跳过
        con.execute("INSERT INTO session VALUES (?,?,?,?,?,?)",
                    ("ses_dddd1", CWD, "子代理", 3000, 3000, "ses_aaaa1"))
        _add_message(con, "ses_dddd1", "m8", "user", "task", 3000)
        # 探测会话 -> 跳过
        con.execute("INSERT INTO session VALUES (?,?,?,?,?,?)",
                    ("ses_eeee1", CWD, "OK", 4000, 4000, None))
        _add_message(con, "ses_eeee1", "m9", "user", "Reply with exactly: OK", 4000)
        _add_message(con, "ses_eeee1", "m10", "assistant", "OK", 4100)
        # 工具前叙述 -> 丢弃
        con.execute("INSERT INTO session VALUES (?,?,?,?,?,?)",
                    ("ses_ffff1", CWD, "叙述任务", 5000, 7000, None))
        _add_message(con, "ses_ffff1", "m11", "assistant", "我去看看", 5000, tool=True)
        _add_message(con, "ses_ffff1", "m12", "assistant", "最终回答", 5100)
        con.commit()
        con.close()

        state = {}
        ingest_opencode_db({"agent": "opencode"}, state)
        odir = os.path.join(config.OUTPUT_ROOT, "opencode")
        files = sorted(os.listdir(odir))
        self.assertEqual(files, sorted(["lab-proj-另一个任务.md",
                                        "lab-proj-分析任务.md",
                                        "lab-proj-叙述任务.md"]))
        main = open(os.path.join(odir, "lab-proj-分析任务.md"),
                    encoding="utf-8").read()
        self.assertEqual(main.count("User"), 1)      # fork 克隆去重
        self.assertIn("fork 后的新回答", main)
        narr = open(os.path.join(odir, "lab-proj-叙述任务.md"),
                    encoding="utf-8").read()
        self.assertNotIn("我去看看", narr)            # 工具叙述丢弃
        self.assertIn("最终回答", narr)

    def test_covered_suppresses(self):
        con = _make_db(self.db)
        con.execute("INSERT INTO session VALUES (?,?,?,?,?,?)",
                    ("ses_aaaa1", CWD, "分析任务", 1000, 5000, None))
        _add_message(con, "ses_aaaa1", "m1", "user", "问题", 1000)
        _add_message(con, "ses_aaaa1", "m2", "assistant", "回答", 1100)
        con.commit()
        con.close()
        state = {"_capture_covered": {covered_key("opencode", CWD): True}}
        ingest_opencode_db({"agent": "opencode"}, state)
        self.assertFalse(os.path.isdir(os.path.join(config.OUTPUT_ROOT, "opencode")))


if __name__ == "__main__":
    unittest.main()
