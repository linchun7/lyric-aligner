from __future__ import annotations

import hashlib
import json
import tempfile
import sys
from pathlib import Path
from unittest.mock import patch
import unittest

from lyric_aligner.alignment.source_asr_acoustic_projection import (
    SOURCE_ASR_ACOUSTIC_POLICY_ID,
    SourceAsrAcousticProjectionError,
    bind_source_asr_acoustic_results,
    build_source_asr_acoustic_plan,
    evaluate_source_asr_acoustic_shadow,
    exact_source_onsets,
)


SOURCE_SHA = "a" * 64
MIX_SHA = "b" * 64
FINGERPRINT = "c" * 64
OBS_KEY = "d" * 64


def text_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def observation(words, *, source_sha: str = SOURCE_SHA):
    rows = []
    cursor = 1000
    for word in words:
        if word is None:
            rows.append({"start_ms": cursor, "end_ms": cursor + 100, "text": "###", "probability": 0.99})
        else:
            rows.append({"start_ms": cursor, "end_ms": cursor + 300, "text": word, "probability": 0.95})
        cursor += 400
    return {
        "status": "observed",
        "audio_basis": "source",
        "source_audio_sha256": source_sha,
        "cache_key_sha256": OBS_KEY,
        "search_domain": {"start_ms": 0, "end_ms": 20_000},
        "words": rows,
    }


class ExactSourceOnsetTests(unittest.TestCase):
    def test_unique_complete_source_word_run_is_accepted(self):
        lines = [
            {"canonical_line_index": 0, "text": "alpha beta gamma delta epsilon"},
            {"canonical_line_index": 1, "text": "unique middle lyric words here"},
        ]
        obs = observation(
            ["alpha", "beta", "gamma", "delta", "epsilon", "other", "words", "go", "right", "here"]
        )
        result = exact_source_onsets(
            canonical_lines=lines,
            selected_line_indices=[0],
            source_observation=obs,
            expected_source_audio_sha256=SOURCE_SHA,
        )[0]
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["source_start_ms"], 1000)
        self.assertEqual(result["lexical_unit_count"], 3)
        self.assertEqual(result["match_mode"], "unique_line_start_prefix")

    def test_unique_line_start_prefix_survives_later_asr_word_error(self):
        lines = [
            {"canonical_line_index": 0, "text": "alpha bravo charlie delta epsilon foxtrot"},
            {"canonical_line_index": 1, "text": "other distinct words live over here"},
        ]
        result = exact_source_onsets(
            canonical_lines=lines,
            selected_line_indices=[0],
            source_observation=observation(
                ["alpha", "bravo", "charlie", "WRONG", "epsilon", "foxtrot"]
            ),
            expected_source_audio_sha256=SOURCE_SHA,
        )[0]
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["source_start_ms"], 1000)
        self.assertEqual(result["match_mode"], "unique_line_start_prefix")
        self.assertEqual(result["canonical_prefix_lexical_unit_count"], 3)
        self.assertGreaterEqual(result["prefix_signal_characters"], 12)

    def test_three_word_unique_line_can_supply_onset_when_signal_is_long_enough(self):
        text = "midnight electric heartbeat"
        result = exact_source_onsets(
            canonical_lines=[{"canonical_line_index": 0, "text": text}],
            selected_line_indices=[0],
            source_observation=observation(["midnight", "electric", "heartbeat"]),
            expected_source_audio_sha256=SOURCE_SHA,
        )[0]
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["lexical_unit_count"], 3)
        self.assertEqual(result["match_mode"], "unique_full_line")

    def test_short_three_word_line_below_signal_floor_is_rejected(self):
        result = exact_source_onsets(
            canonical_lines=[{"canonical_line_index": 0, "text": "we go now"}],
            selected_line_indices=[0],
            source_observation=observation(["we", "go", "now"]),
            expected_source_audio_sha256=SOURCE_SHA,
        )[0]
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "canonical_line_below_minimum_prefix_evidence")

    def test_repeated_canonical_sequence_is_rejected_even_if_source_asr_has_one_copy(self):
        lines = [
            {"canonical_line_index": 0, "text": "alpha beta gamma delta epsilon"},
            {"canonical_line_index": 1, "text": "alpha beta gamma delta epsilon"},
        ]
        result = exact_source_onsets(
            canonical_lines=lines,
            selected_line_indices=[0],
            source_observation=observation(["alpha", "beta", "gamma", "delta", "epsilon"]),
            expected_source_audio_sha256=SOURCE_SHA,
        )[0]
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "canonical_sequence_not_unique_in_full_canonical")

    def test_repeated_source_observation_is_rejected(self):
        lines = [{"canonical_line_index": 0, "text": "alpha beta gamma delta epsilon"}]
        run = ["alpha", "beta", "gamma", "delta", "epsilon"]
        result = exact_source_onsets(
            canonical_lines=lines,
            selected_line_indices=[0],
            source_observation=observation(run + ["other"] + run),
            expected_source_audio_sha256=SOURCE_SHA,
        )[0]
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "observed_sequence_not_unique_in_full_source_observation")

    def test_low_probability_word_blocks_source_onset(self):
        obs = observation(["alpha", "beta", "gamma", "delta", "epsilon"])
        obs["words"][2]["probability"] = 0.69
        result = exact_source_onsets(
            canonical_lines=[{"canonical_line_index": 0, "text": "alpha beta gamma delta epsilon"}],
            selected_line_indices=[0],
            source_observation=obs,
            expected_source_audio_sha256=SOURCE_SHA,
        )[0]
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "observed_word_timing_or_probability_below_threshold")

    def test_source_audio_hash_mismatch_fails_closed(self):
        with self.assertRaisesRegex(SourceAsrAcousticProjectionError, "source observation/source audio SHA mismatch"):
            exact_source_onsets(
                canonical_lines=[{"canonical_line_index": 0, "text": "alpha beta gamma delta epsilon"}],
                selected_line_indices=[0],
                source_observation=observation(["alpha", "beta", "gamma", "delta", "epsilon"], source_sha="e" * 64),
                expected_source_audio_sha256=SOURCE_SHA,
            )


