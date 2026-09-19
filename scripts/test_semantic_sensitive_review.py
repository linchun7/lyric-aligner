import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.review.canonical_splices import file_ref
from lyric_aligner.review.semantic_sensitive import (
    SCHEMA_VERSION,
    build_semantic_sensitive_pack,
    finalize_semantic_sensitive_review,
)
from lyric_aligner.srt import Cue, parse_srt_strict


ROOT = Path(__file__).resolve().parents[1]


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


class SemanticSensitiveReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "pre-final.srt"
        self.songs = self.root / "songs.txt"
        self.pack_path = self.root / "sensitive-pack.json"
        self.review_path = self.root / "sensitive-review.json"
        self.out_srt = self.root / "FINAL.srt"
        self.out_audit = self.root / "FINAL.sensitive.json"
        self.cues = [
            Cue(1, 0, 1000, "It's the Love Shot"),
            Cue(2, 1000, 2000, "never bounce on ya bitch"),
            Cue(3, 2000, 3000, "正常歌词"),
            Cue(4, 3000, 4000, "already b* masked"),
        ]
        write_srt(self.source, self.cues)
        self.songs.write_text("00:00 Artist - Track\n", encoding="utf-8")
        self.pack = build_semantic_sensitive_pack(self.source, song_list=self.songs)
        self.pack_path.write_text(
            json.dumps(self.pack, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def decision(
        self,
        number: int,
        *,
        action: str,
        mask_terms: list[dict] | None = None,
        confidence: str = "high",
        reason: str = "synthetic semantic decision",
    ) -> dict:
        cue = self.cues[number - 1]
        return {
            "cue_number": cue.number,
            "timing": f"{format_time(cue.start_ms)} --> {format_time(cue.end_ms)}",
            "expected_text": cue.text,
            "action": action,
            "mask_terms": mask_terms or [],
            "reason": reason,
            "confidence": confidence,
        }

    def write_review(self, decisions: list[dict], *, full_scan: bool = True) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": "semantic-sensitive-decisions",
            "pack_sha256": file_ref(self.pack_path)["sha256"],
            "source_srt_sha256": self.pack["source_srt"]["sha256"],
            "reviewer_type": "model",
            "reviewer_model": "synthetic-model",
            "full_scan_completed": full_scan,
            "scanned_cue_count": len(self.cues),
            "decisions": decisions,
        }
        self.review_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def passing_decisions(self) -> list[dict]:
        return [
            self.decision(1, action="keep", reason="song title/context should remain"),
            self.decision(
                2,
                action="mask",
                mask_terms=[{"text": "bitch", "occurrence": 1}],
                reason="strong profanity in viewer-facing lyric",
            ),
            self.decision(4, action="keep", reason="already-masked display form retained"),
        ]

    def test_pack_requires_model_decisions_but_never_auto_masks(self):
        self.assertEqual(self.pack["cue_count"], 4)
        self.assertTrue(self.pack["full_scan_required"])
        self.assertEqual(self.pack["required_decision_cue_numbers"], [1, 2, 4])
        self.assertEqual(self.pack["cues"][1]["text"], "never bounce on ya bitch")
        self.assertEqual(self.pack["cues"][1]["song"]["label"], "Artist - Track")
        kinds = {hint["kind"] for hint in self.pack["cues"][1]["hints"]}
        self.assertIn("strong_profanity_candidate", kinds)
        self.assertTrue(
            all(
                hint["automatic_replacement_allowed"] is False
                for cue in self.pack["cues"]
                for hint in cue["hints"]
            )
        )

    def test_high_confidence_model_mask_is_only_display_change(self):
        self.write_review(self.passing_decisions())
        audit = finalize_semantic_sensitive_review(
            self.pack_path,
            self.review_path,
            out_srt=self.out_srt,
            out_audit=self.out_audit,
        )
        final = parse_srt_strict(self.out_srt)
        self.assertEqual([cue.text for cue in final], [
            "It's the Love Shot",
            "never bounce on ya b*",
            "正常歌词",
            "already b* masked",
        ])
        self.assertEqual(
            [(cue.number, cue.start_ms, cue.end_ms) for cue in final],
            [(cue.number, cue.start_ms, cue.end_ms) for cue in self.cues],
        )
        self.assertEqual(audit["masked_cue_count"], 1)
        self.assertEqual(audit["masked_term_count"], 1)
        self.assertTrue(audit["timeline_unchanged"])
        self.assertTrue(audit["mask_only_display_changes"])

    def test_review_action_blocks_finalization(self):
        decisions = self.passing_decisions()
        decisions[1] = self.decision(
            2,
            action="review",
            confidence="medium",
            reason="context still ambiguous",
        )
        self.write_review(decisions)
        with self.assertRaisesRegex(ValueError, "unresolved"):
            finalize_semantic_sensitive_review(
                self.pack_path,
                self.review_path,
                out_srt=self.out_srt,
                out_audit=self.out_audit,
            )
        self.assertFalse(self.out_srt.exists())
        self.assertFalse(self.out_audit.exists())

    def test_every_hint_candidate_needs_explicit_decision(self):
        self.write_review(self.passing_decisions()[:-1])
        with self.assertRaisesRegex(ValueError, "incomplete semantic sensitive candidate decisions"):
            finalize_semantic_sensitive_review(
                self.pack_path,
                self.review_path,
                out_srt=self.out_srt,
                out_audit=self.out_audit,
            )

    def test_full_scan_attestation_is_required(self):
        self.write_review(self.passing_decisions(), full_scan=False)
        with self.assertRaisesRegex(ValueError, "full semantic sensitive SRT scan is required"):
            finalize_semantic_sensitive_review(
                self.pack_path,
                self.review_path,
                out_srt=self.out_srt,
                out_audit=self.out_audit,
            )

    def test_scanned_cue_count_must_equal_pack_cue_count(self):
        self.write_review(self.passing_decisions())
        payload = json.loads(self.review_path.read_text(encoding="utf-8"))
        payload["scanned_cue_count"] -= 1
        self.review_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            ValueError,
            "scanned_cue_count must equal pack cue_count",
        ):
            finalize_semantic_sensitive_review(
                self.pack_path,
                self.review_path,
                out_srt=self.out_srt,
                out_audit=self.out_audit,
            )

    def test_unhinted_model_mask_is_allowed_but_free_rewrite_is_not(self):
        decisions = self.passing_decisions()
        decisions.append(
            self.decision(
                3,
                action="mask",
                mask_terms=[{"text": "歌词", "occurrence": 1}],
                reason="synthetic model-found contextual issue",
            )
        )
        self.write_review(decisions)
        finalize_semantic_sensitive_review(
            self.pack_path,
            self.review_path,
            out_srt=self.out_srt,
            out_audit=self.out_audit,
        )
        self.assertEqual(parse_srt_strict(self.out_srt)[2].text, "正常歌*")

        self.out_srt.unlink()
        self.out_audit.unlink()
        decisions[-1]["mask_terms"] = [{"text": "词", "occurrence": 1}]
        self.write_review(decisions)
        finalize_semantic_sensitive_review(
            self.pack_path,
            self.review_path,
            out_srt=self.out_srt,
            out_audit=self.out_audit,
        )
        self.assertEqual(parse_srt_strict(self.out_srt)[2].text, "正常歌词*")

        self.out_srt.unlink()
        self.out_audit.unlink()
        decisions[-1]["mask_terms"] = [{"text": "不存在", "occurrence": 1}]
        self.write_review(decisions)
        with self.assertRaisesRegex(ValueError, "mask term occurrence not found"):
            finalize_semantic_sensitive_review(
                self.pack_path,
                self.review_path,
                out_srt=self.out_srt,
                out_audit=self.out_audit,
            )

    def test_stale_source_after_pack_fails_closed(self):
        self.write_review(self.passing_decisions())
        write_srt(
            self.source,
            [
                self.cues[0],
                Cue(2, 1000, 2000, "changed after review pack"),
                self.cues[2],
                self.cues[3],
            ],
        )
        with self.assertRaisesRegex(ValueError, "stale bound file"):
            finalize_semantic_sensitive_review(
                self.pack_path,
                self.review_path,
                out_srt=self.out_srt,
                out_audit=self.out_audit,
            )

    def test_cli_pack_and_finalize(self):
        cli_pack = self.root / "cli-pack.json"
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "v4_review_safe_final.py"),
                "sensitive-pack",
                "--source-srt",
                str(self.source),
                "--song-list",
                str(self.songs),
                "--out",
                str(cli_pack),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        cli_payload = json.loads(cli_pack.read_text(encoding="utf-8"))
        review = {
            "schema_version": SCHEMA_VERSION,
            "kind": "semantic-sensitive-decisions",
            "pack_sha256": file_ref(cli_pack)["sha256"],
            "source_srt_sha256": cli_payload["source_srt"]["sha256"],
            "reviewer_type": "model",
            "reviewer_model": "synthetic-model",
            "full_scan_completed": True,
            "scanned_cue_count": len(self.cues),
            "decisions": self.passing_decisions(),
        }
        cli_review = self.root / "cli-review.json"
        cli_review.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
        cli_final = self.root / "cli-final.srt"
        cli_audit = self.root / "cli-final.json"
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "v4_review_safe_final.py"),
                "sensitive-finalize",
                "--pack",
                str(cli_pack),
                "--review",
                str(cli_review),
                "--out-srt",
                str(cli_final),
                "--out-audit",
                str(cli_audit),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        message = json.loads(result.stdout)
        self.assertEqual(message["mutation_scope"], "mask_only_display_text")
        self.assertEqual(parse_srt_strict(cli_final)[1].text, "never bounce on ya b*")


if __name__ == "__main__":
    unittest.main()
