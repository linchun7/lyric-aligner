import hashlib
import json
import unittest
from unittest.mock import patch

import lyric_aligner.evaluation.selective_consensus_calibration as selective_module
from lyric_aligner.evaluation.selective_consensus_calibration import (
    INTERNAL_SELECTIVE_CONSENSUS_PRIOR_DEFINITION,
    build_internal_selective_consensus_calibration,
    sha256_json,
)
from lyric_aligner.text.alignment_lexical import (
    ALIGNMENT_LEXICAL_ID,
    ALIGNMENT_LEXICAL_REVISION,
    segment_units_from_canonical_segments,
)
from lyric_aligner.timeline.internal_segmentation import (
    InternalSegmentationError,
    adjudicate_internal_segmentation,
    apply_internal_segmentation_to_rows,
    build_internal_segmentation_plan,
    build_internal_selective_consensus_evidence,
)
from scripts.test_support_production_calibration import (
    human_anchor_gold_artifact,
    production_anchor_calibration,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SELECTION_LOCK_SHA = "e" * 64


class InternalJointLexicalSelectorTests(unittest.TestCase):
    def segments(self):
        return [
            {"track_index": 1, "lrc_index": 10, "text": "first line", "projected_ms": 10050},
            {"track_index": 1, "lrc_index": 11, "text": "second line", "projected_ms": 13000},
            {"track_index": 1, "lrc_index": 12, "text": "third line", "projected_ms": 16000},
        ]

    def report_rows(self):
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
                "evidence": "canonical+split_suppressed_no_audio_boundary_authority",
                "track": "fixture",
                "lrc_indices": "10;11;12",
                "canonical_segments_json": json.dumps(
                    segments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
            }
        ]

    def plan(self):
        return build_internal_segmentation_plan(
            task_fingerprint_sha256=SHA_A,
            source_srt_sha256=SHA_B,
            final_audio_sha256=SHA_C,
            report_sha256=SHA_D,
            source_cues=[
                {"number": 7, "start_ms": 10000, "end_ms": 19000, "text": "editor recognition"}
            ],
            report_rows=self.report_rows(),
        )

    def human_gold(self):
        return human_anchor_gold_artifact(
            language_scope="en",
            final_audio_sha256=SHA_C,
            selection_lock_sha256=SELECTION_LOCK_SHA,
        )

    def source_calibration(self, human_gold, *, profile, backend, family, group, preferred):
        internal = [row for row in human_gold["records"] if row["boundary_kind"] == "internal"]
        predictions = {}
        for index, row in enumerate(internal):
            gold = int(row["gold_ms"])
            if preferred == "even":
                predictions[row["id"]] = gold + (25 if index % 2 == 0 else 900)
            else:
                predictions[row["id"]] = gold + (900 if index % 2 == 0 else -25)
        return production_anchor_calibration(
            human_gold=human_gold,
            backend_id=backend,
            family=family,
            correlation_group=group,
            language="en",
            backend_version="1",
            backend_profile_id=profile,
            window_policy_id="fixture-window",
            model_id=profile + "-model",
            model_revision="1" * 64,
            implementation_revision="2" * 64,
            adapter_contract_revision="3" * 64,
            lexical_id=ALIGNMENT_LEXICAL_ID,
            lexical_revision=ALIGNMENT_LEXICAL_REVISION,
            predictions_ms=predictions,
        )

    def blind_scope(self, human_gold, source):
        scope = source["scope"]
        profile_id = str(scope["backend_profile_id"])
        predictions = {str(row["id"]): row.get("predicted_ms") for row in source["records"]}
        for index in range(18):
            predictions[f"blind-extra-{profile_id}-{index}:internal"] = 600_000 + index * 1000
        artifact = {
            "schema_version": "subtitle-machine-consensus-scope-1.2-full-adapter-contract",
            "selection_lock_sha256": human_gold["source_full_selection_lock_sha256"],
            "selection_lock_file_sha256": "7" * 64,
            "final_audio_sha256": SHA_C,
            "profile_id": profile_id,
            "backend_id": source["backend_id"],
            "backend_version": scope["backend_version"],
            "family": source["family"],
            "correlation_group": source["correlation_group"],
            "model_id": scope["model_id"],
            "model_revision": scope["model_revision"],
            "implementation_revision": scope["implementation_revision"],
            "adapter_contract_revision": scope["adapter_contract_revision"],
            "window_policy_id": scope["window_policy_id"],
            "boundary_kind": "internal",
            "record_count": 30,
            "nonnull_prediction_count": sum(value is not None for value in predictions.values()),
            "predictions": predictions,
        }
        artifact["artifact_sha256"] = sha256_json(artifact)
        return artifact

    def prepared_sources(self, human_gold):
        sources = [
            self.source_calibration(
                human_gold,
                profile="fixture-sofa",
                backend="sofa-backend",
                family="final_mix_singing_alignment",
                group="sofa-group",
                preferred="even",
            ),
            self.source_calibration(
                human_gold,
                profile="fixture-hubert",
                backend="hubert-backend",
                family="final_mix_forced_alignment",
                group="hubert-group",
                preferred="odd",
            ),
        ]
        blinds = [self.blind_scope(human_gold, source) for source in sources]
        blind_by_profile = {str(row["profile_id"]): row for row in blinds}
        for source in sources:
            profile_id = str(source["scope"]["backend_profile_id"])
            source["prediction_provenance"] = {
                "schema_version": "human-boundary-backend-predictions-1.1",
                "prediction_artifact_sha256": "8" * 64,
                "predictions_sha256": "9" * 64,
                "selection_lock_sha256": human_gold["selection_lock_sha256"],
                "production_provenance_complete": True,
                "source_blind_scope_artifact_sha256": blind_by_profile[profile_id]["artifact_sha256"],
                "reuse_mode": "deterministic_subset_of_full_blind_pre_model_scope_no_model_rerun",
            }
            source["artifact_sha256"] = sha256_json(
                {key: value for key, value in source.items() if key != "artifact_sha256"}
            )
        return sources, blinds

    def setUp(self):
        human_gold = self.human_gold()
        _sources, blinds = self.prepared_sources(human_gold)
        mapping = {str(row["profile_id"]): str(row["artifact_sha256"]) for row in blinds}
        self._freeze_patch = patch.object(
            selective_module,
            "INTERNAL_SELECTIVE_CONSENSUS_FROZEN_SOURCE_SCOPE_SHA256",
            mapping,
        )
        self._freeze_patch.start()
        self.addCleanup(self._freeze_patch.stop)

    def joint(self):
        human_gold = self.human_gold()
        sources, blinds = self.prepared_sources(human_gold)
        suite = {
            "schema_version": "fixture-suite",
            "selection_lock_sha256": SELECTION_LOCK_SHA,
            "human_gold_artifact_sha256": human_gold["artifact_sha256"],
            "final_audio_sha256": SHA_C,
            "subtitle_mutation_performed": False,
        }
        suite["suite_sha256"] = sha256_json(suite)
        priors = {
            row["id"]: int(row["gold_ms"])
            for row in human_gold["records"]
            if row["boundary_kind"] == "internal"
        }
        artifact = build_internal_selective_consensus_calibration(
            suite_summary=suite,
            human_gold_artifact=human_gold,
            source_calibrations=sources,
            source_blind_scope_artifacts=blinds,
            selector_priors_ms=priors,
            selector_prior_provenance={
                "selection_lock_sha256": SELECTION_LOCK_SHA,
                "lexical_id": ALIGNMENT_LEXICAL_ID,
                "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
                "prior_definition": INTERNAL_SELECTIVE_CONSENSUS_PRIOR_DEFINITION,
                "selector_frozen_before_final_replacement_gold": True,
            },
        )
        self.assertTrue(artifact["passed"], artifact)
        return artifact, sources

    def lexical_sha(self, job):
        units = segment_units_from_canonical_segments(job["canonical_segments"], language="en")
        return hashlib.sha256(
            json.dumps(units, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def raw_point(self, job, source, boundary_ms):
        return {
            "family": source["family"],
            "correlation_group": source["correlation_group"],
            "boundary_ms": boundary_ms,
            "uncertainty_ms": 5,
            "backend_id": source["backend_id"],
            "backend_version": source["backend_version"],
            "backend_profile_id": source["backend_profile_id"],
            "model_id": source["model_id"],
            "model_revision": source["model_revision"],
            "implementation_revision": source["implementation_revision"],
            "adapter_contract_revision": source["adapter_contract_revision"],
            "lexical_id": source["lexical_id"],
            "lexical_revision": source["lexical_revision"],
            "lexical_units_sha256": self.lexical_sha(job),
            "language": source["language"],
            "audio_basis": "final_mix",
            "window_policy_id": source["window_policy_id"],
            "evidence_sha256": "8" * 64,
            "calibration_passed": False,
            "calibration_sha256": "",
            "raw_confidence": 0.5,
        }

    def evidence(self, pairs):
        plan = self.plan()
        joint, source_calibrations = self.joint()
        source_map = {row["backend_profile_id"]: row for row in joint["backend_sources"]}
        jobs = sorted(plan["jobs"], key=lambda row: row["boundary_index"])
        by_id = {}
        for job, pair in zip(jobs, pairs):
            by_id[job["boundary_id"]] = [
                self.raw_point(job, source_map["fixture-sofa"], pair[0]),
                self.raw_point(job, source_map["fixture-hubert"], pair[1]),
            ]
        evidence = build_internal_selective_consensus_evidence(
            plan=plan,
            evidence_by_boundary_id=by_id,
            selective_consensus_calibration=joint,
            source_calibrations=source_calibrations,
        )
        return plan, evidence

    def test_runtime_recomputes_prior_and_selects_nearest_backend_even_with_large_spread(self):
        # Runtime lexical priors are 13000 and 16000. Spreads deliberately exceed
        # the old 180ms experimental gate; authority is the pre-frozen selector.
        plan, evidence = self.evidence([(11800, 13030), (16030, 17500)])
        decisions = adjudicate_internal_segmentation(plan=plan, evidence=evidence)
        self.assertEqual(decisions["summary"]["automatic_split_boundary_count"], 2)
        self.assertEqual(
            decisions["summary"]["authority_mode"], "human_gold_joint_lexical_selector_v1"
        )
        by_index = sorted(decisions["decisions"], key=lambda row: row["boundary_index"])
        self.assertEqual(by_index[0]["lexical_ratio_prior_ms"], 13000)
        self.assertEqual(by_index[0]["selected_ms"], 13030)
        self.assertEqual(by_index[0]["selected_backend_profile_id"], "fixture-hubert")
        self.assertEqual(by_index[1]["lexical_ratio_prior_ms"], 16000)
        self.assertEqual(by_index[1]["selected_ms"], 16030)
        self.assertEqual(by_index[1]["selected_backend_profile_id"], "fixture-sofa")

        output = apply_internal_segmentation_to_rows(
            report_rows=self.report_rows(), plan=plan, evidence=evidence, decisions=decisions
        )
        self.assertEqual([row["start_ms"] for row in output], [10000, 13030, 16030])
        self.assertTrue(
            all(
                row["segmentation_authority"]
                == "audio_verified_internal_joint_lexical_selector_v1"
                for row in output
            )
        )

    def test_one_backend_never_authorizes_complete_joint_split(self):
        plan, evidence = self.evidence([(12980, 13020), (15980, 16020)])
        evidence["records"][0]["points"] = evidence["records"][0]["points"][:1]
        decisions = adjudicate_internal_segmentation(plan=plan, evidence=evidence)
        self.assertEqual(decisions["summary"]["automatic_split_boundary_count"], 1)
        output = apply_internal_segmentation_to_rows(
            report_rows=self.report_rows(), plan=plan, evidence=evidence, decisions=decisions
        )
        self.assertEqual(len(output), 1)

    def test_selector_candidate_too_close_to_edge_keeps_cue_unsplit(self):
        plan, evidence = self.evidence([(10100, 10200), (15980, 16020)])
        decisions = adjudicate_internal_segmentation(plan=plan, evidence=evidence)
        first = sorted(decisions["decisions"], key=lambda row: row["boundary_index"])[0]
        self.assertFalse(first["automatic_mutation_allowed"])
        self.assertIn("edge-short", first["reason"])

    def test_tampered_joint_calibration_fails_closed(self):
        plan, evidence = self.evidence([(12980, 13020), (15980, 16020)])
        evidence["selective_consensus_calibration_artifact"]["records"][0][
            "lexical_ratio_prior_ms"
        ] += 1
        with self.assertRaises(InternalSegmentationError):
            adjudicate_internal_segmentation(plan=plan, evidence=evidence)

    def test_raw_point_cannot_fake_individual_calibration(self):
        plan = self.plan()
        joint, source_calibrations = self.joint()
        source_map = {row["backend_profile_id"]: row for row in joint["backend_sources"]}
        job = plan["jobs"][0]
        point = self.raw_point(job, source_map["fixture-sofa"], 13000)
        point["calibration_passed"] = True
        point["calibration_sha256"] = "7" * 64
        with self.assertRaises(InternalSegmentationError):
            build_internal_selective_consensus_evidence(
                plan=plan,
                evidence_by_boundary_id={job["boundary_id"]: [point]},
                selective_consensus_calibration=joint,
                source_calibrations=source_calibrations,
            )

    def test_runtime_plan_tamper_after_evidence_fails_plan_binding(self):
        plan, evidence = self.evidence([(12980, 13020), (15980, 16020)])
        plan["jobs"][0]["canonical_segments"][0]["text"] = "changed text"
        with self.assertRaises(InternalSegmentationError):
            adjudicate_internal_segmentation(plan=plan, evidence=evidence)


if __name__ == "__main__":
    unittest.main()
