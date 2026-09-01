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
PROFILE_ID = "synthetic-profile-id"
PROFILE_VERSION = "synthetic-profile-v1"


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


class V4EditorTopologyRebuttalMaterializerTests(unittest.TestCase):
    def build_fixture(
        self,
        root: Path,
        *,
        gap_witness: bool,
        timing_format: str = "line_lrc",
    ) -> dict:
        task_root = root / "private" / "generic-topology-rebuttal"
        input_dir = task_root / "input"
        qa_dir = task_root / "qa"
        lyrics_dir = input_dir / "lyrics"
        for directory in (input_dir, qa_dir, lyrics_dir):
            directory.mkdir(parents=True, exist_ok=True)

        source_srt = input_dir / "source.srt"
        if gap_witness:
            editor_cues = [
                Cue(10, 0, 1000, "editor alpha"),
                Cue(20, 3000, 4000, "editor beta"),
            ]
            canonical_cues = [
                Cue(1, 200, 800, "canonical alpha"),
                Cue(2, 1600, 2200, "canonical missing topology"),
                Cue(3, 3200, 3800, "canonical beta"),
            ]
        else:
            editor_cues = [
                Cue(10, 0, 1000, "editor alpha"),
                Cue(20, 1000, 2000, "editor beta"),
            ]
            canonical_cues = [Cue(1, 500, 1500, "canonical crossing boundary")]
        write_srt(source_srt, editor_cues)

        audio = input_dir / "mix.wav"
        audio.write_bytes(b"synthetic-audio")
        song_list = input_dir / "songs.txt"
        song_list.write_text("00:00 Generic Artist - Generic Track\n", encoding="utf-8")
        lyric_file = lyrics_dir / "generic.lrc"
        lyric_file.write_text(
            "[00:00.20]canonical alpha\n"
            "[00:01.60]canonical missing topology\n"
            "[00:03.20]canonical beta\n",
            encoding="utf-8",
        )

        manifest = build_task_manifest(
            root,
            "generic-topology-rebuttal",
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
                        "timing_format": timing_format,
                        "end_basis": "next_line_start",
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
            "calibration_profile_id": PROFILE_ID,
            "calibration_profile_version": PROFILE_VERSION,
            "passed": True,
            "structurally_valid": True,
            "fully_reviewed": True,
            "publish_ready": False,
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
                "calibration_profile_id": PROFILE_ID,
                "calibration_profile_version": PROFILE_VERSION,
                "segmentation_authority": "canonical_line_evaluation_only",
                "legacy_fallback": False,
            },
            upstream_artifact_ids=("synthetic-run-artifact",),
            evidence={
                "publish_ready": False,
                "segmentation_authority": "canonical_line_evaluation_only",
                "release_blocked_reason": "editor_cue_reconciliation_required",
            },
        )
        render_artifact_path = render_dir / "EVAL.render.artifact.json"
        write_json_atomic(render_artifact_path, render_artifact)

        reconciliation = render_dir / "reconciliation.json"
        reconciliation_artifact = render_dir / "reconciliation.artifact.json"
        reconcile_result = run_command(
            [
                sys.executable,
                str(ROOT / "scripts" / "v4_editor_cue_reconcile.py"),
                "--task-manifest",
                str(manifest_path),
                "--evaluation-srt",
                str(evaluation_srt),
                "--report",
                str(report),
                "--qa-json",
                str(qa_json),
                "--render-artifact",
                str(render_artifact_path),
                "--out",
                str(reconciliation),
                "--artifact-out",
                str(reconciliation_artifact),
            ]
        )
        self.assertEqual(reconcile_result.returncode, 0, msg=reconcile_result.stderr)

        return {
            "manifest_path": manifest_path,
            "evaluation_srt": evaluation_srt,
            "report": report,
            "qa_json": qa_json,
            "render_artifact_path": render_artifact_path,
            "render_artifact": render_artifact,
            "reconciliation": reconciliation,
            "reconciliation_artifact": reconciliation_artifact,
        }

    def materialize_command(self, fixture: dict, out_dir: Path) -> list[str]:
        return [
            sys.executable,
            str(ROOT / "scripts" / "v4_materialize_editor_reconciled.py"),
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
            "--reconciliation",
            str(fixture["reconciliation"]),
            "--reconciliation-artifact",
            str(fixture["reconciliation_artifact"]),
            "--final-srt",
            str(out_dir / "FINAL.srt"),
            "--final-report",
            str(out_dir / "FINAL.csv"),
            "--final-qa",
            str(out_dir / "FINAL.qa.json"),
            "--artifact-out",
            str(out_dir / "FINAL.render.artifact.json"),
        ]

    def test_gap_witness_materializes_and_passes_release_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(root, gap_witness=True)
            out_dir = root / "production"
            result = run_command(self.materialize_command(fixture, out_dir))
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            stdout = json.loads(result.stdout)
            self.assertEqual(stdout["segmentation_authority"], "editor_reconciled")
            self.assertTrue(stdout["production_authority_granted"])
            self.assertGreaterEqual(stdout["rebuttal_witness_count"], 1)

            self.assertEqual(
                (out_dir / "FINAL.srt").read_bytes(),
                fixture["evaluation_srt"].read_bytes(),
            )
            self.assertEqual(
                (out_dir / "FINAL.csv").read_bytes(),
                fixture["report"].read_bytes(),
            )
            qa = json.loads((out_dir / "FINAL.qa.json").read_text(encoding="utf-8"))
            self.assertTrue(qa["publish_ready"])
            self.assertEqual(qa["segmentation_authority"], "editor_reconciled")
            self.assertEqual(qa["editor_topology_resolution"], "rebutted")
            self.assertEqual(qa["release_blocked_reason"], "")

            production_artifact_path = out_dir / "FINAL.render.artifact.json"
            production_artifact = json.loads(production_artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(production_artifact["stage"], "final_render")
            self.assertEqual(
                production_artifact["normalized_config"]["segmentation_authority"],
                "editor_reconciled",
            )
            self.assertTrue(
                production_artifact["normalized_config"]["production_authority_granted"]
            )
            self.assertIn(
                fixture["render_artifact"]["artifact_id"],
                production_artifact["upstream_artifact_ids"],
            )

            release_manifest = out_dir / "release.json"
            release = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_validate_release.py"),
                    "--task-manifest",
                    str(fixture["manifest_path"]),
                    "--final-srt",
                    str(out_dir / "FINAL.srt"),
                    "--report",
                    str(out_dir / "FINAL.csv"),
                    "--qa-json",
                    str(out_dir / "FINAL.qa.json"),
                    "--algorithm-version",
                    __version__,
                    "--upstream-artifact",
                    str(production_artifact_path),
                    "--out-manifest",
                    str(release_manifest),
                ]
            )
            self.assertEqual(release.returncode, 0, msg=release.stderr)
            self.assertEqual(json.loads(release.stdout)["release_status"], "ready")

    def test_crossing_boundary_without_gap_witness_remains_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(root, gap_witness=False)
            result = run_command(self.materialize_command(fixture, root / "production"))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no editor temporal overlap", result.stderr)

    def test_inconsistent_reconciliation_counts_remain_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(root, gap_witness=True)
            reconciliation = json.loads(
                fixture["reconciliation"].read_text(encoding="utf-8")
            )
            reconciliation["result"]["canonical_unassigned_count"] += 1
            write_json_atomic(fixture["reconciliation"], reconciliation)

            old_artifact = json.loads(
                fixture["reconciliation_artifact"].read_text(encoding="utf-8")
            )
            rebuilt = build_artifact_manifest(
                task_fingerprint_sha256=reconciliation["task_fingerprint_sha256"],
                stage="editor_cue_reconciliation_evaluation",
                algorithm_version=__version__,
                outputs=(("editor_cue_reconciliation", fixture["reconciliation"]),),
                normalized_config=old_artifact["normalized_config"],
                upstream_artifact_ids=old_artifact["upstream_artifact_ids"],
                evidence=old_artifact["evidence"],
            )
            write_json_atomic(fixture["reconciliation_artifact"], rebuilt)

            result = run_command(self.materialize_command(fixture, root / "production"))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("canonical_unassigned_count is inconsistent", result.stderr)

    def test_unsupported_canonical_timing_format_remains_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(root, gap_witness=True, timing_format="untimed")
            result = run_command(self.materialize_command(fixture, root / "production"))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("supported explicit timing authority", result.stderr)


if __name__ == "__main__":
    unittest.main()
