import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import build_artifact_manifest
from lyric_aligner.srt import Cue, cue_id, text_sha256
from task_contract import build_task_manifest, write_json_atomic


ROOT = Path(__file__).resolve().parents[1]


def run_command(command):
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def format_time(value: int) -> str:
    hour, remain = divmod(value, 3_600_000)
    minute, remain = divmod(remain, 60_000)
    second, millis = divmod(remain, 1000)
    return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"


def write_srt(path: Path, cues: list[Cue]) -> None:
    blocks = [
        f"{cue.number}\n{format_time(cue.start_ms)} --> {format_time(cue.end_ms)}\n{cue.text}"
        for cue in cues
    ]
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


class V4EditorCueReconcileCLITests(unittest.TestCase):
    def build_fixture(
        self,
        root: Path,
        *,
        segmentation_authority: str = "canonical_line_evaluation_only",
        publish_ready: bool = False,
    ) -> dict:
        task_root = root / "private" / "generic-editor-reconcile"
        input_dir = task_root / "input"
        qa_dir = task_root / "qa"
        lyrics_dir = input_dir / "lyrics"
        for directory in (input_dir, qa_dir, lyrics_dir):
            directory.mkdir(parents=True, exist_ok=True)

        source_srt = input_dir / "source.srt"
        editor_cues = [
            Cue(10, 0, 3000, "editor alpha"),
            Cue(20, 3000, 6000, "editor beta"),
        ]
        write_srt(source_srt, editor_cues)
        audio = input_dir / "mix.wav"
        audio.write_bytes(b"synthetic-audio")
        song_list = input_dir / "songs.txt"
        song_list.write_text("00:00 Generic Artist - Generic Track\n", encoding="utf-8")
        lyric_file = lyrics_dir / "generic.lrc"
        lyric_file.write_text(
            "[00:00.50]canonical alpha\n[00:03.50]canonical beta\n",
            encoding="utf-8",
        )

        manifest = build_task_manifest(
            root,
            "generic-editor-reconcile",
            source_srt=source_srt,
            audio=audio,
            song_list=song_list,
            lyrics_dir=lyrics_dir,
        )
        manifest_path = qa_dir / "task_manifest.json"
        write_json_atomic(manifest_path, manifest)
        fingerprint = manifest["task_fingerprint_sha256"]

        render_dir = root / "render"
        render_dir.mkdir()
        evaluation_srt = render_dir / "EVAL.srt"
        canonical_cues = [
            Cue(1, 500, 2500, "canonical alpha"),
            Cue(2, 3500, 5500, "canonical beta"),
        ]
        write_srt(evaluation_srt, canonical_cues)

        report = render_dir / "EVAL.csv"
        fieldnames = [
            "position",
            "cue_number",
            "start_ms",
            "end_ms",
            "text",
            "occurrence_id",
            "track_id",
            "ordinal",
            "canonical_line_index",
            "timing_format",
            "end_basis",
            "task_fingerprint_sha256",
            "cue_id",
            "text_sha256",
        ]
        with report.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for position, cue in enumerate(canonical_cues, start=1):
                writer.writerow(
                    {
                        "position": position,
                        "cue_number": cue.number,
                        "start_ms": cue.start_ms,
                        "end_ms": cue.end_ms,
                        "text": cue.text,
                        "occurrence_id": "occ-1",
                        "track_id": "track-1",
                        "ordinal": 1,
                        "canonical_line_index": position - 1,
                        "timing_format": "line_lrc",
                        "end_basis": "synthetic",
                        "task_fingerprint_sha256": fingerprint,
                        "cue_id": cue_id(position, cue),
                        "text_sha256": text_sha256(cue.text),
                    }
                )

        qa_json = render_dir / "EVAL.qa.json"
        qa = {
            "schema_version": "1.0",
            "algorithm_version": __version__,
            "task_fingerprint_sha256": fingerprint,
            "passed": True,
            "structurally_valid": True,
            "fully_reviewed": True,
            "publish_ready": publish_ready,
            "segmentation_authority": "canonical_line_evaluation_only",
            "release_blocked_reason": "editor_cue_reconciliation_required",
            "review_candidate_count": 0,
            "cue_count": len(canonical_cues),
        }
        write_json_atomic(qa_json, qa)

        render_artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="final_render",
            algorithm_version=__version__,
            outputs=(
                ("final_srt", evaluation_srt),
                ("audit_csv", report),
                ("qa_json", qa_json),
            ),
            normalized_config={
                "segmentation_authority": segmentation_authority,
                "legacy_fallback": False,
            },
            upstream_artifact_ids=("synthetic-run-artifact",),
            evidence={
                "publish_ready": publish_ready,
                "segmentation_authority": segmentation_authority,
            },
        )
        render_artifact_path = render_dir / "EVAL.render.artifact.json"
        write_json_atomic(render_artifact_path, render_artifact)

        return {
            "manifest": manifest,
            "manifest_path": manifest_path,
            "source_srt": source_srt,
            "lyric_file": lyric_file,
            "evaluation_srt": evaluation_srt,
            "report": report,
            "qa_json": qa_json,
            "render_artifact": render_artifact,
            "render_artifact_path": render_artifact_path,
        }

    def command(self, fixture: dict, out: Path, artifact_out: Path) -> list[str]:
        return [
            sys.executable,
            str(ROOT / "scripts" / "v4_editor_cue_reconcile.py"),
            "--task-manifest",
            str(fixture["manifest_path"]),
            "--evaluation-srt",
            str(fixture["evaluation_srt"]),
            "--report",
            str(fixture["report"]),
            "--qa-json",
            str(fixture["qa_json"]),
            "--render-artifact",
            str(fixture["render_artifact_path"]),
            "--out",
            str(out),
            "--artifact-out",
            str(artifact_out),
        ]

    def test_cli_emits_evaluation_only_lineage_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(root)
            out = root / "reconcile.json"
            artifact_out = root / "reconcile.artifact.json"
            result = run_command(self.command(fixture, out, artifact_out))
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            stdout = json.loads(result.stdout)
            self.assertEqual(
                stdout["segmentation_authority"],
                "editor_reconciliation_evaluation_only",
            )
            self.assertFalse(stdout["production_authority_granted"])
            self.assertTrue(stdout["full_topology_candidate"])

            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["source_srt_sha256"],
                fixture["manifest"]["inputs"]["source_srt"]["sha256"],
            )
            self.assertEqual(
                payload["source_render_artifact_id"],
                fixture["render_artifact"]["artifact_id"],
            )
            self.assertEqual(payload["result"]["status_counts"]["resolved"], 2)
            self.assertEqual(payload["result"]["status_counts"]["rebutted"], 0)
            self.assertTrue(payload["result"]["full_topology_candidate"])
            self.assertFalse(payload["result"]["production_authority_granted"])

            artifact = json.loads(artifact_out.read_text(encoding="utf-8"))
            self.assertEqual(artifact["stage"], "editor_cue_reconciliation_evaluation")
            self.assertEqual(
                artifact["upstream_artifact_ids"],
                [fixture["render_artifact"]["artifact_id"]],
            )
            self.assertEqual(
                artifact["normalized_config"]["segmentation_authority"],
                "editor_reconciliation_evaluation_only",
            )
            self.assertFalse(
                artifact["normalized_config"]["production_authority_granted"]
            )

    def test_cli_rejects_noncanonical_source_render_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(
                root,
                segmentation_authority="editor_reconciled",
            )
            result = run_command(
                self.command(
                    fixture,
                    root / "reconcile.json",
                    root / "reconcile.artifact.json",
                )
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "requires canonical_line_evaluation_only source render",
                result.stderr,
            )

    def test_cli_rejects_publish_ready_source_qa(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(root, publish_ready=True)
            result = run_command(
                self.command(
                    fixture,
                    root / "reconcile.json",
                    root / "reconcile.artifact.json",
                )
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must remain publish_ready=false", result.stderr)

    def test_cli_refuses_to_overwrite_manifest_directory_member(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(root)
            before = fixture["lyric_file"].read_bytes()
            result = run_command(
                self.command(
                    fixture,
                    fixture["lyric_file"],
                    root / "reconcile.artifact.json",
                )
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("collides with input", result.stderr)
            self.assertEqual(fixture["lyric_file"].read_bytes(), before)

    def test_cli_refuses_outputs_sharing_one_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(root)
            shared = root / "shared.json"
            result = run_command(self.command(fixture, shared, shared))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("share the same path", result.stderr)
            self.assertFalse(shared.exists())


if __name__ == "__main__":
    unittest.main()
