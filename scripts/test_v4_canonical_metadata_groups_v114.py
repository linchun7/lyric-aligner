from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lyric_aligner.text.canonical_lyrics import parse_canonical_lyrics


class CanonicalMetadataGroupsV114Tests(unittest.TestCase):
    def _parse(self, content: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.lrc"
            path.write_text(content, encoding="utf-8")
            return parse_canonical_lyrics(path)

    def test_common_timed_credits_and_role_labels_are_not_lyrics(self) -> None:
        lines = self._parse(
            "[00:00.10]歌手 - 歌名\n"
            "[00:01.00]编曲：某某\n"
            "[00:02.00]监制：某某\n"
            "[00:03.00]Felix Bennett：\n"
            "[00:04.00]h3R3:\n"
            "[00:10.00]第一句真实歌词\n"
            "[00:20.00]第二句真实歌词\n"
        )

        self.assertEqual(
            [item.text for item in lines],
            ["第一句真实歌词", "第二句真实歌词"],
        )
        self.assertEqual([item.time_ms for item in lines], [10_000, 20_000])

    def test_non_metadata_chinese_text_is_not_overfiltered(self) -> None:
        lines = self._parse(
            "[00:01.00]监制不住心里的想念\n"
            "[00:02.00]出品一场自己的故事\n"
        )
        self.assertEqual(
            [item.text for item in lines],
            ["监制不住心里的想念", "出品一场自己的故事"],
        )


if __name__ == "__main__":
    unittest.main()
