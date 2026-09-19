import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from lyric_aligner.alignment.internal_executor import (
    ExternalInternalAlignmentConfig,
    InternalAlignmentExecutionError,
    execute_external_internal_alignment_jobs,
)
from lyric_aligner.timeline.internal_segmentation import build_internal_segmentation_plan


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_D = "d" * 64


class Completed:
    returncode = 0
    stdout = ""
    stderr = ""


class InternalExecutorTests(unittest.TestCase):
    def report_rows(self):
        segments = [
            {"track_index": 0, "lrc_index": 1, "text": "line one", "projected_ms": 1200},
            {"track_index": 0, "lrc_index": 2, "text": "line two", "projected_ms": 3000},
        ]
        return [
            {
                "kind": "existing",
                "original_cue": "1",
                "start_ms": 1000,
                "end_ms": 5000,
                "text": "line one line two",
                "lrc_indices": "1;2",
                "canonical_segments_json": json.dumps(
                    segments, sort_keys=True, separators=(",", ":")
                ),
                "evidence": "split_suppressed_no_audio_boundary_authority",
            }
        ]

    def plan(self, audio_sha):
        return build_internal_segmentation_plan(
            task_fingerprint_sha256=SHA_A,
            source_srt_sha256=SHA_B,
            final_audio_sha256=audio_sha,
            report_sha256=SHA_D,
            source_cues=[{"number": 1, "start_ms": 1000, "end_ms": 5000, "text": "editor"}],
            report_rows=self.report_rows(),
        )

    def config(self):
        return ExternalInternalAlignmentConfig(
            command=sys.executable,
            family="final_mix_singing_alignment",
            correlation_group="fake-sofa",
            backend_id="fake-sofa-backend",
            backend_version="1",
            model_id="fake-sofa-model",
            model_revision="r1",
            language="en",
            backend_profile_id="fixture-profile-v1",
        )

    def test_runner_produces_uncalibrated_direct_final_mix_point(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            sf.write(str(audio), np.zeros((7000, 1), dtype=np.float32), 1000, subtype="PCM_16")
            audio_sha = hashlib.sha256(audio.read_bytes()).hexdigest()
            plan = self.plan(audio_sha)

            def runner(argv, **kwargs):
                request_path = Path(argv[argv.index("--request") + 1])
                response_path = Path(argv[argv.index("--response") + 1])
                request = json.loads(request_path.read_text(encoding="utf-8"))
                response_path.write_text(
                    json.dumps(
                        {
                            "protocol_version": request["protocol_version"],
                            "boundary_id": request["boundary_id"],
                            "backend_id": request["backend_id"],
                            "backend_version": request["backend_version"],
                            "model_id": request["model_id"],
                            "model_revision": request["model_revision"],
                            "backend_profile_id": request["backend_profile_id"],
                            "family": request["family"],
                            "correlation_group": request["correlation_group"],
                            "audio_basis": request["audio_basis"],
                            "language": request["language"],
                            "window_policy_id": request["window_policy_id"],
                            "lexical_id": request["lexical_id"],
                            "lexical_revision": request["lexical_revision"],
                            "lexical_units_sha256": request["lexical_units_sha256"],
                            "status": "aligned",
                            "mix_window_ms": request["mix_window_ms"],
                            "target_match_count": 1,
                            "boundary_ms": 3000,
                            "uncertainty_ms": 40,
                            "raw_confidence": 0.41,
                        }
                    ),
                    encoding="utf-8",
                )
                return Completed()

            result = execute_external_internal_alignment_jobs(
                plan=plan,
                final_audio_path=audio,
                final_audio_sha256=audio_sha,
                config=self.config(),
                runner=runner,
            )
            self.assertEqual(result["job_count"], 1)
            point = result["jobs"][0]["point"]
            self.assertEqual(point["boundary_ms"], 3000)
            self.assertEqual(point["audio_basis"], "final_mix")
            self.assertFalse(point["calibration_passed"])
            self.assertEqual(point["calibration_sha256"], "")

    def test_ambiguous_target_match_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            sf.write(str(audio), np.zeros((7000, 1), dtype=np.float32), 1000, subtype="PCM_16")
            audio_sha = hashlib.sha256(audio.read_bytes()).hexdigest()
            plan = self.plan(audio_sha)

            def runner(argv, **kwargs):
                request_path = Path(argv[argv.index("--request") + 1])
                response_path = Path(argv[argv.index("--response") + 1])
                request = json.loads(request_path.read_text(encoding="utf-8"))
                response_path.write_text(
                    json.dumps(
                        {
                            "protocol_version": request["protocol_version"],
                            "boundary_id": request["boundary_id"],
                            "backend_id": request["backend_id"],
                            "backend_version": request["backend_version"],
                            "model_id": request["model_id"],
                            "model_revision": request["model_revision"],
                            "backend_profile_id": request["backend_profile_id"],
                            "family": request["family"],
                            "correlation_group": request["correlation_group"],
                            "audio_basis": request["audio_basis"],
                            "language": request["language"],
                            "window_policy_id": request["window_policy_id"],
                            "lexical_id": request["lexical_id"],
                            "lexical_revision": request["lexical_revision"],
                            "lexical_units_sha256": request["lexical_units_sha256"],
                            "status": "aligned",
                            "mix_window_ms": request["mix_window_ms"],
                            "target_match_count": 2,
                            "boundary_ms": 3000,
                        }
                    ),
                    encoding="utf-8",
                )
                return Completed()

            with self.assertRaises(InternalAlignmentExecutionError):
                execute_external_internal_alignment_jobs(
                    plan=plan,
                    final_audio_path=audio,
                    final_audio_sha256=audio_sha,
                    config=self.config(),
                    runner=runner,
                )

    def test_final_audio_hash_mismatch_fails_before_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            sf.write(str(audio), np.zeros((7000, 1), dtype=np.float32), 1000, subtype="PCM_16")
            audio_sha = hashlib.sha256(audio.read_bytes()).hexdigest()
            plan = self.plan(audio_sha)
            with self.assertRaises(InternalAlignmentExecutionError):
                execute_external_internal_alignment_jobs(
                    plan=plan,
                    final_audio_path=audio,
                    final_audio_sha256="f" * 64,
                    config=self.config(),
                )


if __name__ == "__main__":
    unittest.main()
