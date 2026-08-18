from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import atomic_write_json, build_artifact_manifest
from lyric_aligner.contracts.verification_session import (
    clear_verified_input_session,
    create_verified_input_session,
    file_is_attested,
    install_verified_input_session,
    role_is_attested,
)
from lyric_aligner.pipeline.stage_runner import SafeStageRunner
from task_contract import build_task_manifest, verify_manifest_inputs, write_json_atomic


FINGERPRINT = "1" * 64
ASSET_ID = "2" * 64


class VerificationSessionTests(unittest.TestCase):
    def tearDown(self):
        clear_verified_input_session()

    def test_fresh_session_attests_verified_role_and_detects_stat_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = root / "private" / "session_test"
            input_dir = task / "input"
            lyrics = input_dir / "lyrics"
            sources = input_dir / "source-audio"
            qa = task / "qa"
            for directory in (lyrics, sources, qa):
                directory.mkdir(parents=True, exist_ok=True)
            source_srt = input_dir / "source.srt"
            audio = input_dir / "mix.wav"
            song_list = input_dir / "songs.txt"
            lyric = lyrics / "song.lrc"
            source_audio = sources / "song.wav"
            for path, content in (
                (source_srt, "1\n00:00:00,000 --> 00:00:01,000\nx\n"),
                (audio, "audio"),
                (song_list, "00:00 artist - song\n"),
                (lyric, "[00:00.00]x\n"),
                (source_audio, "source"),
            ):
                path.write_text(content, encoding="utf-8")
            manifest = build_task_manifest(
                root,
                "session_test",
                source_srt=source_srt,
                audio=audio,
                song_list=song_list,
                lyrics_dir=lyrics,
                source_audio_dir=sources,
            )
            manifest_path = qa / "task_manifest.json"
            write_json_atomic(manifest_path, manifest)
            self.assertEqual(verify_manifest_inputs(manifest_path, manifest), [])

            session_path = root / "output" / "session.json"
            token = create_verified_input_session(
                manifest_path=manifest_path,
                manifest=manifest,
                repository_root=root,
                session_path=session_path,
            )
            install_verified_input_session(session_path, token)
            self.assertTrue(role_is_attested(manifest_path, manifest, "audio"))
            self.assertTrue(file_is_attested(audio, manifest["inputs"]["audio"]["sha256"]))

            stat = audio.stat()
            os.utime(audio, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            self.assertFalse(role_is_attested(manifest_path, manifest, "audio"))
            self.assertFalse(file_is_attested(audio, manifest["inputs"]["audio"]["sha256"]))

    def test_wrong_token_never_attests(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = root / "private" / "token_test"
            input_dir = task / "input"
            lyrics = input_dir / "lyrics"
            qa = task / "qa"
            input_dir.mkdir(parents=True)
            lyrics.mkdir()
            qa.mkdir()
            source_srt = input_dir / "source.srt"
            audio = input_dir / "mix.wav"
            song_list = input_dir / "songs.txt"
            lyric = lyrics / "song.lrc"
            source_srt.write_text("srt", encoding="utf-8")
            audio.write_text("audio", encoding="utf-8")
            song_list.write_text("song", encoding="utf-8")
            lyric.write_text("lyric", encoding="utf-8")
            manifest = build_task_manifest(
                root,
                "token_test",
                source_srt=source_srt,
                audio=audio,
                song_list=song_list,
                lyrics_dir=lyrics,
            )
            manifest_path = qa / "task_manifest.json"
            write_json_atomic(manifest_path, manifest)
            session_path = root / "session.json"
            create_verified_input_session(
                manifest_path=manifest_path,
                manifest=manifest,
                repository_root=root,
                session_path=session_path,
            )
            install_verified_input_session(session_path, "wrong-token")
            self.assertFalse(role_is_attested(manifest_path, manifest, "audio"))


class SafeResumeTests(unittest.TestCase):
    def _coarse_fixture(self, directory: Path):
        output = directory / "coarse.json"
        artifact_path = directory / "coarse.artifact.json"
        asset_artifact = directory / "asset.artifact.json"
        atomic_write_json(asset_artifact, {"artifact_id": ASSET_ID})
        atomic_write_json(
            output,
            {
                "schema_version": "1.1",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": FINGERPRINT,
                "occurrence_id": "occ-1",
            },
        )
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=FINGERPRINT,
            stage="coarse_audio_alignment",
            algorithm_version=__version__,
            outputs=(("coarse_alignment", output),),
            normalized_config={
                "asset_artifact_id": ASSET_ID,
                "mix_start": 1.0,
                "mix_end": 9.0,
            },
            producer={"git_commit": "same-commit"},
            upstream_artifact_ids=(ASSET_ID,),
            evidence={"occurrence_id": "occ-1"},
        )
        atomic_write_json(artifact_path, artifact)
        command = [
            "python",
            str(directory / "v4_coarse_align.py"),
            "--asset-artifact",
            str(asset_artifact),
            "--occurrence-id",
            "occ-1",
            "--mix-start",
            "1.000000",
            "--mix-end",
            "9.000000",
            "--out",
            str(output),
            "--artifact-out",
            str(artifact_path),
        ]
        return output, command

    def test_exact_artifact_is_reused_without_subprocess_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, command = self._coarse_fixture(directory)
            runner = SafeStageRunner(
                repository_root=directory,
                task_fingerprint_sha256=FINGERPRINT,
                git_commit="same-commit",
                workers=2,
                resume=True,
            )
            runner.run(command)
            summary = runner.summary()
            self.assertEqual(summary.resume_hits, 1)
            self.assertEqual(summary.executed, 0)

    def test_tampered_output_fails_reuse_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            output, command = self._coarse_fixture(directory)
            output.write_text("{}\n", encoding="utf-8")
            runner = SafeStageRunner(
                repository_root=directory,
                task_fingerprint_sha256=FINGERPRINT,
                git_commit="same-commit",
                workers=2,
                resume=True,
            )
            reusable, reason = runner._check_reusable(command)
            self.assertFalse(reusable)
            self.assertIn(reason, {"output_task_fingerprint_mismatch", "output_digest_mismatch"})

    def test_different_git_commit_fails_reuse_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, command = self._coarse_fixture(directory)
            runner = SafeStageRunner(
                repository_root=directory,
                task_fingerprint_sha256=FINGERPRINT,
                git_commit="new-commit",
                workers=2,
                resume=True,
            )
            reusable, reason = runner._check_reusable(command)
            self.assertFalse(reusable)
            self.assertEqual(reason, "producer_git_commit_mismatch")


class BoundedWorkerTests(unittest.TestCase):
    def test_run_many_never_exceeds_worker_bound(self):
        class ProbeRunner(SafeStageRunner):
            def __init__(self):
                super().__init__(
                    repository_root=Path.cwd(),
                    task_fingerprint_sha256=FINGERPRINT,
                    git_commit="commit",
                    workers=2,
                    resume=False,
                )
                self.active = 0
                self.peak = 0
                self.probe_lock = threading.Lock()

            def run(self, command, *, allow_resume=True):
                with self.probe_lock:
                    self.active += 1
                    self.peak = max(self.peak, self.active)
                try:
                    time.sleep(0.04)
                    return ""
                finally:
                    with self.probe_lock:
                        self.active -= 1

        runner = ProbeRunner()
        runner.run_many([["python", f"job-{index}.py"] for index in range(6)])
        self.assertEqual(runner.peak, 2)

    def test_worker_limit_rejects_unbounded_values(self):
        with self.assertRaises(ValueError):
            SafeStageRunner(
                repository_root=Path.cwd(),
                task_fingerprint_sha256=FINGERPRINT,
                git_commit="commit",
                workers=5,
            )


if __name__ == "__main__":
    unittest.main()
