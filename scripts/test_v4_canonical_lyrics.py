import tempfile
import unittest
from pathlib import Path

from lyric_aligner.text.canonical_lyrics import CanonicalLyricError, parse_canonical_lyrics


class V4CanonicalLyricTests(unittest.TestCase):
    def parse(self, content: str, *, selection=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.lrc"
            path.write_text(content, encoding="utf-8")
            return parse_canonical_lyrics(path, original_index_by_timestamp=selection)

    def test_same_timestamp_second_alternative_can_be_canonical(self):
        lines = self.parse(
            "[00:01.00]翻译行\n[00:01.00]真正原文\n[00:03.00]下一句\n",
            selection={1000: 1, 3000: 0},
        )
        self.assertEqual([line.text for line in lines], ["真正原文", "下一句"])

    def test_ambiguous_same_timestamp_without_selection_blocks(self):
        with self.assertRaisesRegex(CanonicalLyricError, "ambiguous"):
            self.parse("[00:01.00]候选一\n[00:01.00]候选二\n")

    def test_enhanced_lrc_preserves_word_timing(self):
        lines = self.parse("[00:01.00]<00:01.00>Hello <00:01.50>world\n", selection={1000: 0})
        self.assertEqual(lines[0].text, "Hello world")
        self.assertEqual([token.start_ms for token in lines[0].tokens], [1000, 1500])

    def test_qrc_preserves_token_timing(self):
        lines = self.parse("[1000,2000]Hello(0,500) world(500,600)\n")
        self.assertEqual(lines[0].text, "Hello world")
        self.assertEqual(lines[0].timing_format, "qrc_word_timing")
        self.assertEqual([token.start_ms for token in lines[0].tokens], [1000, 1500])

    def test_qrc_second_alternative_can_be_canonical(self):
        lines = self.parse(
            "[1000,2000]translation(0,500)\n[1000,2000]canonical(0,500)\n",
            selection={1000: 1},
        )
        self.assertEqual([line.text for line in lines], ["canonical"])

    def test_qrc_ambiguous_alternatives_block_without_selection(self):
        with self.assertRaisesRegex(CanonicalLyricError, "ambiguous"):
            self.parse("[1000,2000]first(0,500)\n[1000,2000]second(0,500)\n")

    def test_empty_timestamp_rows_do_not_shift_selection_indexes(self):
        lines = self.parse(
            "[00:01.00]\n[00:01.00]translation\n[00:01.00]canonical\n",
            selection={1000: 1},
        )
        self.assertEqual([line.text for line in lines], ["canonical"])

    def test_selection_cannot_point_to_metadata(self):
        with self.assertRaisesRegex(CanonicalLyricError, "metadata"):
            self.parse("[00:01.00]作词: someone\n[00:01.00]real lyric\n", selection={1000: 0})


if __name__ == "__main__":
    unittest.main()
