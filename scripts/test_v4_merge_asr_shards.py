from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import build_artifact_manifest, atomic_write_json
from v4_merge_asr_shards import merge_asr_shards


class MergeAsrShardsTests(unittest.TestCase):
    def _fixture(self, root: Path):
        fingerprint = "1" * 64
        run_id = "run-artifact"
        plan_id = "plan-artifact"
        lock = root / "lock.json"
        lock.write_text(json.dumps({
            "schema_version": "semantic-asr-selection-lock-1.0",
            "asr_result_read": False,
            "selected_job_count": 4,
            "selected_job_ids": ["j1", "j2", "j3", "j4"],
            "plan_sha256": "2" * 64,
        }), encoding="utf-8")
        payload_paths = []
        artifact_paths = []
        for index, ids in enumerate((["j1", "j2"], ["j3", "j4"]), start=1):
            payload = root / f"s{index}.json"
            artifact = root / f"s{index}.artifact.json"
            body = {
                "schema_version": "1.0",
                "backend": "faster_whisper",
                "execution_strategy": "disjoint_window_batches_v2",
                "word_match_policy_id": "word",
                "language_hint_policy_id": "lang",
                "config": {
                    "model_id": "model",
                    "device": "cpu",
                    "compute_type": "int8",
                    "beam_size": 5,
                    "temperature": 0.0,
                    "include_private_text": False,
                },
                "model_loaded": True,
                "job_count": len(ids),
                "jobs": [
                    {
                        "job_id": job_id,
                        "occurrence_id": "occ",
                        "canonical_line_index": pos,
                        "mix_window_ms": [pos * 1000, pos * 1000 + 500],
                    }
                    for pos, job_id in enumerate(ids)
                ],
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "source_plan_artifact_id": plan_id,
                "source_run_artifact_id": run_id,
                "mix_audio_sha256": "3" * 64,
                "selected_job_ids": ids,
                "privacy": "raw ASR text omitted unless include_private_text=true",
            }
            atomic_write_json(payload, body)
            art = build_artifact_manifest(
                task_fingerprint_sha256=fingerprint,
                stage="asr_evidence_local",
                algorithm_version=__version__,
                outputs=(("asr_evidence", payload),),
                normalized_config={"source_run_artifact_id": run_id},
                upstream_artifact_ids=(run_id, plan_id),
            )
            atomic_write_json(artifact, art)
            payload_paths.append(payload)
            artifact_paths.append(artifact)
        return lock, payload_paths, artifact_paths

    def test_exact_union_merges_in_lock_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, payloads, artifacts = self._fixture(root)
            out = root / "merged.json"
            art = root / "merged.artifact.json"
            merged, manifest = merge_asr_shards(
                selection_lock=lock,
                shard_paths=payloads,
                shard_artifact_paths=artifacts,
                out=out,
                artifact_out=art,
            )
            self.assertEqual(merged["job_count"], 4)
            self.assertEqual(merged["selected_job_ids"], ["j1", "j2", "j3", "j4"])
            self.assertEqual([row["job_id"] for row in merged["jobs"]], ["j1", "j2", "j3", "j4"])
            self.assertEqual(merged["shard_merge"]["shard_count"], 2)
            self.assertEqual(manifest["stage"], "asr_evidence_local")
            self.assertIn("run-artifact", manifest["upstream_artifact_ids"])

    def test_duplicate_or_missing_job_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, payloads, artifacts = self._fixture(root)
            second = json.loads(payloads[1].read_text(encoding="utf-8"))
            second["jobs"][0]["job_id"] = "j2"
            second["selected_job_ids"][0] = "j2"
            atomic_write_json(payloads[1], second)
            rebuilt = build_artifact_manifest(
                task_fingerprint_sha256="1" * 64,
                stage="asr_evidence_local",
                algorithm_version=__version__,
                outputs=(("asr_evidence", payloads[1]),),
                normalized_config={"source_run_artifact_id": "run-artifact"},
                upstream_artifact_ids=("run-artifact", "plan-artifact"),
            )
            atomic_write_json(artifacts[1], rebuilt)
            with self.assertRaisesRegex(ValueError, "more than one shard"):
                merge_asr_shards(
                    selection_lock=lock,
                    shard_paths=payloads,
                    shard_artifact_paths=artifacts,
                    out=root / "merged.json",
                    artifact_out=root / "merged.artifact.json",
                )


if __name__ == "__main__":
    unittest.main()
