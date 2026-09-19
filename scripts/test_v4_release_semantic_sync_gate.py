from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from semantic_sync_test_support import write_passing_semantic_sync_fixture
from task_contract import build_task_manifest, sha256, write_json_atomic
from v4_validate_release import _validate_semantic_sync_qa


class ReleaseSemanticSyncGateTests(unittest.TestCase):
    def build_fixture(self, root: Path) -> dict:
        task_root = root / "private" / "semantic-release-test"
        input_dir = task_root / "input"
        qa_dir = task_root / "qa"
        lyrics_dir = input_dir / "lyrics"
        for directory in (input_dir, qa_dir, lyrics_dir):
            directory.mkdir(parents=True, exist_ok=True)
        source_srt = input_dir / "source.srt"
        source_srt.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nalpha lyric\n",
            encoding="utf-8",
        )
        audio = input_dir / "mix.wav"
        audio.write_bytes(b"synthetic-audio")
        songs = input_dir / "songs.txt"
        songs.write_text("00:00 Artist - Track\n", encoding="utf-8")
        (lyrics_dir / "track.lrc").write_text("[00:01.00]alpha lyric\n", encoding="utf-8")
        manifest = build_task_manifest(
            root,
            "semantic-release-test",
            source_srt=source_srt,
            audio=audio,
            song_list=songs,
            lyrics_dir=lyrics_dir,
        )
        manifest_path = qa_dir / "task_manifest.json"
        write_json_atomic(manifest_path, manifest)
        fingerprint = str(manifest["task_fingerprint_sha256"])
        final_srt = root / "FINAL.srt"
        final_srt.write_text(source_srt.read_text(encoding="utf-8"), encoding="utf-8")
        final_report = root / "FINAL.audit.csv"
        final_report.write_text(
            "position,start_ms,end_ms,text,task_fingerprint_sha256\n"
            f"1,1000,2000,alpha lyric,{fingerprint}\n",
            encoding="utf-8-sig",
        )
        run_path, fusion_path, semantic_qa = write_passing_semantic_sync_fixture(
            manifest_path=manifest_path,
            final_srt=final_srt,
            final_report=final_report,
            out_dir=root / "semantic",
        )
        return {
            "manifest": manifest,
            "manifest_path": manifest_path,
            "final_srt": final_srt,
            "final_report": final_report,
            "run_path": run_path,
            "fusion_path": fusion_path,
            "semantic_qa": semantic_qa,
        }

    def validate(
        self,
        fixture: dict,
        *,
        source_clock_map: Path | None = None,
        promotion_analysis: Path | None = None,
        promotion_selection: Path | None = None,
        promotion_protocol: Path | None = None,
    ):
        return _validate_semantic_sync_qa(
            fixture["semantic_qa"],
            run_path=fixture["run_path"],
            final_srt=fixture["final_srt"],
            manifest_path=fixture["manifest_path"],
            manifest=fixture["manifest"],
            algorithm_version=__version__,
            fusion_path=fixture["fusion_path"],
            final_report=fixture["final_report"],
            source_clock_map_path=source_clock_map,
            source_clock_promotion_analysis_path=promotion_analysis,
            source_clock_promotion_selection_path=promotion_selection,
            source_clock_promotion_protocol_path=promotion_protocol,
        )

    def test_exact_passed_semantic_qa_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.build_fixture(Path(temporary))
            self.assertTrue(self.validate(fixture)["passed"])

    def test_failed_semantic_qa_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.build_fixture(Path(temporary))
            payload = json.loads(fixture["semantic_qa"].read_text(encoding="utf-8"))
            payload["passed"] = False
            write_json_atomic(fixture["semantic_qa"], payload)
            with self.assertRaisesRegex(ValueError, "semantic sync QA did not pass"):
                self.validate(fixture)

    def test_weakened_semantic_thresholds_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.build_fixture(Path(temporary))
            payload = json.loads(fixture["semantic_qa"].read_text(encoding="utf-8"))
            payload["projection_sync"]["thresholds"]["max_median_abs_error_ms"] = 12000
            write_json_atomic(fixture["semantic_qa"], payload)
            with self.assertRaisesRegex(ValueError, "thresholds differ from release policy"):
                self.validate(fixture)

    def test_editor_only_evidence_basis_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.build_fixture(Path(temporary))
            payload = json.loads(fixture["semantic_qa"].read_text(encoding="utf-8"))
            payload["projection_sync"]["tracks"][0]["evidence_basis"] = "editor_only"
            write_json_atomic(fixture["semantic_qa"], payload)
            with self.assertRaisesRegex(ValueError, "contains a failed track"):
                self.validate(fixture)

    def test_non_auxiliary_editor_authority_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.build_fixture(Path(temporary))
            payload = json.loads(fixture["semantic_qa"].read_text(encoding="utf-8"))
            payload["editor_witness"]["authority"] = "timing_authority"
            write_json_atomic(fixture["semantic_qa"], payload)
            with self.assertRaisesRegex(ValueError, "auxiliary_only"):
                self.validate(fixture)

    def test_final_srt_changed_after_qa_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.build_fixture(Path(temporary))
            fixture["final_srt"].write_text(
                "1\n00:00:09,000 --> 00:00:10,000\nalpha lyric\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "final_srt_sha256"):
                self.validate(fixture)

    def test_run_changed_after_qa_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.build_fixture(Path(temporary))
            fixture["run_path"].write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "run_sha256"):
                self.validate(fixture)

    def test_fusion_changed_after_qa_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.build_fixture(Path(temporary))
            fixture["fusion_path"].write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fusion_sha256"):
                self.validate(fixture)

    def test_report_changed_after_qa_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.build_fixture(Path(temporary))
            fixture["final_report"].write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "final_report_sha256"):
                self.validate(fixture)


    def source_clock_fixture(self, fixture: dict, root: Path) -> tuple[Path, Path, Path, Path]:
        source_clock_map = root / "source_clock_map.json"
        promotion = root / "promotion.json"
        selection = root / "selection.json"
        protocol = root / "protocol.json"
        write_json_atomic(source_clock_map, {"test": "source-clock-map"})
        canonical_selection_sha = "c" * 64
        fingerprint = str(fixture["manifest"]["task_fingerprint_sha256"])
        write_json_atomic(
            protocol,
            {
                "schema_version": "source-clock-final-mix-promotion-protocol-2.0",
                "purpose": "fresh final-mix holdout promotion for BPM-fixed source-clock candidates",
                "valid_final_mix_anchor": {
                    "canonical_start_covered": True,
                    "canonical_match_ambiguous": False,
                    "minimum_canonical_match_support_score": 0.85,
                    "minimum_mean_word_probability": 0.70,
                },
                "minimum_valid_final_mix_anchors": 3,
                "new_projection_thresholds_ms": {
                    "maximum_median_abs_error": 750,
                    "maximum_p90_abs_error": 900,
                    "maximum_worst_abs_error": 1200,
                },
                "risk_score": "median_abs_error + 0.5*p90_abs_error + 0.25*worst_abs_error",
                "minimum_risk_score_improvement_ms": 300,
                "human_check_if_present": {
                    "maximum_new_abs_error_ms": 1000,
                    "must_not_regress_vs_old_projection": True,
                    "human_result_never_changes_rate_or_offset": True,
                },
            },
        )
        protocol_sha = sha256(protocol)
        write_json_atomic(
            selection,
            {
                "schema_version": "source-clock-final-mix-promotion-v2-selection-lock-1.0",
                "purpose": "fresh final-mix holdout selection excluding previously observed canonical identities",
                "expanded_candidate_sha256": "d" * 64,
                "promotion_protocol_sha256": protocol_sha,
                "final_mix_asr_result_read": False,
                "human_result_used_for_selection": False,
                "candidate_count": 1,
                "tracks": [{
                    "ordinal": 1,
                    "occurrence_id": "occ-1",
                    "track_id": "synthetic-track",
                    "source_audio_sha256": "e" * 64,
                    "canonical_lyric_sha256": "f" * 64,
                    "canonical_selection_sha256": canonical_selection_sha,
                    "source_clock": {"policy_id": "bpm-fixed-source-clock-1.0", "rate": 1.0, "offset_ms": 0.0, "provenance_sha256": "d" * 64},
                    "testable": True,
                    "selected_probe_count": 3,
                    "probes": [
                        {"canonical_line_index": 0, "old_projection_start_ms": 0, "new_projection_start_ms": 1000},
                        {"canonical_line_index": 1, "old_projection_start_ms": 1000, "new_projection_start_ms": 2000},
                        {"canonical_line_index": 2, "old_projection_start_ms": 2000, "new_projection_start_ms": 3000},
                    ],
                }],
            },
        )
        selection_sha = sha256(selection)
        write_json_atomic(
            promotion,
            {
                "schema_version": "source-clock-final-mix-promotion-v2-analysis-1.0",
                "purpose": "fresh unseen final-mix holdout verdict for source-clock production promotion",
                "selection_lock_sha256": selection_sha,
                "promotion_protocol_sha256": protocol_sha,
                "human_used_for_clock_estimation": False,
                "human_used_only_as_final_nonregression_check": True,
                "promoted_count": 1,
                "promoted_ordinals": [1],
                "tracks": [
                    {
                        "ordinal": 1,
                        "testable": True,
                        "promoted": True,
                        "metric_pass": True,
                        "risk_pass": True,
                        "human_pass": True,
                        "valid_final_mix_anchor_count": 3,
                        "source_clock": {
                            "policy_id": "bpm-fixed-source-clock-1.0",
                            "rate": 1.0,
                            "offset_ms": 0.0,
                            "provenance_sha256": "d" * 64,
                        },
                        "new_metrics": {
                            "count": 3,
                            "median_abs_error_ms": 10.0,
                            "p90_abs_error_ms": 18.0,
                            "worst_abs_error_ms": 20.0,
                        },
                        "old_metrics": {
                            "count": 3,
                            "median_abs_error_ms": 1005.0,
                            "p90_abs_error_ms": 1017.0,
                            "worst_abs_error_ms": 1020.0,
                        },
                        "old_risk_score": 1768.5,
                        "new_risk_score": 24.0,
                        "risk_improvement_ms": 1744.5,
                        "ledger": [
                            {"canonical_line_index": 0, "valid": True, "asr_start_ms": 1005, "old_projection_start_ms": 0, "new_projection_start_ms": 1000, "support": 1.0, "mean_word_probability": 0.9, "ambiguous": False, "old_signed_error_ms": -1005, "new_signed_error_ms": -5},
                            {"canonical_line_index": 1, "valid": True, "asr_start_ms": 1990, "old_projection_start_ms": 1000, "new_projection_start_ms": 2000, "support": 1.0, "mean_word_probability": 0.9, "ambiguous": False, "old_signed_error_ms": -990, "new_signed_error_ms": 10},
                            {"canonical_line_index": 2, "valid": True, "asr_start_ms": 3020, "old_projection_start_ms": 2000, "new_projection_start_ms": 3000, "support": 1.0, "mean_word_probability": 0.9, "ambiguous": False, "old_signed_error_ms": -1020, "new_signed_error_ms": -20},
                        ],
                    }
                ],
            },
        )
        promotion_sha = sha256(promotion)
        write_json_atomic(
            source_clock_map,
            {
                "schema_version": "source-clock-map-1.0",
                "policy_id": "bpm-fixed-source-clock-1.0",
                "task_fingerprint_sha256": fingerprint,
                "algorithm_version": __version__,
                "automatic_timing_change_allowed": True,
                "final_mix_promotion_selection_sha256": selection_sha,
                "final_mix_promotion_protocol_sha256": protocol_sha,
                "final_mix_promotion_analysis_sha256": promotion_sha,
                "tracks": [
                    {
                        "ordinal": 1,
                        "occurrence_id": "occ-1",
                        "track_id": "synthetic-track",
                        "source_audio_sha256": "e" * 64,
                        "canonical_lyric_sha256": "f" * 64,
                        "canonical_selection_sha256": canonical_selection_sha,
                        "policy_id": "bpm-fixed-source-clock-1.0",
                        "rate": 1.0,
                        "offset_ms": 0.0,
                        "provenance_sha256": promotion_sha,
                    }
                ],
            },
        )
        timeline = root / "timeline.json"
        write_json_atomic(
            timeline,
            {
                "task_fingerprint_sha256": fingerprint,
                "algorithm_version": __version__,
                "source_clock_map_sha256": sha256(source_clock_map),
                "occurrence_id": "occ-1",
                "track_id": "synthetic-track",
                "result": {"canonical_selection_sha256": canonical_selection_sha},
            },
        )
        write_json_atomic(
            fixture["run_path"],
            {
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "occurrences": [
                    {
                        "ordinal": 1,
                        "occurrence_id": "occ-1",
                        "timeline_path": str(timeline),
                        "source_clock": {
                            "policy_id": "bpm-fixed-source-clock-1.0",
                            "rate": 1.0,
                            "offset_ms": 0.0,
                            "provenance_sha256": promotion_sha,
                        },
                    }
                ],
            },
        )
        payload = json.loads(fixture["semantic_qa"].read_text(encoding="utf-8"))
        payload["schema_version"] = "semantic-sync-qa-1.2"
        payload["audio_evidence_policy"] = (
            "verified_source_clock_or_forced_alignment_or_asr_plus_reliable_editor_v2"
        )
        payload["bindings"]["run_sha256"] = sha256(fixture["run_path"])
        payload["bindings"]["source_clock_map_sha256"] = sha256(source_clock_map)
        payload["bindings"]["source_clock_promotion_analysis_sha256"] = sha256(promotion)
        payload["bindings"]["source_clock_promotion_selection_sha256"] = sha256(selection)
        payload["bindings"]["source_clock_promotion_protocol_sha256"] = sha256(protocol)
        payload["verified_source_clock_authority"] = {
            "track_count": 1,
            "ordinals": [1],
            "policy_ids": ["verified-source-clock-final-mix-holdout-1.1"],
        }
        for key in ("projection_sync", "final_sync"):
            row = payload[key]["tracks"][0]
            row.update(
                evidence_basis="verified_source_clock_final_mix_holdout",
                verified_source_clock_authority=True,
                authority_policy_id="verified-source-clock-final-mix-holdout-1.1",
                authority_timeline_map_binding="exact_sha",
                authority_holdout_anchor_count=3,
            )
        write_json_atomic(fixture["semantic_qa"], payload)
        return source_clock_map, promotion, selection, protocol

    def test_source_clock_v2_semantic_contract_requires_exact_external_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(root)
            source_clock_map, promotion, selection, protocol = self.source_clock_fixture(fixture, root)
            self.assertTrue(
                self.validate(
                    fixture,
                    source_clock_map=source_clock_map,
                    promotion_analysis=promotion,
                    promotion_selection=selection,
                    promotion_protocol=protocol,
                )["passed"]
            )

    def test_source_clock_v2_semantic_contract_rejects_missing_external_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(root)
            self.source_clock_fixture(fixture, root)
            with self.assertRaisesRegex(ValueError, "requires source clock map"):
                self.validate(fixture)

    def test_source_clock_v2_semantic_contract_rejects_missing_frozen_selection_or_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(root)
            source_clock_map, promotion, _, _ = self.source_clock_fixture(fixture, root)
            with self.assertRaisesRegex(ValueError, "promotion selection and promotion protocol"):
                self.validate(
                    fixture,
                    source_clock_map=source_clock_map,
                    promotion_analysis=promotion,
                )

    def test_source_clock_v2_semantic_contract_rejects_fake_basis_without_authority_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(root)
            source_clock_map, promotion, selection, protocol = self.source_clock_fixture(fixture, root)
            payload = json.loads(fixture["semantic_qa"].read_text(encoding="utf-8"))
            payload.pop("verified_source_clock_authority")
            write_json_atomic(fixture["semantic_qa"], payload)
            with self.assertRaisesRegex(ValueError, "lacks authority metadata"):
                self.validate(
                    fixture,
                    source_clock_map=source_clock_map,
                    promotion_analysis=promotion,
                    promotion_selection=selection,
                    promotion_protocol=protocol,
                )

    def test_source_clock_v2_semantic_contract_rejects_false_timeline_binding_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(root)
            source_clock_map, promotion, selection, protocol = self.source_clock_fixture(fixture, root)
            payload = json.loads(fixture["semantic_qa"].read_text(encoding="utf-8"))
            for key in ("projection_sync", "final_sync"):
                payload[key]["tracks"][0]["authority_timeline_map_binding"] = (
                    "legacy_overlap_numeric_replay"
                )
            write_json_atomic(fixture["semantic_qa"], payload)
            with self.assertRaisesRegex(ValueError, "timeline-map binding mismatch"):
                self.validate(
                    fixture,
                    source_clock_map=source_clock_map,
                    promotion_analysis=promotion,
                    promotion_selection=selection,
                    promotion_protocol=protocol,
                )


if __name__ == "__main__":
    unittest.main()
