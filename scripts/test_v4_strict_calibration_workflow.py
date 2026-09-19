import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "scripts" / "v4_calibration_workflow.py"


def run(*args):
    return subprocess.run(
        [sys.executable, str(WORKFLOW), *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def write_srt(path: Path, text: str, start_ms: int = 1000, end_ms: int = 2000):
    path.parent.mkdir(parents=True, exist_ok=True)
    def ts(value):
        hour, remain = divmod(value, 3_600_000)
        minute, remain = divmod(remain, 60_000)
        second, millis = divmod(remain, 1000)
        return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"
    path.write_text(
        f"1\n{ts(start_ms)} --> {ts(end_ms)}\n{text}\n",
        encoding="utf-8",
    )


def write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def qa(path: Path, *, algorithm: str, profile_id: str):
    write_json(
        path,
        {
            "algorithm_version": algorithm,
            "calibration_profile_version": "profile-test-r1",
            "calibration_profile_id": profile_id,
            "review_candidate_count": 0,
            "publish_ready": True,
        },
    )


class V4StrictCalibrationWorkflowTests(unittest.TestCase):
    def test_calibration_does_not_read_blind_prediction_and_blind_locks_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            refs = root / "refs"
            refs.mkdir()
            write_srt(refs / "cal.srt", "calibration lyric")
            write_srt(refs / "blind.srt", "blind lyric")

            baseline_cal_pred = root / "baseline" / "cal.srt"
            candidate_cal_pred = root / "candidate" / "cal.srt"
            write_srt(baseline_cal_pred, "calibration lyric", 1100, 2100)
            write_srt(candidate_cal_pred, "calibration lyric", 1000, 2000)
            baseline_cal_qa = root / "baseline" / "cal.qa.json"
            candidate_cal_qa = root / "candidate" / "cal.qa.json"
            qa(baseline_cal_qa, algorithm="baseline-v1", profile_id="b" * 64)
            qa(candidate_cal_qa, algorithm="candidate-v2", profile_id="c" * 64)

            # Blind predictions/QA deliberately do not exist during calibration.
            baseline_blind_pred = root / "baseline" / "blind.srt"
            candidate_blind_pred = root / "candidate" / "blind.srt"
            baseline_blind_qa = root / "baseline" / "blind.qa.json"
            candidate_blind_qa = root / "candidate" / "blind.qa.json"

            def manifest(pred_cal, qa_cal, pred_blind, qa_blind):
                return {
                    "schema_version": "1.1",
                    "dataset": "private-v1",
                    "dataset_revision": "dataset-rev-001",
                    "cases": [
                        {
                            "id": "cal-001",
                            "split": "calibration",
                            "language": "zh",
                            "source_group": "source-cal-001",
                            "structural_scenarios": ["hard_cut"],
                            "reference_srt": str(refs / "cal.srt"),
                            "predicted_srt": str(pred_cal),
                            "qa_json": str(qa_cal),
                            "audio_duration_seconds": 3.0,
                        },
                        {
                            "id": "blind-001",
                            "split": "blind_test",
                            "language": "ja",
                            "source_group": "source-blind-001",
                            "structural_scenarios": ["true_overlap"],
                            "reference_srt": str(refs / "blind.srt"),
                            "predicted_srt": str(pred_blind),
                            "qa_json": str(qa_blind),
                            "audio_duration_seconds": 3.0,
                        },
                    ],
                }

            baseline_manifest = root / "baseline.dataset.json"
            candidate_manifest = root / "candidate.dataset.json"
            baseline_manifest_payload = manifest(
                baseline_cal_pred,
                baseline_cal_qa,
                baseline_blind_pred,
                baseline_blind_qa,
            )
            candidate_manifest_payload = manifest(
                candidate_cal_pred,
                candidate_cal_qa,
                candidate_blind_pred,
                candidate_blind_qa,
            )
            for manifest_payload in (
                baseline_manifest_payload,
                candidate_manifest_payload,
            ):
                manifest_payload["cases"][0]["structural_scenarios"] = ["reorder"]
                manifest_payload["cases"][0]["expected_structural_events"] = [
                    {"kind": "reorder", "start_ms": 1000, "end_ms": 2500}
                ]
            baseline_manifest_payload["cases"][0]["predicted_structural_events"] = []
            candidate_manifest_payload["cases"][0]["predicted_structural_events"] = [
                {"kind": "reorder", "start_ms": 1100, "end_ms": 2400}
            ]
            write_json(baseline_manifest, baseline_manifest_payload)
            write_json(candidate_manifest, candidate_manifest_payload)

            baseline_cal_eval = root / "baseline.calibration.eval.json"
            candidate_cal_eval = root / "candidate.calibration.eval.json"
            baseline_result = run(
                "evaluate",
                "--dataset",
                baseline_manifest,
                "--split",
                "calibration",
                "--candidate-id",
                "baseline",
                "--candidate-revision",
                "baseline-rev-1",
                "--out",
                baseline_cal_eval,
            )
            self.assertEqual(baseline_result.returncode, 0, msg=baseline_result.stderr)
            candidate_result = run(
                "evaluate",
                "--dataset",
                candidate_manifest,
                "--split",
                "calibration",
                "--candidate-id",
                "candidate",
                "--candidate-revision",
                "candidate-rev-2",
                "--out",
                candidate_cal_eval,
            )
            self.assertEqual(candidate_result.returncode, 0, msg=candidate_result.stderr)

            baseline_payload = json.loads(baseline_cal_eval.read_text(encoding="utf-8"))
            candidate_payload = json.loads(candidate_cal_eval.read_text(encoding="utf-8"))
            self.assertEqual(
                baseline_payload["dataset_identity"]["dataset_ground_truth_sha256"],
                candidate_payload["dataset_identity"]["dataset_ground_truth_sha256"],
            )
            self.assertIn("structural:reorder", baseline_payload["groups"])
            self.assertEqual(
                baseline_payload["dataset_validation"]["structural_scenario_counts"],
                {"reorder": 1, "true_overlap": 1},
            )
            self.assertEqual(
                baseline_payload["groups"]["structural:reorder"][
                    "structural_event_recall"
                ],
                0.0,
            )
            self.assertEqual(
                candidate_payload["groups"]["structural:reorder"][
                    "structural_event_recall"
                ],
                1.0,
            )
            self.assertGreater(
                candidate_payload["groups"]["structural:reorder"][
                    "structural_event_interval_mean_iou"
                ],
                0.8,
            )

            calibration_policy = root / "calibration.policy.json"
            write_json(
                calibration_policy,
                {
                    "schema_version": "1.0",
                    "policy_id": "calibration-policy-v1",
                    "split": "calibration",
                    "gates": [
                        {
                            "scope": "overall",
                            "metric": "line_exact_recall",
                            "direction": "higher",
                            "max_regression_abs": 0.0,
                        }
                    ],
                    "ranking": [
                        {
                            "scope": "overall",
                            "metric": "onset_mae_ms",
                            "direction": "lower",
                        }
                    ],
                },
            )
            selection = root / "selection.json"
            select_result = run(
                "select",
                "--baseline",
                baseline_cal_eval,
                "--candidate",
                candidate_cal_eval,
                "--policy",
                calibration_policy,
                "--out",
                selection,
            )
            self.assertEqual(select_result.returncode, 0, msg=select_result.stderr)
            selected = json.loads(selection.read_text(encoding="utf-8"))
            self.assertEqual(selected["selected_candidate_id"], "candidate")
            self.assertEqual(selected["selected_candidate_revision"], "candidate-rev-2")
            self.assertEqual(
                selected["selected_runtime_identity"]["algorithm_version"],
                "candidate-v2",
            )

            # Only after calibration selection do blind predictions materialize.
            write_srt(baseline_blind_pred, "blind lyric", 1000, 2000)
            write_srt(candidate_blind_pred, "blind lyric", 1000, 2000)
            qa(baseline_blind_qa, algorithm="baseline-v1", profile_id="b" * 64)
            qa(candidate_blind_qa, algorithm="candidate-v2", profile_id="c" * 64)

            baseline_blind_eval = root / "baseline.blind.eval.json"
            candidate_blind_eval = root / "candidate.blind.eval.json"
            self.assertEqual(
                run(
                    "evaluate",
                    "--dataset",
                    baseline_manifest,
                    "--split",
                    "blind_test",
                    "--candidate-id",
                    "baseline",
                    "--candidate-revision",
                    "baseline-rev-1",
                    "--out",
                    baseline_blind_eval,
                ).returncode,
                0,
            )
            self.assertEqual(
                run(
                    "evaluate",
                    "--dataset",
                    candidate_manifest,
                    "--split",
                    "blind_test",
                    "--candidate-id",
                    "candidate",
                    "--candidate-revision",
                    "candidate-rev-2",
                    "--out",
                    candidate_blind_eval,
                ).returncode,
                0,
            )
            self.assertIn(
                "structural:true_overlap",
                json.loads(candidate_blind_eval.read_text(encoding="utf-8"))["groups"],
            )

            blind_policy = root / "blind.policy.json"
            write_json(
                blind_policy,
                {
                    "schema_version": "1.0",
                    "policy_id": "blind-policy-v1",
                    "split": "blind_test",
                    "gates": [
                        {
                            "scope": "overall",
                            "metric": "line_exact_recall",
                            "direction": "higher",
                            "max_regression_abs": 0.0,
                        }
                    ],
                },
            )
            blind_gate = root / "blind.gate.json"
            blind_result = run(
                "blind",
                "--baseline",
                baseline_blind_eval,
                "--candidate",
                candidate_blind_eval,
                "--selection",
                selection,
                "--policy",
                blind_policy,
                "--out",
                blind_gate,
            )
            self.assertEqual(blind_result.returncode, 0, msg=blind_result.stderr)
            self.assertTrue(json.loads(blind_gate.read_text(encoding="utf-8"))["passed"])

            wrong_revision_eval = root / "candidate.wrong-revision.blind.eval.json"
            wrong_eval_result = run(
                "evaluate",
                "--dataset",
                candidate_manifest,
                "--split",
                "blind_test",
                "--candidate-id",
                "candidate",
                "--candidate-revision",
                "candidate-rev-CHANGED",
                "--out",
                wrong_revision_eval,
            )
            self.assertEqual(wrong_eval_result.returncode, 0, msg=wrong_eval_result.stderr)
            rejected = run(
                "blind",
                "--baseline",
                baseline_blind_eval,
                "--candidate",
                wrong_revision_eval,
                "--selection",
                selection,
                "--policy",
                blind_policy,
                "--out",
                root / "should-not-pass.json",
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("revision differs", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
