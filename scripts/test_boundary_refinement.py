import hashlib
import json
import unittest

from lyric_aligner.text.alignment_lexical import (
    ALIGNMENT_LEXICAL_ID,
    ALIGNMENT_LEXICAL_REVISION,
    alignment_units,
)
from lyric_aligner.timeline.boundary_refinement import (
    BoundaryRefinementError,
    adjudicate_boundary_refinement,
    apply_boundary_decisions_to_rows,
    build_boundary_evidence_artifact,
    build_boundary_refinement_plan,
    bind_calibration_to_boundary_point,
)
from lyric_aligner.timeline.boundary_calibration import evaluate_boundary_backend_calibration
from scripts.test_support_production_calibration import production_calibration


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


class BoundaryRefinementArtifactTests(unittest.TestCase):
    def source_cues(self):
        return [
            {"number": 1, "start_ms": 1000, "end_ms": 4000, "text": "alpha beta"},
            {"number": 2, "start_ms": 4200, "end_ms": 12500, "text": "gamma delta epsilon"},
        ]

    def report_rows(self):
        return [
            {
                "kind": "existing",
                "original_cue": "1",
                "start_ms": 1000,
                "end_ms": 4000,
                "text": "alpha beta",
                "lrc_indices": "1",
                "status": "replace_existing",
                "evidence": "jianying+canonical",
            },
            {
                "kind": "existing",
                "original_cue": "2",
                "start_ms": 4200,
                "end_ms": 12500,
                "text": "gamma delta epsilon",
                "lrc_indices": "2;3;4",
                "status": "replace_existing_unsplit_boundary_safe",
                "evidence": "canonical+split_suppressed_no_audio_boundary_authority",
            },
        ]

    def plan(self):
        return build_boundary_refinement_plan(
            task_fingerprint_sha256=SHA_A,
            source_srt_sha256=SHA_B,
            final_audio_sha256=SHA_C,
            report_sha256="f" * 64,
            source_cues=self.source_cues(),
            report_rows=self.report_rows(),
        )

    def point(
        self,
        family,
        boundary_ms,
        *,
        group=None,
        confidence=0.95,
        uncertainty_ms=40,
        boundary_kind="start",
        language="en",
        canonical_text="alpha beta",
        calibration_error_ms=0,
    ):
        correlation_group = group or family
        backend_id = family
        backend_version = "1"
        model_id = family + "-model"
        model_revision = "r1"
        audio_basis = "final_mix" if family.startswith("final_mix") else "source_projection"
        calibration = production_calibration(
            backend_id=backend_id,
            family=family,
            correlation_group=correlation_group,
            boundary_kind=boundary_kind,
            audio_basis=audio_basis,
            language=language,
            backend_version=backend_version,
            model_id=model_id,
            model_revision=model_revision,
            lexical_id=ALIGNMENT_LEXICAL_ID,
            lexical_revision=ALIGNMENT_LEXICAL_REVISION,
            error_ms=calibration_error_ms,
        )
        lexical_units_sha256 = hashlib.sha256(
            json.dumps(
                [alignment_units(language, canonical_text)],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "family": family,
            "correlation_group": correlation_group,
            "boundary_ms": boundary_ms,
            "confidence": confidence,
            "uncertainty_ms": uncertainty_ms,
            "backend_id": backend_id,
            "backend_version": backend_version,
            "backend_profile_id": "fixture-profile-v1",
            "model_id": model_id,
            "model_revision": model_revision,
            "implementation_revision": "a" * 64,
            "adapter_contract_revision": "b" * 64,
            "lexical_id": ALIGNMENT_LEXICAL_ID,
            "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
            "lexical_units_sha256": lexical_units_sha256,
            "audio_basis": audio_basis,
            "language": language,
            "window_policy_id": "fixture-window-policy-v1",
            "evidence_sha256": "d" * 64,
            "calibration_passed": True,
            "calibration_sha256": calibration["artifact_sha256"],
            "calibration_artifact": calibration,
        }

    def boundary_job(self, plan, cue_number, boundary_kind):
        return next(
            row
            for row in plan["jobs"]
            if row["cue_number"] == cue_number and row["boundary_kind"] == boundary_kind
        )

    def test_plan_routes_every_editor_start_and_end(self):
        plan = self.plan()
        self.assertEqual(plan["summary"]["source_cue_count"], 2)
        self.assertEqual(plan["summary"]["boundary_job_count"], 4)
        self.assertFalse(any(row["automatic_mutation_allowed"] for row in plan["jobs"]))
        first = self.boundary_job(plan, 1, "start")
        self.assertEqual(first["editor_ms"], 1000)
        self.assertEqual(first["canonical_text"], "alpha beta")
        self.assertEqual(first["difficulty_tier"], "A_local_refine")

    def test_long_multiline_suppressed_split_routes_structural_or_heavy(self):
        plan = self.plan()
        start = self.boundary_job(plan, 2, "start")
        # split_suppressed marks a mapping/topology concern, which must not be
        # treated as an ordinary few-frame polish.
        self.assertEqual(start["difficulty_tier"], "D_structural")
        self.assertIn("structural_realignment", start["requested_capabilities"])
        self.assertGreaterEqual(start["mix_window_ms"][1] - start["mix_window_ms"][0], 8000)

    def test_empty_evidence_keeps_all_editor_boundaries(self):
        plan = self.plan()
        evidence = build_boundary_evidence_artifact(plan=plan, evidence_by_boundary_id={})
        decisions = adjudicate_boundary_refinement(plan=plan, evidence=evidence)
        self.assertEqual(decisions["summary"]["automatic_mutation_count"], 0)
        self.assertTrue(all(row["action"] in {"keep_editor", "structural_escalation"} for row in decisions["decisions"]))

    def test_direct_final_mix_plus_independent_source_projection_can_refine(self):
        plan = self.plan()
        job = self.boundary_job(plan, 1, "start")
        evidence = build_boundary_evidence_artifact(
            plan=plan,
            evidence_by_boundary_id={
                job["boundary_id"]: [
                    self.point("final_mix_singing_alignment", 1015, group="final_model"),
                    self.point("source_forced_alignment_projection", 1040, group="source_model"),
                ]
            },
        )
        decisions = adjudicate_boundary_refinement(plan=plan, evidence=evidence)
        decision = next(row for row in decisions["decisions"] if row["boundary_id"] == job["boundary_id"])
        self.assertTrue(decision["automatic_mutation_allowed"])
        # Final-mix observation remains primary after source cross-validation.
        self.assertEqual(decision["selected_ms"], 1015)

    def test_calibration_p90_sets_uncertainty_floor(self):
        plan = self.plan()
        job = self.boundary_job(plan, 1, "start")
        evidence = build_boundary_evidence_artifact(
            plan=plan,
            evidence_by_boundary_id={
                job["boundary_id"]: [
                    self.point(
                        "final_mix_singing_alignment",
                        1015,
                        group="final_model",
                        uncertainty_ms=5,
                        calibration_error_ms=60,
                    )
                ]
            },
        )
        record = next(
            row for row in evidence["records"] if row["boundary_id"] == job["boundary_id"]
        )
        point = record["points"][0]
        self.assertEqual(point["calibration_uncertainty_floor_ms"], 60)
        self.assertEqual(point["uncertainty_ms"], 60)

    def test_same_model_correlation_group_cannot_authorize(self):
        plan = self.plan()
        job = self.boundary_job(plan, 1, "start")
        evidence = build_boundary_evidence_artifact(
            plan=plan,
            evidence_by_boundary_id={
                job["boundary_id"]: [
                    self.point("final_mix_singing_alignment", 1015, group="same_model"),
                    self.point("source_forced_alignment_projection", 1030, group="same_model"),
                ]
            },
        )
        decisions = adjudicate_boundary_refinement(plan=plan, evidence=evidence)
        decision = next(row for row in decisions["decisions"] if row["boundary_id"] == job["boundary_id"])
        self.assertFalse(decision["automatic_mutation_allowed"])
        self.assertEqual(decision["action"], "keep_editor")

    def test_structural_job_does_not_mutate_without_explicit_confirmation(self):
        plan = self.plan()
        job = self.boundary_job(plan, 2, "start")
        evidence = build_boundary_evidence_artifact(
            plan=plan,
            evidence_by_boundary_id={
                job["boundary_id"]: [
                    self.point(
                        "final_mix_singing_alignment",
                        4250,
                        group="final_model",
                        canonical_text="gamma delta epsilon",
                    ),
                    self.point(
                        "source_forced_alignment_projection",
                        4270,
                        group="source_model",
                        canonical_text="gamma delta epsilon",
                    ),
                ]
            },
        )
        decisions = adjudicate_boundary_refinement(plan=plan, evidence=evidence)
        decision = next(row for row in decisions["decisions"] if row["boundary_id"] == job["boundary_id"])
        self.assertEqual(decision["action"], "structural_escalation")
        self.assertFalse(decision["automatic_mutation_allowed"])

    def test_apply_changes_only_audio_authorized_outer_boundary(self):
        plan = self.plan()
        start_job = self.boundary_job(plan, 1, "start")
        evidence = build_boundary_evidence_artifact(
            plan=plan,
            evidence_by_boundary_id={
                start_job["boundary_id"]: [
                    self.point("final_mix_singing_alignment", 1030, group="final_model"),
                    self.point("source_forced_alignment_projection", 1050, group="source_model"),
                ]
            },
        )
        decisions = adjudicate_boundary_refinement(plan=plan, evidence=evidence)
        output = apply_boundary_decisions_to_rows(
            report_rows=self.report_rows(),
            source_cues=self.source_cues(),
            plan=plan,
            evidence=evidence,
            decisions=decisions,
        )
        first = next(row for row in output if row["original_cue"] == "1")
        second = next(row for row in output if row["original_cue"] == "2")
        self.assertEqual(first["start_ms"], 1030)
        self.assertEqual(first["end_ms"], 4000)
        self.assertEqual(first["boundary_authority"], "audio_verified_boundary_v1")
        self.assertEqual(second["start_ms"], 4200)

    def test_apply_refuses_to_structurally_retime_multirow_original_cue(self):
        plan = self.plan()
        job = self.boundary_job(plan, 1, "start")
        evidence = build_boundary_evidence_artifact(
            plan=plan,
            evidence_by_boundary_id={
                job["boundary_id"]: [
                    self.point("final_mix_singing_alignment", 1030, group="final_model"),
                    self.point("source_forced_alignment_projection", 1040, group="source_model"),
                ]
            },
        )
        decisions = adjudicate_boundary_refinement(plan=plan, evidence=evidence)
        rows = self.report_rows()
        rows[0] = {**rows[0], "kind": "split", "end_ms": 2000}
        rows.insert(1, {**rows[0], "start_ms": 2000, "end_ms": 4000, "text": "beta"})
        output = apply_boundary_decisions_to_rows(
            report_rows=rows,
            source_cues=self.source_cues(),
            plan=plan,
            evidence=evidence,
            decisions=decisions,
        )
        cue_one_rows = [row for row in output if row["original_cue"] == "1"]
        self.assertTrue(all("boundary_authority" not in row for row in cue_one_rows))

    def test_evidence_artifact_rejects_unknown_boundary(self):
        plan = self.plan()
        with self.assertRaises(BoundaryRefinementError):
            build_boundary_evidence_artifact(
                plan=plan,
                evidence_by_boundary_id={"unknown": [self.point("final_mix_singing_alignment", 1000)]},
            )

    def test_evidence_is_bound_to_exact_plan(self):
        plan = self.plan()
        evidence = build_boundary_evidence_artifact(plan=plan, evidence_by_boundary_id={})
        changed = dict(plan)
        changed["source_srt_sha256"] = "e" * 64
        with self.assertRaises(BoundaryRefinementError):
            adjudicate_boundary_refinement(plan=changed, evidence=evidence)

    def test_forged_calibration_sha_cannot_authorize_mutation(self):
        plan = self.plan()
        job = self.boundary_job(plan, 1, "start")
        evidence = build_boundary_evidence_artifact(
            plan=plan,
            evidence_by_boundary_id={
                job["boundary_id"]: [
                    self.point("final_mix_singing_alignment", 1015, group="final_model"),
                    self.point("source_forced_alignment_projection", 1040, group="source_model"),
                ]
            },
        )
        record = next(row for row in evidence["records"] if row["boundary_id"] == job["boundary_id"])
        record["points"][0]["calibration_sha256"] = "f" * 64
        with self.assertRaises(BoundaryRefinementError):
            adjudicate_boundary_refinement(plan=plan, evidence=evidence)

    def test_synthetic_passing_calibration_cannot_enter_production_evidence(self):
        plan = self.plan()
        job = self.boundary_job(plan, 1, "start")
        family = "final_mix_singing_alignment"
        gold_records = [
            {
                "id": f"synthetic-{i}",
                "kind": "boundary",
                "boundary_kind": "start",
                "gold_ms": 1000 + i * 1000,
                "gold_uncertainty_ms": 0,
                "partition": "calibration" if i < 20 else "holdout",
                "track": f"track-{i % 5}",
            }
            for i in range(30)
        ]
        synthetic = evaluate_boundary_backend_calibration(
            backend_id=family,
            family=family,
            correlation_group="final_model",
            scope={
                "boundary_kind": "start",
                "audio_basis": "final_mix",
                "language": "fixture",
                "backend_version": "1",
                "model_id": family + "-model",
                "model_revision": "r1",
            },
            gold_records=gold_records,
            predictions_ms={row["id"]: row["gold_ms"] for row in gold_records},
        )
        self.assertTrue(synthetic["passed"])
        point = self.point(family, 1015, group="final_model")
        point["calibration_artifact"] = synthetic
        point["calibration_sha256"] = synthetic["artifact_sha256"]
        with self.assertRaises(BoundaryRefinementError):
            build_boundary_evidence_artifact(
                plan=plan,
                evidence_by_boundary_id={job["boundary_id"]: [point]},
            )

    def test_forged_decision_is_rejected_before_outer_materialization(self):
        plan = self.plan()
        job = self.boundary_job(plan, 1, "start")
        evidence = build_boundary_evidence_artifact(
            plan=plan,
            evidence_by_boundary_id={
                job["boundary_id"]: [
                    self.point("final_mix_singing_alignment", 1030, group="final_model"),
                    self.point("source_forced_alignment_projection", 1050, group="source_model"),
                ]
            },
        )
        decisions = adjudicate_boundary_refinement(plan=plan, evidence=evidence)
        forged = dict(decisions)
        forged["decisions"] = [dict(row) for row in decisions["decisions"]]
        forged["decisions"][0]["selected_ms"] += 33
        with self.assertRaises(BoundaryRefinementError):
            apply_boundary_decisions_to_rows(
                report_rows=self.report_rows(),
                source_cues=self.source_cues(),
                plan=plan,
                evidence=evidence,
                decisions=forged,
            )

    def test_start_calibration_cannot_be_reused_for_end_boundary(self):
        plan = self.plan()
        job = self.boundary_job(plan, 1, "end")
        with self.assertRaises(BoundaryRefinementError):
            build_boundary_evidence_artifact(
                plan=plan,
                evidence_by_boundary_id={
                    job["boundary_id"]: [
                        self.point("final_mix_singing_alignment", 3990, group="final_model")
                    ]
                },
            )

    def test_bind_api_reconstructs_calibration_claim_from_raw_point(self):
        calibrated = self.point("final_mix_singing_alignment", 1015, group="final_model")
        artifact = calibrated["calibration_artifact"]
        raw = dict(calibrated)
        raw.pop("calibration_artifact")
        raw["calibration_passed"] = False
        raw["calibration_sha256"] = ""
        bound = bind_calibration_to_boundary_point(raw, artifact, boundary_kind="start")
        self.assertTrue(bound["calibration_passed"])
        self.assertEqual(bound["calibration_sha256"], artifact["artifact_sha256"])

    def test_bind_api_rejects_profile_scope_mismatch(self):
        calibrated = self.point("final_mix_singing_alignment", 1015, group="final_model")
        artifact = calibrated["calibration_artifact"]
        raw = dict(calibrated)
        raw.pop("calibration_artifact")
        raw["calibration_passed"] = False
        raw["calibration_sha256"] = ""
        raw["backend_profile_id"] = "another-profile"
        with self.assertRaises(BoundaryRefinementError):
            bind_calibration_to_boundary_point(raw, artifact, boundary_kind="start")


if __name__ == "__main__":
    unittest.main()
