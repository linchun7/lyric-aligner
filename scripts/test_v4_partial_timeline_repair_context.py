from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import atomic_write_json, build_artifact_manifest
from lyric_aligner.srt import Cue
from lyric_aligner.timeline.partial_repair import PartialTimelineRepairError
from lyric_aligner.timeline.partial_repair_context import (
    bridge_effective_run_to_partial_repair,
    derive_effective_run_mapping_context,
)
from lyric_aligner.timeline.partial_repair_evidence import ExplicitCueTrust


FINGERPRINT = "f" * 64


def continuous_mapping(mode: str) -> dict:
    if mode == "AFFINE":
        breakpoints: list[float] = []
        deltas: list[float] = []
    else:
        breakpoints = [12.0]
        deltas = [0.08]
    return {
        "mode": mode,
        "intercept": 0.2,
        "base_slope": 1.05,
        "breakpoints": breakpoints,
        "slope_deltas": deltas,
        "diagnostics": {},
        "objective": 0.1,
    }


def write_stage(
    root: Path,
    *,
    name: str,
    payload: dict,
    stage: str,
    role: str,
    upstreams: tuple[str, ...] = (),
    normalized_config: dict | None = None,
    evidence: dict | None = None,
) -> tuple[Path, Path, dict]:
    payload_path = root / f"{name}.json"
    artifact_path = root / f"{name}.artifact.json"
    atomic_write_json(payload_path, payload)
    artifact = build_artifact_manifest(
        task_fingerprint_sha256=FINGERPRINT,
        stage=stage,
        algorithm_version=__version__,
        outputs=((role, payload_path),),
        normalized_config=normalized_config or {},
        upstream_artifact_ids=upstreams,
        evidence=evidence or {},
    )
    atomic_write_json(artifact_path, artifact)
    return payload_path, artifact_path, artifact


def coarse_stage(root: Path, *, mode: str = "AFFINE", blocked: bool = False):
    payload = {
        "schema_version": "1.0",
        "algorithm_version": __version__,
        "task_fingerprint_sha256": FINGERPRINT,
        "occurrence_id": "occ-1",
        "track_id": "track-1",
        "canonical_selection_sha256": "selection-1",
        "result": {
            "timewarp": {
                "mapping": continuous_mapping(mode),
                "blocked": blocked,
                "selection": f"{mode}_TEST",
            }
        },
    }
    return write_stage(
        root,
        name="coarse",
        payload=payload,
        stage="coarse_audio_alignment",
        role="coarse_alignment",
    )


def fine_stage(root: Path, *, coarse_id: str, mode: str = "PIECEWISE_RATE"):
    payload = {
        "schema_version": "1.0",
        "algorithm_version": __version__,
        "task_fingerprint_sha256": FINGERPRINT,
        "occurrence_id": "occ-1",
        "track_id": "track-1",
        "canonical_selection_sha256": "selection-1",
        "result": {
            "applied": True,
            "timewarp": {
                "mapping": continuous_mapping(mode),
                "blocked": False,
                "selection": f"{mode}_TEST",
            },
        },
    }
    return write_stage(
        root,
        name="fine",
        payload=payload,
        stage="fine_audio_alignment",
        role="fine_alignment",
        upstreams=(coarse_id,),
    )


def cut_stage(root: Path, *, review_id: str = "review-artifact"):
    result = {
        "kind": "CUT_AWARE",
        "segments": [
            {
                "index": 0,
                "mix_start": 0.0,
                "mix_end": 10.0,
                "source_start": 0.0,
                "source_end": 10.0,
                "mapping": continuous_mapping("AFFINE"),
            },
            {
                "index": 1,
                "mix_start": 10.0,
                "mix_end": 20.0,
                "source_start": 14.0,
                "source_end": 24.0,
                "mapping": continuous_mapping("AFFINE"),
            },
        ],
        "cuts": [
            {
                "candidate_id": "cut-1",
                "mix_boundary": 10.0,
                "source_gap_start": 10.0,
                "source_gap_end": 14.0,
            }
        ],
    }
    payload = {
        "schema_version": "1.0",
        "algorithm_version": __version__,
        "task_fingerprint_sha256": FINGERPRINT,
        "occurrence_id": "occ-1",
        "track_id": "track-1",
        "canonical_selection_sha256": "selection-1",
        "source_alignment_path": "coarse",
        "result": result,
    }
    return write_stage(
        root,
        name="cut-timewarp",
        payload=payload,
        stage="cut_timewarp_rebuild",
        role="cut_aware_timewarp",
        upstreams=(review_id,),
        normalized_config={
            "source_review_artifact_id": review_id,
            "confirmed_candidate_ids": ["cut-1"],
        },
        evidence={"occurrence_id": "occ-1", "cut_count": 1},
    )


