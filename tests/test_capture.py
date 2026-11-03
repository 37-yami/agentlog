"""Regression tests for proxy capture ingestion (daily aggregation etc.)."""
import json
import os
import shutil
import tempfile
import unittest

from agentlog import config
from agentlog.capture import CAPTURE_DIR, covered_key, ingest_capture, norm_cwd


class TestCapture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agcap_")
        self.old_cap = CAPTURE_DIR
        self.old_root = config.OUTPUT_ROOT
        import agentlog.capture as cap
        cap.CAPTURE_DIR = os.path.join(self.tmp, "capture")
        cap._capture_cache.clear()
        config.OUTPUT_ROOT = os.path.join(self.tmp, "out")
        self.cap = cap

    def tearDown(self):
        self.cap.CAPTURE_DIR = self.old_cap
        self.cap._capture_cache.clear()
        config.OUTPUT_ROOT = self.old_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _turn(self, day, ts, u, a):
        return json.dumps(
            {"ts": ts, "agent": "claude", "cwd": "E:\\lab\\proj", "model": "m",
             "minor": False, "user": u, "assistant": a}, ensure_ascii=False)

    def test_multi_day_aggregation(self):
        d = os.path.join(self.cap.CAPTURE_DIR, "claude")
        os.makedirs(d)
        with open(os.path.join(d, "20261031.jsonl"), "w", encoding="utf-8") as f:
            f.write(self._turn("d1", "2026-10-31T10:00:00+08:00",
                               "第一天的问题", "第一天的回答") + "\n")
        with open(os.path.join(d, "20261101.jsonl"), "w", encoding="utf-8") as f:
            f.write(self._turn("d2", "2026-11-01T22:00:00+08:00",
                               "第二天的问题", "第二天的回答") + "\n")
        state = {}
        ingest_capture(state)
        exp = os.path.join(config.OUTPUT_ROOT, "claude", "lab-proj.md")
        content = open(exp, encoding="utf-8").read()
        self.assertIn("第一天的问题", content)
        self.assertIn("第二天的问题", content)
        ck = covered_key("claude", "E:\\lab\\proj")  # same form as the data
        self.assertIn(ck, state["_capture_covered"])

    def test_minor_skipped_and_covered_rebuilds(self):
        d = os.path.join(self.cap.CAPTURE_DIR, "claude")
        os.makedirs(d)
        fn = os.path.join(d, "20261101.jsonl")
        with open(fn, "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-11-01T10:00:00+08:00",
                                "agent": "claude", "cwd": "E:\\p", "minor": True,
                                "user": "x", "assistant": "y"}) + "\n")
        state = {}
        ingest_capture(state)
        self.assertEqual(state["_capture_covered"], {})  # minor 不算覆盖
        os.remove(fn)
        self.cap._capture_cache.clear()
        ingest_capture(state)
        self.assertEqual(state["_capture_covered"], {})  # 数据删光自动解除

    def test_norm_cwd_platform(self):
        if os.name == "nt":
            self.assertEqual(norm_cwd("/home/u/proj/"), "\\home\\u\\proj")
            self.assertEqual(norm_cwd("E:/a/b"), "E:\\a\\b")
        else:
            self.assertEqual(norm_cwd("/home/u/proj/"), "/home/u/proj")
            self.assertEqual(norm_cwd("E:/a/b"), "E:/a/b")


if __name__ == "__main__":
    unittest.main()
