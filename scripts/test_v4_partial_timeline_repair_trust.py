from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lyric_aligner import __version__
from lyric_aligner.evaluation.strict_workflow import (
    canonical_sha256,
    evaluate_gates,
    file_sha256,
    selection_hash,
)
from lyric_aligner.srt import Cue
from lyric_aligner.timeline.partial_repair import PartialTimelineRepairError
from lyric_aligner.timeline.partial_repair_evidence import ExplicitCueTrust
from lyric_aligner.timeline.partial_repair_trust import (
    build_calibrated_trust_policy_lock,
    bridge_calibrated_trust_to_partial_repair,
    calibrated_decisions_to_explicit_trust,
    load_calibrated_trust_policy_lock,
)


RUNTIME = {
    "algorithm_version": __version__,
    "calibration_profile_version": "profile-v1",
    "calibration_profile_id": "profile-id",
}


def write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def eval_payload(
    *,
    split: str,
    candidate_id: str,
    candidate_revision: str,
    ground_truth: str,
    case_ids: str,
    overall_score: float,
    zh_score: float,
) -> dict:
    return {
        "schema_version": "3.0",
        "dataset": "private-real-v1",
        "dataset_revision": "revision-1",
        "evaluated_split": split,
        "candidate_id": candidate_id,
        "candidate_revision": candidate_revision,
        "runtime_identity": dict(RUNTIME),
        "dataset_identity": {
            "dataset_ground_truth_sha256": ground_truth,
            "case_ids_sha256": case_ids,
            "case_count": 2,
        },
        "dataset_validation": {
            "schema_version": "1.1",
            "source_group_isolation_enforced": True,
        },
        "overall": {"line_exact_f1": overall_score},
        "groups": {"language:zh": {"line_exact_f1": zh_score}},
        "cases": [],
    }


def gate_policy(*, split: str, include_language: bool = True) -> dict:
    gates = [
        {
            "scope": "overall",
            "metric": "line_exact_f1",
            "direction": "higher",
            "max_regression_abs": 0.0,
        }
    ]
    if include_language:
        gates.append(
            {
                "scope": "language:zh",
                "metric": "line_exact_f1",
                "direction": "higher",
                "max_regression_abs": 0.0,
            }
        )
    payload = {
        "schema_version": "1.0",
        "split": split,
        "gates": gates,
    }
    if split == "calibration":
        payload["ranking"] = [
            {
                "scope": "overall",
                "metric": "line_exact_f1",
                "direction": "higher",
            }
        ]
    return payload


