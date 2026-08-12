import json
import tempfile
import unittest
from pathlib import Path

from redo_karaoke_pipeline import (
    Cue,
    Track,
    apply_global_sequence_repairs,
    auto_resegment_high_confidence_boundaries,
    deduplicate_boundary_vocalizations,
    discard_noncanonical_vocalization_rows,
    evaluate_regression_cases,
    global_sequence_alignment,
    is_generic_vocalization,
    preferred_boundary_observation,
    sha256,
    validate_source_srt_scope,
)


class GlobalSequenceAlignmentTests(unittest.TestCase):
    def test_repeated_phrase_uses_global_order(self):
        observations = [
            {"id": 1, "start_ms": 900, "end_ms": 1700, "text": "signal returns"},
            {
                "id": 2,
                "start_ms": 2000,
                "end_ms": 3300,
                "text": "middle sequence stays unique",
            },
            {"id": 3, "start_ms": 4900, "end_ms": 5700, "text": "signal returns"},
        ]
        events = [
            {"lrc_index": 1, "projected_ms": 1000, "text": "signal returns"},
            {
                "lrc_index": 2,
                "projected_ms": 2200,
                "text": "middle sequence stays unique",
            },
            {"lrc_index": 3, "projected_ms": 5000, "text": "signal returns"},
        ]

        result = global_sequence_alignment(observations, events)

        self.assertEqual(
            [row["event_indices"] for row in result["matches"]],
            [[1], [2], [3]],
        )
        self.assertEqual(result["skipped_lyric_indices"], [])

    def test_global_repair_adds_suffix_line_hidden_by_local_match(self):
        cues = [
            Cue(1, 1000, 2200, "alpha opens beta continues"),
            Cue(2, 2200, 4000, "bridge completes gamma follows"),
        ]
        track = Track(1, 0, 5000, "artist", "title", "")
        events = [
            {"track_index": 1, "track": "title", "lrc_index": 1, "projected_ms": 1100, "text": "alpha opens", "mapping_method": "test"},
            {"track_index": 1, "track": "title", "lrc_index": 2, "projected_ms": 1600, "text": "beta continues", "mapping_method": "test"},
            {"track_index": 1, "track": "title", "lrc_index": 3, "projected_ms": 2100, "text": "bridge completes", "mapping_method": "test"},
            {"track_index": 1, "track": "title", "lrc_index": 4, "projected_ms": 3100, "text": "gamma follows", "mapping_method": "test"},
        ]
        cue_events = {1: events[:2], 2: [events[3]]}

        repairs, _ = apply_global_sequence_repairs(
            cues, cue_events, events, [track], set()
        )

        self.assertGreaterEqual(repairs, 1)
        self.assertEqual(
            [row["lrc_index"] for row in cue_events[2]],
            [3, 4],
        )

    def test_global_repair_cannot_drop_unique_existing_coverage(self):
        cues = [Cue(1, 1000, 2600, "alpha beta gamma")]
        track = Track(1, 0, 5000, "artist", "title", "")
        events = [
            {"track_index": 1, "track": "title", "lrc_index": 93, "projected_ms": 1100, "text": "alpha beta gamma", "mapping_method": "test"},
            {"track_index": 1, "track": "title", "lrc_index": 94, "projected_ms": 4200, "text": "beta beta beta", "mapping_method": "test"},
        ]
        cue_events = {1: list(events)}

        repairs, _ = apply_global_sequence_repairs(
            cues, cue_events, events, [track], set()
        )

        self.assertEqual(repairs, 0)
        self.assertEqual(
            [row["lrc_index"] for row in cue_events[1]],
            [93, 94],
        )

    def test_merged_jianying_cell_consumes_consecutive_lyrics(self):
        observations = [
            {
                "id": 10,
                "start_ms": 1000,
                "end_ms": 4000,
                "text": "first sequence second sequence",
            }
        ]
        events = [
            {"lrc_index": 20, "projected_ms": 1200, "text": "first sequence"},
            {"lrc_index": 21, "projected_ms": 2500, "text": "second sequence"},
        ]

        result = global_sequence_alignment(observations, events)

        self.assertEqual(result["matches"][0]["event_indices"], [20, 21])
        self.assertEqual(result["skipped_lyric_indices"], [])

    def test_one_lrc_line_can_continue_across_adjacent_cells(self):
        observations = [
            {
                "id": 1,
                "start_ms": 1000,
                "end_ms": 2200,
                "text": "alpha opens beta continues bridge",
            },
            {
                "id": 2,
                "start_ms": 2200,
                "end_ms": 4000,
                "text": "bridge completes gamma follows",
            },
        ]
        events = [
            {"lrc_index": 49, "projected_ms": 1100, "text": "alpha opens"},
            {"lrc_index": 50, "projected_ms": 1600, "text": "beta continues"},
            {"lrc_index": 51, "projected_ms": 2100, "text": "bridge bridge completes"},
            {"lrc_index": 52, "projected_ms": 3100, "text": "gamma follows"},
        ]

        result = global_sequence_alignment(observations, events)

        self.assertEqual(
            [row["event_indices"] for row in result["matches"]],
            [[49, 50, 51], [51, 52]],
        )
        self.assertEqual(result["skipped_lyric_indices"], [])


