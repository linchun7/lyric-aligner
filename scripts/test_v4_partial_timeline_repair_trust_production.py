from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import atomic_write_json, build_artifact_manifest
from lyric_aligner.evaluation.strict_workflow import canonical_sha256
from lyric_aligner.srt import Cue
from lyric_aligner.timeline.partial_repair import PartialTimelineRepairError
from lyric_aligner.timeline.partial_repair_trust_production import (
    bridge_calibrated_trust_artifacts_to_partial_repair,
)


FINGERPRINT = "f" * 64
RUNTIME = {
    "algorithm_version": __version__,
    "calibration_profile_version": "profile-v1",
    "calibration_profile_id": "profile-id",
}


def write_json(path: Path, payload: dict) -> None:
    atomic_write_json(path, payload)


def lock_payload() -> dict:
    payload = {
        "schema_version": "1.0",
        "mode": "partial_timeline_repair_calibrated_trust_lock",
        "dataset": "private-real-v1",
        "dataset_revision": "revision-1",
        "candidate_id": "trust-candidate",
        "candidate_revision": "trust-r1",
        "runtime_identity": dict(RUNTIME),
        "selection_payload_sha256": "selection",
        "selection_file_sha256": "selection-file",
        "selected_calibration_evaluation_sha256": "cal-selected",
        "calibration_baseline_evaluation_sha256": "cal-base",
        "calibration_policy_sha256": "cal-policy",
        "blind_gate_payload_sha256": "blind-gate",
        "blind_gate_file_sha256": "blind-gate-file",
        "blind_candidate_evaluation_sha256": "blind-candidate",
        "blind_baseline_evaluation_sha256": "blind-base",
        "blind_policy_sha256": "blind-policy",
        "calibration_dataset_ground_truth_sha256": "a" * 64,
        "calibration_case_ids_sha256": "b" * 64,
        "blind_dataset_ground_truth_sha256": "c" * 64,
        "blind_case_ids_sha256": "d" * 64,
        "eligible_language_scopes": ["language:zh"],
        "cue_trust_generation_allowed": True,
        "policy_calibrated": True,
        "independent_blind_gate_passed": True,
        "automatic_timing_change_allowed": False,
        "release_gate_eligible": False,
        "privacy": "aggregate",
    }
    payload["trust_policy_lock_sha256"] = canonical_sha256(payload)
    return payload


def decision_payload(lock: dict) -> dict:
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
                "scope": "language:zh",
                "status": "untrusted",
                "reason_code": "private_policy",
            }
        ],
    }
    payload["decision_payload_sha256"] = canonical_sha256(payload)
    return payload


def fusion_payload() -> dict:
    return {
        "schema_version": "1.1",
        "algorithm_version": __version__,
        "task_fingerprint_sha256": FINGERPRINT,
        "mode": "shadow_only",
        "policy_calibrated": False,
        "release_gate_eligible": False,
        "automatic_timing_change_allowed": False,
        "lines": [],
    }


def write_fusion_artifact(root: Path, fusion_path: Path) -> tuple[Path, dict]:
    artifact_path = root / "fusion.artifact.json"
    artifact = build_artifact_manifest(
        task_fingerprint_sha256=FINGERPRINT,
        stage="evidence_fusion_shadow",
        algorithm_version=__version__,
        outputs=(("evidence_fusion", fusion_path),),
        normalized_config={"source_run_artifact_id": "run-artifact"},
        upstream_artifact_ids=("run-artifact",),
        evidence={
            "mode": "shadow_only",
            "policy_calibrated": False,
            "release_gate_eligible": False,
            "automatic_timing_change_allowed": False,
        },
    )
    artifact["artifact_id"] = "fusion-artifact"
    # Rebuild the artifact rather than mutating its self identity in tests.
    artifact = build_artifact_manifest(
        task_fingerprint_sha256=FINGERPRINT,
        stage="evidence_fusion_shadow",
        algorithm_version=__version__,
        outputs=(("evidence_fusion", fusion_path),),
        normalized_config={"source_run_artifact_id": "run-artifact"},
        upstream_artifact_ids=("run-artifact",),
        evidence={
            "mode": "shadow_only",
            "policy_calibrated": False,
            "release_gate_eligible": False,
            "automatic_timing_change_allowed": False,
        },
    )
    write_json(artifact_path, artifact)
    return artifact_path, artifact


