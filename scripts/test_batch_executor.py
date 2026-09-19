import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from lyric_aligner.alignment.batch_executor import (
    BatchAlignmentExecutionError,
    ExternalBatchAlignmentConfig,
    PRODUCTION_ALIGNMENT_BATCH_PROTOCOL_VERSION,
    execute_external_alignment_batch,
)
from lyric_aligner.timeline.boundary_refinement import build_boundary_refinement_plan
from lyric_aligner.timeline.internal_segmentation import INTERNAL_SEGMENTATION_PLAN_SCHEMA_VERSION


class Completed:
    returncode = 0
    stdout = ""
    stderr = ""


class BatchExecutorTests(unittest.TestCase):
    def make_audio(self, root: Path):
        audio = root / "mix.wav"
        sf.write(str(audio), np.zeros((8000, 1), dtype=np.float32), 1000, subtype="PCM_16")
        return audio, hashlib.sha256(audio.read_bytes()).hexdigest()

    def config(self):
        return ExternalBatchAlignmentConfig(
            command=sys.executable,
            family="final_mix_singing_alignment",
            correlation_group="fixture-group",
            backend_id="fixture-backend",
            backend_version="full-sequence-alignment-core-1.0",
            model_id="fixture-model",
            model_revision="r1",
            language="en",
            backend_profile_id="fixture-profile-v1",
        )

    def outer_plan(self, audio_sha: str):
        return build_boundary_refinement_plan(
            task_fingerprint_sha256="a" * 64,
            source_srt_sha256="b" * 64,
            final_audio_sha256=audio_sha,
            report_sha256="d" * 64,
            source_cues=[{"number": 1, "start_ms": 1000, "end_ms": 5000, "text": "hello world"}],
            report_rows=[{
                "kind": "existing",
                "original_cue": "1",
                "start_ms": 1000,
                "end_ms": 5000,
                "text": "hello world",
                "lrc_indices": "1",
                "evidence": "fixture",
            }],
        )

    def internal_plan(self, audio_sha: str):
        return {
            "schema_version": INTERNAL_SEGMENTATION_PLAN_SCHEMA_VERSION,
            "policy_id": "audio-verified-internal-segmentation-v1",
            "mode": "plan_only",
            "task_fingerprint_sha256": "a" * 64,
            "source_srt_sha256": "b" * 64,
            "final_audio_sha256": audio_sha,
            "report_sha256": "d" * 64,
            "jobs": [{
                "boundary_id": "i" * 64,
                "cue_number": 1,
                "boundary_kind": "internal",
                "boundary_index": 1,
                "canonical_text": "hello world",
                "canonical_segments": [{"text": "hello"}, {"text": "world"}],
                "row_interval_ms": [1000, 5000],
            }],
        }

    @staticmethod
    def response_for(request, *, unaligned_last=False, tamper_profile=False, drop_last=False):
        records = []
        for index, row in enumerate(request["records"]):
            if unaligned_last and index == len(request["records"]) - 1:
                records.append({
                    "boundary_id": row["boundary_id"],
                    "boundary_kind": row["boundary_kind"],
                    "lexical_units_sha256": row["lexical_units_sha256"],
                    "mix_window_ms": row["mix_window_ms"],
                    "status": "unaligned",
                    "boundary_ms": None,
                    "reason": "fixture_unaligned",
                })
            else:
                window = row["mix_window_ms"]
                boundary_ms = max(window[0], min(window[1], row["row_interval_ms"][0] + 50))
                records.append({
                    "boundary_id": row["boundary_id"],
                    "boundary_kind": row["boundary_kind"],
                    "lexical_units_sha256": row["lexical_units_sha256"],
                    "mix_window_ms": row["mix_window_ms"],
                    "status": "aligned",
                    "boundary_ms": boundary_ms,
                    "target_match_count": 1,
                    "uncertainty_ms": 40,
                    "raw_confidence": 0.4,
                    "evidence_sha256": "e" * 64,
                    "reason": "",
                })
        if drop_last:
            records = records[:-1]
        return {
            "protocol_version": PRODUCTION_ALIGNMENT_BATCH_PROTOCOL_VERSION,
            "mode": request["mode"],
            "backend_id": request["backend_id"],
            "backend_version": request["backend_version"],
            "backend_profile_id": "wrong-profile" if tamper_profile else request["backend_profile_id"],
            "model_id": request["model_id"],
            "model_revision": request["model_revision"],
            "family": request["family"],
            "correlation_group": request["correlation_group"],
            "audio_basis": request["audio_basis"],
            "language": request["language"],
            "window_policy_id": request["window_policy_id"],
            "lexical_id": request["lexical_id"],
            "lexical_revision": request["lexical_revision"],
            "final_audio_sha256": request["final_audio_sha256"],
            "record_count": len(records),
            "records": records,
        }

    def runner(self, responder):
        def run(argv, **kwargs):
            request_path = Path(argv[argv.index("--request") + 1])
            response_path = Path(argv[argv.index("--response") + 1])
            request = json.loads(request_path.read_text(encoding="utf-8"))
            response_path.write_text(json.dumps(responder(request)), encoding="utf-8")
            return Completed()
        return run

    def test_outer_batch_preserves_complete_job_set_and_allows_per_job_unaligned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio, audio_sha = self.make_audio(root)
            plan = self.outer_plan(audio_sha)
            result = execute_external_alignment_batch(
                plan=plan,
                mode="outer",
                final_audio_path=audio,
                final_audio_sha256=audio_sha,
                config=self.config(),
                runner=self.runner(lambda req: self.response_for(req, unaligned_last=True)),
            )
            self.assertEqual(result["job_count"], 2)
            self.assertEqual(result["aligned_job_count"], 1)
            self.assertEqual(result["unaligned_job_count"], 1)
            self.assertEqual({row["boundary_id"] for row in result["jobs"]}, {row["boundary_id"] for row in plan["jobs"]})
            aligned = next(row for row in result["jobs"] if row["status"] == "aligned")
            unaligned = next(row for row in result["jobs"] if row["status"] == "unaligned")
            self.assertIsNotNone(aligned["point"])
            self.assertFalse(aligned["point"]["calibration_passed"])
            self.assertIsNone(unaligned["point"])
            self.assertEqual(unaligned["reason"], "fixture_unaligned")

    def test_internal_batch_prepares_segment_lexical_units(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio, audio_sha = self.make_audio(root)
            plan = self.internal_plan(audio_sha)
            seen = {}

            def responder(request):
                seen.update(request)
                return self.response_for(request)

            result = execute_external_alignment_batch(
                plan=plan,
                mode="internal",
                final_audio_path=audio,
                final_audio_sha256=audio_sha,
                config=self.config(),
                runner=self.runner(responder),
            )
            self.assertEqual(result["job_count"], 1)
            self.assertEqual(seen["records"][0]["segment_lexical_units"], [["hello"], ["world"]])
            self.assertEqual(seen["records"][0]["boundary_index"], 1)

    def test_response_cannot_swap_profile_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio, audio_sha = self.make_audio(root)
            plan = self.outer_plan(audio_sha)
            with self.assertRaises(BatchAlignmentExecutionError):
                execute_external_alignment_batch(
                    plan=plan,
                    mode="outer",
                    final_audio_path=audio,
                    final_audio_sha256=audio_sha,
                    config=self.config(),
                    runner=self.runner(lambda req: self.response_for(req, tamper_profile=True)),
                )

    def test_partial_response_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio, audio_sha = self.make_audio(root)
            plan = self.outer_plan(audio_sha)
            with self.assertRaises(BatchAlignmentExecutionError):
                execute_external_alignment_batch(
                    plan=plan,
                    mode="outer",
                    final_audio_path=audio,
                    final_audio_sha256=audio_sha,
                    config=self.config(),
                    runner=self.runner(lambda req: self.response_for(req, drop_last=True)),
                )

    def test_unknown_selected_boundary_is_rejected_before_sidecar(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio, audio_sha = self.make_audio(root)
            plan = self.outer_plan(audio_sha)
            with self.assertRaises(BatchAlignmentExecutionError):
                execute_external_alignment_batch(
                    plan=plan,
                    mode="outer",
                    final_audio_path=audio,
                    final_audio_sha256=audio_sha,
                    config=self.config(),
                    selected_boundary_ids=["not-in-plan"],
                    runner=lambda *args, **kwargs: self.fail("runner must not be invoked"),
                )


if __name__ == "__main__":
    unittest.main()