class BoundaryEvidenceTests(unittest.TestCase):
    def test_word_asr_can_replace_bad_jianying_observation(self):
        row = {
            "original": "random phonetic noise",
            "text": "가나다라마바사",
            "asr_text": "가나다라마바사",
            "asr_score": "0.93",
        }

        observation, source = preferred_boundary_observation(row)

        self.assertEqual(observation, "가나다라마바사")
        self.assertEqual(source, "word_asr")

    def test_short_english_edge_is_restored_even_when_whole_cue_gain_is_small(self):
        rows = [
            {
                "kind": "existing",
                "original_cue": "1",
                "start_ms": 1000,
                "end_ms": 2000,
                "track": "song",
                "original": "alpha beta gamma delta",
                "text": "Alpha beta gamma",
                "status": "replace_existing",
                "confidence": "high",
                "evidence": "test",
            },
            {
                "kind": "existing",
                "original_cue": "2",
                "start_ms": 2000,
                "end_ms": 4000,
                "track": "song",
                "original": "epsilon zeta eta theta",
                "text": "Delta epsilon zeta eta theta",
                "status": "replace_existing",
                "confidence": "high",
                "evidence": "test",
            },
        ]

        applied = auto_resegment_high_confidence_boundaries(rows)

        self.assertGreaterEqual(applied, 1)
        self.assertEqual(rows[0]["text"], "Alpha beta gamma Delta")
        self.assertEqual(
            rows[1]["text"],
            "epsilon zeta eta theta",
        )

    def test_short_chinese_edge_uses_same_generic_rule(self):
        rows = [
            {
                "kind": "existing",
                "original_cue": "10",
                "start_ms": 1000,
                "end_ms": 2000,
                "track": "中文歌",
                "original": "春夏秋冬",
                "text": "春夏",
                "status": "replace_existing",
                "confidence": "high",
                "evidence": "test",
            },
            {
                "kind": "existing",
                "original_cue": "11",
                "start_ms": 2000,
                "end_ms": 4000,
                "track": "中文歌",
                "original": "风雨雷电",
                "text": "秋冬风雨雷电",
                "status": "replace_existing",
                "confidence": "high",
                "evidence": "test",
            },
        ]

        applied = auto_resegment_high_confidence_boundaries(rows)

        self.assertGreaterEqual(applied, 1)
        self.assertEqual(rows[0]["text"], "春夏秋冬")
        self.assertEqual(rows[1]["text"], "风雨雷电")

    def test_boundary_resegment_moves_single_canonical_uh_without_duplication(self):
        rows = [
            {
                "kind": "existing",
                "original_cue": "20",
                "start_ms": 1000,
                "end_ms": 2000,
                "track": "song",
                "original": "alpha beta uh",
                "text": "Alpha beta",
                "status": "replace_existing",
                "confidence": "high",
                "evidence": "test",
            },
            {
                "kind": "existing",
                "original_cue": "21",
                "start_ms": 2000,
                "end_ms": 4000,
                "track": "song",
                "original": "gamma delta",
                "text": "Uh gamma delta",
                "status": "manual_verified_override",
                "confidence": "high",
                "evidence": "manual_context_review",
            },
        ]

        applied = auto_resegment_high_confidence_boundaries(rows)

        self.assertEqual(applied, 1)
        self.assertEqual(rows[0]["text"], "Alpha beta Uh")
        self.assertEqual(rows[1]["text"], "gamma delta")

    def test_manual_restore_does_not_duplicate_lrc_authored_uh(self):
        rows = [
            {
                "kind": "existing",
                "original_cue": "20",
                "start_ms": 1000,
                "end_ms": 2000,
                "track": "song",
                "text": "Alpha beta Uh",
                "status": "auto_resegmented_boundary",
                "confidence": "high",
                "evidence": "jianying",
                "lrc_indices": "9",
            },
            {
                "kind": "existing",
                "original_cue": "21",
                "start_ms": 2000,
                "end_ms": 4000,
                "track": "song",
                "text": "Uh gamma delta",
                "status": "manual_verified_override",
                "confidence": "high",
                "evidence": "manual_context_review",
                "lrc_indices": "10",
            },
        ]
        events = [
            {"track": "song", "lrc_index": 9, "text": "Alpha beta"},
            {"track": "song", "lrc_index": 10, "text": "Uh gamma delta"},
        ]

        removed = deduplicate_boundary_vocalizations(rows, events)

        self.assertEqual(removed, 1)
        self.assertEqual(rows[0]["text"], "Alpha beta")
        self.assertEqual(rows[1]["text"], "Uh gamma delta")

    def test_source_only_adlib_row_is_discarded(self):
        rows = [
            {
                "original_cue": "20",
                "text": "Uh Oh Yeah",
                "status": "keep_existing",
                "evidence": "jianying_only",
                "lrc_indices": "",
            },
            {
                "original_cue": "21",
                "text": "Uh lexical phrase",
                "status": "replace_existing",
                "evidence": "lrc",
                "lrc_indices": "10",
            },
        ]

        discarded = discard_noncanonical_vocalization_rows(rows)

        self.assertEqual(discarded, 1)
        self.assertEqual([row["original_cue"] for row in rows], ["21"])

    def test_foreign_lyric_with_trailing_oh_is_not_an_adlib_only_row(self):
        self.assertFalse(is_generic_vocalization("가나다 oh"))
        self.assertFalse(is_generic_vocalization("甲乙丙 oh"))
        self.assertTrue(is_generic_vocalization("Uh Oh Yeah"))


