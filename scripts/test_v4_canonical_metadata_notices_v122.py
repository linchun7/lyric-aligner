from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lyric_aligner.text.canonical_lyrics import parse_canonical_lyrics
from lyric_aligner.text.normalization import is_metadata_text


class CanonicalMetadataNoticesV122Tests(unittest.TestCase):
    def test_license_notice_is_metadata(self) -> None:
        self.assertTrue(is_metadata_text("【未经授权不得翻唱或使用】"))

    def test_prefixed_business_contact_is_metadata(self) -> None:
        self.assertTrue(
            is_metadata_text("平台音乐人商务合作：contact@example.com")
        )

    def test_ordinary_business_wording_without_contact_marker_is_not_broadly_removed(self) -> None:
        self.assertFalse(is_metadata_text("我们谈一场商务合作般的爱情"))

    def test_timestamped_notices_are_ignored_but_lyrics_remain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.lrc"
            path.write_text(
                "[00:01.00]【未经授权不得翻唱或使用】\n"
                "[00:02.00]平台音乐人商务合作：contact@example.com\n"
                "[00:03.00]第一句真正歌词\n"
                "[00:05.00]第二句真正歌词\n",
                encoding="utf-8",
            )
            rows = parse_canonical_lyrics(path)
        self.assertEqual([item.text for item in rows], ["第一句真正歌词", "第二句真正歌词"])


if __name__ == "__main__":
    unittest.main()