class StrictFixture:
    def __init__(
        self,
        root: Path,
        *,
        blind_language_scope: bool = True,
        same_split_identity: bool = False,
    ) -> None:
        self.root = root
        self.cal_base = root / "cal-base.json"
        self.cal_candidate = root / "cal-candidate.json"
        self.cal_policy = root / "cal-policy.json"
        self.selection = root / "selection.json"
        self.blind_base = root / "blind-base.json"
        self.blind_candidate = root / "blind-candidate.json"
        self.blind_policy = root / "blind-policy.json"
        self.blind_gate = root / "blind-gate.json"
        cal_gt = "a" * 64
        cal_cases = "b" * 64
        blind_gt = cal_gt if same_split_identity else "c" * 64
        blind_cases = cal_cases if same_split_identity else "d" * 64
        cal_base = eval_payload(
            split="calibration",
            candidate_id="baseline",
            candidate_revision="baseline-r1",
            ground_truth=cal_gt,
            case_ids=cal_cases,
            overall_score=0.80,
            zh_score=0.80,
        )
        cal_candidate = eval_payload(
            split="calibration",
            candidate_id="trust-candidate",
            candidate_revision="trust-r1",
            ground_truth=cal_gt,
            case_ids=cal_cases,
            overall_score=0.90,
            zh_score=0.91,
        )
        cal_policy = gate_policy(split="calibration")
        write_json(self.cal_base, cal_base)
        write_json(self.cal_candidate, cal_candidate)
        write_json(self.cal_policy, cal_policy)
        cal_gate = evaluate_gates(cal_base, cal_candidate, cal_policy)
        selection = {
            "schema_version": "1.0",
            "dataset": "private-real-v1",
            "dataset_revision": "revision-1",
            "calibration_dataset_ground_truth_sha256": cal_gt,
            "calibration_case_ids_sha256": cal_cases,
            "baseline_candidate_id": "baseline",
            "baseline_candidate_revision": "baseline-r1",
            "baseline_runtime_identity": dict(RUNTIME),
            "baseline_calibration_evaluation_sha256": file_sha256(self.cal_base),
            "selected_candidate_id": "trust-candidate",
            "selected_candidate_revision": "trust-r1",
            "selected_runtime_identity": dict(RUNTIME),
            "selected_calibration_evaluation_sha256": file_sha256(
                self.cal_candidate
            ),
            "selected_calibration_ground_truth_sha256": cal_gt,
            "policy_id": "cal-policy",
            "policy_sha256": file_sha256(self.cal_policy),
            "selection": {
                "selected_candidate_id": "trust-candidate",
                "selected_candidate_revision": "trust-r1",
                "selected_runtime_identity": dict(RUNTIME),
                "selected_gate": cal_gate,
                "candidate_gate_results": {"trust-candidate": cal_gate},
            },
            "privacy": "aggregate",
        }
        selection["selection_payload_sha256"] = selection_hash(selection)
        write_json(self.selection, selection)

        blind_base = eval_payload(
            split="blind_test",
            candidate_id="baseline",
            candidate_revision="baseline-r1",
            ground_truth=blind_gt,
            case_ids=blind_cases,
            overall_score=0.79,
            zh_score=0.79,
        )
        blind_candidate = eval_payload(
            split="blind_test",
            candidate_id="trust-candidate",
            candidate_revision="trust-r1",
            ground_truth=blind_gt,
            case_ids=blind_cases,
            overall_score=0.88,
            zh_score=0.89,
        )
        blind_policy = gate_policy(
            split="blind_test", include_language=blind_language_scope
        )
        write_json(self.blind_base, blind_base)
        write_json(self.blind_candidate, blind_candidate)
        write_json(self.blind_policy, blind_policy)
        blind_gate_result = evaluate_gates(
            blind_base, blind_candidate, blind_policy
        )
        blind_gate = {
            "schema_version": "1.0",
            "passed": True,
            "dataset": "private-real-v1",
            "dataset_revision": "revision-1",
            "blind_dataset_ground_truth_sha256": blind_gt,
            "blind_case_ids_sha256": blind_cases,
            "baseline_candidate_id": "baseline",
            "baseline_candidate_revision": "baseline-r1",
            "baseline_runtime_identity": dict(RUNTIME),
            "selected_candidate_id": "trust-candidate",
            "selected_candidate_revision": "trust-r1",
            "selected_runtime_identity": dict(RUNTIME),
            "selection_payload_sha256": selection[
                "selection_payload_sha256"
            ],
            "selection_file_sha256": file_sha256(self.selection),
            "baseline_blind_evaluation_sha256": file_sha256(self.blind_base),
            "candidate_blind_evaluation_sha256": file_sha256(
                self.blind_candidate
            ),
            "blind_policy_id": "blind-policy",
            "blind_policy_sha256": file_sha256(self.blind_policy),
            "gate": blind_gate_result,
            "privacy": "aggregate",
        }
        blind_gate["blind_gate_payload_sha256"] = selection_hash(blind_gate)
        write_json(self.blind_gate, blind_gate)

    def build_lock(self) -> dict:
        return build_calibrated_trust_policy_lock(
            selection_path=self.selection,
            calibration_baseline_path=self.cal_base,
            calibration_candidate_path=self.cal_candidate,
            calibration_policy_path=self.cal_policy,
            blind_gate_path=self.blind_gate,
            blind_baseline_path=self.blind_base,
            blind_candidate_path=self.blind_candidate,
            blind_policy_path=self.blind_policy,
        )