class SourceAsrAcousticPlanTests(unittest.TestCase):
    def fixture(self):
        text = "alpha beta gamma delta epsilon"
        selection_job_id = "f" * 64
        selection = {
            "task_fingerprint_sha256": FINGERPRINT,
            "policy_id": "targeted-semantic-failed-expansion-v1",
            "selected_ordinals": [1],
            "selected_job_count": 1,
            "selection": {
                "1": [
                    {
                        "job_id": selection_job_id,
                        "ordinal": 1,
                        "occurrence_id": "occ-1",
                        "canonical_line_index": 0,
                        "canonical_text_sha256": text_sha(text),
                    }
                ]
            },
        }
        alignment_plan = {
            "jobs": [
                {
                    "job_id": selection_job_id,
                    "ordinal": 1,
                    "occurrence_id": "occ-1",
                    "canonical_line_index": 0,
                    "canonical_text_sha256": text_sha(text),
                    "mix_window_ms": [9000, 15000],
                    "region_id": "r1",
                    "region_mix_window_ms": [8500, 15500],
                }
            ]
        }
        assets = {
            "assets": [
                {
                    "track_id": "track-1",
                    "source_audio_sha256": SOURCE_SHA,
                }
            ],
            "occurrences": [
                {
                    "ordinal": 1,
                    "occurrence_id": "occ-1",
                    "track_id": "track-1",
                }
            ],
        }
        timeline = {
            "result": {
                "lines": [
                    {"canonical_line_index": 0, "text": text},
                    {"canonical_line_index": 1, "text": "other unique words in this line"},
                ]
            }
        }
        obs = observation(["alpha", "beta", "gamma", "delta", "epsilon", "tail"])
        canonical = [{"timestamp_ms": 1000 + index * 2000, "alternative_index": 0,
                      "text": row["text"]} for index, row in enumerate(timeline["result"]["lines"])]
        canonical_sha = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                                                  separators=(",", ":")).encode()).hexdigest()
        assets["assets"][0]["canonical_selection_sha256"] = canonical_sha
        alignment_plan["jobs"][0].update(track_id="track-1", canonical_selection_sha256=canonical_sha)
        assets["resolution"] = [{"track_id": "track-1", "canonical_selection": canonical}]
        timeline.update(task_fingerprint_sha256=FINGERPRINT, occurrence_id="occ-1", track_id="track-1")
        timeline["result"]["canonical_selection_sha256"] = canonical_sha
        return selection, alignment_plan, assets, timeline, obs

    def test_pruned_timeline_cannot_change_global_uniqueness(self):
        selection, alignment, assets, timeline, obs = self.fixture()
        canonical = assets["resolution"][0]["canonical_selection"]
        canonical[1]["text"] = canonical[0]["text"]
        digest = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
        assets["assets"][0]["canonical_selection_sha256"] = digest
        alignment["jobs"][0]["canonical_selection_sha256"] = digest
        timeline["result"]["canonical_selection_sha256"] = digest
        timeline["result"]["lines"] = timeline["result"]["lines"][:1]
        plan = build_source_asr_acoustic_plan(
            task_fingerprint_sha256=FINGERPRINT, selection_lock=selection,
            alignment_plan=alignment, track_assets=assets, timelines_by_ordinal={1: timeline},
            source_observations_by_ordinal={1: obs}, mix_audio_sha256=MIX_SHA)
        self.assertEqual(plan["accepted_source_onset_count"], 0)

    def test_duplicate_binder_ids_are_rejected(self):
        selection, alignment, assets, timeline, obs = self.fixture()
        plan = build_source_asr_acoustic_plan(
            task_fingerprint_sha256=FINGERPRINT, selection_lock=selection,
            alignment_plan=alignment, track_assets=assets, timelines_by_ordinal={1: timeline},
            source_observations_by_ordinal={1: obs}, mix_audio_sha256=MIX_SHA)
        row = {"job_id": plan["jobs"][0]["job_id"], "predicted_mix_start_ms": 10250}
        for jobs, results in [(plan["jobs"] * 2, [row]), (plan["jobs"], [row, row])]:
            with self.assertRaisesRegex(SourceAsrAcousticProjectionError, "job identity mismatch"):
                bind_source_asr_acoustic_results(plan={**plan, "jobs": jobs},
                                                acoustic_result={"jobs": results})

    def test_duplicate_selected_ordinals_fail_closed(self):
        selection, alignment, assets, timeline, obs = self.fixture()
        selection["selected_ordinals"] = [1, 1]
        with self.assertRaisesRegex(SourceAsrAcousticProjectionError, "duplicate selected ordinal"):
            build_source_asr_acoustic_plan(
                task_fingerprint_sha256=FINGERPRINT, selection_lock=selection,
                alignment_plan=alignment, track_assets=assets, timelines_by_ordinal={1: timeline},
                source_observations_by_ordinal={1: obs}, mix_audio_sha256=MIX_SHA)

    def test_artifact_cannot_overwrite_reused_cache_entry(self):
        with patch.object(sys, "path", [str(Path(__file__).resolve().parent), *sys.path]):
            from scripts.v4_source_asr_acoustic_projection import _validate_cache_artifact_separation
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.mkdir()
            _validate_cache_artifact_separation(cache, {"plan": root / "plan.json"})
            with self.assertRaises(ValueError):
                _validate_cache_artifact_separation(cache, {"plan": cache / "entry.source.json"})
            with self.assertRaises(ValueError):
                _validate_cache_artifact_separation(root / "future.json" / "cache",
                                                     {"plan": root / "future.json"})

    def test_plan_uses_source_asr_onset_not_editor_or_source_clock(self):
        selection, alignment, assets, timeline, obs = self.fixture()
        plan = build_source_asr_acoustic_plan(
            task_fingerprint_sha256=FINGERPRINT,
            selection_lock=selection,
            alignment_plan=alignment,
            track_assets=assets,
            timelines_by_ordinal={1: timeline},
            source_observations_by_ordinal={1: obs},
            mix_audio_sha256=MIX_SHA,
        )
        self.assertEqual(plan["accepted_source_onset_count"], 1)
        job = plan["jobs"][0]
        self.assertEqual(job["expected_source_time_ms"], 1000)
        self.assertIsNone(job["rate_prior"])
        self.assertNotIn("editor_cue_start_ms", job)
        self.assertFalse(plan["constants"]["editor_timing_used_for_prediction"])
        self.assertFalse(plan["constants"]["source_clock_used_for_predicted_onset"])
        self.assertIn("source_asr_acoustic_projection", job["requested_capabilities"])

    def test_frozen_identity_mismatch_fails_closed(self):
        selection, alignment, assets, timeline, obs = self.fixture()
        alignment["jobs"][0]["canonical_line_index"] = 1
        with self.assertRaisesRegex(SourceAsrAcousticProjectionError, "frozen targeted identity differs"):
            build_source_asr_acoustic_plan(
                task_fingerprint_sha256=FINGERPRINT,
                selection_lock=selection,
                alignment_plan=alignment,
                track_assets=assets,
                timelines_by_ordinal={1: timeline},
                source_observations_by_ordinal={1: obs},
                mix_audio_sha256=MIX_SHA,
            )

    def test_source_window_is_clamped_to_observed_media_extent(self):
        selection, alignment, assets, timeline, obs = self.fixture()
        for index, row in enumerate(obs["words"]):
            row["start_ms"] = 16000 + index * 400
            row["end_ms"] = row["start_ms"] + 300
        obs["search_domain"]["end_ms"] = 19_000
        plan = build_source_asr_acoustic_plan(
            task_fingerprint_sha256=FINGERPRINT,
            selection_lock=selection,
            alignment_plan=alignment,
            track_assets=assets,
            timelines_by_ordinal={1: timeline},
            source_observations_by_ordinal={1: obs},
            mix_audio_sha256=MIX_SHA,
        )
        self.assertEqual(plan["jobs"][0]["source_window_ms"][1], 19_000)

    def test_binding_rejects_missing_or_extra_acoustic_result(self):
        selection, alignment, assets, timeline, obs = self.fixture()
        plan = build_source_asr_acoustic_plan(
            task_fingerprint_sha256=FINGERPRINT,
            selection_lock=selection,
            alignment_plan=alignment,
            track_assets=assets,
            timelines_by_ordinal={1: timeline},
            source_observations_by_ordinal={1: obs},
            mix_audio_sha256=MIX_SHA,
        )
        with self.assertRaisesRegex(SourceAsrAcousticProjectionError, "job identity mismatch"):
            bind_source_asr_acoustic_results(plan=plan, acoustic_result={"jobs": []})

    def test_binding_keeps_shadow_authority_and_acoustic_health(self):
        selection, alignment, assets, timeline, obs = self.fixture()
        plan = build_source_asr_acoustic_plan(
            task_fingerprint_sha256=FINGERPRINT,
            selection_lock=selection,
            alignment_plan=alignment,
            track_assets=assets,
            timelines_by_ordinal={1: timeline},
            source_observations_by_ordinal={1: obs},
            mix_audio_sha256=MIX_SHA,
        )
        job = plan["jobs"][0]
        result = bind_source_asr_acoustic_results(
            plan=plan,
            acoustic_result={
                "jobs": [
                    {
                        "job_id": job["job_id"],
                        "predicted_mix_start_ms": 10_250,
                        "mix_window_ms": job["mix_window_ms"],
                        "projection_within_mix_window": True,
                        "projection_extrapolation_ms": 0,
                        "estimated_slope": 1.0,
                        "fused_score": 0.9,
                        "margin": 0.05,
                        "feature_agreement": 3,
                        "local_match_gate_passed": True,
                        "ambiguous": False,
                        "slope_search_boundary_hit": False,
                        "source_search_boundary_hit": False,
                        "timing_fusion_evidence_eligible": True,
                    }
                ]
            },
        )
        self.assertEqual(result["policy_id"], SOURCE_ASR_ACOUSTIC_POLICY_ID)
        self.assertEqual(result["eligible_job_count"], 1)
        self.assertFalse(result["automatic_timing_change_allowed"])
        self.assertEqual(result["jobs"][0]["authority"], "shadow_audio_evidence_only")

    def test_v1_shadow_contract_remains_readable(self):
        evidence = bind_source_asr_acoustic_results(
            plan={
                "schema_version": "source-asr-acoustic-projection-1.0",
                "policy_id": "source-asr-acoustic-projection-2026-09-10-v1",
                "task_fingerprint_sha256": FINGERPRINT,
                "jobs": [],
            },
            acoustic_result={"jobs": []},
        )
        self.assertEqual(evidence["schema_version"], "source-asr-acoustic-projection-1.0")
        self.assertEqual(evidence["policy_id"], "source-asr-acoustic-projection-2026-09-10-v1")
        evaluation = evaluate_source_asr_acoustic_shadow(
            evidence=evidence,
            fusion={"lines": []},
            final_rows=[],
        )
        self.assertEqual(evaluation["eligible_job_count"], 0)

    def test_shadow_binding_and_evaluation_reject_unqualified_projection(self):
        selection, alignment, assets, timeline, obs = self.fixture()
        plan = build_source_asr_acoustic_plan(
            task_fingerprint_sha256=FINGERPRINT, selection_lock=selection,
            alignment_plan=alignment, track_assets=assets,
            timelines_by_ordinal={1: timeline},
            source_observations_by_ordinal={1: obs}, mix_audio_sha256=MIX_SHA,
        )
        row = {"job_id": plan["jobs"][0]["job_id"],
               "local_match_gate_passed": True, "ambiguous": False, "feature_agreement": 2,
               "slope_search_boundary_hit": False, "source_search_boundary_hit": False,
               "mix_window_ms": plan["jobs"][0]["mix_window_ms"],
               "predicted_mix_start_ms": 10250,
               "timing_fusion_evidence_eligible": True,
               "projection_within_mix_window": True,
               "projection_extrapolation_ms": 0}
        self.assertEqual(bind_source_asr_acoustic_results(
            plan=plan, acoustic_result={"jobs": [row]})["eligible_job_count"], 1)
        for field, value in [("projection_within_mix_window", None),
                             ("projection_within_mix_window", False),
                             ("projection_extrapolation_ms", 1720),
                             ("projection_extrapolation_ms", None),
                             ("local_match_gate_passed", False),
                             ("slope_search_boundary_hit", True),
                             ("source_search_boundary_hit", True),
                             ("ambiguous", True), ("feature_agreement", 1)]:
            with self.subTest(field=field, value=value):
                evidence = bind_source_asr_acoustic_results(
                    plan=plan, acoustic_result={"jobs": [{**row, field: value}]})
                self.assertEqual(evidence["eligible_job_count"], 0)
                # Even a stale stored grant cannot bypass evaluation.
                evidence["jobs"][0]["timing_fusion_evidence_eligible"] = True
                self.assertEqual(evaluate_source_asr_acoustic_shadow(
                    evidence=evidence, fusion={"lines": []}, final_rows=[]
                )["eligible_job_count"], 0)
        for changes in ({"predicted_mix_start_ms": 864690, "mix_window_ms": [866410, 872858]},
                        {"mix_window_ms": [0, 900000]}):
            self.assertEqual(bind_source_asr_acoustic_results(
                plan=plan, acoustic_result={"jobs": [{**row, **changes}]}
            )["eligible_job_count"], 0)

    def test_shadow_evaluation_is_descriptive_and_joins_same_canonical_identity(self):
        selection, alignment, assets, timeline, obs = self.fixture()
        plan = build_source_asr_acoustic_plan(
            task_fingerprint_sha256=FINGERPRINT,
            selection_lock=selection,
            alignment_plan=alignment,
            track_assets=assets,
            timelines_by_ordinal={1: timeline},
            source_observations_by_ordinal={1: obs},
            mix_audio_sha256=MIX_SHA,
        )
        job = plan["jobs"][0]
        evidence = bind_source_asr_acoustic_results(
            plan=plan,
            acoustic_result={
                "jobs": [
                    {
                        "job_id": job["job_id"],
                        "predicted_mix_start_ms": 10_250,
                        "mix_window_ms": job["mix_window_ms"],
                        "projection_within_mix_window": True,
                        "projection_extrapolation_ms": 0,
                        "estimated_slope": 1.0,
                        "fused_score": 0.9,
                        "margin": 0.05,
                        "feature_agreement": 3,
                        "local_match_gate_passed": True,
                        "ambiguous": False,
                        "slope_search_boundary_hit": False,
                        "source_search_boundary_hit": False,
                        "timing_fusion_evidence_eligible": True,
                    }
                ]
            },
        )
        fusion = {
            "lines": [
                {
                    "occurrence_id": "occ-1",
                    "canonical_line_index": 0,
                    "families": [
                        {
                            "family": "asr",
                            "canonical_onset": {
                                "start_ms": 10_400,
                                "support_score": 0.95,
                                "basis": "canonical_prefix_word_span",
                                "canonical_start_covered": True,
                                "canonical_match_ambiguous": False,
                            },
                        }
                    ],
                }
            ]
        }
        result = evaluate_source_asr_acoustic_shadow(
            evidence=evidence,
            fusion=fusion,
            final_rows=[
                {"occurrence_id": "occ-1", "canonical_line_index": "", "start_ms": ""},
                {"occurrence_id": "occ-1", "canonical_line_index": 0, "start_ms": 10_500},
            ],
        )
        self.assertEqual(result["authority"], "descriptive_only_no_threshold_selection")
        self.assertFalse(result["automatic_timing_change_allowed"])
        self.assertEqual(result["eligible_job_count"], 1)
        self.assertEqual(result["rows"][0]["source_minus_final_asr_ms"], -150)
        self.assertEqual(result["rows"][0]["final_srt_minus_source_ms"], 250)
        self.assertEqual(result["tracks"][0]["source_final_asr_agreement_bins"]["250"], 1)


if __name__ == "__main__":
    unittest.main()
