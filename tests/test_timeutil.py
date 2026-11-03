"""Regression tests for agentlog.timeutil."""
import unittest

from agentlog.timeutil import parse_ts, path_tail


class TestParseTs(unittest.TestCase):
    def test_ms_epoch(self):
        dt = parse_ts(1760000000000)
        # Beijing wall time: 2025-10-09 08:53:20 UTC -> 16:53 +08:00
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour), (2025, 10, 9, 16))

    def test_seconds_epoch(self):
        self.assertEqual(parse_ts(1760000000).year, 2025)

    def test_iso_z(self):
        self.assertEqual(parse_ts("2026-10-12T08:53:01Z").hour, 16)  # +8

    def test_iso_naive_treated_as_utc(self):
        self.assertEqual(parse_ts("2026-10-12T08:53:01").hour, 16)

    def test_garbage(self):
        self.assertIsNone(parse_ts("not a time"))
        self.assertIsNone(parse_ts(""))
        self.assertIsNone(parse_ts(None))


class TestPathTail(unittest.TestCase):
    def test_windows_three(self):
        self.assertEqual(path_tail("E:\\lab\\proj\\v2"), "lab-proj-v2")

    def test_forward_slashes(self):
        self.assertEqual(path_tail("/home/u/proj"), "home-u-proj")

    def test_few_components(self):
        self.assertEqual(path_tail("E:\\proj"), "proj")

    def test_empty(self):
        self.assertEqual(path_tail(""), "unknown")

    def test_sanitizes(self):
        self.assertEqual(path_tail("E:\\a\\b: c"), "a-b- c")  # ':' -> '-', space kept


if __name__ == "__main__":
    unittest.main()
