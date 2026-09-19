import copy
import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.alignment.boundary_executor import BOUNDARY_ALIGNMENT_RUN_SCHEMA_VERSION
from lyric_aligner.alignment.internal_executor import INTERNAL_ALIGNMENT_RUN_SCHEMA_VERSION
from lyric_aligner.text.alignment_lexical import (
    ALIGNMENT_LEXICAL_ID,
    ALIGNMENT_LEXICAL_REVISION,
    alignment_units,
    segment_units_from_canonical_segments,
)
from lyric_aligner.timeline.boundary_refinement import build_boundary_refinement_plan
from lyric_aligner.timeline.internal_segmentation import build_internal_segmentation_plan
from scripts.test_support_production_calibration import production_calibration
from scripts.v4_adjudicate_calibrated_alignment import (
    ADJUDICATION_BUNDLE_SCHEMA_VERSION,
    BLIND_REUSE_ANCHOR_SUITE_SCHEMA_VERSION,
    CalibratedAlignmentAdjudicationError,
    run_adjudication,
    sha256_json,
)
from scripts.v4_run_human_boundary_calibration_suite import SUITE_SCHEMA_VERSION


FINAL_AUDIO_SHA = "5" * 64


class CalibratedAlignmentAdjudicationTests(unittest.TestCase):
    def setUp(self):
        self.profiles = [
            {
                "profile_id": "profile-sofa",
                "backend_id": "backend-sofa",
                "backend_version": "full-sequence-alignment-core-1.0",
                "family": "final_mix_singing_alignment",
                "correlation_group": "group-sofa",
                "model_id": "model-sofa",
                "model_revision": "sofa-r1",
                "implementation_revision": "a" * 64,
                "adapter_contract_revision": "b" * 64,
            },
            {
                "profile_id": "profile-hubertfa",
                "backend_id": "backend-hubertfa",
                "backend_version": "full-sequence-alignment-core-1.0",
                "family": "final_mix_forced_alignment",
                "correlation_group": "group-hubertfa",
                "model_id": "model-hubertfa",
                "model_revision": "hubertfa-r1",
                "implementation_revision": "c" * 64,
                "adapter_contract_revision": "d" * 64,
            },
        ]

    def outer_plan(self):
        return build_boundary_refinement_plan(
            task_fingerprint_sha256="a" * 64,
            source_srt_sha256="b" * 64,
            final_audio_sha256=FINAL_AUDIO_SHA,
            report_sha256="c" * 64,
            source_cues=[
                {
                    "number": 1,
                    "start_ms": 1000,
                    "end_ms": 4000,
                    "text": "alpha beta",
                }
            ],
            report_rows=[
                {
                    "kind": "existing",
                    "original_cue": "1",
                    "start_ms": 1000,
                    "end_ms": 4000,
                    "text": "alpha beta",
                    "lrc_indices": "1",
                    "status": "replace_existing",
                    "evidence": "fixture",
                }
            ],
        )

    def internal_plan(self):
        segments = [
            {"track_index": 0, "lrc_index": 1, "text": "first line", "projected_ms": 1200},
            {"track_index": 0, "lrc_index": 2, "text": "second line", "projected_ms": 3000},
            {"track_index": 0, "lrc_index": 3, "text": "third line", "projected_ms": 4400},
        ]
        return build_internal_segmentation_plan(
            task_fingerprint_sha256="a" * 64,
            source_srt_sha256="b" * 64,
            final_audio_sha256=FINAL_AUDIO_SHA,
            report_sha256="c" * 64,
            source_cues=[
                {
                    "number": 1,
                    "start_ms": 1000,
                    "end_ms": 5000,
                    "text": "editor recognition",
                }
            ],
            report_rows=[
                {
                    "kind": "existing",
                    "original_cue": "1",
                    "start_ms": 1000,
                    "end_ms": 5000,
                    "original": "editor recognition",
                    "text": "first line second line third line",
                    "status": "replace_existing_unsplit_boundary_safe",
                    "confidence": "review",
                    "evidence": "canonical+split_suppressed_no_audio_boundary_authority",
                    "track": "fixture",
                    "lrc_indices": "1;2;3",
                    "canonical_segments_json": json.dumps(
                        segments,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            ],
        )

    def lexical_sha(self, text):
        return sha256_json([alignment_units("en", text)])

    def internal_lexical_sha(self, job):
        return sha256_json(
            segment_units_from_canonical_segments(job["canonical_segments"], language="en")
        )

    def calibration(self, profile, kind):
        return production_calibration(
            backend_id=profile["backend_id"],
            family=profile["family"],
            correlation_group=profile["correlation_group"],
            boundary_kind=kind,
            audio_basis="final_mix",
            language="en",
            backend_version=profile["backend_version"],
            model_id=profile["model_id"],
            model_revision=profile["model_revision"],
            implementation_revision=profile["implementation_revision"],
            adapter_contract_revision=profile["adapter_contract_revision"],
            lexical_id=ALIGNMENT_LEXICAL_ID,
            lexical_revision=ALIGNMENT_LEXICAL_REVISION,
            backend_profile_id=profile["profile_id"],
            window_policy_id="fixture-window-policy-v1",
        )

    def write_suite(
        self,
        root: Path,
        *,
        non_authoritative=(),
        schema_version=SUITE_SCHEMA_VERSION,
        mode="calibration_only_no_subtitle_mutation",
        include_kind_summary=False,
    ):
        suite_dir = root / "suite"
        suite_dir.mkdir()
        disabled = {(str(profile_id), str(kind)) for profile_id, kind in non_authoritative}
        rows = []
        profile_rows = []
        calibrations = {}
        authority_by_scope = {}
        for profile in self.profiles:
            profile_dir = suite_dir / profile["profile_id"]
            profile_dir.mkdir()
            profile_rows.append(
                {
                    **profile,
                    "language": "en",
                    "audio_basis": "final_mix",
                    "window_policy_id": "fixture-window-policy-v1",
                }
            )
            for kind in ("start", "end", "internal"):
                authoritative = (profile["profile_id"], kind) not in disabled
                calibration = production_calibration(
                    backend_id=profile["backend_id"],
                    family=profile["family"],
                    correlation_group=profile["correlation_group"],
                    boundary_kind=kind,
                    audio_basis="final_mix",
                    language="en",
                    backend_version=profile["backend_version"],
                    model_id=profile["model_id"],
                    model_revision=profile["model_revision"],
                    implementation_revision=profile["implementation_revision"],
                    adapter_contract_revision=profile["adapter_contract_revision"],
                    lexical_id=ALIGNMENT_LEXICAL_ID,
                    lexical_revision=ALIGNMENT_LEXICAL_REVISION,
                    backend_profile_id=profile["profile_id"],
                    window_policy_id="fixture-window-policy-v1",
                    error_ms=0 if authoritative else 10000,
                )
                calibrations[(profile["profile_id"], kind)] = calibration
                authority_by_scope[(profile["profile_id"], kind)] = bool(calibration["passed"])
                (profile_dir / f"{kind}.calibration.json").write_text(
                    json.dumps(calibration, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                rows.append(
                    {
                        "profile_id": profile["profile_id"],
                        "backend_id": profile["backend_id"],
                        "family": profile["family"],
                        "correlation_group": profile["correlation_group"],
                        "boundary_kind": kind,
                        "passed": bool(calibration["passed"]),
                        "production_authoritative": bool(calibration["passed"]),
                        "prediction_count": 30,
                        "prediction_coverage": 1.0,
                        "holdout_prediction_coverage": 1.0,
                        "holdout_median_effective_error_frames": 0.0 if authoritative else 300.0,
                        "holdout_p90_effective_error_frames": 0.0 if authoritative else 300.0,
                        "holdout_max_effective_error_frames": 0.0 if authoritative else 300.0,
                        "holdout_catastrophic_fraction": 0.0 if authoritative else 1.0,
                        "prediction_artifact_sha256": "9" * 64,
                        "calibration_artifact_sha256": calibration["artifact_sha256"],
                    }
                )
        kind_ready = {
            kind: all(authority_by_scope[(profile["profile_id"], kind)] for profile in self.profiles)
            for kind in ("start", "end", "internal")
        }
        summary = {
            "schema_version": schema_version,
            "mode": mode,
            "selection_lock_sha256": "1" * 64,
            "human_gold_artifact_sha256": "2" * 64,
            "final_audio_sha256": FINAL_AUDIO_SHA,
            "language_scope": "en",
            "backend_profiles": profile_rows,
            "scope_count": len(rows),
            "production_authority_ready": all(kind_ready.values()),
            "subtitle_mutation_performed": False,
            "scopes": rows,
        }
        if include_kind_summary:
            summary["boundary_kind_authority_ready"] = kind_ready
        summary["suite_sha256"] = sha256_json(summary)
        (suite_dir / "suite.summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return suite_dir, summary, calibrations

    def raw_run(self, plan, profile, *, offset_ms):
        jobs = []
        for job in plan["jobs"]:
            editor_ms = int(job["editor_ms"])
            if job["boundary_kind"] == "start":
                boundary_ms = editor_ms + offset_ms
            else:
                boundary_ms = editor_ms - offset_ms
            point = {
                "family": profile["family"],
                "correlation_group": profile["correlation_group"],
                "boundary_ms": boundary_ms,
                "confidence": 0.5,
                "raw_confidence": 0.5,
                "uncertainty_ms": 20,
                "backend_id": profile["backend_id"],
                "backend_version": profile["backend_version"],
                "backend_profile_id": profile["profile_id"],
                "model_id": profile["model_id"],
                "model_revision": profile["model_revision"],
                "implementation_revision": profile["implementation_revision"],
                "adapter_contract_revision": profile["adapter_contract_revision"],
                "lexical_id": ALIGNMENT_LEXICAL_ID,
                "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
                "lexical_units_sha256": self.lexical_sha(job["canonical_text"]),
                "audio_basis": "final_mix",
                "language": "en",
                "window_policy_id": "fixture-window-policy-v1",
                "evidence_sha256": ("d" if offset_ms == 20 else "e") * 64,
                "calibration_passed": False,
                "calibration_sha256": "",
            }
            jobs.append(
                {
                    "boundary_id": job["boundary_id"],
                    "cue_number": job["cue_number"],
                    "boundary_kind": job["boundary_kind"],
                    "point": point,
                }
            )
        return {
            "schema_version": BOUNDARY_ALIGNMENT_RUN_SCHEMA_VERSION,
            "protocol_version": "final-mix-boundary-alignment-1.0",
            "backend_id": profile["backend_id"],
            "backend_version": profile["backend_version"],
            "backend_profile_id": profile["profile_id"],
            "model_id": profile["model_id"],
            "model_revision": profile["model_revision"],
            "family": profile["family"],
            "correlation_group": profile["correlation_group"],
            "language": "en",
            "window_policy_id": "fixture-window-policy-v1",
            "final_audio_sha256": FINAL_AUDIO_SHA,
            "plan_sha256": sha256_json(plan),
            "timing_authority": "diagnostic_uncalibrated_direct_final_mix_evidence",
            "command_invoked": True,
            "job_count": len(jobs),
            "jobs": jobs,
        }

    def raw_internal_run(self, plan, profile, *, offset_ms):
        jobs = []
        for job in plan["jobs"]:
            boundary_ms = int(job["routing_prior_ms"]) + offset_ms
            point = {
                "family": profile["family"],
                "correlation_group": profile["correlation_group"],
                "boundary_ms": boundary_ms,
                "confidence": 0.5,
                "raw_confidence": 0.5,
                "uncertainty_ms": 20,
                "backend_id": profile["backend_id"],
                "backend_version": profile["backend_version"],
                "backend_profile_id": profile["profile_id"],
                "model_id": profile["model_id"],
                "model_revision": profile["model_revision"],
                "implementation_revision": profile["implementation_revision"],
                "adapter_contract_revision": profile["adapter_contract_revision"],
                "lexical_id": ALIGNMENT_LEXICAL_ID,
                "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
                "lexical_units_sha256": self.internal_lexical_sha(job),
                "audio_basis": "final_mix",
                "language": "en",
                "window_policy_id": "fixture-window-policy-v1",
                "evidence_sha256": ("a" if offset_ms < 0 else "b") * 64,
                "calibration_passed": False,
                "calibration_sha256": "",
            }
            jobs.append(
                {
                    "boundary_id": job["boundary_id"],
                    "cue_number": job["cue_number"],
                    "boundary_index": job["boundary_index"],
                    "point": point,
                }
            )
        return {
            "schema_version": INTERNAL_ALIGNMENT_RUN_SCHEMA_VERSION,
            "protocol_version": "internal-final-mix-alignment-1.0",
            "backend_id": profile["backend_id"],
            "backend_version": profile["backend_version"],
            "backend_profile_id": profile["profile_id"],
            "model_id": profile["model_id"],
            "model_revision": profile["model_revision"],
            "family": profile["family"],
            "correlation_group": profile["correlation_group"],
            "language": "en",
            "window_policy_id": "fixture-window-policy-v1",
            "final_audio_sha256": FINAL_AUDIO_SHA,
            "plan_sha256": sha256_json(plan),
            "timing_authority": "diagnostic_uncalibrated_direct_final_mix_evidence",
            "command_invoked": True,
            "job_count": len(jobs),
            "jobs": jobs,
        }

    def test_outer_transaction_binds_two_calibrated_backends_and_adjudicates(self):
        plan = self.outer_plan()
        runs = [
            self.raw_run(plan, self.profiles[0], offset_ms=20),
            self.raw_run(plan, self.profiles[1], offset_ms=40),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            suite_dir, summary, _ = self.write_suite(Path(temporary))
            evidence, decisions, bundle = run_adjudication(
                mode="outer",
                plan=plan,
                backend_runs=runs,
                suite_dir=suite_dir,
            )
        self.assertEqual(bundle["schema_version"], ADJUDICATION_BUNDLE_SCHEMA_VERSION)
        self.assertEqual(bundle["calibration_suite_sha256"], summary["suite_sha256"])
        self.assertEqual(bundle["structural_confirmations"], [])
        self.assertFalse(bundle["subtitle_mutation_performed"])
        self.assertEqual(evidence["record_count"], 2)
        self.assertEqual(len(evidence["calibration_artifacts"]), 4)
        self.assertTrue(
            all(
                len(row["points"]) == 2
                and all(point["calibration_passed"] for point in row["points"])
                for row in evidence["records"]
            )
        )
        self.assertEqual(decisions["summary"]["boundary_count"], 2)
        self.assertEqual(decisions["summary"]["automatic_mutation_count"], 2)
        self.assertTrue(all(row["automatic_mutation_allowed"] for row in decisions["decisions"]))

    def test_internal_transaction_binds_two_calibrated_backends_and_adjudicates(self):
        plan = self.internal_plan()
        runs = [
            self.raw_internal_run(plan, self.profiles[0], offset_ms=-20),
            self.raw_internal_run(plan, self.profiles[1], offset_ms=20),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            suite_dir, summary, _ = self.write_suite(Path(temporary))
            evidence, decisions, bundle = run_adjudication(
                mode="internal",
                plan=plan,
                backend_runs=runs,
                suite_dir=suite_dir,
            )
        self.assertEqual(bundle["schema_version"], ADJUDICATION_BUNDLE_SCHEMA_VERSION)
        self.assertEqual(bundle["calibration_suite_sha256"], summary["suite_sha256"])
        self.assertEqual(bundle["structural_confirmations"], [])
        self.assertFalse(bundle["subtitle_mutation_performed"])
        self.assertEqual(len(evidence["records"]), 2)
        self.assertEqual(len(evidence["calibration_artifacts"]), 2)
        self.assertTrue(
            all(
                len(row["points"]) == 2
                and all(point["calibration_passed"] for point in row["points"])
                for row in evidence["records"]
            )
        )
        self.assertEqual(decisions["summary"]["internal_boundary_count"], 2)
        self.assertEqual(decisions["summary"]["automatic_split_boundary_count"], 2)
        self.assertTrue(all(row["automatic_mutation_allowed"] for row in decisions["decisions"]))

    def test_one_unaligned_backend_only_blocks_that_boundary(self):
        plan = self.outer_plan()
        runs = [
            self.raw_run(plan, self.profiles[0], offset_ms=20),
            self.raw_run(plan, self.profiles[1], offset_ms=40),
        ]
        start_row = next(row for row in runs[1]["jobs"] if row["boundary_kind"] == "start")
        start_row["status"] = "unaligned"
        start_row["reason"] = "fixture_difficult_singing"
        start_row["point"] = None
        with tempfile.TemporaryDirectory() as temporary:
            suite_dir, _, _ = self.write_suite(Path(temporary))
            evidence, decisions, _ = run_adjudication(
                mode="outer",
                plan=plan,
                backend_runs=runs,
                suite_dir=suite_dir,
            )
        records = {row["boundary_kind"]: row for row in evidence["records"]}
        self.assertEqual(len(records["start"]["points"]), 1)
        self.assertEqual(len(records["end"]["points"]), 2)
        by_kind = {row["boundary_kind"]: row for row in decisions["decisions"]}
        self.assertFalse(by_kind["start"]["automatic_mutation_allowed"])
        self.assertTrue(by_kind["end"]["automatic_mutation_allowed"])
        self.assertEqual(decisions["summary"]["automatic_mutation_count"], 1)

    def test_blind_reuse_anchor_suite_schema_is_accepted(self):
        plan = self.outer_plan()
        runs = [
            self.raw_run(plan, self.profiles[0], offset_ms=20),
            self.raw_run(plan, self.profiles[1], offset_ms=40),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            suite_dir, summary, _ = self.write_suite(
                Path(temporary),
                schema_version=BLIND_REUSE_ANCHOR_SUITE_SCHEMA_VERSION,
                mode="blind_scope_subset_calibration_only_no_subtitle_mutation",
                include_kind_summary=True,
            )
            evidence, decisions, bundle = run_adjudication(
                mode="outer",
                plan=plan,
                backend_runs=runs,
                suite_dir=suite_dir,
            )
        self.assertEqual(bundle["calibration_suite_sha256"], summary["suite_sha256"])
        self.assertEqual(len(evidence["calibration_artifacts"]), 4)
        self.assertEqual(decisions["summary"]["automatic_mutation_count"], 2)

    def test_outer_authority_is_selective_by_boundary_kind(self):
        plan = self.outer_plan()
        runs = [
            self.raw_run(plan, self.profiles[0], offset_ms=20),
            self.raw_run(plan, self.profiles[1], offset_ms=40),
        ]
        disabled = {
            (self.profiles[0]["profile_id"], "end"),
            (self.profiles[1]["profile_id"], "end"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            suite_dir, summary, _ = self.write_suite(
                Path(temporary),
                non_authoritative=disabled,
                schema_version=BLIND_REUSE_ANCHOR_SUITE_SCHEMA_VERSION,
                mode="blind_scope_subset_calibration_only_no_subtitle_mutation",
                include_kind_summary=True,
            )
            evidence, decisions, _ = run_adjudication(
                mode="outer",
                plan=plan,
                backend_runs=runs,
                suite_dir=suite_dir,
            )
        self.assertFalse(summary["production_authority_ready"])
        self.assertEqual(
            summary["boundary_kind_authority_ready"],
            {"start": True, "end": False, "internal": True},
        )
        records = {row["boundary_kind"]: row for row in evidence["records"]}
        self.assertEqual(len(records["start"]["points"]), 2)
        self.assertEqual(len(records["end"]["points"]), 0)
        by_kind = {row["boundary_kind"]: row for row in decisions["decisions"]}
        self.assertTrue(by_kind["start"]["automatic_mutation_allowed"])
        self.assertFalse(by_kind["end"]["automatic_mutation_allowed"])
        self.assertEqual(decisions["summary"]["automatic_mutation_count"], 1)

    def test_one_authoritative_backend_does_not_open_kind_authority(self):
        plan = self.outer_plan()
        runs = [
            self.raw_run(plan, self.profiles[0], offset_ms=20),
            self.raw_run(plan, self.profiles[1], offset_ms=40),
        ]
        disabled = {(self.profiles[1]["profile_id"], "start")}
        with tempfile.TemporaryDirectory() as temporary:
            suite_dir, summary, _ = self.write_suite(
                Path(temporary),
                non_authoritative=disabled,
                schema_version=BLIND_REUSE_ANCHOR_SUITE_SCHEMA_VERSION,
                mode="blind_scope_subset_calibration_only_no_subtitle_mutation",
                include_kind_summary=True,
            )
            evidence, decisions, _ = run_adjudication(
                mode="outer",
                plan=plan,
                backend_runs=runs,
                suite_dir=suite_dir,
            )
        self.assertFalse(summary["boundary_kind_authority_ready"]["start"])
        records = {row["boundary_kind"]: row for row in evidence["records"]}
        self.assertEqual(len(records["start"]["points"]), 0)
        by_kind = {row["boundary_kind"]: row for row in decisions["decisions"]}
        self.assertFalse(by_kind["start"]["automatic_mutation_allowed"])

    def test_internal_kind_without_dual_authority_preserves_editor(self):
        plan = self.internal_plan()
        runs = [
            self.raw_internal_run(plan, self.profiles[0], offset_ms=-20),
            self.raw_internal_run(plan, self.profiles[1], offset_ms=20),
        ]
        disabled = {(self.profiles[1]["profile_id"], "internal")}
        with tempfile.TemporaryDirectory() as temporary:
            suite_dir, summary, _ = self.write_suite(
                Path(temporary),
                non_authoritative=disabled,
                schema_version=BLIND_REUSE_ANCHOR_SUITE_SCHEMA_VERSION,
                mode="blind_scope_subset_calibration_only_no_subtitle_mutation",
                include_kind_summary=True,
            )
            evidence, decisions, _ = run_adjudication(
                mode="internal",
                plan=plan,
                backend_runs=runs,
                suite_dir=suite_dir,
            )
        self.assertFalse(summary["boundary_kind_authority_ready"]["internal"])
        self.assertTrue(all(len(row["points"]) == 0 for row in evidence["records"]))
        self.assertEqual(decisions["summary"]["automatic_split_boundary_count"], 0)
        self.assertTrue(all(not row["automatic_mutation_allowed"] for row in decisions["decisions"]))

    def test_inconsistent_boundary_kind_summary_is_rejected(self):
        plan = self.outer_plan()
        runs = [
            self.raw_run(plan, self.profiles[0], offset_ms=20),
            self.raw_run(plan, self.profiles[1], offset_ms=40),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            suite_dir, summary, _ = self.write_suite(
                Path(temporary),
                schema_version=BLIND_REUSE_ANCHOR_SUITE_SCHEMA_VERSION,
                mode="blind_scope_subset_calibration_only_no_subtitle_mutation",
                include_kind_summary=True,
            )
            tampered = copy.deepcopy(summary)
            tampered["boundary_kind_authority_ready"]["end"] = False
            tampered.pop("suite_sha256")
            tampered["suite_sha256"] = sha256_json(tampered)
            (suite_dir / "suite.summary.json").write_text(
                json.dumps(tampered, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                CalibratedAlignmentAdjudicationError,
                "boundary-kind authority summary is inconsistent",
            ):
                run_adjudication(
                    mode="outer",
                    plan=plan,
                    backend_runs=runs,
                    suite_dir=suite_dir,
                )

    def test_scope_authority_claim_must_match_calibration_artifact(self):
        plan = self.outer_plan()
        runs = [
            self.raw_run(plan, self.profiles[0], offset_ms=20),
            self.raw_run(plan, self.profiles[1], offset_ms=40),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            suite_dir, summary, _ = self.write_suite(Path(temporary))
            tampered = copy.deepcopy(summary)
            tampered["scopes"][0]["production_authoritative"] = False
            tampered.pop("suite_sha256")
            tampered["suite_sha256"] = sha256_json(tampered)
            (suite_dir / "suite.summary.json").write_text(
                json.dumps(tampered, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                CalibratedAlignmentAdjudicationError,
                "scope authority claim differs",
            ):
                run_adjudication(
                    mode="outer",
                    plan=plan,
                    backend_runs=runs,
                    suite_dir=suite_dir,
                )

    def test_tampered_plan_sha_is_rejected_before_binding(self):
        plan = self.outer_plan()
        runs = [
            self.raw_run(plan, self.profiles[0], offset_ms=20),
            self.raw_run(plan, self.profiles[1], offset_ms=40),
        ]
        runs[0]["plan_sha256"] = "f" * 64
        with tempfile.TemporaryDirectory() as temporary:
            suite_dir, _, _ = self.write_suite(Path(temporary))
            with self.assertRaises(CalibratedAlignmentAdjudicationError):
                run_adjudication(mode="outer", plan=plan, backend_runs=runs, suite_dir=suite_dir)

    def test_missing_backend_job_is_rejected(self):
        plan = self.outer_plan()
        runs = [
            self.raw_run(plan, self.profiles[0], offset_ms=20),
            self.raw_run(plan, self.profiles[1], offset_ms=40),
        ]
        runs[1]["jobs"] = runs[1]["jobs"][:-1]
        runs[1]["job_count"] = len(runs[1]["jobs"])
        with tempfile.TemporaryDirectory() as temporary:
            suite_dir, _, _ = self.write_suite(Path(temporary))
            with self.assertRaises(CalibratedAlignmentAdjudicationError):
                run_adjudication(mode="outer", plan=plan, backend_runs=runs, suite_dir=suite_dir)

    def test_replaced_calibration_sha_is_rejected(self):
        plan = self.outer_plan()
        runs = [
            self.raw_run(plan, self.profiles[0], offset_ms=20),
            self.raw_run(plan, self.profiles[1], offset_ms=40),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite_dir, summary, _ = self.write_suite(root)
            tampered = copy.deepcopy(summary)
            tampered["scopes"][0]["calibration_artifact_sha256"] = "f" * 64
            tampered.pop("suite_sha256")
            tampered["suite_sha256"] = sha256_json(tampered)
            (suite_dir / "suite.summary.json").write_text(
                json.dumps(tampered, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            with self.assertRaises(CalibratedAlignmentAdjudicationError):
                run_adjudication(mode="outer", plan=plan, backend_runs=runs, suite_dir=suite_dir)

    def test_unknown_structural_confirmation_is_rejected(self):
        plan = self.outer_plan()
        runs = [
            self.raw_run(plan, self.profiles[0], offset_ms=20),
            self.raw_run(plan, self.profiles[1], offset_ms=40),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            suite_dir, _, _ = self.write_suite(Path(temporary))
            with self.assertRaisesRegex(
                CalibratedAlignmentAdjudicationError,
                "unknown boundary_id",
            ):
                run_adjudication(
                    mode="outer",
                    plan=plan,
                    backend_runs=runs,
                    suite_dir=suite_dir,
                    structural_confirmations=["not-a-plan-boundary"],
                )

    def test_internal_structural_confirmation_is_rejected(self):
        plan = self.internal_plan()
        runs = [
            self.raw_internal_run(plan, self.profiles[0], offset_ms=-20),
            self.raw_internal_run(plan, self.profiles[1], offset_ms=20),
        ]
        boundary_id = str(plan["jobs"][0]["boundary_id"])
        with tempfile.TemporaryDirectory() as temporary:
            suite_dir, _, _ = self.write_suite(Path(temporary))
            with self.assertRaisesRegex(
                CalibratedAlignmentAdjudicationError,
                "internal adjudication does not accept structural confirmations",
            ):
                run_adjudication(
                    mode="internal",
                    plan=plan,
                    backend_runs=runs,
                    suite_dir=suite_dir,
                    structural_confirmations=[boundary_id],
                )


if __name__ == "__main__":
    unittest.main()
