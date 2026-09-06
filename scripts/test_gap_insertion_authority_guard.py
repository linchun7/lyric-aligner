import unittest
from pathlib import Path


class GapInsertionAuthorityGuardTests(unittest.TestCase):
    def test_automatic_projected_gap_insertion_never_self_grants_manual_authority(self):
        source = (Path(__file__).resolve().parent / "redo_karaoke_pipeline.py").read_text(encoding="utf-8")
        marker = '"status": "inserted_missing_lyric"'
        start = source.index(marker)
        block = source[start : start + 700]
        self.assertIn("projected_lrc_gap_candidate_no_boundary_authority", block)
        self.assertNotIn('"boundary_authority": "manual_verified_interval"', block)


if __name__ == "__main__":
    unittest.main()