class ProjectRegressionTests(unittest.TestCase):
    def test_manual_override_scope_accepts_matching_canonical_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.srt"
            source.write_text("1\n00:00:01,000 --> 00:00:02,000\nhello\n", encoding="utf-8")

            actual, issue = validate_source_srt_scope(
                {"source_srt_sha256": sha256(source)},
                source,
                "manual override file",
            )

            self.assertEqual(actual, sha256(source))
            self.assertIsNone(issue)

    def test_manual_override_scope_rejects_missing_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.srt"
            source.write_text("1\n00:00:01,000 --> 00:00:02,000\nhello\n", encoding="utf-8")

            _, issue = validate_source_srt_scope(
                {},
                source,
                "manual override file",
            )

            self.assertIn("no source_srt_sha256 guard", str(issue))

    def test_case_file_is_rejected_for_another_source_srt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.srt"
            source.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nhello\n",
                encoding="utf-8",
            )
            cases = root / "cases.json"
            cases.write_text(
                json.dumps(
                    {
                        "project": "different mix",
                        "source_srt_sha256": "0" * 64,
                        "cases": [],
                    }
                ),
                encoding="utf-8",
            )

            issues, _ = evaluate_regression_cases([], source, cases)

            self.assertTrue(any("different source SRT" in issue for issue in issues))

    def test_exact_project_case_passes_with_matching_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.srt"
            source.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nwrong\n",
                encoding="utf-8",
            )
            cases = root / "cases.json"
            cases.write_text(
                json.dumps(
                    {
                        "project": "one mix",
                        "source_srt_sha256": sha256(source),
                        "cases": [
                            {
                                "id": "corrected",
                                "kind": "interval_text",
                                "start_ms": 1000,
                                "end_ms": 2000,
                                "text": "correct",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            issues, summary = evaluate_regression_cases(
                [Cue(1, 1000, 2000, "correct")], source, cases
            )

            self.assertEqual(issues, [])
            self.assertEqual(summary["passed"], 1)


if __name__ == "__main__":
    unittest.main()
