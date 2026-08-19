from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import atomic_write_json, build_artifact_manifest
from lyric_aligner.srt import Cue
from lyric_aligner.timeline.partial_repair import PartialTimelineRepairError
from lyric_aligner.timeline.partial_repair_evidence import ExplicitCueTrust
from lyric_aligner.timeline.partial_repair_production import (
    bridge_effective_artifacts_to_partial_repair,
)


FINGERPRINT = "f" * 64


def _write_stage(
    root: Path,
    *,
    name: str,
    payload: dict,
    stage: str,
    role: str,
    upstreams: tuple[str, ...] = (),
    normalized_config: dict | None = None,
    evidence: dict | None = None,
):
    payload_path = root / f"{name}.json"
    artifact_path = root / f"{name}.artifact.json"
    atomic_write_json(payload_path, payload)
    artifact = build_artifact_manifest(
        task_fingerprint_sha256=FINGERPRINT,
        stage=stage,
        algorithm_version=__version__,
        outputs=((role, payload_path),),
        upstream_artifact_ids=upstreams,
        normalized_config=normalized_config or {},
        evidence=evidence or {},
    )
    atomic_write_json(artifact_path, artifact)
    return payload_path, artifact_path, artifact


def _fixture(root: Path):
    coarse_payload = {
        "schema_version": "1.0",
        "algorithm_version": __version__,
        "task_fingerprint_sha256": FINGERPRINT,
        "occurrence_id": "occ-1",
        "track_id": "track-1",
        "canonical_selection_sha256": "selection-1",
        "result": {
            "timewarp": {
                "mapping": {
                    "mode": "AFFINE",
                    "intercept": 0.0,
                    "base_slope": 1.0,
                    "breakpoints": [],
                    "slope_deltas": [],
                    "diagnostics": {},
                    "objective": 0.1,
                },
                "blocked": False,
            }
        },
    }
    coarse_path, coarse_artifact_path, coarse_artifact = _write_stage(
        root,
        name="coarse",
        payload=coarse_payload,
        stage="coarse_audio_alignment",
        role="coarse_alignment",
    )

    run_payload = {
        "schema_version": "1.2",
        "algorithm_version": __version__,
        "task_fingerprint_sha256": FINGERPRINT,
        "status": "ready_for_render",
        "legacy_fallback_used": False,
        "occurrences": [
            {
                "occurrence_id": "occ-1",
                "mapping_source": "coarse",
                "mapping_blocked": False,
                "fine_applied": False,
                "coarse_path": str(coarse_path),
                "coarse_artifact_path": str(coarse_artifact_path),
            }
        ],
        "issues": [],
    }
    run_path, run_artifact_path, run_artifact = _write_stage(
        root,
        name="run",
        payload=run_payload,
        stage="production_orchestration",
        role="v4_production_run",
        upstreams=(coarse_artifact["artifact_id"],),
    )

    fusion_payload = {
        "schema_version": "1.1",
        "algorithm_version": __version__,
        "task_fingerprint_sha256": FINGERPRINT,
        "policy_id": "test-shadow",
        "mode": "shadow_only",
        "policy_calibrated": False,
        "release_gate_eligible": False,
        "automatic_timing_change_allowed": False,
        "source_run_stage": "production_orchestration",
        "source_run_artifact_id": run_artifact["artifact_id"],
        "authority": {
            "canonical_text": "canonical_lyrics_only",
            "primary_timing": "source_to_mix_only",
        },
        "lines": [
            {
                "occurrence_id": "occ-1",
                "track_id": "track-1",
                "ordinal": 0,
                "canonical_line_index": 0,
                "canonical_text_sha256": "line-hash",
                "source_timeline_boundary_ms": [2300, 3200],
                "shadow_level": "HIGH",
                "shadow_level_calibrated": False,
                "release_gate_eligible": False,
                "automatic_timing_change_allowed": False,
                "families": [
                    {
                        "family": "source_timeline",
                        "available": True,
                        "authoritative_for_primary_timing": True,
                        "boundary_ms": [2300, 3200],
                    },
                    {
                        "family": "editor",
                        "available": True,
                        "authoritative_for_primary_timing": False,
                        "boundary_ms": [2200, 3200],
                        "cue_number": 2,
                    },
                ],
            }
        ],
    }
    fusion_path, fusion_artifact_path, fusion_artifact = _write_stage(
        root,
        name="fusion",
        payload=fusion_payload,
        stage="evidence_fusion_shadow",
        role="evidence_fusion",
        upstreams=(run_artifact["artifact_id"],),
        normalized_config={
            "source_run_artifact_id": run_artifact["artifact_id"],
        },
        evidence={
            "mode": "shadow_only",
            "policy_calibrated": False,
            "release_gate_eligible": False,
            "automatic_timing_change_allowed": False,
        },
    )
    return {
        "run_path": run_path,
        "run_artifact_path": run_artifact_path,
        "run_artifact": run_artifact,
        "fusion_path": fusion_path,
        "fusion_artifact_path": fusion_artifact_path,
        "fusion_artifact": fusion_artifact,
        "fusion_payload": fusion_payload,
    }


