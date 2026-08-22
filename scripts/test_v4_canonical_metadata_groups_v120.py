from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lyric_aligner.text.canonical_lyrics import CanonicalLyricError, parse_canonical_lyrics


class CanonicalMetadataGroupsV120Tests(unittest.TestCase):
    def _parse(self, content: str, selection: dict[int, int] | None = None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.lrc"
            path.write_text(content, encoding="utf-8")
            return parse_canonical_lyrics(path, original_index_by_timestamp=selection)

    def test_common_timed_credits_title_and_role_labels_are_not_lyrics(self) -> None:
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

    def test_cjk_and_mixed_singer_role_labels_are_not_lyrics(self) -> None:
        lines = self._parse(
            "[00:01.00]合：\n"
            "[00:01.50]主唱：\n"
            "[00:02.00]男声：\n"
            "[00:02.50]女声：\n"
            "[00:03.00]周小雨/林晓/孙子涵：\n"
            "[00:04.00]Nova/River（Rap）：\n"
            "[00:05.00]李明（主唱）：\n"
            "[00:10.00]第一句真实歌词\n"
            "[00:20.00]第二句真实歌词\n"
        )
        self.assertEqual(
            [item.text for item in lines],
            ["第一句真实歌词", "第二句真实歌词"],
        )

    def test_bare_cjk_colon_lines_fail_closed_as_lexical(self) -> None:
        lines = self._parse(
            "[00:01.00]夏天：\n"
            "[00:02.00]白天：\n"
            "[00:03.00]向前：\n"
            "[00:04.00]李明：\n"
        )
        self.assertEqual(
            [item.text for item in lines],
            ["夏天：", "白天：", "向前：", "李明："],
        )

    def test_explicit_multi_cast_can_prove_matching_bare_role_name(self) -> None:
        lines = self._parse(
            "[00:01.00]李明/王芳：\n"
            "[00:02.00]李明：\n"
            "[00:03.00]夏天：\n"
            "[00:04.00]第一句真实歌词\n"
        )
        self.assertEqual(
            [item.text for item in lines],
            ["夏天：", "第一句真实歌词"],
        )

    def test_repeated_bare_role_requires_strong_ensemble_context(self) -> None:
        lines = self._parse(
            "[00:01.00]王甲/李乙/赵丙/陈丁：\n"
            "[00:02.00]王甲：\n"
            "[00:02.50]甲的歌词\n"
            "[00:04.00]李乙：\n"
            "[00:04.50]乙的歌词\n"
            "[00:06.00]赵丙：\n"
            "[00:06.50]丙的歌词\n"
            "[00:08.00]陈丁：\n"
            "[00:08.50]丁的歌词\n"
            "[00:10.00]周戊：\n"
            "[00:10.50]重复角色歌词一\n"
            "[00:12.00]周戊：\n"
            "[00:12.50]重复角色歌词二\n"
            "[00:14.00]夏天：\n"
        )
        self.assertEqual(
            [item.text for item in lines],
            [
                "甲的歌词",
                "乙的歌词",
                "丙的歌词",
                "丁的歌词",
                "重复角色歌词一",
                "重复角色歌词二",
                "夏天：",
            ],
        )

    def test_short_chinese_lyric_question_is_not_treated_as_role_label(self) -> None:
        lines = self._parse(
            "[00:01.00]为什么：\n"
            "[00:02.00]我还在这里等你\n"
        )
        self.assertEqual(
            [item.text for item in lines],
            ["为什么：", "我还在这里等你"],
        )

    def test_non_metadata_chinese_text_is_not_overfiltered(self) -> None:
        lines = self._parse(
            "[00:01.00]监制不住心里的想念\n"
            "[00:02.00]出品一场自己的故事\n"
        )
        self.assertEqual(
            [item.text for item in lines],
            ["监制不住心里的想念", "出品一场自己的故事"],
        )

    def test_explicit_selection_cannot_reintroduce_metadata(self) -> None:
        with self.assertRaisesRegex(CanonicalLyricError, "metadata/blank"):
            self._parse(
                "[00:01.00]编曲：某某\n[00:02.00]真正歌词\n",
                selection={1000: 0},
            )


if __name__ == "__main__":
    unittest.main()