def fusion_payload(*, level: str = "HIGH", language: str = "zh") -> dict:
    return {
        "lines": [
            {
                "occurrence_id": "occ-1",
                "language_profile": language,
                "shadow_level": level,
                "families": [
                    {
                        "family": "editor",
                        "available": True,
                        "cue_number": 2,
                    }
                ],
            }
        ]
    }


def decision_payload(
    lock: dict,
    *,
    status: str = "untrusted",
    scope: str = "language:zh",
) -> dict:
    payload = {
        "schema_version": "1.0",
        "mode": "partial_timeline_repair_calibrated_trust_decisions",
        "trust_policy_lock_sha256": lock["trust_policy_lock_sha256"],
        "candidate_id": lock["candidate_id"],
        "candidate_revision": lock["candidate_revision"],
        "runtime_identity": dict(lock["runtime_identity"]),
        "source_fusion_artifact_id": "fusion-artifact",
        "automatic_timing_change_allowed": False,
        "decisions": [
            {
                "cue_number": 2,
                "scope": scope,
                "status": status,
                "reason_code": "private_calibrated_policy_result",
            }
        ],
    }
    payload["decision_payload_sha256"] = canonical_sha256(payload)
    return payload


class PartialTimelineRepairTrustTests(unittest.TestCase):
    def test_valid_strict_calibration_and_blind_gate_create_zh_trust_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = StrictFixture(Path(tmp))
            lock = fixture.build_lock()
            self.assertTrue(lock["policy_calibrated"])
            self.assertTrue(lock["independent_blind_gate_passed"])
            self.assertEqual(
                lock["eligible_language_scopes"], ["language:zh"]
            )
            self.assertTrue(lock["cue_trust_generation_allowed"])
            self.assertFalse(lock["automatic_timing_change_allowed"])

    def test_blind_policy_without_language_gate_creates_non_actionable_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = StrictFixture(Path(tmp), blind_language_scope=False)
            lock = fixture.build_lock()
            self.assertEqual(lock["eligible_language_scopes"], [])
            self.assertFalse(lock["cue_trust_generation_allowed"])

    def test_calibration_and_blind_identity_reuse_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = StrictFixture(
                Path(tmp), same_split_identity=True
            )
            with self.assertRaisesRegex(
                PartialTimelineRepairError,
                "calibration and blind ground-truth identities must differ",
            ):
                fixture.build_lock()

    def test_failed_blind_gate_cannot_create_trust_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = StrictFixture(Path(tmp))
            gate = json.loads(
                fixture.blind_gate.read_text(encoding="utf-8")
            )
            gate["passed"] = False
            gate["blind_gate_payload_sha256"] = selection_hash(gate)
            write_json(fixture.blind_gate, gate)
            with self.assertRaisesRegex(
                PartialTimelineRepairError,
                "strict blind gate did not pass",
            ):
                fixture.build_lock()

    def test_lock_self_hash_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = StrictFixture(root)
            lock = fixture.build_lock()
            path = root / "lock.json"
            write_json(path, lock)
            loaded = load_calibrated_trust_policy_lock(path)
            self.assertEqual(loaded["candidate_revision"], "trust-r1")
            lock["candidate_revision"] = "tampered"
            write_json(path, lock)
            with self.assertRaisesRegex(
                PartialTimelineRepairError,
                "trust policy lock payload hash mismatch",
            ):
                load_calibrated_trust_policy_lock(path)

    def test_covered_language_can_emit_calibrated_untrusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = StrictFixture(root)
            lock = fixture.build_lock()
            decisions = decision_payload(lock)
            path = root / "decisions.json"
            write_json(path, decisions)
            trust, report = calibrated_decisions_to_explicit_trust(
                decision_path=path,
                lock=lock,
                fusion=fusion_payload(),
                fusion_artifact_id="fusion-artifact",
            )
            self.assertEqual(len(trust), 1)
            self.assertEqual(trust[0].status, "untrusted")
            self.assertEqual(trust[0].source, "calibrated_policy")
            self.assertEqual(report["counts"]["untrusted"], 1)

    def test_uncovered_language_is_downgraded_to_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = StrictFixture(
                root, blind_language_scope=False
            )
            lock = fixture.build_lock()
            decisions = decision_payload(lock)
            path = root / "decisions.json"
            write_json(path, decisions)
            trust, report = calibrated_decisions_to_explicit_trust(
                decision_path=path,
                lock=lock,
                fusion=fusion_payload(),
                fusion_artifact_id="fusion-artifact",
            )
            self.assertEqual(trust[0].status, "unknown")
            self.assertEqual(report["counts"]["uncovered_scope"], 1)

    def test_p9_conflict_can_never_be_auto_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = StrictFixture(root)
            lock = fixture.build_lock()
            decisions = decision_payload(lock, status="trusted")
            path = root / "decisions.json"
            write_json(path, decisions)
            trust, report = calibrated_decisions_to_explicit_trust(
                decision_path=path,
                lock=lock,
                fusion=fusion_payload(level="CONFLICT"),
                fusion_artifact_id="fusion-artifact",
            )
            self.assertEqual(trust[0].status, "unknown")
            self.assertEqual(
                report["counts"]["conflict_downgraded"], 1
            )

    def test_p9_high_alone_does_not_generate_any_trust(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = StrictFixture(root)
            lock = fixture.build_lock()
            payload = decision_payload(lock)
            payload["decisions"] = []
            payload["decision_payload_sha256"] = canonical_sha256(
                {
                    key: value
                    for key, value in payload.items()
                    if key != "decision_payload_sha256"
                }
            )
            path = root / "decisions.json"
            write_json(path, payload)
            trust, _ = calibrated_decisions_to_explicit_trust(
                decision_path=path,
                lock=lock,
                fusion=fusion_payload(level="HIGH"),
                fusion_artifact_id="fusion-artifact",
            )
            self.assertEqual(trust, [])

    def test_decision_must_bind_exact_fusion_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = StrictFixture(root)
            lock = fixture.build_lock()
            path = root / "decisions.json"
            write_json(path, decision_payload(lock))
            with self.assertRaisesRegex(
                PartialTimelineRepairError,
                "trust decision fusion artifact identity mismatch",
            ):
                calibrated_decisions_to_explicit_trust(
                    decision_path=path,
                    lock=lock,
                    fusion=fusion_payload(),
                    fusion_artifact_id="other-fusion-artifact",
                )

    def test_human_override_takes_precedence_over_calibrated_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = StrictFixture(root)
            lock = fixture.build_lock()
            lock_path = root / "lock.json"
            decision_path = root / "decision.json"
            fusion_path = root / "fusion.json"
            fusion_artifact_path = root / "fusion.artifact.json"
            write_json(lock_path, lock)
            write_json(
                decision_path, decision_payload(lock, status="untrusted")
            )
            write_json(fusion_path, fusion_payload())
            write_json(
                fusion_artifact_path, {"artifact_id": "fusion-artifact"}
            )

            captured: dict = {}

            def fake_bridge(**kwargs):
                captured["explicit_trust"] = kwargs["explicit_trust"]
                rows = [
                    type("Trust", (), {"status": row.status})()
                    for row in kwargs["explicit_trust"]
                ]
                return rows, [], {"proposal_only": True}

            with patch(
                "lyric_aligner.timeline.partial_repair_trust."
                "bridge_effective_artifacts_to_partial_repair",
                side_effect=fake_bridge,
            ):
                trust, _, report = bridge_calibrated_trust_to_partial_repair(
                    cues=[Cue(2, 2000, 3000, "x")],
                    run_path=Path("unused-run.json"),
                    run_artifact_path=Path("unused-run.artifact.json"),
                    fusion_path=fusion_path,
                    fusion_artifact_path=fusion_artifact_path,
                    trust_lock_path=lock_path,
                    decision_path=decision_path,
                    human_overrides=[
                        ExplicitCueTrust(
                            2,
                            "trusted",
                            "human checked",
                            source="human_review",
                        )
                    ],
                )
            self.assertEqual(
                captured["explicit_trust"][0].source, "human_review"
            )
            self.assertEqual(trust[0].status, "trusted")
            self.assertEqual(
                report["calibrated_trust_policy"]["human_override_count"],
                1,
            )


if __name__ == "__main__":
    unittest.main()
