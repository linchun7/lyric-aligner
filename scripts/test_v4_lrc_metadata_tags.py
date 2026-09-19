import tempfile
import unittest
from pathlib import Path

from lyric_aligner.assets.lyric_roles import inspect_lyric_roles


class V4LrcMetadataTagTests(unittest.TestCase):
    def test_timestamped_standard_lrc_tags_are_metadata_not_lyrics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.lrc"
            path.write_text(
                "[00:00.000]作曲 : David Macias/Stefan Gordy\n"
                "[00:00.000][by:alotofmeaning]\n"
                "[00:00.000][ar:redfoo]\n"
                "[00:00.000][ti:New Thang]\n"
                "[00:00.860]Redfoo - New Thang\n"
                "[00:09.740]Oh, the way that you pop, girl\n",
                encoding="utf-8",
            )
            result = inspect_lyric_roles(path, language="en")

        self.assertEqual(result["canonical_original_count"], 1)
        self.assertEqual(result["lexical_timestamp_group_count"], 1)
        self.assertEqual(result["ignored_metadata_group_count"], 2)
        self.assertEqual(result["groups"][0]["timestamp_ms"], 9740)

    def test_nonstandard_bracketed_lyric_is_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.lrc"
            path.write_text("[00:01.000][Verse] sing it loud\n", encoding="utf-8")
            result = inspect_lyric_roles(path, language="en")

        self.assertEqual(result["canonical_original_count"], 1)
        self.assertEqual(result["groups"][0]["alternatives"][0]["text"], "[Verse] sing it loud")


if __name__ == "__main__":
    unittest.main()
