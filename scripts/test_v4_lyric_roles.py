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
        self.assertEqual(result["groups"][0]["alternatives"][0]["role"], "original")

    def test_korean_native_line_beats_latin_same_timestamp_without_guessing_latin_role(self):
        result = self.inspect(
            "[00:01.00]사랑해 너를\n[00:01.00]saranghae neoreul\n",
            "ko",
        )
        alternatives = result["groups"][0]["alternatives"]
        self.assertEqual([row["role"] for row in alternatives], ["original", "unknown"])

    def test_english_original_can_be_selected_against_cjk_translation(self):
        result = self.inspect(
            "[00:01.00]we go together\n[00:01.00]我们一起走\n",
            "en",
        )
        alternatives = result["groups"][0]["alternatives"]
        self.assertEqual([row["role"] for row in alternatives], ["original", "unknown"])

    def test_two_han_rows_at_same_timestamp_fail_closed_for_chinese(self):
        with self.assertRaisesRegex(LyricRoleError, "canonical original is ambiguous"):
            self.inspect(
                "[00:01.00]我们一起走\n[00:01.00]我們一起走\n",
                "zh",
            )

    def test_auto_language_with_two_alternatives_fails_closed(self):
        with self.assertRaises(LyricRoleError):
            self.inspect(
                "[00:01.00]first candidate\n[00:01.00]第二候选\n",
                "auto",
            )

    def test_explicit_original_index_resolves_same_script_ambiguity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.lrc"
            path.write_text(
                "[00:01.00]我们一起走\n[00:01.00]我們一起走\n",
                encoding="utf-8",
            )
            result = inspect_lyric_roles(
                path,
                language="zh",
                original_index_overrides={1000: 1},
            )
            alternatives = result["groups"][0]["alternatives"]
            self.assertEqual([row["role"] for row in alternatives], ["unknown", "original"])

    def test_invalid_original_index_override_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.lrc"
            path.write_text("[00:01.00]line\n", encoding="utf-8")
            with self.assertRaisesRegex(LyricRoleError, "out of range"):
                inspect_lyric_roles(
                    path,
                    language="en",
                    original_index_overrides={1000: 4},
                )


if __name__ == "__main__":
    unittest.main()
