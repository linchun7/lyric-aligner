import tempfile
import unittest
from pathlib import Path

from lyric_aligner.review.text_region_adjudication import Policy, _partition_words, adjudicate_regions
from lyric_aligner.text_repair import SubtitleCue, _normalize_for_match


def cue(ordinal: int, text: str, start: int) -> SubtitleCue:
    return SubtitleCue(
        ordinal=ordinal,
        number=str(ordinal + 1),
        timing=f"00:00:{start:02d},000 --> 00:00:{start + 1:02d},000",
        text=text,
        normalized=_normalize_for_match(text),
        raw_block_index=ordinal,
    )


class TextRegionAdjudicationTest(unittest.TestCase):
    def test_word_partition_preserves_complete_canonical_stream(self):
        result = _partition_words(
            ["to those who are free", "the mind shall be key"],
            "To those who are free their minds shall be key",
            Policy(),
        )
        self.assertIsNotNone(result)
        assignments, scores = result
        self.assertEqual(assignments, ["To those who are free", "their minds shall be key"])
        self.assertEqual(
            _normalize_for_match(" ".join(assignments)),
            _normalize_for_match("To those who are free their minds shall be key"),
        )
        self.assertEqual(len(scores), 2)

    def _fixture(self, *, cross_source=False, reason="layout_boundary_insertion_requires_review"):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        first = root / "a.lrc"
        first.write_text(
            "[00:00.00]Left anchor\n"
            "[00:01.00]To those who are free their minds shall be key\n"
            "[00:02.00]Right anchor\n",
            encoding="utf-8",
        )
        manifest = {"canonical_lyrics": [{"ordinal": 0, "path": str(first)}]}
        if cross_source:
            second = root / "b.lrc"
            second.write_text("[00:00.00]Right anchor\n", encoding="utf-8")
            manifest = {
                "canonical_lyrics": [
                    {"ordinal": 0, "path": str(first)},
                    {"ordinal": 1, "path": str(second)},
                ]
            }
        rows = [
            {"cue_ordinal": 0, "action": "replace", "canonical_span": [0, 1]},
            {"cue_ordinal": 1, "action": "review", "reason": reason, "canonical_span": [1, 2]},
            {"cue_ordinal": 2, "action": "review", "reason": reason, "canonical_span": [1, 2]},
            {
                "cue_ordinal": 3,
                "action": "replace",
                "canonical_span": [3, 4] if cross_source else [2, 3],
            },
        ]
        safe_report = {
            "remaining_manual": 2,
            "decisions": [
                {"cue_ordinal": 1, "resolution": "unresolved_manual", "canonical_span": [1, 2]},
                {"cue_ordinal": 2, "resolution": "unresolved_manual", "canonical_span": [1, 2]},
            ],
        }
        cues = [
            cue(0, "Left anchor", 0),
            cue(1, "to those who are free", 2),
            cue(2, "the mind shall be key", 4),
            cue(3, "Right anchor", 6),
        ]
        return temp, root, manifest, {"text_decisions": rows}, safe_report, cues

    def test_exact_same_source_bracket_resolves_region(self):
        temp, root, manifest, smart, safe, cues = self._fixture()
        self.addCleanup(temp.cleanup)
        result = adjudicate_regions(
            repo_root=root,
            manifest=manifest,
            smart=smart,
            safe_report=safe,
            cues=cues,
            transition_boundaries_ms=[],
        )
        self.assertEqual(result["resolved_region_count"], 1)
        self.assertEqual(result["resolved_cue_count"], 2)
        self.assertEqual(result["remaining_manual"], 0)
        self.assertEqual(result["replacements"][1], "To those who are free")
        self.assertEqual(result["replacements"][2], "their minds shall be key")

    def test_multi_line_gap_with_missing_canonical_content_abstains(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        lrc = root / "a.lrc"
        lrc.write_text(
            "[00:00.00]Left anchor\n"
            "[00:01.00]I don't play no games so don't don't get it confused no\n"
            "[00:02.00]Cause you will lose yeah\n"
            "[00:03.00]Now now pump pump pump pum pum pump pump it up\n"
            "[00:04.00]And back it up like a Tonka truck\n"
            "[00:05.00]Dale\n"
            "[00:06.00]Right anchor\n",
            encoding="utf-8",
        )
        manifest = {"canonical_lyrics": [{"ordinal": 0, "path": str(lrc)}]}
        smart = {"text_decisions": [
            {"cue_ordinal": 0, "action": "replace", "canonical_span": [0, 1]},
            {"cue_ordinal": 1, "action": "review", "reason": "adjacent_alignment_gap_requires_review", "canonical_span": [1, 4]},
            {"cue_ordinal": 2, "action": "review", "reason": "adjacent_alignment_gap_requires_review", "canonical_span": [1, 4]},
            {"cue_ordinal": 3, "action": "review", "reason": "adjacent_alignment_gap_requires_review", "canonical_span": [1, 4]},
            {"cue_ordinal": 4, "action": "review", "reason": "adjacent_alignment_gap_requires_review", "canonical_span": [1, 4]},
            {"cue_ordinal": 5, "action": "replace", "canonical_span": [6, 7]},
        ]}
        safe = {"remaining_manual": 4, "decisions": [
            {"cue_ordinal": i, "resolution": "unresolved_manual", "canonical_span": [1, 4]}
            for i in range(1, 5)
        ]}
        cues = [
            cue(0, "Left anchor", 0),
            cue(1, "I don't play no games", 2),
            cue(2, "so don't don't don't", 4),
            cue(3, "don't get it confused no cause you will lose", 6),
            cue(4, "yeah now now pump pump pump pump pump pump it up", 8),
            cue(5, "Right anchor", 10),
        ]
        result = adjudicate_regions(
            repo_root=root,
            manifest=manifest,
            smart=smart,
            safe_report=safe,
            cues=cues,
            transition_boundaries_ms=[],
        )
        self.assertEqual(result["resolved_cue_count"], 0)
        self.assertIn(result["decisions"][0]["reason"], {
            "multi_line_region_similarity_below_policy",
            "multi_line_length_ratio_outside_policy",
            "multi_line_canonical_coverage_below_policy",
            "multi_line_line_coverage_below_policy",
            "multi_line_short_line_not_observed",
        })

    def test_transition_guard_abstains(self):
        temp, root, manifest, smart, safe, cues = self._fixture()
        self.addCleanup(temp.cleanup)
        result = adjudicate_regions(
            repo_root=root,
            manifest=manifest,
            smart=smart,
            safe_report=safe,
            cues=cues,
            transition_boundaries_ms=[3000],
        )
        self.assertEqual(result["resolved_cue_count"], 0)
        self.assertEqual(result["decisions"][0]["reason"], "near_unresolved_transition")

    def test_split_word_review_remains_manual(self):
        temp, root, manifest, smart, safe, cues = self._fixture(reason="cue_boundary_splits_canonical_latin_word")
        self.addCleanup(temp.cleanup)
        result = adjudicate_regions(
            repo_root=root,
            manifest=manifest,
            smart=smart,
            safe_report=safe,
            cues=cues,
            transition_boundaries_ms=[],
        )
        self.assertEqual(result["resolved_cue_count"], 0)
        self.assertEqual(result["decisions"][0]["reason"], "blocked_review_reason")

    def test_cross_source_bracket_abstains(self):
        temp, root, manifest, smart, safe, cues = self._fixture(cross_source=True)
        self.addCleanup(temp.cleanup)
        result = adjudicate_regions(
            repo_root=root,
            manifest=manifest,
            smart=smart,
            safe_report=safe,
            cues=cues,
            transition_boundaries_ms=[],
        )
        self.assertEqual(result["resolved_cue_count"], 0)
        self.assertIn(result["decisions"][0]["reason"], {"bracket_crosses_source_boundary", "canonical_gap_crosses_source"})


if __name__ == "__main__":
    unittest.main()