def _cues():
    return [
        Cue(1, 1000, 2000, "第一句"),
        Cue(2, 2200, 3200, "第二句"),
        Cue(3, 3500, 4500, "第三句"),
    ]


def _trust():
    return [
        ExplicitCueTrust(1, "trusted", "checked"),
        ExplicitCueTrust(2, "untrusted", "late"),
        ExplicitCueTrust(3, "trusted", "checked"),
    ]


class PartialTimelineRepairProductionBridgeTests(unittest.TestCase):
    def test_verified_run_and_fusion_artifacts_emit_source_to_mix_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _fixture(Path(tmp))
            trust, candidates, report = bridge_effective_artifacts_to_partial_repair(
                cues=_cues(),
                run_path=fixture["run_path"],
                run_artifact_path=fixture["run_artifact_path"],
                fusion_path=fixture["fusion_path"],
                fusion_artifact_path=fixture["fusion_artifact_path"],
                explicit_trust=_trust(),
            )
            self.assertEqual([row.status for row in trust], ["trusted", "untrusted", "trusted"])
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].mapping_kind, "AFFINE")
            self.assertEqual(candidates[0].source, "source_to_mix")
            self.assertTrue(report["production_inputs_artifact_verified"])
            self.assertEqual(
                report["fusion_context_source"],
                "formal_evidence_fusion_artifact_lineage",
            )

    def test_tampered_fusion_payload_is_rejected_by_formal_output_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _fixture(Path(tmp))
            tampered = dict(fixture["fusion_payload"])
            tampered["lines"] = [dict(fixture["fusion_payload"]["lines"][0])]
            tampered["lines"][0]["source_timeline_boundary_ms"] = [9000, 10000]
            atomic_write_json(fixture["fusion_path"], tampered)
            with self.assertRaisesRegex(
                PartialTimelineRepairError,
                "invalid P9 fusion artifact",
            ):
                bridge_effective_artifacts_to_partial_repair(
                    cues=_cues(),
                    run_path=fixture["run_path"],
                    run_artifact_path=fixture["run_artifact_path"],
                    fusion_path=fixture["fusion_path"],
                    fusion_artifact_path=fixture["fusion_artifact_path"],
                    explicit_trust=_trust(),
                )

    def test_fusion_artifact_must_have_effective_run_as_upstream(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _fixture(root)
            _, bad_artifact_path, _ = _write_stage(
                root,
                name="fusion-no-run-upstream",
                payload=fixture["fusion_payload"],
                stage="evidence_fusion_shadow",
                role="evidence_fusion",
                upstreams=("other-artifact",),
                normalized_config={
                    "source_run_artifact_id": fixture["run_artifact"]["artifact_id"],
                },
                evidence={
                    "mode": "shadow_only",
                    "policy_calibrated": False,
                    "release_gate_eligible": False,
                    "automatic_timing_change_allowed": False,
                },
            )
            # The artifact protects another copy, so use that exact payload path.
            bad_payload_path = root / "fusion-no-run-upstream.json"
            with self.assertRaisesRegex(
                PartialTimelineRepairError,
                "effective run artifact is not upstream of P9 fusion",
            ):
                bridge_effective_artifacts_to_partial_repair(
                    cues=_cues(),
                    run_path=fixture["run_path"],
                    run_artifact_path=fixture["run_artifact_path"],
                    fusion_path=bad_payload_path,
                    fusion_artifact_path=bad_artifact_path,
                    explicit_trust=_trust(),
                )

    def test_fusion_artifact_config_must_bind_exact_effective_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _fixture(root)
            bad_payload_path, bad_artifact_path, _ = _write_stage(
                root,
                name="fusion-wrong-config",
                payload=fixture["fusion_payload"],
                stage="evidence_fusion_shadow",
                role="evidence_fusion",
                upstreams=(fixture["run_artifact"]["artifact_id"],),
                normalized_config={"source_run_artifact_id": "wrong-run-artifact"},
                evidence={
                    "mode": "shadow_only",
                    "policy_calibrated": False,
                    "release_gate_eligible": False,
                    "automatic_timing_change_allowed": False,
                },
            )
            with self.assertRaisesRegex(
                PartialTimelineRepairError,
                "artifact config is bound to another effective run",
            ):
                bridge_effective_artifacts_to_partial_repair(
                    cues=_cues(),
                    run_path=fixture["run_path"],
                    run_artifact_path=fixture["run_artifact_path"],
                    fusion_path=bad_payload_path,
                    fusion_artifact_path=bad_artifact_path,
                    explicit_trust=_trust(),
                )


if __name__ == "__main__":
    unittest.main()