def write_decision_artifact(
    root: Path,
    *,
    decision_path: Path,
    fusion_artifact_id: str,
    lock: dict,
    upstreams: tuple[str, ...] | None = None,
    config_override: dict | None = None,
) -> tuple[Path, dict]:
    config = {
        "trust_policy_lock_sha256": lock["trust_policy_lock_sha256"],
        "source_fusion_artifact_id": fusion_artifact_id,
        "candidate_id": lock["candidate_id"],
        "candidate_revision": lock["candidate_revision"],
        "runtime_identity": lock["runtime_identity"],
    }
    if config_override:
        config.update(config_override)
    artifact = build_artifact_manifest(
        task_fingerprint_sha256=FINGERPRINT,
        stage="partial_timeline_trust_decisions",
        algorithm_version=__version__,
        outputs=(("cue_trust_decisions", decision_path),),
        normalized_config=config,
        upstream_artifact_ids=upstreams or (fusion_artifact_id,),
        evidence={
            "policy_calibrated": True,
            "independent_blind_gate_passed": True,
            "automatic_timing_change_allowed": False,
            "release_gate_eligible": False,
        },
    )
    path = root / "decision.artifact.json"
    write_json(path, artifact)
    return path, artifact


class PartialTimelineRepairTrustProductionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lock = lock_payload()
        self.lock_path = self.root / "lock.json"
        write_json(self.lock_path, self.lock)
        self.fusion_path = self.root / "fusion.json"
        write_json(self.fusion_path, fusion_payload())
        self.fusion_artifact_path, self.fusion_artifact = write_fusion_artifact(
            self.root, self.fusion_path
        )
        # Bind the decision payload to the actual formal fusion artifact id.
        self.decision = decision_payload(self.lock)
        self.decision["source_fusion_artifact_id"] = self.fusion_artifact["artifact_id"]
        self.decision["decision_payload_sha256"] = canonical_sha256(
            {
                key: value
                for key, value in self.decision.items()
                if key != "decision_payload_sha256"
            }
        )
        self.decision_path = self.root / "decision.json"
        write_json(self.decision_path, self.decision)
        self.decision_artifact_path, self.decision_artifact = write_decision_artifact(
            self.root,
            decision_path=self.decision_path,
            fusion_artifact_id=self.fusion_artifact["artifact_id"],
            lock=self.lock,
        )

    def _call(self, *, decision_artifact_path: Path | None = None):
        with patch(
            "lyric_aligner.timeline.partial_repair_trust_production."
            "bridge_calibrated_trust_to_partial_repair",
            return_value=([], [], {"calibrated_trust_policy": {}}),
        ):
            return bridge_calibrated_trust_artifacts_to_partial_repair(
                cues=[Cue(1, 1000, 2000, "x")],
                run_path=Path("unused-run.json"),
                run_artifact_path=Path("unused-run.artifact.json"),
                fusion_path=self.fusion_path,
                fusion_artifact_path=self.fusion_artifact_path,
                trust_lock_path=self.lock_path,
                decision_path=self.decision_path,
                decision_artifact_path=(
                    decision_artifact_path or self.decision_artifact_path
                ),
            )

    def test_formal_decision_artifact_is_required_and_reported(self):
        _, _, report = self._call()
        self.assertTrue(
            report["calibrated_trust_policy"]["decision_artifact_verified"]
        )
        self.assertEqual(
            report["calibrated_trust_policy"]["decision_artifact_id"],
            self.decision_artifact["artifact_id"],
        )

    def test_tampered_decision_payload_fails_formal_output_hash(self):
        tampered = dict(self.decision)
        tampered["decisions"] = []
        write_json(self.decision_path, tampered)
        with self.assertRaisesRegex(
            PartialTimelineRepairError,
            "invalid calibrated trust decision artifact",
        ):
            self._call()

    def test_decision_artifact_requires_fusion_upstream(self):
        bad_path, _ = write_decision_artifact(
            self.root,
            decision_path=self.decision_path,
            fusion_artifact_id=self.fusion_artifact["artifact_id"],
            lock=self.lock,
            upstreams=("other-artifact",),
        )
        with self.assertRaisesRegex(
            PartialTimelineRepairError,
            "P9 fusion artifact is not upstream",
        ):
            self._call(decision_artifact_path=bad_path)

    def test_decision_artifact_must_bind_exact_policy_lock(self):
        bad_path, _ = write_decision_artifact(
            self.root,
            decision_path=self.decision_path,
            fusion_artifact_id=self.fusion_artifact["artifact_id"],
            lock=self.lock,
            config_override={"trust_policy_lock_sha256": "wrong-lock"},
        )
        with self.assertRaisesRegex(
            PartialTimelineRepairError,
            "trust_policy_lock_sha256 binding mismatch",
        ):
            self._call(decision_artifact_path=bad_path)


if __name__ == "__main__":
    unittest.main()
