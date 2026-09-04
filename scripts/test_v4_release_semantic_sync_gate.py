from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from semantic_sync_test_support import write_passing_semantic_sync_fixture
from task_contract import build_task_manifest, write_json_atomic
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

    def validate(self, fixture: dict):
        return _validate_semantic_sync_qa(
            fixture["semantic_qa"],
            run_path=fixture["run_path"],
            final_srt=fixture["final_srt"],
            manifest_path=fixture["manifest_path"],
            manifest=fixture["manifest"],
            algorithm_version=__version__,
            fusion_path=fixture["fusion_path"],
            final_report=fixture["final_report"],
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


if __name__ == "__main__":
    unittest.main()
