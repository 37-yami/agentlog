"""Regression tests for agentlog.extract."""
import unittest

from agentlog.extract import (clean_title, decode_folder_cwd, extract_text,
                              first_user_title, resolve_cwd)


class TestExtractText(unittest.TestCase):
    def test_text_and_thinking(self):
        t, th = extract_text([
            {"type": "text", "text": "hello"},
            {"type": "thinking", "thinking": "let me think"},
            {"type": "tool_use", "input": {}},
        ])
        self.assertEqual(t, "hello")
        self.assertEqual(th, "let me think")

    def test_nested(self):
        t, th = extract_text([
            {"type": "text", "content": [
                {"type": "text", "text": "a"},
                {"type": "thinking", "text": "b"},
            ]},
        ])
        self.assertEqual(t, "a")
        self.assertEqual(th, "b")

    def test_plain_string(self):
        self.assertEqual(extract_text("  hi "), ("hi", ""))


class TestTitles(unittest.TestCase):
    def test_clean_title(self):
        self.assertEqual(clean_title('a/b\\c:d*e?f"g<h>i|j'), "a b c d e f g h i j")
        self.assertEqual(clean_title("  x  y  "), "x y")
        self.assertEqual(clean_title("t" * 80), "t" * 40)
        self.assertEqual(clean_title(None), "")

    def test_first_user_title_skips_injections(self):
        msgs = [(None, "user", "<local-command-caveat>injected", ""),
                (None, "assistant", "hi", ""),
                (None, "user", "真实提问内容", "")]
        self.assertEqual(first_user_title(msgs), "真实提问内容")

    def test_first_user_title_none_when_only_injections(self):
        self.assertEqual(first_user_title([(None, "user", "<x>", "")]), "")


class TestDecodeFolderCwd(unittest.TestCase):
    def test_pi_style(self):
        self.assertEqual(decode_folder_cwd("--E--other--project--"), "E:\\other\\project")

    def test_pi_style_single_dir(self):
        self.assertEqual(decode_folder_cwd("--E--other-project--"), "E:\\other-project")

    def test_claude_style(self):
        self.assertEqual(decode_folder_cwd("e-lab-Group_meeting-202608"),
                         "E:\\lab\\Group_meeting\\202608")

    def test_undecodable(self):
        self.assertIsNone(decode_folder_cwd("nodashes"))

    def test_resolve_prefers_raw(self):
        self.assertEqual(resolve_cwd("E:\\x\\y", "e-x"), "E:\\x\\y")


if __name__ == "__main__":
    unittest.main()
