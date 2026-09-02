import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import build_artifact_manifest
from lyric_aligner.srt import Cue, cue_id, parse_srt_strict, text_sha256
from lyric_aligner.text.display_policy import (
    DisplayOverride,
    DisplayPolicy,
    DisplayPolicyError,
    DisplayTimingPolicy,
    apply_display_policy,
    apply_display_timing_policy,
    load_display_policy,
    mask_strong_profanity,
)
from task_contract import build_task_manifest, write_json_atomic


ROOT = Path(__file__).resolve().parents[1]
PROFILE_ID = "synthetic-display-profile-id"
PROFILE_VERSION = "synthetic-display-profile-v1"


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
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8-sig")


class DisplayPolicyUnitTests(unittest.TestCase):
    def test_strong_profanity_mask_is_narrow(self):
        masked, count = mask_strong_profanity(
            "fuck FUCKING fuckin fuckin’ shit Bullshit bitch asshole cunt sexy shot bullet damn shitake"
        )
        self.assertEqual(
            masked,
            "f* F* f* f* s* B* b* a* c* sexy shot bullet damn shitake",
        )
        self.assertEqual(count, 9)

    def test_expected_text_mismatch_fails_closed(self):
        override = DisplayOverride(
            occurrence_id="occ-1",
            track_id="track-1",
            canonical_line_index=0,
            expected_text="expected source text",
            display_text="corrected display text",
            reason="synthetic typo",
            reviewer="synthetic-model",
        )
        policy = DisplayPolicy(
            policy_id="policy",
            task_fingerprint_sha256="f" * 64,
            mask_profile="none",
            overrides={override.key: override},
            reviewer_model="synthetic-model",
            timing_policy=None,
        )
        with self.assertRaises(DisplayPolicyError):
            apply_display_policy(
                "unexpected source text",
                occurrence_id="occ-1",
                track_id="track-1",
                canonical_line_index=0,
                policy=policy,
            )

    def test_extreme_unknown_end_trim_only_shortens_matching_cue(self):
        policy = DisplayPolicy(
            policy_id="policy",
            task_fingerprint_sha256="f" * 64,
            mask_profile="none",
            overrides={},
            reviewer_model="synthetic-model",
            timing_policy=DisplayTimingPolicy(
                mode="trim_extreme_unknown_end_v1",
                source_end_basis=frozenset({"next_line_start"}),
                source_duration_at_least_ms=8000,
                max_display_hold_ms=6000,
            ),
        )
        trimmed = apply_display_timing_policy(
            start_ms=2000,
            end_ms=12000,
            end_basis="next_line_start",
            policy=policy,
        )
        self.assertTrue(trimmed.changed)
        self.assertEqual((trimmed.start_ms, trimmed.end_ms), (2000, 8000))
        explicit = apply_display_timing_policy(
            start_ms=2000,
            end_ms=12000,
            end_basis="open_end",
            policy=policy,
        )
        self.assertFalse(explicit.changed)
        self.assertEqual(explicit.end_ms, 12000)

    def test_policy_rejects_non_high_confidence_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "policy.json"
            payload = {
                "schema_version": "display-text-policy-1.0",
                "task_fingerprint_sha256": "f" * 64,
                "mask_profile": "none",
                "model_review": {"reviewer_model": "synthetic-model"},
                "overrides": [
                    {
                        "occurrence_id": "occ-1",
                        "track_id": "track-1",
                        "canonical_line_index": 0,
                        "expected_text": "old",
                        "display_text": "new",
                        "reason": "synthetic typo",
                        "reviewer": "synthetic-model",
                        "confidence": "medium",
                    }
                ],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(DisplayPolicyError):
                load_display_policy(path, expected_task_fingerprint="f" * 64)


class V4DisplayTextPolicyEndToEndTests(unittest.TestCase):
    def build_fixture(self, root: Path) -> dict:
        task_root = root / "private" / "generic-display-policy"
        input_dir = task_root / "input"
        lyrics_dir = input_dir / "lyrics"
        qa_dir = task_root / "qa"
        for directory in (input_dir, lyrics_dir, qa_dir):
            directory.mkdir(parents=True, exist_ok=True)

        source_editor_srt = input_dir / "source.srt"
        write_srt(source_editor_srt, [Cue(1, 0, 1000, "editor evidence")])
        audio = input_dir / "mix.wav"
        audio.write_bytes(b"synthetic-audio")
        song_list = input_dir / "songs.txt"
        song_list.write_text("00:00 Generic Artist - Generic Track\n", encoding="utf-8")
        lyric_file = lyrics_dir / "generic.lrc"
        lyric_file.write_text(
            "[00:00.00]Synthetic dont token\n"
            "[00:01.00]placeholder fuck token\n"
            "[00:02.00]long synthetic line\n",
            encoding="utf-8",
        )

        manifest = build_task_manifest(
            root,
            "generic-display-policy",
            source_srt=source_editor_srt,
            audio=audio,
            song_list=song_list,
            lyrics_dir=lyrics_dir,
        )
        manifest_path = qa_dir / "task_manifest.json"
        write_json_atomic(manifest_path, manifest)
        fingerprint = manifest["task_fingerprint_sha256"]

        production_dir = root / "production"
        production_dir.mkdir()
        source_srt = production_dir / "SOURCE.srt"
        source_cues = [
            Cue(1, 100, 900, "Synthetic dont token"),
            Cue(2, 1000, 1900, "placeholder fuck token"),
            Cue(3, 2000, 12000, "long synthetic line"),
        ]
        write_srt(source_srt, source_cues)

        source_report = production_dir / "SOURCE.audit.csv"
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
        with source_report.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for position, cue in enumerate(source_cues, start=1):
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
                        "end_basis": "next_line_start",
                        "task_fingerprint_sha256": fingerprint,
                        "cue_id": cue_id(position, cue),
                        "text_sha256": text_sha256(cue.text),
                    }
                )

        source_qa = production_dir / "SOURCE.qa.json"
        write_json_atomic(
            source_qa,
            {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "calibration_profile_id": PROFILE_ID,
                "calibration_profile_version": PROFILE_VERSION,
                "passed": True,
                "structurally_valid": True,
                "fully_reviewed": True,
                "publish_ready": True,
                "segmentation_authority": "editor_reconciled",
                "release_blocked_reason": "",
                "review_candidate_count": 0,
                "cue_count": len(source_cues),
            },
        )

        source_artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="final_render",
            algorithm_version=__version__,
            outputs=(
                ("final_srt", source_srt),
                ("audit_csv", source_report),
                ("qa_json", source_qa),
            ),
            normalized_config={
                "calibration_profile_id": PROFILE_ID,
                "calibration_profile_version": PROFILE_VERSION,
                "segmentation_authority": "editor_reconciled",
                "production_authority_granted": True,
                "legacy_fallback": False,
            },
            upstream_artifact_ids=("synthetic-production-upstream",),
            evidence={
                "cue_count": len(source_cues),
                "review_candidate_count": 0,
                "publish_ready": True,
                "segmentation_authority": "editor_reconciled",
                "release_blocked_reason": "",
            },
        )
        source_artifact_path = production_dir / "SOURCE.render.artifact.json"
        write_json_atomic(source_artifact_path, source_artifact)

        policy_path = qa_dir / "display_policy.json"
        write_json_atomic(
            policy_path,
            {
                "schema_version": "display-text-policy-1.0",
                "task_fingerprint_sha256": fingerprint,
                "mask_profile": "strong_profanity_v1",
                "model_review": {
                    "reviewer_model": "synthetic-model",
                    "scope": "synthetic display-only review",
                },
                "timing_policy": {
                    "mode": "trim_extreme_unknown_end_v1",
                    "source_end_basis": ["next_line_start"],
                    "source_duration_at_least_ms": 8000,
                    "max_display_hold_ms": 6000,
                },
                "overrides": [
                    {
                        "occurrence_id": "occ-1",
                        "track_id": "track-1",
                        "canonical_line_index": 0,
                        "expected_text": "Synthetic dont token",
                        "display_text": "Synthetic don't token",
                        "reason": "synthetic high-confidence contraction correction",
                        "reviewer": "synthetic-model",
                        "confidence": "high",
                    }
                ],
            },
        )

        return {
            "manifest_path": manifest_path,
            "fingerprint": fingerprint,
            "source_srt": source_srt,
            "source_report": source_report,
            "source_qa": source_qa,
            "source_artifact_path": source_artifact_path,
            "source_cues": source_cues,
            "policy_path": policy_path,
        }

    def display_command(self, fixture: dict, out_dir: Path) -> list[str]:
        return [
            sys.executable,
            str(ROOT / "scripts" / "v4_apply_display_policy.py"),
            "--task-manifest",
            str(fixture["manifest_path"]),
            "--source-srt",
            str(fixture["source_srt"]),
            "--source-report",
            str(fixture["source_report"]),
            "--source-qa",
            str(fixture["source_qa"]),
            "--source-render-artifact",
            str(fixture["source_artifact_path"]),
            "--display-policy",
            str(fixture["policy_path"]),
            "--final-srt",
            str(out_dir / "FINAL.srt"),
            "--final-report",
            str(out_dir / "FINAL.audit.csv"),
            "--final-qa",
            str(out_dir / "FINAL.qa.json"),
            "--artifact-out",
            str(out_dir / "FINAL.render.artifact.json"),
        ]

    def test_display_stage_preserves_timeline_and_passes_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(root)
            out_dir = root / "display"
            result = run_command(self.display_command(fixture, out_dir))
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            final_cues = parse_srt_strict(out_dir / "FINAL.srt")
            self.assertEqual(
                [(cue.number, cue.start_ms) for cue in final_cues],
                [(cue.number, cue.start_ms) for cue in fixture["source_cues"]],
            )
            self.assertEqual(final_cues[0].end_ms, fixture["source_cues"][0].end_ms)
            self.assertEqual(final_cues[1].end_ms, fixture["source_cues"][1].end_ms)
            self.assertEqual(final_cues[2].end_ms, 8000)
            self.assertEqual(final_cues[0].text, "Synthetic don't token")
            self.assertEqual(final_cues[1].text, "placeholder f* token")
            self.assertEqual(final_cues[2].text, "long synthetic line")

            with (out_dir / "FINAL.audit.csv").open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["canonical_text"], "Synthetic dont token")
            self.assertEqual(rows[0]["display_text"], "Synthetic don't token")
            self.assertEqual(rows[1]["canonical_text"], "placeholder fuck token")
            self.assertEqual(rows[1]["display_text"], "placeholder f* token")
            self.assertEqual(rows[2]["source_end_ms"], "12000")
            self.assertEqual(rows[2]["display_end_ms"], "8000")
            self.assertEqual(rows[2]["display_timing_changed"], "true")

            qa = json.loads((out_dir / "FINAL.qa.json").read_text(encoding="utf-8"))
            self.assertEqual(qa["display_text_changed_count"], 2)
            self.assertEqual(qa["display_text_model_override_count"], 1)
            self.assertEqual(qa["display_text_sensitive_mask_count"], 1)
            self.assertEqual(qa["display_timing_changed_count"], 1)
            self.assertTrue(qa["display_text_canonical_preserved_in_audit"])
            self.assertTrue(qa["display_timing_source_preserved_in_audit"])

            release_path = out_dir / "FINAL.release.json"
            release = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_validate_release.py"),
                    "--task-manifest",
                    str(fixture["manifest_path"]),
                    "--final-srt",
                    str(out_dir / "FINAL.srt"),
                    "--report",
                    str(out_dir / "FINAL.audit.csv"),
                    "--qa-json",
                    str(out_dir / "FINAL.qa.json"),
                    "--algorithm-version",
                    __version__,
                    "--upstream-artifact",
                    str(out_dir / "FINAL.render.artifact.json"),
                    "--out-manifest",
                    str(release_path),
                ]
            )
            self.assertEqual(release.returncode, 0, msg=release.stderr)
            release_payload = json.loads(release_path.read_text(encoding="utf-8"))
            self.assertEqual(release_payload["stage"], "release")

    def test_unmatched_explicit_override_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.build_fixture(root)
            policy = json.loads(fixture["policy_path"].read_text(encoding="utf-8"))
            policy["overrides"][0]["canonical_line_index"] = 99
            fixture["policy_path"].write_text(json.dumps(policy), encoding="utf-8")
            result = run_command(self.display_command(fixture, root / "display"))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must match exactly one final cue", result.stderr)


if __name__ == "__main__":
    unittest.main()
