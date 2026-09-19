import copy
import unittest
from unittest.mock import patch

import lyric_aligner.evaluation.selective_consensus_calibration as selective_module
from lyric_aligner.evaluation.selective_consensus_calibration import (
    INTERNAL_SELECTIVE_CONSENSUS_PRIOR_DEFINITION,
    SelectiveConsensusCalibrationError,
    build_internal_selective_consensus_calibration,
    frozen_selector_evidence,
    internal_selective_consensus_calibration_is_authoritative,
    select_nearest_lexical_prior_prediction,
    sha256_json,
)
from lyric_aligner.text.alignment_lexical import ALIGNMENT_LEXICAL_ID, ALIGNMENT_LEXICAL_REVISION
from scripts.test_support_production_calibration import (
    human_anchor_gold_artifact,
    production_anchor_calibration,
)


FINAL_AUDIO_SHA = "b" * 64
SELECTION_LOCK_SHA = "1" * 64


class JointLexicalSelectorCalibrationTests(unittest.TestCase):
    def human_gold(self):
        return human_anchor_gold_artifact(
            language_scope="en",
            final_audio_sha256=FINAL_AUDIO_SHA,
            selection_lock_sha256=SELECTION_LOCK_SHA,
        )

    def suite(self, human_gold):
        payload = {
            "schema_version": "fixture-suite",
            "selection_lock_sha256": SELECTION_LOCK_SHA,
            "human_gold_artifact_sha256": human_gold["artifact_sha256"],
            "final_audio_sha256": FINAL_AUDIO_SHA,
            "subtitle_mutation_performed": False,
        }
        payload["suite_sha256"] = sha256_json(payload)
        return payload

    def source(self, human_gold, *, profile, backend, family, group, preferred, overrides=None):
        internal = [row for row in human_gold["records"] if row["boundary_kind"] == "internal"]
        overrides = dict(overrides or {})
        predictions = {}
        for index, row in enumerate(internal):
            gold = int(row["gold_ms"])
            if row["id"] in overrides:
                predictions[row["id"]] = int(overrides[row["id"]])
            elif preferred == "even":
                predictions[row["id"]] = gold + (30 if index % 2 == 0 else 1000)
            else:
                predictions[row["id"]] = gold + (1000 if index % 2 == 0 else -30)
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
            model_revision="2" * 64,
            implementation_revision="3" * 64,
            adapter_contract_revision="4" * 64,
            lexical_id=ALIGNMENT_LEXICAL_ID,
            lexical_revision=ALIGNMENT_LEXICAL_REVISION,
            predictions_ms=predictions,
        )

    def sources(self, human_gold, *, overrides=None):
        return [
            self.source(
                human_gold,
                profile="profile-sofa",
                backend="sofa",
                family="final_mix_singing_alignment",
                group="sofa-group",
                preferred="even",
                overrides=overrides,
            ),
            self.source(
                human_gold,
                profile="profile-hubert",
                backend="hubert",
                family="final_mix_forced_alignment",
                group="hubert-group",
                preferred="odd",
                overrides=overrides,
            ),
        ]

    def blind_scope(self, human_gold, source):
        scope = source["scope"]
        profile_id = str(scope["backend_profile_id"])
        predictions = {str(row["id"]): row.get("predicted_ms") for row in source["records"]}
        for index in range(18):
            predictions[f"blind-extra-{profile_id}-{index}:internal"] = 500_000 + index * 1000
        artifact = {
            "schema_version": "subtitle-machine-consensus-scope-1.2-full-adapter-contract",
            "selection_lock_sha256": human_gold["source_full_selection_lock_sha256"],
            "selection_lock_file_sha256": "7" * 64,
            "final_audio_sha256": FINAL_AUDIO_SHA,
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

    def prepared_sources(self, human_gold, *, overrides=None, sources=None):
        sources = copy.deepcopy(sources) if sources is not None else self.sources(human_gold, overrides=overrides)
        blinds = [self.blind_scope(human_gold, source) for source in sources]
        blind_by_profile = {str(row["profile_id"]): row for row in blinds}
        for source in sources:
            profile_id = str(source["scope"]["backend_profile_id"])
            blind_sha = str(blind_by_profile[profile_id]["artifact_sha256"])
            source["prediction_provenance"] = {
                "schema_version": "human-boundary-backend-predictions-1.1",
                "prediction_artifact_sha256": "8" * 64,
                "predictions_sha256": "9" * 64,
                "selection_lock_sha256": human_gold["selection_lock_sha256"],
                "production_provenance_complete": True,
                "source_blind_scope_artifact_sha256": blind_sha,
                "reuse_mode": "deterministic_subset_of_full_blind_pre_model_scope_no_model_rerun",
            }
            source["artifact_sha256"] = sha256_json(
                {key: value for key, value in source.items() if key != "artifact_sha256"}
            )
        mapping = {str(row["profile_id"]): str(row["artifact_sha256"]) for row in blinds}
        return sources, blinds, mapping

    def authoritative(self, artifact, *, sources=None, expected_final_audio_sha256=FINAL_AUDIO_SHA, expected_source_suite_sha256=None):
        mapping = dict(
            (artifact.get("selector_freeze_evidence") or {}).get(
                "source_scope_artifact_sha256"
            )
            or {}
        )
        with patch.object(
            selective_module,
            "INTERNAL_SELECTIVE_CONSENSUS_FROZEN_SOURCE_SCOPE_SHA256",
            mapping,
        ):
            return internal_selective_consensus_calibration_is_authoritative(
                artifact,
                expected_final_audio_sha256=expected_final_audio_sha256,
                expected_source_suite_sha256=expected_source_suite_sha256,
                source_calibrations=sources,
            )

    def priors(self, human_gold):
        return {
            row["id"]: int(row["gold_ms"])
            for row in human_gold["records"]
            if row["boundary_kind"] == "internal"
        }

    def provenance(self):
        return {
            "selection_lock_sha256": SELECTION_LOCK_SHA,
            "lexical_id": ALIGNMENT_LEXICAL_ID,
            "lexical_revision": ALIGNMENT_LEXICAL_REVISION,
            "prior_definition": INTERNAL_SELECTIVE_CONSENSUS_PRIOR_DEFINITION,
            "selector_frozen_before_final_replacement_gold": True,
        }

    def build(self, *, human_gold=None, sources=None, priors=None, provenance=None):
        human_gold = human_gold or self.human_gold()
        sources, blinds, mapping = self.prepared_sources(human_gold, sources=sources)
        priors = priors or self.priors(human_gold)
        provenance = provenance or self.provenance()
        with patch.object(
            selective_module,
            "INTERNAL_SELECTIVE_CONSENSUS_FROZEN_SOURCE_SCOPE_SHA256",
            mapping,
        ):
            return build_internal_selective_consensus_calibration(
                suite_summary=self.suite(human_gold),
                human_gold_artifact=human_gold,
                source_calibrations=sources,
                source_blind_scope_artifacts=blinds,
                selector_priors_ms=priors,
                selector_prior_provenance=provenance,
            )

    def test_frozen_selector_passes_existing_anchor_policy(self):
        human_gold = self.human_gold()
        suite = self.suite(human_gold)
        artifact = self.build(human_gold=human_gold)
        sources = list(artifact["source_calibration_artifacts"].values())
        self.assertTrue(artifact["passed"], artifact)
        self.assertEqual(artifact["summary"]["calibration"]["prediction_coverage"], 1.0)
        self.assertEqual(artifact["summary"]["holdout"]["prediction_coverage"], 1.0)
        self.assertEqual(
            artifact["selector_freeze_evidence"]["source_scope_artifact_sha256"],
            {
                profile_id: raw["artifact_sha256"]
                for profile_id, raw in artifact["source_blind_scope_artifacts"].items()
            },
        )
        self.assertTrue(
            self.authoritative(
                artifact,
                expected_source_suite_sha256=suite["suite_sha256"],
                sources=sources,
            )
        )
        self.assertFalse(any(source["passed"] for source in sources))

    def test_selector_is_nearest_prior_not_median_or_spread_gate(self):
        selected, profile, reason = select_nearest_lexical_prior_prediction(
            {"a": 1000, "b": 2500}, lexical_prior_ms=2450
        )
        self.assertEqual(selected, 2500)
        self.assertEqual(profile, "b")
        self.assertEqual(reason, "choose_direct_aligner_nearest_lexical_ratio_prior")

    def test_exact_distance_tie_uses_median(self):
        selected, profile, reason = select_nearest_lexical_prior_prediction(
            {"a": 900, "b": 1100}, lexical_prior_ms=1000
        )
        self.assertEqual(selected, 1000)
        self.assertEqual(profile, "tie_median")
        self.assertEqual(reason, "equal_distance_to_lexical_ratio_prior_use_median")

    def test_missing_one_aligner_is_no_prediction(self):
        selected, profile, reason = select_nearest_lexical_prior_prediction(
            {"a": 1000, "b": None}, lexical_prior_ms=1000
        )
        self.assertIsNone(selected)
        self.assertIsNone(profile)
        self.assertEqual(reason, "requires_two_direct_aligners")

    def test_freeze_evidence_tamper_fails_authority(self):
        artifact = self.build()
        artifact["selector_freeze_evidence"]["plan_sha256"] = "0" * 64
        artifact["artifact_sha256"] = sha256_json(
            {key: value for key, value in artifact.items() if key != "artifact_sha256"}
        )
        self.assertFalse(self.authoritative(artifact))

    def test_posthoc_prior_tamper_fails_authority(self):
        artifact = self.build()
        artifact["records"][0]["lexical_ratio_prior_ms"] += 1
        self.assertFalse(self.authoritative(artifact))

    def test_frozen_blind_prediction_tamper_fails_standalone_authority(self):
        artifact = self.build()
        profile_id = sorted(artifact["source_blind_scope_artifacts"])[0]
        blind = artifact["source_blind_scope_artifacts"][profile_id]
        record_id = next(iter(blind["predictions"]))
        blind["predictions"][record_id] = int(blind["predictions"][record_id]) + 1
        blind["artifact_sha256"] = sha256_json(
            {key: value for key, value in blind.items() if key != "artifact_sha256"}
        )
        artifact["artifact_sha256"] = sha256_json(
            {key: value for key, value in artifact.items() if key != "artifact_sha256"}
        )
        self.assertFalse(self.authoritative(artifact))

    def test_source_prediction_tamper_fails_bound_authority(self):
        human_gold = self.human_gold()
        sources = self.sources(human_gold)
        artifact = self.build(human_gold=human_gold, sources=sources)
        tampered = copy.deepcopy(sources)
        tampered[0]["records"][0]["predicted_ms"] += 1
        tampered[0]["artifact_sha256"] = sha256_json(
            {key: value for key, value in tampered[0].items() if key != "artifact_sha256"}
        )
        self.assertFalse(self.authoritative(artifact, sources=tampered))

    def test_synthetic_source_without_human_gold_provenance_cannot_enter(self):
        human_gold = self.human_gold()
        sources = self.sources(human_gold)
        synthetic = copy.deepcopy(sources)
        synthetic[0].pop("human_gold_provenance", None)
        synthetic[0]["artifact_sha256"] = sha256_json(
            {key: value for key, value in synthetic[0].items() if key != "artifact_sha256"}
        )
        with self.assertRaises((SelectiveConsensusCalibrationError, ValueError)):
            self.build(human_gold=human_gold, sources=synthetic)

    def test_selector_prior_must_bind_same_anchor_lock_as_suite(self):
        human_gold = self.human_gold()
        provenance = self.provenance()
        provenance["selection_lock_sha256"] = "9" * 64
        with self.assertRaises(SelectiveConsensusCalibrationError):
            self.build(
                human_gold=human_gold,
                sources=self.sources(human_gold),
                provenance=provenance,
            )

    def test_non_independent_backend_ids_fail_closed(self):
        human_gold = self.human_gold()
        sources = self.sources(human_gold)
        internal = [row for row in human_gold["records"] if row["boundary_kind"] == "internal"]
        duplicate = production_anchor_calibration(
            human_gold=human_gold,
            backend_id=sources[0]["backend_id"],
            family=sources[1]["family"],
            correlation_group=sources[1]["correlation_group"],
            language="en",
            backend_version="1",
            backend_profile_id="profile-hubert",
            window_policy_id="fixture-window",
            model_id="profile-hubert-model",
            model_revision="2" * 64,
            implementation_revision="3" * 64,
            adapter_contract_revision="4" * 64,
            lexical_id=ALIGNMENT_LEXICAL_ID,
            lexical_revision=ALIGNMENT_LEXICAL_REVISION,
            predictions_ms={row["id"]: int(row["gold_ms"]) - 30 for row in internal},
        )
        with self.assertRaises(SelectiveConsensusCalibrationError):
            self.build(human_gold=human_gold, sources=[sources[0], duplicate])

    def test_joint_selector_catastrophic_holdout_fails_existing_policy(self):
        human_gold = self.human_gold()
        internal = [row for row in human_gold["records"] if row["boundary_kind"] == "internal"]
        target = internal[-1]
        bad_value = int(target["gold_ms"]) + 1000
        sources = self.sources(human_gold, overrides={target["id"]: bad_value})
        priors = self.priors(human_gold)
        priors[target["id"]] = bad_value
        artifact = self.build(human_gold=human_gold, sources=sources, priors=priors)
        self.assertFalse(artifact["passed"])
        self.assertIn("joint_selector_holdout_partition_exceeds_anchor_policy", artifact["reasons"])

    def test_wrong_final_audio_identity_fails_authority(self):
        artifact = self.build()
        self.assertFalse(
            self.authoritative(artifact, expected_final_audio_sha256="f" * 64)
        )


if __name__ == "__main__":
    unittest.main()
