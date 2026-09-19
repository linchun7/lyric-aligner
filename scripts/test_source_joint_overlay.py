"""Unit tests for the experimental atomic adjacent-pair overlay producer."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lyric_aligner.alignment.source_joint_context import SOURCE_JOINT_CONTEXT_POLICY_ID
from lyric_aligner.contracts.artifacts import sha256_file
from lyric_aligner.srt import Cue
from lyric_aligner.timeline.boundary_sequence_optimizer import IntervalCandidate, IntervalNode
from scripts import source_joint_overlay as overlay


class _Config:
    def __init__(self, dictionary_path: Path):
        self.dictionary_path = dictionary_path


def _candidate(candidate_id, start, end, *, baseline=False):
    return IntervalCandidate(
        candidate_id=candidate_id,
        start_ms=start,
        end_ms=end,
        lane_id="occ-main",
        occurrence_path_id="path-main",
        map_path_id="map-main",
        is_baseline=baseline,
        selection_cost=1.0 if baseline else 0.0,
    )


class SourceJointOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.dictionary = self.root / "dict.txt"
        self.dictionary.write_text("alpha\tAH\nbravo\tB R\n", encoding="utf-8")
        self.staging = self.root / "staging"
        self.cues = [Cue(1, 100, 200, "alpha"), Cue(2, 300, 400, "bravo")]
        self.rows = [
            {"start_ms": "100", "end_ms": "200", "text": "alpha"},
            {"start_ms": "300", "end_ms": "400", "text": "bravo"},
        ]
        self.contexts = {
            "occ-main": {
                "prepared": {"occurrence_id": "occ-main", "fixture": "full-prepared"},
                "mapping": {"kind": "AFFINE", "intercept": 0.0, "base_slope": 1.0},
                "primary_interval": [0.0, 1.0],
                "mapping_checks": [],
                "path_id": "path-main",
                "map_path_id": "map-main",
                "cue_specs": [
                    {"position": 1, "canonical_character_ranges": [{"canonical_line_index": 1, "start_char": 0, "end_char": 5}]},
                    {"position": 2, "canonical_character_ranges": [{"canonical_line_index": 2, "start_char": 0, "end_char": 5}]},
                ],
            }
        }

    def tearDown(self):
        self.temp.cleanup()

    def _nodes(self, *, right_hfa=True):
        left = IntervalNode(
            node_id="1",
            continuity_group_id="occ-main",
            candidates=(
                _candidate("1:KEEP", 100, 200, baseline=True),
                _candidate("1:HFA:left", 250, 350),
            ),
        )
        right_candidates = [_candidate("2:KEEP", 300, 400, baseline=True)]
        if right_hfa:
            right_candidates.append(_candidate("2:HFA:right", 250, 350))
        right = IntervalNode(
            node_id="2",
            continuity_group_id="occ-main",
            candidates=tuple(right_candidates),
        )
        return [left, right]

    @staticmethod
    def _prepared_result(*pairs):
        records = []
        pair_results = []
        for number, pair in enumerate(pairs, 1):
            record_id = f"joint-{number}"
            records.append({
                "record_id": record_id,
                "joint_proposal_policy_id": SOURCE_JOINT_CONTEXT_POLICY_ID,
                "context_policy": "anchored-path-v1",
                "target_outputs": [
                    {"position": pair[0], "target_segment_index": 1},
                    {"position": pair[1], "target_segment_index": 2},
                ],
            })
            pair_results.append({"pair": list(pair), "status": "ready", "record_id": record_id})
        return {
            "joint_proposal_policy_id": SOURCE_JOINT_CONTEXT_POLICY_ID,
            "records": records,
            "pair_results": pair_results,
        }

    def _fake_execute(self, _config, records, *, work_dir):
        work_dir.mkdir(parents=True, exist_ok=True)
        artifacts = {}
        for name in ("request", "response"):
            path = work_dir / f"{name}.json"
            path.write_text(name, encoding="utf-8")
            artifacts[name] = {"path": str(path), "sha256": sha256_file(path)}
        return {
            "response": {"records": [
                {"record_id": str(record["record_id"]), "status": "aligned", "words": [
                    {"text": "alpha", "start": 0.1, "end": 0.2},
                    {"text": "bravo", "start": 0.3, "end": 0.4},
                ]}
                for record in records
            ]},
            "artifacts": artifacts,
        }

    @staticmethod
    def _fake_finalize(records, _responses, *, effective_mapping):
        del effective_mapping
        record = records[0]
        index = int(record["target_segment_index"])
        interval = [210, 280] if index == 1 else [300, 370]
        return {str(record["record_id"]): {
            "record_id": str(record["record_id"]),
            "status": "observed_complete_interval",
            "projected_mix_interval_ms": interval,
            "words_sha256": "same-response-words",
            "reason": "",
        }}

    def _run(self, nodes, *, finalize=None, right_hfa=True):
        del right_hfa
        with patch.object(overlay, "prepare_joint_pairs", side_effect=lambda prepared, pairs, cue_specs, dictionary_path:
                          self._prepared_result(*pairs)), \
             patch.object(overlay, "execute_batch", side_effect=self._fake_execute), \
             patch.object(overlay, "finalize_records", side_effect=finalize or self._fake_finalize):
            return overlay.run_joint_overlay(
                _Config(self.dictionary), self.contexts, nodes, self.rows, self.cues, self.staging
            )

    def test_conflict_triggers_fresh_pair_and_selects_both_atomically(self):
        result = self._run(self._nodes())
        self.assertEqual(result["conflict_pair_count"], 1)
        self.assertEqual(result["prepared_record_count"], 1)
        self.assertEqual(result["accepted_atomic_pair_count"], 1)
        self.assertEqual(result["selected_joint_candidate_count"], 2)
        self.assertEqual(result["pair_outcomes"][0]["status"], "conflict_triggered")
        selected = result["selection"]["selected_candidate_ids"]
        self.assertTrue(all(":JOINT:joint-1:" in value for value in selected))
        self.assertTrue((self.staging / "joint_overlay.csv").is_file())
        self.assertTrue((self.staging / "joint_overlay.srt").is_file())
        self.assertFalse(result["publish_ready"])
        self.assertIn("alpha", (self.staging / "joint_overlay.srt").read_text(encoding="utf-8"))
        self.assertIn("bravo", (self.staging / "joint_overlay.srt").read_text(encoding="utf-8"))

    def test_incomplete_second_target_rejects_pair_without_half_adoption(self):
        def missing_second(records, _responses, *, effective_mapping):
            del effective_mapping
            record = records[0]
            if int(record["target_segment_index"]) == 2:
                return {str(record["record_id"]): {
                    "record_id": str(record["record_id"]),
                    "status": "unavailable",
                    "projected_mix_interval_ms": None,
                    "reason": "fixture_missing_target",
                }}
            return self._fake_finalize(records, _responses, effective_mapping={})

        result = self._run(self._nodes(), finalize=missing_second)
        self.assertEqual(result["accepted_atomic_pair_count"], 0)
        self.assertEqual(result["selected_joint_candidate_count"], 0)
        self.assertEqual(result["record_evaluations"][0]["status"], "rejected")
        self.assertEqual(result["record_evaluations"][0]["reason"], "joint_target_incomplete")
        self.assertTrue(all(":JOINT:" not in value for value in result["selection"]["selected_candidate_ids"]))

    def test_right_without_hfa_compares_keep_and_triggers_same_gate(self):
        result = self._run(self._nodes(right_hfa=False))
        self.assertEqual(result["conflict_pair_count"], 1)
        self.assertEqual(result["accepted_atomic_pair_count"], 1)
        self.assertEqual(result["selected_joint_candidate_count"], 2)

    def test_ambiguous_existing_mapping_check_rejects_both_targets(self):
        contexts = {key: {**value, "mapping_checks": [
            {"status": "available", "ambiguous": True, "top1": {"mix_start": 0.0, "mix_end": 1.0}}
        ]}
                    for key, value in self.contexts.items()}
        with patch.object(overlay, "prepare_joint_pairs", side_effect=lambda prepared, pairs, cue_specs, dictionary_path:
                          self._prepared_result(*pairs)), \
             patch.object(overlay, "execute_batch", side_effect=self._fake_execute), \
             patch.object(overlay, "finalize_records", side_effect=self._fake_finalize):
            result = overlay.run_joint_overlay(
                _Config(self.dictionary), contexts, self._nodes(), self.rows, self.cues, self.staging
            )
        self.assertEqual(result["accepted_atomic_pair_count"], 0)
        self.assertEqual(result["record_evaluations"][0]["reason"], "existing_mapping_check_ambiguous")

    def test_empty_contexts_emit_overlay_without_fresh_records(self):
        result = overlay.run_joint_overlay(
            _Config(self.dictionary), {}, self._nodes(), self.rows, self.cues, self.staging
        )
        self.assertEqual(result["prepared_record_count"], 0)
        self.assertEqual(result["accepted_atomic_pair_count"], 0)
        self.assertEqual(result["batch"], {})
        self.assertTrue(all(":JOINT:" not in value for value in result["selection"]["selected_candidate_ids"]))


if __name__ == "__main__":
    unittest.main()
