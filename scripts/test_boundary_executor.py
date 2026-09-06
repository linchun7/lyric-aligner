import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from lyric_aligner.alignment.boundary_executor import (
    BoundaryAlignmentExecutionError,
    ExternalBoundaryAlignmentConfig,
    execute_external_boundary_alignment_jobs,
)
from lyric_aligner.timeline.boundary_refinement import build_boundary_refinement_plan


class Completed:
    returncode = 0
    stdout = ""
    stderr = ""


class BoundaryExecutorTests(unittest.TestCase):
    def plan(self, audio_sha):
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

    def config(self):
        return ExternalBoundaryAlignmentConfig(
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

    def make_audio(self, root):
        audio = root / "mix.wav"
        sf.write(str(audio), np.zeros((7000, 1), dtype=np.float32), 1000, subtype="PCM_16")
        return audio, hashlib.sha256(audio.read_bytes()).hexdigest()

    @staticmethod
    def echo_response(request, *, match_count=1, tamper_profile=False):
        return {
            "protocol_version": request["protocol_version"],
            "boundary_id": request["boundary_id"],
            "boundary_kind": request["boundary_kind"],
            "backend_id": request["backend_id"],
            "backend_version": request["backend_version"],
            "model_id": request["model_id"],
            "model_revision": request["model_revision"],
            "backend_profile_id": "wrong-profile" if tamper_profile else request["backend_profile_id"],
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
            "target_match_count": match_count,
            "boundary_ms": 1020 if request["boundary_kind"] == "start" else 4980,
            "uncertainty_ms": 40,
            "raw_confidence": 0.4,
            "evidence_sha256": "e" * 64,
        }

    def test_runner_derives_policy_window_and_returns_uncalibrated_point(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio, audio_sha = self.make_audio(root)
            plan = self.plan(audio_sha)
            start_job = next(row for row in plan["jobs"] if row["boundary_kind"] == "start")
            seen = {}

            def runner(argv, **kwargs):
                request_path = Path(argv[argv.index("--request") + 1])
                response_path = Path(argv[argv.index("--response") + 1])
                request = json.loads(request_path.read_text(encoding="utf-8"))
                seen.update(request)
                response_path.write_text(json.dumps(self.echo_response(request)), encoding="utf-8")
                return Completed()

            result = execute_external_boundary_alignment_jobs(
                plan=plan,
                final_audio_path=audio,
                final_audio_sha256=audio_sha,
                config=self.config(),
                selected_boundary_ids=[start_job["boundary_id"]],
                runner=runner,
            )
            self.assertEqual(seen["mix_window_ms"], [0, 6500])
            self.assertEqual(result["job_count"], 1)
            point = result["jobs"][0]["point"]
            self.assertEqual(point["boundary_ms"], 1020)
            self.assertEqual(point["backend_profile_id"], "fixture-profile-v1")
            self.assertFalse(point["calibration_passed"])

    def test_ambiguous_target_match_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio, audio_sha = self.make_audio(root)
            plan = self.plan(audio_sha)
            start_job = next(row for row in plan["jobs"] if row["boundary_kind"] == "start")

            def runner(argv, **kwargs):
                request_path = Path(argv[argv.index("--request") + 1])
                response_path = Path(argv[argv.index("--response") + 1])
                request = json.loads(request_path.read_text(encoding="utf-8"))
                response_path.write_text(json.dumps(self.echo_response(request, match_count=2)), encoding="utf-8")
                return Completed()

            with self.assertRaises(BoundaryAlignmentExecutionError):
                execute_external_boundary_alignment_jobs(
                    plan=plan,
                    final_audio_path=audio,
                    final_audio_sha256=audio_sha,
                    config=self.config(),
                    selected_boundary_ids=[start_job["boundary_id"]],
                    runner=runner,
                )

    def test_response_cannot_swap_backend_profile_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio, audio_sha = self.make_audio(root)
            plan = self.plan(audio_sha)
            start_job = next(row for row in plan["jobs"] if row["boundary_kind"] == "start")

            def runner(argv, **kwargs):
                request_path = Path(argv[argv.index("--request") + 1])
                response_path = Path(argv[argv.index("--response") + 1])
                request = json.loads(request_path.read_text(encoding="utf-8"))
                response_path.write_text(json.dumps(self.echo_response(request, tamper_profile=True)), encoding="utf-8")
                return Completed()

            with self.assertRaises(BoundaryAlignmentExecutionError):
                execute_external_boundary_alignment_jobs(
                    plan=plan,
                    final_audio_path=audio,
                    final_audio_sha256=audio_sha,
                    config=self.config(),
                    selected_boundary_ids=[start_job["boundary_id"]],
                    runner=runner,
                )


if __name__ == "__main__":
    unittest.main()