def run_stage(
    root: Path,
    *,
    occurrence: dict,
    stage: str,
    role: str,
    upstreams: tuple[str, ...],
    extra: dict | None = None,
):
    payload = {
        "schema_version": "1.4",
        "algorithm_version": __version__,
        "task_fingerprint_sha256": FINGERPRINT,
        "calibration_profile_version": "test",
        "calibration_profile_id": "test",
        "status": "ready_for_render",
        "legacy_fallback_used": False,
        "occurrences": [occurrence],
        "issues": [],
        **(extra or {}),
    }
    return write_stage(
        root,
        name=f"run-{stage}",
        payload=payload,
        stage=stage,
        role=role,
        upstreams=upstreams,
    )


def fusion_payload(*, run_stage_name: str, run_artifact_id: str) -> dict:
    return {
        "schema_version": "1.1",
        "algorithm_version": __version__,
        "task_fingerprint_sha256": FINGERPRINT,
        "policy_id": "test-shadow",
        "mode": "shadow_only",
        "policy_calibrated": False,
        "release_gate_eligible": False,
        "automatic_timing_change_allowed": False,
        "source_run_stage": run_stage_name,
        "source_run_artifact_id": run_artifact_id,
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


class PartialTimelineRepairContextTests(unittest.TestCase):
    def test_affine_is_derived_from_effective_coarse_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coarse_path, coarse_artifact_path, coarse_artifact = coarse_stage(root)
            occurrence = {
                "occurrence_id": "occ-1",
                "mapping_source": "coarse",
                "mapping_blocked": False,
                "fine_applied": False,
                "coarse_path": str(coarse_path),
                "coarse_artifact_path": str(coarse_artifact_path),
            }
            run_path, run_artifact_path, _ = run_stage(
                root,
                occurrence=occurrence,
                stage="production_orchestration",
                role="v4_production_run",
                upstreams=(coarse_artifact["artifact_id"],),
            )
            context = derive_effective_run_mapping_context(
                run_path=run_path, run_artifact_path=run_artifact_path
            )
            self.assertEqual(context.mapping_kind_by_occurrence, {"occ-1": "AFFINE"})
            row = context.occurrences[0]
            self.assertEqual(row.source_stage, "coarse_audio_alignment")
            self.assertFalse(row.confirmed_cut)

    def test_piecewise_rate_is_derived_from_applied_fine_not_bpm(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coarse_path, coarse_artifact_path, coarse_artifact = coarse_stage(root)
            fine_path, fine_artifact_path, fine_artifact = fine_stage(
                root, coarse_id=coarse_artifact["artifact_id"]
            )
            occurrence = {
                "occurrence_id": "occ-1",
                "mapping_source": "fine",
                "mapping_blocked": False,
                "fine_applied": True,
                "coarse_path": str(coarse_path),
                "coarse_artifact_path": str(coarse_artifact_path),
                "fine_path": str(fine_path),
                "fine_artifact_path": str(fine_artifact_path),
            }
            run_path, run_artifact_path, _ = run_stage(
                root,
                occurrence=occurrence,
                stage="review_resolution",
                role="v4_reviewed_run",
                upstreams=(
                    coarse_artifact["artifact_id"],
                    fine_artifact["artifact_id"],
                ),
            )
            context = derive_effective_run_mapping_context(
                run_path=run_path, run_artifact_path=run_artifact_path
            )
            self.assertEqual(
                context.mapping_kind_by_occurrence,
                {"occ-1": "PIECEWISE_RATE"},
            )
            self.assertEqual(context.occurrences[0].mapping_source, "fine")

    def test_blocked_reviewed_mapping_is_unavailable_not_confirmed_cut(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coarse_path, coarse_artifact_path, coarse_artifact = coarse_stage(
                root, blocked=True
            )
            occurrence = {
                "occurrence_id": "occ-1",
                "mapping_source": "coarse",
                "mapping_blocked": True,
                "fine_applied": False,
                "coarse_path": str(coarse_path),
                "coarse_artifact_path": str(coarse_artifact_path),
            }
            run_path, run_artifact_path, _ = run_stage(
                root,
                occurrence=occurrence,
                stage="review_resolution",
                role="v4_reviewed_run",
                upstreams=(coarse_artifact["artifact_id"],),
                extra={
                    "status": "review_required",
                    "issues": [
                        {
                            "kind": "timewarp_discontinuity",
                            "occurrence_id": "occ-1",
                            "decision_action": "confirmed_cut",
                            "requires_timeline_rebuild": True,
                        }
                    ],
                },
            )
            context = derive_effective_run_mapping_context(
                run_path=run_path, run_artifact_path=run_artifact_path
            )
            self.assertEqual(context.mapping_kind_by_occurrence, {})
            self.assertEqual(context.confirmed_cut_occurrence_ids, set())
            self.assertEqual(context.occurrences[0].status, "unavailable")

    def test_materialized_cut_artifact_is_the_only_cut_aware_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_id = "review-artifact"
            cut_path, cut_artifact_path, cut_artifact = cut_stage(
                root, review_id=review_id
            )
            occurrence = {
                "occurrence_id": "occ-1",
                "mapping_source": "cut_aware_rebuild",
                "mapping_blocked": False,
                "cut_rebuilt": True,
                "cut_count": 1,
                "cut_mapping_path": str(cut_path),
                "cut_mapping_artifact_path": str(cut_artifact_path),
            }
            run_path, run_artifact_path, _ = run_stage(
                root,
                occurrence=occurrence,
                stage="cut_rebuild",
                role="v4_cut_rebuilt_run",
                upstreams=(review_id, cut_artifact["artifact_id"]),
                extra={"cut_rebuild": {"source_review_artifact_id": review_id}},
            )
            context = derive_effective_run_mapping_context(
                run_path=run_path, run_artifact_path=run_artifact_path
            )
            self.assertEqual(context.mapping_kind_by_occurrence, {"occ-1": "CUT_AWARE"})
            self.assertEqual(context.confirmed_cut_occurrence_ids, {"occ-1"})
            self.assertEqual(context.occurrences[0].cut_count, 1)

    def test_combined_cut_overlap_run_preserves_cut_aware_lineage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_id = "review-artifact"
            cut_path, cut_artifact_path, cut_artifact = cut_stage(
                root, review_id=review_id
            )
            occurrence = {
                "occurrence_id": "occ-1",
                "mapping_source": "cut_aware_rebuild",
                "mapping_blocked": False,
                "cut_rebuilt": True,
                "overlap_recomposed": True,
                "combined_recomposed": True,
                "cut_count": 1,
                "cut_mapping_path": str(cut_path),
                "cut_mapping_artifact_path": str(cut_artifact_path),
            }
            run_path, run_artifact_path, _ = run_stage(
                root,
                occurrence=occurrence,
                stage="combined_recomposition",
                role="v4_combined_run",
                upstreams=(review_id, cut_artifact["artifact_id"], "overlap-artifact"),
                extra={"cut_rebuild": {"source_review_artifact_id": review_id}},
            )
            context = derive_effective_run_mapping_context(
                run_path=run_path, run_artifact_path=run_artifact_path
            )
            self.assertEqual(context.mapping_kind_by_occurrence["occ-1"], "CUT_AWARE")
            self.assertEqual(context.confirmed_cut_occurrence_ids, {"occ-1"})

    def test_overlap_only_run_keeps_underlying_continuous_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coarse_path, coarse_artifact_path, coarse_artifact = coarse_stage(root)
            occurrence = {
                "occurrence_id": "occ-1",
                "mapping_source": "coarse",
                "mapping_blocked": False,
                "fine_applied": False,
                "overlap_recomposed": True,
                "coarse_path": str(coarse_path),
                "coarse_artifact_path": str(coarse_artifact_path),
            }
            run_path, run_artifact_path, _ = run_stage(
                root,
                occurrence=occurrence,
                stage="overlap_recomposition",
                role="v4_recomposed_run",
                upstreams=(coarse_artifact["artifact_id"], "overlap-artifact"),
            )
            context = derive_effective_run_mapping_context(
                run_path=run_path, run_artifact_path=run_artifact_path
            )
            self.assertEqual(context.mapping_kind_by_occurrence, {"occ-1": "AFFINE"})

    def test_forged_cut_label_without_materialized_artifact_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            occurrence = {
                "occurrence_id": "occ-1",
                "mapping_source": "cut_aware_rebuild",
                "mapping_blocked": False,
                "cut_rebuilt": True,
                "cut_count": 1,
                "cut_mapping_path": str(root / "missing.json"),
                "cut_mapping_artifact_path": str(root / "missing.artifact.json"),
            }
            run_path, run_artifact_path, _ = run_stage(
                root,
                occurrence=occurrence,
                stage="cut_rebuild",
                role="v4_cut_rebuilt_run",
                upstreams=("review-artifact",),
                extra={"cut_rebuild": {"source_review_artifact_id": "review-artifact"}},
            )
            with self.assertRaises(PartialTimelineRepairError):
                derive_effective_run_mapping_context(
                    run_path=run_path, run_artifact_path=run_artifact_path
                )

    def test_mapping_artifact_must_be_upstream_of_effective_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coarse_path, coarse_artifact_path, _ = coarse_stage(root)
            occurrence = {
                "occurrence_id": "occ-1",
                "mapping_source": "coarse",
                "mapping_blocked": False,
                "fine_applied": False,
                "coarse_path": str(coarse_path),
                "coarse_artifact_path": str(coarse_artifact_path),
            }
            run_path, run_artifact_path, _ = run_stage(
                root,
                occurrence=occurrence,
                stage="production_orchestration",
                role="v4_production_run",
                upstreams=(),
            )
            with self.assertRaises(PartialTimelineRepairError):
                derive_effective_run_mapping_context(
                    run_path=run_path, run_artifact_path=run_artifact_path
                )

    def test_production_bridge_needs_no_mapping_or_cut_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coarse_path, coarse_artifact_path, coarse_artifact = coarse_stage(root)
            occurrence = {
                "occurrence_id": "occ-1",
                "mapping_source": "coarse",
                "mapping_blocked": False,
                "fine_applied": False,
                "coarse_path": str(coarse_path),
                "coarse_artifact_path": str(coarse_artifact_path),
            }
            run_path, run_artifact_path, run_artifact = run_stage(
                root,
                occurrence=occurrence,
                stage="production_orchestration",
                role="v4_production_run",
                upstreams=(coarse_artifact["artifact_id"],),
            )
            cues = [
                Cue(1, 1000, 2000, "第一句"),
                Cue(2, 2200, 3200, "第二句"),
                Cue(3, 3500, 4500, "第三句"),
            ]
            trust, candidates, report = bridge_effective_run_to_partial_repair(
                cues=cues,
                fusion=fusion_payload(
                    run_stage_name="production_orchestration",
                    run_artifact_id=run_artifact["artifact_id"],
                ),
                run_path=run_path,
                run_artifact_path=run_artifact_path,
                explicit_trust=[
                    ExplicitCueTrust(1, "trusted", "checked"),
                    ExplicitCueTrust(2, "untrusted", "late"),
                    ExplicitCueTrust(3, "trusted", "checked"),
                ],
            )
            self.assertEqual([row.status for row in trust], ["trusted", "untrusted", "trusted"])
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].mapping_kind, "AFFINE")
            self.assertEqual(
                report["mapping_context_source"],
                "formal_effective_run_artifact_lineage",
            )
            context_report = report["effective_run_mapping_context"]
            self.assertNotIn("coarse_path", context_report["occurrences"][0])
            self.assertNotIn("cut_mapping_path", context_report["occurrences"][0])

    def test_fusion_must_bind_exact_effective_run_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coarse_path, coarse_artifact_path, coarse_artifact = coarse_stage(root)
            occurrence = {
                "occurrence_id": "occ-1",
                "mapping_source": "coarse",
                "mapping_blocked": False,
                "fine_applied": False,
                "coarse_path": str(coarse_path),
                "coarse_artifact_path": str(coarse_artifact_path),
            }
            run_path, run_artifact_path, _ = run_stage(
                root,
                occurrence=occurrence,
                stage="production_orchestration",
                role="v4_production_run",
                upstreams=(coarse_artifact["artifact_id"],),
            )
            with self.assertRaises(PartialTimelineRepairError):
                bridge_effective_run_to_partial_repair(
                    cues=[Cue(1, 1000, 2000, "句")],
                    fusion=fusion_payload(
                        run_stage_name="production_orchestration",
                        run_artifact_id="wrong-run-artifact",
                    ),
                    run_path=run_path,
                    run_artifact_path=run_artifact_path,
                    explicit_trust=[],
                )


if __name__ == "__main__":
    unittest.main()
