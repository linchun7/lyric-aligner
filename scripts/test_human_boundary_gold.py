import json
import unittest

from lyric_aligner.evaluation.human_boundary_gold import (
    HUMAN_GOLD_INTERNAL_POLICY_ID,
    HUMAN_GOLD_OUTER_POLICY_ID,
    HumanBoundaryGoldError,
    candidate_from_report_row,
    select_gold_candidates,
    validate_audited_selection,
)


class HumanBoundaryGoldTests(unittest.TestCase):
    def lyric_map(self):
        return {
            "track-a": [
                {"index": 0, "text": "first line"},
                {"index": 1, "text": "second line"},
                {"index": 2, "text": "third line"},
            ]
        }

    def test_single_segment_candidate_is_valid_for_outer_population(self):
        candidate = candidate_from_report_row(
            {
                "kind": "existing",
                "original_cue": "7",
                "start_ms": "1000",
                "end_ms": "4000",
                "text": "first line",
                "track": "track-a",
                "lrc_indices": "0",
                "status": "high",
                "confidence": "high",
                "evidence": "fixture",
            },
            lyric_lines_by_track=self.lyric_map(),
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(len(candidate.segments), 1)
        self.assertIsNone(candidate.internal_boundary_index)

    def test_multisegment_candidate_preserves_canonical_segments(self):
        candidate = candidate_from_report_row(
            {
                "kind": "existing",
                "original_cue": "8",
                "start_ms": "5000",
                "end_ms": "9000",
                "text": "first line second line third line",
                "track": "track-a",
                "lrc_indices": "0;1;2",
            },
            lyric_lines_by_track=self.lyric_map(),
        )
        self.assertIsNotNone(candidate)
        self.assertEqual([row["lrc_index"] for row in candidate.segments], [0, 1, 2])
        self.assertIn(candidate.internal_boundary_index, {1, 2})

    def test_candidate_rejects_text_provenance_mismatch(self):
        candidate = candidate_from_report_row(
            {
                "kind": "existing",
                "original_cue": "7",
                "start_ms": "1000",
                "end_ms": "7000",
                "text": "changed text",
                "track": "track-a",
                "lrc_indices": "0;1",
            },
            lyric_lines_by_track=self.lyric_map(),
        )
        self.assertIsNone(candidate)

    def make_candidates(self):
        result = []
        for track_number in range(20):
            track = f"track-{track_number:02d}"
            lyric_map = {
                track: [
                    {"index": 0, "text": "single alpha"},
                    {"index": 1, "text": "single beta"},
                    {"index": 2, "text": "multi left"},
                    {"index": 3, "text": "multi right"},
                    {"index": 4, "text": "other left"},
                    {"index": 5, "text": "other right"},
                ]
            }
            rows = [
                (1, "single alpha", "0"),
                (2, "single beta", "1"),
                (3, "multi left multi right", "2;3"),
                (4, "other left other right", "4;5"),
            ]
            for cue_number, text, indices in rows:
                base = 100000 * track_number + cue_number * 5000
                candidate = candidate_from_report_row(
                    {
                        "kind": "existing",
                        "original_cue": str(cue_number),
                        "start_ms": str(base),
                        "end_ms": str(base + 4000),
                        "text": text,
                        "track": track,
                        "lrc_indices": indices,
                        "status": "fixture",
                        "confidence": "high",
                        "evidence": "fixture",
                    },
                    lyric_lines_by_track=lyric_map,
                )
                self.assertIsNotNone(candidate)
                result.append(candidate)
        return result

    def test_outer_selection_is_30_with_explicit_single_multi_coverage(self):
        selection = select_gold_candidates(self.make_candidates(), purpose="outer")
        self.assertEqual(selection["policy_id"], HUMAN_GOLD_OUTER_POLICY_ID)
        self.assertEqual(len(selection["records"]), 30)
        self.assertEqual(selection["single_segment_count"], 20)
        self.assertEqual(selection["multi_segment_count"], 10)
        self.assertEqual(
            sum(row["partition"] == "holdout" for row in selection["records"]),
            10,
        )
        self.assertGreaterEqual(selection["calibration_distinct_track_count"], 5)
        self.assertGreaterEqual(selection["holdout_distinct_track_count"], 5)
        self.assertTrue(all(row["boundary_kinds"] == ["start", "end"] for row in selection["records"]))

    def test_internal_selection_is_30_multisegment_and_excludes_outer(self):
        candidates = self.make_candidates()
        outer = select_gold_candidates(candidates, purpose="outer")
        outer_ids = {row["case_id"] for row in outer["records"]}
        internal = select_gold_candidates(
            candidates,
            purpose="internal",
            excluded_case_ids=outer_ids,
        )
        internal_ids = {row["case_id"] for row in internal["records"]}
        self.assertEqual(internal["policy_id"], HUMAN_GOLD_INTERNAL_POLICY_ID)
        self.assertEqual(len(internal_ids), 30)
        self.assertFalse(outer_ids & internal_ids)
        self.assertEqual(internal["single_segment_count"], 0)
        self.assertEqual(internal["multi_segment_count"], 30)
        self.assertTrue(all(row["boundary_kinds"] == ["internal"] for row in internal["records"]))
        self.assertTrue(all(row["internal_boundary_index"] is not None for row in internal["records"]))

    def test_two_population_selection_is_input_order_independent(self):
        candidates = self.make_candidates()
        outer_forward = select_gold_candidates(candidates, purpose="outer")
        outer_reverse = select_gold_candidates(list(reversed(candidates)), purpose="outer")
        self.assertEqual(outer_forward, outer_reverse)
        excluded = {row["case_id"] for row in outer_forward["records"]}
        internal_forward = select_gold_candidates(
            candidates,
            purpose="internal",
            excluded_case_ids=excluded,
        )
        internal_reverse = select_gold_candidates(
            list(reversed(candidates)),
            purpose="internal",
            excluded_case_ids=excluded,
        )
        self.assertEqual(internal_forward, internal_reverse)

    def test_auditor_cannot_change_locked_identity_or_embed_backend_prediction(self):
        selection = select_gold_candidates(self.make_candidates(), purpose="outer")
        audited = json.loads(json.dumps(selection["records"]))
        validate_audited_selection(selection, audited)

        changed = json.loads(json.dumps(audited))
        changed[0]["track"] = "other-track"
        with self.assertRaises(HumanBoundaryGoldError):
            validate_audited_selection(selection, changed)

        leaked = json.loads(json.dumps(audited))
        leaked[0]["backend_id"] = "forbidden"
        with self.assertRaises(HumanBoundaryGoldError):
            validate_audited_selection(selection, leaked)


if __name__ == "__main__":
    unittest.main()
