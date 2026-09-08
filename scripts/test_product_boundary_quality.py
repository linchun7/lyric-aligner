import copy
import unittest
import csv
import tempfile
from pathlib import Path

from lyric_aligner.evaluation.product_boundary_quality import evaluate_boundaries
from scripts.v4_evaluate_product_boundaries import final_outer_index


def record(**kwargs):
    return {"id": "a:start", "track": "song-a", "partition": "calibration", "boundary_kind": "start", "gold_ms": 1000, "uncertainty_ms": 100, "editor_ms": 1300, "candidates": {"aligner": 1050}, **kwargs}


class ProductBoundaryQualityTests(unittest.TestCase):
    def test_raw_error_never_disguised_by_tolerance(self):
        result = evaluate_boundaries([record()])["scopes"]["start:calibration"]["methods"]
        self.assertEqual(result["editor"]["raw"]["mae_ms"], 300)
        self.assertEqual(result["editor"]["tolerance_adjusted"]["mae_ms"], 200)

    def test_missing_selection_is_unknown_not_perfect_writeback(self):
        scope = evaluate_boundaries([record()])["scopes"]["start:calibration"]
        self.assertIsNone(scope["writeback_mismatch_count"])
        self.assertIsNone(scope["selection_regret_mean_ms"])
        self.assertEqual(scope["methods"]["final"]["raw"]["coverage"], 0)

    def test_candidates_selection_and_writeback_are_distinct(self):
        scope = evaluate_boundaries([record(selected_ms=1050, final_ms=1300)])["scopes"]["start:calibration"]
        self.assertEqual(scope["candidate_better_than_editor_count"], 1)
        self.assertEqual(scope["selection_regret_mean_ms"], 0)
        self.assertEqual(scope["writeback_mismatch_count"], 1)
        self.assertEqual(scope["methods"]["final"]["paired_vs_editor"]["changed_count"], 0)

    def test_missing_candidate_remains_in_denominator(self):
        rows = [record(), record(id="b", track="b", candidates={"aligner": None})]
        stats = evaluate_boundaries(rows)["scopes"]["start:calibration"]["methods"]["backend:aligner"]
        self.assertEqual(stats["raw"]["coverage"], .5)

    def test_harmful_change_and_new_catastrophe(self):
        scope = evaluate_boundaries([record(final_ms=2100)])["scopes"]["start:calibration"]
        stats = scope["methods"]["final"]["paired_vs_editor"]
        self.assertEqual(stats["harmful_change_rate"], 1)
        self.assertEqual(stats["new_over_500ms_count"], 1)

    def test_track_bootstrap_does_not_weight_repeated_cues_as_tracks(self):
        rows = [record(id=str(i), final_ms=1000) for i in range(10)]
        rows.append(record(id="b", track="b", final_ms=1600))
        stats = evaluate_boundaries(rows)["scopes"]["start:calibration"]["methods"]["final"]["paired_vs_editor"]
        self.assertEqual(stats["track_equal_mean_improvement_ms"], 0)
        self.assertEqual(stats["track_count"], 2)

    def test_partition_leakage_is_reported(self):
        result = evaluate_boundaries([record(), record(id="b", partition="holdout")])
        self.assertEqual(result["calibration_holdout_shared_tracks"], ["song-a"])

    def test_empty_duplicate_nonfinite_and_bool_rejected(self):
        for rows in ([], [record(), record()], [record(editor_ms=True)], [record(candidates={"a": float("nan")})], [record(selected_ms=999)]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                evaluate_boundaries(rows)

    def test_deterministic_and_does_not_mutate_input(self):
        rows = [record(), record(id="b", track="b", final_ms=1000)]
        original = copy.deepcopy(rows)
        self.assertEqual(evaluate_boundaries(rows), evaluate_boundaries(rows))
        self.assertEqual(rows, original)

    def test_final_report_must_match_actual_srt_and_preserves_split_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt = root / 'final.srt'
            report = root / 'final.csv'
            srt.write_text('1\n00:00:01,000 --> 00:00:02,000\nfirst\n\n2\n00:00:02,000 --> 00:00:03,000\nsecond\n',encoding='utf-8')
            with report.open('w',newline='',encoding='utf-8') as handle:
                writer=csv.DictWriter(handle,fieldnames=['start_ms','end_ms','text','original_cue','track'])
                writer.writeheader()
                writer.writerows([dict(start_ms=1000,end_ms=2000,text='first',original_cue=42,track='song'),dict(start_ms=2000,end_ms=3000,text='second',original_cue=42,track='song')])
            self.assertEqual(len(final_outer_index(report,srt)[('song',42)]),2)
            srt.write_text(srt.read_text().replace('03,000','04,000'),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'exact SRT'):
                final_outer_index(report,srt)

    def test_final_measurement_rejects_wrong_task_even_when_text_and_times_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt, report = root/'final.srt', root/'final.csv'
            srt.write_text('1\n00:00:01,000 --> 00:00:02,000\nfirst\n', encoding='utf-8')
            report.write_text('start_ms,end_ms,text,original_cue,track,task_fingerprint_sha256\n1000,2000,first,42,song,'+'b'*64+'\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'another task'):
                final_outer_index(report, srt, expected_task_fingerprint='a'*64)


if __name__ == "__main__":
    unittest.main()
