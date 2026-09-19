import hashlib
import json
import unittest

from lyric_aligner.timeline.boundary_calibration import evaluate_boundary_backend_calibration
from scripts.test_support_production_calibration import production_calibration
from lyric_aligner.text.alignment_lexical import (
    ALIGNMENT_LEXICAL_ID,
    ALIGNMENT_LEXICAL_REVISION,
)
from lyric_aligner.timeline.internal_segmentation import (
    InternalSegmentationError,
    adjudicate_internal_segmentation,
    apply_internal_segmentation_to_rows,
    build_internal_segmentation_evidence,
    build_internal_segmentation_plan,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


class InternalSegmentationTests(unittest.TestCase):
    def source_cues(self):
        return [
            {
                "number": 7,
                "start_ms": 10000,
                "end_ms": 19000,
                "text": "editor recognition",
            }
        ]

    def segments(self):
        return [
            {"track_index": 1, "lrc_index": 10, "text": "first line", "projected_ms": 10050},
            {"track_index": 1, "lrc_index": 11, "text": "second line", "projected_ms": 13000},
            {"track_index": 1, "lrc_index": 12, "text": "third line", "projected_ms": 16000},
        ]

    def report_rows(self, *, evidence="canonical+split_suppressed_no_audio_boundary_authority"):
        segments = self.segments()
        return [
            {
                "kind": "existing",
                "original_cue": "7",
                "start_ms": 10000,
                "end_ms": 19000,
                "original": "editor recognition",
                "text": " ".join(row["text"] for row in segments),
                "status": "replace_existing_unsplit_boundary_safe",
                "confidence": "review",
                "evidence": evidence,
                "track": "fixture",
                "lrc_indices": "10;11;12",
                "canonical_segments_json": json.dumps(
                    segments,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        ]

    def plan(self, *, evidence="canonical+split_suppressed_no_audio_boundary_authority"):
        return build_internal_segmentation_plan(
            task_fingerprint_sha256=SHA_A,
            source_srt_sha256=SHA_B,
            final_audio_sha256=SHA_C,
            report_sha256=SHA_D,
            source_cues=self.source_cues(),
            report_rows=self.report_rows(evidence=evidence),
        )

    def calibration(self, family, group, *, model_id, backend_id=None):
        backend_id = backend_id or family + "-backend"
        backend_version = "1"
        model_revision = "r1"
        artifact = production_calibration(
            backend_id=backend_id,
            family=family,
            correlation_group=group,
            boundary_kind="internal",
            audio_basis="final_mix",
            language="en",
            backend_version=backend_version,
            model_id=model_id,
            model_revision=model_revision,
            lexical_id=ALIGNMENT_LEXICAL_ID,
            lexical_revision=ALIGNMENT_LEXICAL_REVISION,
            error_ms=20,
        )
        self.assertTrue(artifact["passed"], artifact)
        return artifact

    def point(self, family, group, boundary_ms, *, model_id, backend_id=None):
        resolved_backend_id = backend_id or family + "-backend"
        artifact = self.calibration(
            family,
            group,
            model_id=model_id,
            backend_id=resolved_backend_id,
        )
        return {
            "family": family,
            "correlation_group": group,
            "boundary_ms": boundary_ms,
            "uncertainty_ms": 5,
            "backend_id": resolved_backend_id,
            "backend_version": "1",
            "backend_profile_id": "fixture-profile-v1",
            "model_id": model_id,
            "model_revision": "r1",
            "implementation_revision": "a" * 64,
            "adapter_contract_revision": "b" * 64,
            "lexical_id": ALIGNMENT_LEXICAL_ID,
            "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
            "lexical_units_sha256": hashlib.sha256(
                json.dumps(
                    [["first", "line"], ["second", "line"], ["third", "line"]],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "language": "en",
            "audio_basis": "final_mix",
            "window_policy_id": "fixture-window-policy-v1",
            "evidence_sha256": "e" * 64,
            "calibration_passed": True,
            "calibration_sha256": artifact["artifact_sha256"],
            "calibration_artifact": artifact,
            "raw_confidence": 0.4,
        }

    def two_points(self, center_ms):
        return [
            self.point(
                "final_mix_singing_alignment",
                "sofa-independent",
                center_ms - 30,
                model_id="sofa-model",
            ),
            self.point(
                "final_mix_forced_alignment",
                "hubertfa-independent",
                center_ms + 30,
                model_id="hubertfa-model",
            ),
        ]

    def test_plan_preserves_canonical_segment_identity_and_routes_on_lrc_prior(self):
        plan = self.plan()
        self.assertEqual(plan["summary"]["candidate_cue_count"], 1)
        self.assertEqual(plan["summary"]["internal_boundary_job_count"], 2)
        jobs = sorted(plan["jobs"], key=lambda row: row["boundary_index"])
        self.assertEqual(jobs[0]["routing_prior_ms"], 13000)
        self.assertEqual(jobs[1]["routing_prior_ms"], 16000)
        self.assertEqual(jobs[0]["left_segment"]["text"], "first line")
        self.assertEqual(jobs[0]["right_segment"]["text"], "second line")
        self.assertFalse(jobs[0]["automatic_mutation_allowed"])

    def test_one_aligner_never_authorizes_internal_split(self):
        plan = self.plan()
        job = plan["jobs"][0]
        evidence = build_internal_segmentation_evidence(
            plan=plan,
            evidence_by_boundary_id={
                job["boundary_id"]: [
                    self.point(
                        "final_mix_singing_alignment",
                        "sofa-independent",
                        13000,
                        model_id="sofa-model",
                    )
                ]
            },
        )
        decisions = adjudicate_internal_segmentation(plan=plan, evidence=evidence)
        decision = next(row for row in decisions["decisions"] if row["boundary_id"] == job["boundary_id"])
        self.assertFalse(decision["automatic_mutation_allowed"])
        self.assertEqual(decision["action"], "keep_unsplit")

    def test_two_independent_direct_aligners_authorize_complete_split(self):
        plan = self.plan()
        jobs = sorted(plan["jobs"], key=lambda row: row["boundary_index"])
        evidence = build_internal_segmentation_evidence(
            plan=plan,
            evidence_by_boundary_id={
                jobs[0]["boundary_id"]: self.two_points(13000),
                jobs[1]["boundary_id"]: self.two_points(16000),
            },
        )
        decisions = adjudicate_internal_segmentation(plan=plan, evidence=evidence)
        self.assertEqual(decisions["summary"]["automatic_split_boundary_count"], 2)
        output = apply_internal_segmentation_to_rows(
            report_rows=self.report_rows(),
            plan=plan,
            evidence=evidence,
            decisions=decisions,
        )
        self.assertEqual(len(output), 3)
        self.assertEqual([row["text"] for row in output], ["first line", "second line", "third line"])
        self.assertEqual([row["start_ms"] for row in output], [10000, 13000, 16000])
        self.assertEqual([row["end_ms"] for row in output], [13000, 16000, 19000])
        self.assertTrue(all(row["segmentation_authority"] == "audio_verified_internal_segmentation_v1" for row in output))

    def test_partial_boundary_authority_keeps_entire_cue_unsplit(self):
        plan = self.plan()
        jobs = sorted(plan["jobs"], key=lambda row: row["boundary_index"])
        evidence = build_internal_segmentation_evidence(
            plan=plan,
            evidence_by_boundary_id={
                jobs[0]["boundary_id"]: self.two_points(13000),
                jobs[1]["boundary_id"]: [],
            },
        )
        decisions = adjudicate_internal_segmentation(plan=plan, evidence=evidence)
        output = apply_internal_segmentation_to_rows(
            report_rows=self.report_rows(),
            plan=plan,
            evidence=evidence,
            decisions=decisions,
        )
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["lrc_indices"], "10;11;12")

    def test_same_correlation_group_cannot_fake_independence(self):
        plan = self.plan()
        job = plan["jobs"][0]
        evidence = build_internal_segmentation_evidence(
            plan=plan,
            evidence_by_boundary_id={
                job["boundary_id"]: [
                    self.point(
                        "final_mix_singing_alignment",
                        "same-group",
                        12980,
                        model_id="sofa-model",
                    ),
                    self.point(
                        "final_mix_forced_alignment",
                        "same-group",
                        13020,
                        model_id="hubertfa-model",
                    ),
                ]
            },
        )
        decisions = adjudicate_internal_segmentation(plan=plan, evidence=evidence)
        decision = next(row for row in decisions["decisions"] if row["boundary_id"] == job["boundary_id"])
        self.assertFalse(decision["automatic_mutation_allowed"])
        self.assertIn("not independent", decision["reason"])

    def test_same_backend_id_cannot_fake_two_independent_aligners(self):
        plan = self.plan()
        job = plan["jobs"][0]
        evidence = build_internal_segmentation_evidence(
            plan=plan,
            evidence_by_boundary_id={
                job["boundary_id"]: [
                    self.point(
                        "final_mix_singing_alignment",
                        "group-a",
                        12980,
                        model_id="sofa-model",
                        backend_id="shared-backend",
                    ),
                    self.point(
                        "final_mix_forced_alignment",
                        "group-b",
                        13020,
                        model_id="hubertfa-model",
                        backend_id="shared-backend",
                    ),
                ]
            },
        )
        decisions = adjudicate_internal_segmentation(plan=plan, evidence=evidence)
        decision = next(row for row in decisions["decisions"] if row["boundary_id"] == job["boundary_id"])
        self.assertFalse(decision["automatic_mutation_allowed"])
        self.assertEqual(decision["backend_count"], 1)
        self.assertIn("distinct backends", decision["reason"])

    def test_crossfade_or_overlap_risk_blocks_topology_mutation(self):
        plan = self.plan(evidence="canonical+split_suppressed_no_audio_boundary_authority+crossfade")
        job = plan["jobs"][0]
        evidence = build_internal_segmentation_evidence(
            plan=plan,
            evidence_by_boundary_id={job["boundary_id"]: self.two_points(13000)},
        )
        decisions = adjudicate_internal_segmentation(plan=plan, evidence=evidence)
        decision = next(row for row in decisions["decisions"] if row["boundary_id"] == job["boundary_id"])
        self.assertFalse(decision["automatic_mutation_allowed"])
        self.assertIn("topology risk", decision["reason"])

    def test_stale_report_row_is_rejected_before_materialization(self):
        plan = self.plan()
        jobs = sorted(plan["jobs"], key=lambda row: row["boundary_index"])
        evidence = build_internal_segmentation_evidence(
            plan=plan,
            evidence_by_boundary_id={
                jobs[0]["boundary_id"]: self.two_points(13000),
                jobs[1]["boundary_id"]: self.two_points(16000),
            },
        )
        decisions = adjudicate_internal_segmentation(plan=plan, evidence=evidence)
        changed = self.report_rows()
        changed[0]["text"] = "changed after planning"
        with self.assertRaises(InternalSegmentationError):
            apply_internal_segmentation_to_rows(
                report_rows=changed,
                plan=plan,
                evidence=evidence,
                decisions=decisions,
            )

    def test_forged_decision_is_rejected_before_materialization(self):
        plan = self.plan()
        jobs = sorted(plan["jobs"], key=lambda row: row["boundary_index"])
        evidence = build_internal_segmentation_evidence(
            plan=plan,
            evidence_by_boundary_id={
                jobs[0]["boundary_id"]: self.two_points(13000),
                jobs[1]["boundary_id"]: self.two_points(16000),
            },
        )
        decisions = adjudicate_internal_segmentation(plan=plan, evidence=evidence)
        forged = json.loads(json.dumps(decisions))
        forged["decisions"][0]["selected_ms"] += 33
        with self.assertRaises(InternalSegmentationError):
            apply_internal_segmentation_to_rows(
                report_rows=self.report_rows(),
                plan=plan,
                evidence=evidence,
                decisions=forged,
            )

    def test_duplicate_evidence_record_id_fails_closed(self):
        plan = self.plan()
        evidence = build_internal_segmentation_evidence(plan=plan, evidence_by_boundary_id={})
        evidence["records"].append(dict(evidence["records"][0]))
        with self.assertRaises(InternalSegmentationError):
            adjudicate_internal_segmentation(plan=plan, evidence=evidence)

    def test_synthetic_passing_calibration_cannot_enter_internal_evidence(self):
        plan = self.plan()
        job = plan["jobs"][0]
        family = "final_mix_singing_alignment"
        backend_id = family + "-backend"
        gold = [
            {
                "id": f"synthetic-{i}",
                "kind": "boundary",
                "boundary_kind": "internal",
                "gold_ms": 1000 + i * 1000,
                "gold_uncertainty_ms": 0,
                "partition": "calibration" if i < 20 else "holdout",
                "track": f"track-{i % 5}",
            }
            for i in range(30)
        ]
        synthetic = evaluate_boundary_backend_calibration(
            backend_id=backend_id,
            family=family,
            correlation_group="sofa-independent",
            scope={
                "boundary_kind": "internal",
                "audio_basis": "final_mix",
                "language": "en",
                "backend_version": "1",
                "model_id": "sofa-model",
                "model_revision": "r1",
                "lexical_id": ALIGNMENT_LEXICAL_ID,
                "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
            },
            gold_records=gold,
            predictions_ms={row["id"]: row["gold_ms"] for row in gold},
        )
        self.assertTrue(synthetic["passed"])
        point = self.point(
            family,
            "sofa-independent",
            13000,
            model_id="sofa-model",
        )
        point["calibration_artifact"] = synthetic
        point["calibration_sha256"] = synthetic["artifact_sha256"]
        with self.assertRaises(InternalSegmentationError):
            build_internal_segmentation_evidence(
                plan=plan,
                evidence_by_boundary_id={job["boundary_id"]: [point]},
            )

    def test_wrong_lexical_identity_cannot_enter_calibrated_evidence(self):
        plan = self.plan()
        job = plan["jobs"][0]
        point = self.point(
            "final_mix_singing_alignment",
            "sofa-independent",
            13000,
            model_id="sofa-model",
        )
        point["lexical_units_sha256"] = "0" * 64
        with self.assertRaises(InternalSegmentationError):
            build_internal_segmentation_evidence(
                plan=plan,
                evidence_by_boundary_id={job["boundary_id"]: [point]},
            )

    def test_calibrated_internal_point_requires_evidence_sha(self):
        plan = self.plan()
        job = plan["jobs"][0]
        point = self.point(
            "final_mix_singing_alignment",
            "sofa-independent",
            13000,
            model_id="sofa-model",
        )
        point["evidence_sha256"] = ""
        with self.assertRaises(InternalSegmentationError):
            build_internal_segmentation_evidence(
                plan=plan,
                evidence_by_boundary_id={job["boundary_id"]: [point]},
            )


if __name__ == "__main__":
    unittest.main()
