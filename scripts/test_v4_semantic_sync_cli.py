from __future__ import annotations

import json
import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from task_contract import build_task_manifest, write_json_atomic


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "v4_audit_semantic_sync.py"


def run_command(command: list[str]):
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)


class SemanticSyncCliTests(unittest.TestCase):
    def build_fixture(self, root: Path) -> dict:
        task_root = root / "private" / "semantic-sync-cli"
        input_dir = task_root / "input"
        qa_dir = task_root / "qa"
        lyrics_dir = input_dir / "lyrics"
        for directory in (input_dir, qa_dir, lyrics_dir):
            directory.mkdir(parents=True, exist_ok=True)
        source_srt = input_dir / "source.srt"
        source_srt.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nalpha lyric words\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\nbeta lyric words\n",
            encoding="utf-8",
        )
        final_srt = root / "FINAL.srt"
        final_srt.write_text(source_srt.read_text(encoding="utf-8"), encoding="utf-8")
        audio = input_dir / "mix.wav"
        audio.write_bytes(b"synthetic-audio")
        songs = input_dir / "songs.txt"
        songs.write_text("00:00 Artist - Track\n", encoding="utf-8")
        (lyrics_dir / "track.lrc").write_text(
            "[00:01.00]alpha lyric words\n[00:03.00]beta lyric words\n",
            encoding="utf-8",
        )
        manifest = build_task_manifest(
            root,
            "semantic-sync-cli",
            source_srt=source_srt,
            audio=audio,
            song_list=songs,
            lyrics_dir=lyrics_dir,
        )
        manifest_path = qa_dir / "task_manifest.json"
        write_json_atomic(manifest_path, manifest)
        fingerprint = str(manifest["task_fingerprint_sha256"])
        timeline = root / "timeline.json"
        write_json_atomic(
            timeline,
            {
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "occurrence_id": "occ-1",
                "result": {
                    "lines": [
                        {
                            "mix_start_ms": 1000,
                            "mix_end_ms": 2000,
                            "text": "alpha lyric words",
                            "occurrence_id": "occ-1",
                            "track_id": "track-1",
                            "ordinal": 1,
                            "canonical_line_index": 0,
                        },
                        {
                            "mix_start_ms": 3000,
                            "mix_end_ms": None,
                            "text": "beta lyric words",
                            "occurrence_id": "occ-1",
                            "track_id": "track-1",
                            "ordinal": 1,
                            "canonical_line_index": 1,
                        },
                    ]
                },
            },
        )
        run_path = root / "run.json"
        write_json_atomic(
            run_path,
            {
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "plan": {"content_end": 5.0},
                "occurrences": [
                    {
                        "ordinal": 1,
                        "occurrence_id": "occ-1",
                        "timeline_path": str(timeline.resolve()),
                    }
                ],
            },
        )
        final_report = root / "FINAL.csv"
        with final_report.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["position", "start_ms", "end_ms", "text", "task_fingerprint_sha256", "occurrence_id", "track_id", "ordinal", "canonical_line_index"],
            )
            writer.writeheader()
            writer.writerows([
                {"position": 1, "start_ms": 1000, "end_ms": 2000, "text": "alpha lyric words", "task_fingerprint_sha256": fingerprint, "occurrence_id": "occ-1", "track_id": "track-1", "ordinal": 1, "canonical_line_index": 0},
                {"position": 2, "start_ms": 3000, "end_ms": 4000, "text": "beta lyric words", "task_fingerprint_sha256": fingerprint, "occurrence_id": "occ-1", "track_id": "track-1", "ordinal": 1, "canonical_line_index": 1},
            ])
        fusion = root / "fusion.json"
        write_json_atomic(fusion, {
            "algorithm_version": __version__,
            "task_fingerprint_sha256": fingerprint,
            "mode": "shadow_only",
            "lines": [
                {"occurrence_id": "occ-1", "track_id": "track-1", "ordinal": 1, "canonical_line_index": 0, "source_timeline_boundary_ms": [1000, 2000], "families": [{"family": "source_timeline", "available": True, "boundary_ms": [1000, 2000]}, {"family": "forced_alignment", "available": True, "boundary_ms": [1000, 2000]}], "line_confidence": 0.95},
                {"occurrence_id": "occ-1", "track_id": "track-1", "ordinal": 1, "canonical_line_index": 1, "source_timeline_boundary_ms": [3000, 4000], "families": [{"family": "source_timeline", "available": True, "boundary_ms": [3000, 4000]}, {"family": "forced_alignment", "available": True, "boundary_ms": [3000, 4000]}], "line_confidence": 0.95},
            ],
        })
        return {
            "manifest_path": manifest_path,
            "run_path": run_path,
            "final_srt": final_srt,
            "fusion": fusion,
            "final_report": final_report,
        }

    def command(self, fixture: dict, out: Path) -> list[str]:
        return [
            sys.executable,
            str(SCRIPT),
            "--task-manifest",
            str(fixture["manifest_path"]),
            "--run",
            str(fixture["run_path"]),
            "--final-srt",
            str(fixture["final_srt"]),
            "--fusion",
            str(fixture["fusion"]),
            "--final-report",
            str(fixture["final_report"]),
            "--out",
            str(out),
        ]

    def test_cli_passes_exact_semantic_timing_and_supports_open_end_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(root)
            out = root / "semantic-sync.json"
            result = run_command(self.command(fixture, out))
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(payload["passed"])
            self.assertTrue(payload["projection_sync"]["passed"])
            self.assertTrue(payload["final_sync"]["passed"])

    def test_cli_refuses_to_overwrite_final_srt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(root)
            before = fixture["final_srt"].read_bytes()
            result = run_command(self.command(fixture, fixture["final_srt"]))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("collides with input final_srt", result.stderr)
            self.assertEqual(fixture["final_srt"].read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
