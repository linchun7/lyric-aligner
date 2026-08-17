import tempfile
import unittest
from pathlib import Path

from lyric_aligner.assets.lyric_roles import LyricRoleError, inspect_lyric_roles


class V4LyricRoleTests(unittest.TestCase):
    def inspect(self, content: str, language: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.lrc"
            path.write_text(content, encoding="utf-8")
            return inspect_lyric_roles(path, language=language)

    def test_single_timestamp_line_is_canonical_even_for_auto_language(self):
        result = self.inspect("[00:01.00]opaque canonical line\n", "auto")
        self.assertEqual(result["canonical_original_count"], 1)

    def test_korean_native_line_beats_latin_without_guessing_latin_role(self):
        result = self.inspect("[00:01.00]사랑해 너를\n[00:01.00]saranghae neoreul\n", "ko")
        self.assertEqual([row["role"] for row in result["groups"][0]["alternatives"]], ["original", "unknown"])

    def test_two_han_rows_fail_closed(self):
        with self.assertRaisesRegex(LyricRoleError, "ambiguous"):
            self.inspect("[00:01.00]我们一起走\n[00:01.00]我們一起走\n", "zh")

    def test_explicit_original_index_resolves_same_script_ambiguity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.lrc"
            path.write_text("[00:01.00]我们一起走\n[00:01.00]我們一起走\n", encoding="utf-8")
            result = inspect_lyric_roles(path, language="zh", original_index_overrides={1000: 1})
            self.assertEqual([row["role"] for row in result["groups"][0]["alternatives"]], ["unknown", "original"])

    def test_original_index_override_cannot_select_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.lrc"
            path.write_text("[00:01.00]作词: someone\n[00:01.00]real lyric\n", encoding="utf-8")
            with self.assertRaisesRegex(LyricRoleError, "selects metadata"):
                inspect_lyric_roles(path, language="zh", original_index_overrides={1000: 0})

    def test_enhanced_lrc_markup_does_not_hide_metadata(self):
        result = self.inspect(
            "[00:01.00]<00:01.00>作词: someone\n[00:01.00]<00:01.00>真实歌词\n",
            "zh",
        )
        self.assertEqual([row["role"] for row in result["groups"][0]["alternatives"]], ["metadata", "original"])
        self.assertIn("enhanced_lrc", result["groups"][0]["formats"])

    def test_qrc_is_supported_by_role_preflight(self):
        result = self.inspect("[1000,2000]Hello(0,500) world(500,600)\n", "en")
        group = result["groups"][0]
        self.assertEqual(group["timestamp_ms"], 1000)
        self.assertEqual(group["alternatives"][0]["text"], "Hello world")
        self.assertEqual(group["alternatives"][0]["role"], "original")
        self.assertIn("qrc", group["formats"])

    def test_qrc_alternatives_accept_explicit_original_index(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.lrc"
            path.write_text("[1000,2000]translation(0,500)\n[1000,2000]canonical(0,500)\n", encoding="utf-8")
            result = inspect_lyric_roles(path, language="en", original_index_overrides={1000: 1})
            self.assertEqual([row["role"] for row in result["groups"][0]["alternatives"]], ["unknown", "original"])


if __name__ == "__main__":
    unittest.main()
