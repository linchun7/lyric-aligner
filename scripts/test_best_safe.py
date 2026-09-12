import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.pipeline.best_safe import (
    BestSafeError,
    _apply_text_adjudication,
    _multi_cue_latin_ownership_preserved,
    build_best_safe,
)
from lyric_aligner.srt import parse_srt_strict


FP = "a" * 64


def write_srt(path: Path, rows):
    blocks = []
    for number, start, end, text in rows:
        def fmt(ms):
            h, rem = divmod(ms, 3_600_000)
            m, rem = divmod(rem, 60_000)
            s, milli = divmod(rem, 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"
        blocks.append(f"{number}\n{fmt(start)} --> {fmt(end)}\n{text}")
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def write_max_audit(path: Path, rows):
    fields = [
        "ordinal",
        "occurrence_id",
        "task_fingerprint_sha256",
        "timing_basis",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


class BestSafeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.smart = self.root / "smart.srt"
        self.max = self.root / "max.srt"
        self.audit = self.root / "max.csv"
        self.semantic = self.root / "semantic.json"
        self.songs = self.root / "songs.txt"
        self.transitions = self.root / "transitions.json"
        self.out = self.root / "out"
        self.songs.write_text("00:00 A\n00:10 B\n00:20 C\n", encoding="utf-8")
        write_srt(
            self.smart,
            [
                (1, 1000, 9500, "A smart"),
                (2, 10000, 19500, "B smart"),
                (3, 20000, 29000, "C smart"),
            ],
        )
        write_srt(
            self.max,
            [
                (1, 1000, 9400, "A max"),
                (2, 9000, 19600, "B max"),
                (3, 20000, 28500, "C max"),
            ],
        )
        write_max_audit(
            self.audit,
            [
                {"ordinal": 1, "occurrence_id": "oa", "task_fingerprint_sha256": FP, "timing_basis": "max"},
                {"ordinal": 2, "occurrence_id": "ob", "task_fingerprint_sha256": FP, "timing_basis": "max"},
                {"ordinal": 3, "occurrence_id": "oc", "task_fingerprint_sha256": FP, "timing_basis": "max"},
            ],
        )
        self.semantic.write_text(
            json.dumps(
                {
                    "task_fingerprint_sha256": FP,
                    "final_sync": {
                        "tracks": [
                            {"ordinal": 1, "passed": False, "errors": ["blocked"], "evidence_basis": "insufficient"},
                            {"ordinal": 2, "passed": True, "errors": [], "evidence_basis": "independent"},
                            {"ordinal": 3, "passed": False, "errors": ["blocked"], "evidence_basis": "insufficient"},
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def _transition(self, action=None):
        items = []
        if action:
            items.append(
                {
                    "issue": {"left_occurrence_id": "oa", "right_occurrence_id": "ob"},
                    "decision": {"action": action},
                }
            )
        self.transitions.write_text(
            json.dumps({"task_fingerprint_sha256": FP, "review_items": items}),
            encoding="utf-8",
        )

    def _run(self):
        self.out.mkdir(exist_ok=True)
        return build_best_safe(
            smart_srt=self.smart,
            max_srt=self.max,
            max_audit=self.audit,
            semantic_sync=self.semantic,
            song_list=self.songs,
            transition_authorization=self.transitions,
            out_srt=self.out / "FINAL.srt",
            out_audit=self.out / "FINAL.audit.csv",
            out_qa=self.out / "QA.json",
            out_artifact=self.out / "ARTIFACT.json",
            expected_task_fingerprint=FP,
        )

    def test_semantic_pass_is_candidate_only_and_smart_floor_is_unchanged(self):
        self._transition("resolved_clear")
        qa = self._run()
        cues = parse_srt_strict(self.out / "FINAL.srt")
        self.assertEqual([cue.text for cue in cues], ["A smart", "B smart", "C smart"])
        self.assertEqual([(cue.start_ms, cue.end_ms) for cue in cues], [(1000, 9500), (10000, 19500), (20000, 29000)])
        self.assertEqual(
            qa["selected_track_modes"],
            {"1": "smart_timing_floor", "2": "smart_timing_floor", "3": "smart_timing_floor"},
        )
        self.assertEqual(qa["max_candidate_track_ordinals"], [2])
        self.assertEqual(qa["timing_floor"]["timing_changed_boundary_count"], 0)
        self.assertEqual(qa["timing_floor"]["unsupported_timing_change_count"], 0)
        self.assertEqual(qa["transition_adjustments"], [])
        self.assertTrue(qa["publish_ready"])

    def test_transition_authority_cannot_silently_mutate_smart_timing(self):
        write_srt(
            self.smart,
            [
                (1, 1000, 10500, "A smart"),
                (2, 10000, 19500, "B smart"),
                (3, 20000, 29000, "C smart"),
            ],
        )
        self._transition("resolved_clear")
        qa = self._run()
        cues = parse_srt_strict(self.out / "FINAL.srt")
        self.assertEqual(cues[0].end_ms, 10500)
        self.assertEqual(cues[1].start_ms, 10000)
        self.assertEqual(qa["geometry"]["cross_track_overlap_count"], 1)
        self.assertEqual(qa["transition_adjustments"], [])
        self.assertEqual(qa["transition_authority_mode"], "diagnostic_only_no_smart_timing_mutation")

    def test_human_timing_truth_guards_smart_boundary(self):
        self._transition(None)
        truth = self.root / "timing-truth.json"
        truth.write_text(
            json.dumps(
                {
                    "schema_version": "best-safe-timing-truth-1.0",
                    "task_fingerprint_sha256": FP,
                    "smart_srt_sha256": hashlib.sha256(self.smart.read_bytes()).hexdigest(),
                    "points": [
                        {
                            "id": "b-start",
                            "track_ordinal": 2,
                            "source_cue_number": 2,
                            "boundary": "start",
                            "truth_ms": 10000,
                            "tolerance_ms": 17,
                            "authority": "human_truth",
                            "expected_text": "B smart",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.out.mkdir(exist_ok=True)
        qa = build_best_safe(
            smart_srt=self.smart,
            max_srt=self.max,
            max_audit=self.audit,
            semantic_sync=self.semantic,
            song_list=self.songs,
            transition_authorization=self.transitions,
            timing_truth=truth,
            out_srt=self.out / "FINAL.srt",
            out_audit=self.out / "FINAL.audit.csv",
            out_qa=self.out / "QA.json",
            out_artifact=self.out / "ARTIFACT.json",
            expected_task_fingerprint=FP,
        )
        check = qa["timing_floor"]["truth_checks"][0]
        self.assertTrue(check["passed"])
        self.assertEqual(check["actual_ms"], 10000)
        self.assertEqual(qa["timing_floor"]["timing_changed_boundary_count"], 0)

    def test_human_truth_difference_requires_explicit_boundary_promotion(self):
        self._transition(None)
        truth = self.root / "timing-truth-needs-promotion.json"
        truth.write_text(
            json.dumps(
                {
                    "schema_version": "best-safe-timing-truth-1.0",
                    "task_fingerprint_sha256": FP,
                    "smart_srt_sha256": hashlib.sha256(self.smart.read_bytes()).hexdigest(),
                    "points": [
                        {
                            "id": "b-start-corrected",
                            "track_ordinal": 2,
                            "source_cue_number": 2,
                            "boundary": "start",
                            "truth_ms": 10300,
                            "tolerance_ms": 17,
                            "authority": "human_truth",
                            "expected_text": "B smart",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.out.mkdir(exist_ok=True)
        with self.assertRaises(BestSafeError):
            build_best_safe(
                smart_srt=self.smart,
                max_srt=self.max,
                max_audit=self.audit,
                semantic_sync=self.semantic,
                song_list=self.songs,
                transition_authorization=self.transitions,
                timing_truth=truth,
                out_srt=self.out / "FINAL.srt",
                out_audit=self.out / "FINAL.audit.csv",
                out_qa=self.out / "QA.json",
                out_artifact=self.out / "ARTIFACT.json",
                expected_task_fingerprint=FP,
            )

    def test_human_truth_bound_promotion_is_the_only_allowed_timing_change(self):
        self._transition(None)
        truth = self.root / "timing-truth-promotion.json"
        truth.write_text(
            json.dumps(
                {
                    "schema_version": "best-safe-timing-truth-1.0",
                    "task_fingerprint_sha256": FP,
                    "smart_srt_sha256": hashlib.sha256(self.smart.read_bytes()).hexdigest(),
                    "points": [
                        {
                            "id": "b-start-corrected",
                            "track_ordinal": 2,
                            "source_cue_number": 2,
                            "boundary": "start",
                            "truth_ms": 10300,
                            "tolerance_ms": 17,
                            "authority": "human_truth",
                            "expected_text": "B smart",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        promotions = self.root / "timing-promotions.json"
        promotions.write_text(
            json.dumps(
                {
                    "schema_version": "best-safe-timing-promotions-1.0",
                    "task_fingerprint_sha256": FP,
                    "smart_srt_sha256": hashlib.sha256(self.smart.read_bytes()).hexdigest(),
                    "promotions": [
                        {
                            "track_ordinal": 2,
                            "source_cue_number": 2,
                            "boundary": "start",
                            "expected_smart_ms": 10000,
                            "promoted_ms": 10300,
                            "authority_type": "human_truth",
                            "truth_id": "b-start-corrected",
                            "expected_text": "B smart",
                            "reason": "human_verified_boundary",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.out.mkdir(exist_ok=True)
        qa = build_best_safe(
            smart_srt=self.smart,
            max_srt=self.max,
            max_audit=self.audit,
            semantic_sync=self.semantic,
            song_list=self.songs,
            transition_authorization=self.transitions,
            timing_truth=truth,
            timing_promotions=promotions,
            out_srt=self.out / "FINAL.srt",
            out_audit=self.out / "FINAL.audit.csv",
            out_qa=self.out / "QA.json",
            out_artifact=self.out / "ARTIFACT.json",
            expected_task_fingerprint=FP,
        )
        cues = parse_srt_strict(self.out / "FINAL.srt")
        self.assertEqual(cues[1].start_ms, 10300)
        self.assertEqual(qa["timing_floor"]["authorized_promotion_count"], 1)
        self.assertEqual(qa["timing_floor"]["timing_changed_boundary_count"], 1)
        self.assertEqual(qa["timing_floor"]["unsupported_timing_change_count"], 0)
        self.assertEqual(qa["timing_floor"]["truth_failure_count"], 0)

    def test_machine_only_timing_promotion_is_rejected(self):
        self._transition(None)
        promotions = self.root / "timing-promotions-machine.json"
        promotions.write_text(
            json.dumps(
                {
                    "schema_version": "best-safe-timing-promotions-1.0",
                    "task_fingerprint_sha256": FP,
                    "smart_srt_sha256": hashlib.sha256(self.smart.read_bytes()).hexdigest(),
                    "promotions": [
                        {
                            "track_ordinal": 2,
                            "source_cue_number": 2,
                            "boundary": "start",
                            "expected_smart_ms": 10000,
                            "promoted_ms": 10300,
                            "authority_type": "max_semantic_pass",
                            "truth_id": "",
                            "expected_text": "B smart",
                            "reason": "track_pass_is_not_boundary_truth",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.out.mkdir(exist_ok=True)
        with self.assertRaises(BestSafeError):
            build_best_safe(
                smart_srt=self.smart,
                max_srt=self.max,
                max_audit=self.audit,
                semantic_sync=self.semantic,
                song_list=self.songs,
                transition_authorization=self.transitions,
                timing_promotions=promotions,
                out_srt=self.out / "FINAL.srt",
                out_audit=self.out / "FINAL.audit.csv",
                out_qa=self.out / "QA.json",
                out_artifact=self.out / "ARTIFACT.json",
                expected_task_fingerprint=FP,
            )

    def test_smart_canonical_identity_overrides_rounded_song_boundary(self):
        self._transition("resolved_clear")
        write_srt(
            self.smart,
            [
                (1, 1000, 9500, "Asmart"),
                (2, 9900, 19500, "B smart"),
                (3, 20000, 29000, "C smart"),
            ],
        )
        lyrics = []
        for name, text in (("A", "A smart"), ("B", "B smart"), ("C", "C smart")):
            path = self.root / f"{name}.lrc"
            path.write_text(f"[00:00.000]{text}\n", encoding="utf-8")
            lyrics.append(path)
        smart_report = self.root / "smart.json"
        smart_report.write_text(
            json.dumps(
                {
                    "text_decisions": [
                        {"cue_ordinal": 0, "action": "unchanged", "canonical_span": [0, 1]},
                        {"cue_ordinal": 1, "action": "unchanged", "canonical_span": [1, 2]},
                        {"cue_ordinal": 2, "action": "unchanged", "canonical_span": [2, 3]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.out.mkdir(exist_ok=True)
        qa = build_best_safe(
            smart_srt=self.smart,
            smart_report=smart_report,
            canonical_lyrics=lyrics,
            max_srt=self.max,
            max_audit=self.audit,
            semantic_sync=self.semantic,
            song_list=self.songs,
            transition_authorization=self.transitions,
            out_srt=self.out / "FINAL.srt",
            out_audit=self.out / "FINAL.audit.csv",
            out_qa=self.out / "QA.json",
            out_artifact=self.out / "ARTIFACT.json",
            expected_task_fingerprint=FP,
        )
        self.assertEqual(qa["smart_track_identity"]["canonical_identity_bound_cue_count"], 3)
        self.assertEqual(qa["smart_track_identity"]["time_boundary_disagreement_count"], 1)
        disagreement = qa["smart_track_identity"]["time_boundary_disagreements"][0]
        self.assertEqual(disagreement["identity_track_ordinal"], 2)
        self.assertEqual(disagreement["time_track_ordinal"], 1)
        final_cues = parse_srt_strict(self.out / "FINAL.srt")
        self.assertEqual(final_cues[0].text, "Asmart")
        self.assertEqual(qa["smart_track_identity"]["canonical_presentation_change_count"], 0)

    def test_canonical_presentation_auto_restores_only_explicit_mask(self):
        self._transition("resolved_clear")
        write_srt(
            self.smart,
            [
                (1, 1000, 9500, "You gotta turn the up"),
                (2, 10000, 19500, "B smart"),
                (3, 20000, 29000, "C smart"),
            ],
        )
        lyrics = []
        for name, text in (("A", "You gotta turn the **** up"), ("B", "B smart"), ("C", "C smart")):
            path = self.root / f"mask-{name}.lrc"
            path.write_text(f"[00:00.000]{text}\n", encoding="utf-8")
            lyrics.append(path)
        smart_report = self.root / "smart-mask.json"
        smart_report.write_text(
            json.dumps(
                {
                    "text_decisions": [
                        {"cue_ordinal": 0, "action": "unchanged", "canonical_span": [0, 1]},
                        {"cue_ordinal": 1, "action": "unchanged", "canonical_span": [1, 2]},
                        {"cue_ordinal": 2, "action": "unchanged", "canonical_span": [2, 3]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.out.mkdir(exist_ok=True)
        qa = build_best_safe(
            smart_srt=self.smart,
            smart_report=smart_report,
            canonical_lyrics=lyrics,
            max_srt=self.max,
            max_audit=self.audit,
            semantic_sync=self.semantic,
            song_list=self.songs,
            transition_authorization=self.transitions,
            out_srt=self.out / "FINAL.srt",
            out_audit=self.out / "FINAL.audit.csv",
            out_qa=self.out / "QA.json",
            out_artifact=self.out / "ARTIFACT.json",
            expected_task_fingerprint=FP,
        )
        final_cues = parse_srt_strict(self.out / "FINAL.srt")
        self.assertEqual(final_cues[0].text, "You gotta turn the **** up")
        self.assertEqual(qa["smart_track_identity"]["canonical_presentation_change_count"], 1)
        self.assertEqual(
            qa["smart_track_identity"]["canonical_presentation_changes"][0]["reason"],
            "single_line_canonical_explicit_mask_restore",
        )

    def test_display_override_is_model_proposed_but_normalized_equivalent(self):
        self._transition("resolved_clear")
        overrides = self.root / "display.json"
        overrides.write_text(
            json.dumps(
                {
                    "schema_version": "best-safe-display-overrides-1.0",
                    "task_fingerprint_sha256": FP,
                    "reviewer_model": "GPT-5.6 Sol",
                    "overrides": [
                        {
                            "track_ordinal": 2,
                            "source_cue_number": 2,
                            "expected_text": "B smart",
                            "display_text": "B SMART",
                            "reason": "presentation_case_only",
                            "confidence": "high",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.out.mkdir(exist_ok=True)
        qa = build_best_safe(
            smart_srt=self.smart,
            max_srt=self.max,
            max_audit=self.audit,
            semantic_sync=self.semantic,
            song_list=self.songs,
            transition_authorization=self.transitions,
            display_overrides=overrides,
            out_srt=self.out / "FINAL.srt",
            out_audit=self.out / "FINAL.audit.csv",
            out_qa=self.out / "QA.json",
            out_artifact=self.out / "ARTIFACT.json",
            expected_task_fingerprint=FP,
        )
        cues = parse_srt_strict(self.out / "FINAL.srt")
        self.assertEqual(cues[1].text, "B SMART")
        self.assertEqual(qa["display_overrides"]["applied_count"], 1)
        self.assertEqual(qa["display_overrides"]["reviewer_model"], "GPT-5.6 Sol")

    def test_display_override_cannot_change_lexical_content(self):
        self._transition("resolved_clear")
        overrides = self.root / "display.json"
        overrides.write_text(
            json.dumps(
                {
                    "schema_version": "best-safe-display-overrides-1.0",
                    "task_fingerprint_sha256": FP,
                    "reviewer_model": "GPT-5.6 Sol",
                    "overrides": [
                        {
                            "track_ordinal": 2,
                            "source_cue_number": 2,
                            "expected_text": "B smart",
                            "display_text": "B different",
                            "reason": "invalid_lexical_change",
                            "confidence": "high",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.out.mkdir(exist_ok=True)
        with self.assertRaises(BestSafeError):
            build_best_safe(
                smart_srt=self.smart,
                max_srt=self.max,
                max_audit=self.audit,
                semantic_sync=self.semantic,
                song_list=self.songs,
                transition_authorization=self.transitions,
                display_overrides=overrides,
                out_srt=self.out / "FINAL.srt",
                out_audit=self.out / "FINAL.audit.csv",
                out_qa=self.out / "QA.json",
                out_artifact=self.out / "ARTIFACT.json",
                expected_task_fingerprint=FP,
            )

    def test_multi_cue_latin_ownership_allows_in_place_word_substitutions(self):
        self.assertTrue(
            _multi_cue_latin_ownership_preserved(
                ["to those who are free", "the mind shall be key"],
                ["To those who are free", "their minds shall be key"],
            )
        )
        self.assertTrue(
            _multi_cue_latin_ownership_preserved(
                ["we say oh we oh we oh we oh", "we say oh we oh we oh we oh"],
                ["We sayin' oh we oh we oh we oh", "We sayin' oh we oh we oh we oh"],
            )
        )

    def test_multi_cue_latin_ownership_rejects_token_insertion_or_repartition(self):
        self.assertFalse(
            _multi_cue_latin_ownership_preserved(
                ["do you believe", "can you receive it"],
                ["Do you believe", "it can you receive it"],
            )
        )
        self.assertFalse(
            _multi_cue_latin_ownership_preserved(
                ["hey yo rock it out rock it out", "if you know what we talking bout"],
                ["Hey yo Rock it out and rock it", "nowIf you know what we talking bout"],
            )
        )

    def test_text_adjudication_requires_complete_smart_review_coverage(self):
        smart = self.root / "text-smart-coverage.srt"
        write_srt(
            smart,
            [
                (1, 1000, 2000, "Left anchor"),
                (2, 2100, 2600, "Review one"),
                (3, 2700, 3200, "Review two"),
                (4, 3300, 4200, "Right anchor"),
            ],
        )
        lyric = self.root / "TextTrackCoverage.lrc"
        lyric.write_text(
            "[00:01.000]Left anchor\n[00:02.000]Review one\n[00:02.500]Review two\n[00:03.000]Right anchor\n",
            encoding="utf-8",
        )
        smart_report = self.root / "text-smart-coverage.json"
        smart_report.write_text(
            json.dumps(
                {
                    "text_decisions": [
                        {"cue_ordinal": 0, "action": "replace", "canonical_span": [0, 1]},
                        {"cue_ordinal": 1, "action": "review", "canonical_span": [1, 2]},
                        {"cue_ordinal": 2, "action": "review", "canonical_span": [2, 3]},
                        {"cue_ordinal": 3, "action": "replace", "canonical_span": [3, 4]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        ledger = self.root / "text-adjudication-coverage.json"
        ledger.write_text(
            json.dumps(
                {
                    "schema_version": "best-safe-text-adjudication-1.0",
                    "task_fingerprint_sha256": FP,
                    "smart_srt_sha256": hashlib.sha256(smart.read_bytes()).hexdigest(),
                    "smart_report_sha256": hashlib.sha256(smart_report.read_bytes()).hexdigest(),
                    "canonical_lyrics_sha256": [hashlib.sha256(lyric.read_bytes()).hexdigest()],
                    "reviewer_model": "GPT-5.6 Sol",
                    "review_region_count": 1,
                    "model_keep_smart_regions": [
                        {"region_id": 1, "cue_ordinals": [1], "reason": "keep first review"}
                    ],
                    "proposals": [],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(BestSafeError):
            _apply_text_adjudication(
                cue_texts=["Left anchor", "Review one", "Review two", "Right anchor"],
                smart_srt=smart,
                smart_report=smart_report,
                canonical_lyrics=[lyric],
                proposal_path=ledger,
                task_fingerprint=FP,
            )

    def test_text_adjudication_applies_only_exact_bounded_canonical_gap(self):
        smart = self.root / "text-smart.srt"
        write_srt(
            smart,
            [
                (1, 1000, 2000, "Left anchor"),
                (2, 2100, 3100, "Corret words"),
                (3, 3200, 4200, "Right anchor"),
            ],
        )
        lyric = self.root / "TextTrack.lrc"
        lyric.write_text(
            "[00:01.000]Left anchor\n[00:02.000]Correct words\n[00:03.000]Right anchor\n",
            encoding="utf-8",
        )
        smart_report = self.root / "text-smart.json"
        smart_report.write_text(
            json.dumps(
                {
                    "text_decisions": [
                        {"cue_ordinal": 0, "action": "replace", "canonical_span": [0, 1]},
                        {"cue_ordinal": 1, "action": "review", "canonical_span": [1, 2]},
                        {"cue_ordinal": 2, "action": "replace", "canonical_span": [2, 3]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        proposal = self.root / "text-adjudication.json"
        proposal.write_text(
            json.dumps(
                {
                    "schema_version": "best-safe-text-adjudication-1.0",
                    "task_fingerprint_sha256": FP,
                    "smart_srt_sha256": hashlib.sha256(smart.read_bytes()).hexdigest(),
                    "smart_report_sha256": hashlib.sha256(smart_report.read_bytes()).hexdigest(),
                    "canonical_lyrics_sha256": [hashlib.sha256(lyric.read_bytes()).hexdigest()],
                    "reviewer_model": "GPT-5.6 Sol",
                    "proposals": [
                        {
                            "action": "apply_canonical_gap",
                            "cue_ordinals": [1],
                            "canonical_span": [1, 2],
                            "expected_texts": ["Corret words"],
                            "reason": "bounded_semantic_correction",
                            "confidence": "high",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        values, report = _apply_text_adjudication(
            cue_texts=["Left anchor", "Corret words", "Right anchor"],
            smart_srt=smart,
            smart_report=smart_report,
            canonical_lyrics=[lyric],
            proposal_path=proposal,
            task_fingerprint=FP,
        )
        self.assertEqual(values, ["Left anchor", "Correct words", "Right anchor"])
        self.assertEqual(report["applied_region_count"], 1)
        self.assertEqual(report["rejected_region_count"], 0)

    def test_text_adjudication_rejects_unobserved_extra_canonical_line(self):
        smart = self.root / "text-smart-extra.srt"
        write_srt(
            smart,
            [
                (1, 1000, 2000, "Left anchor"),
                (2, 2100, 3100, "Dance the night away"),
                (3, 3200, 4200, "Right anchor"),
            ],
        )
        lyric = self.root / "TextTrackExtra.lrc"
        lyric.write_text(
            "[00:01.000]Left anchor\n[00:02.000]Dale\n[00:02.200]Dance the night away\n[00:03.000]Right anchor\n",
            encoding="utf-8",
        )
        smart_report = self.root / "text-smart-extra.json"
        smart_report.write_text(
            json.dumps(
                {
                    "text_decisions": [
                        {"cue_ordinal": 0, "action": "replace", "canonical_span": [0, 1]},
                        {"cue_ordinal": 1, "action": "review", "canonical_span": [1, 3]},
                        {"cue_ordinal": 2, "action": "replace", "canonical_span": [3, 4]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        proposal = self.root / "text-adjudication-extra.json"
        proposal.write_text(
            json.dumps(
                {
                    "schema_version": "best-safe-text-adjudication-1.0",
                    "task_fingerprint_sha256": FP,
                    "smart_srt_sha256": hashlib.sha256(smart.read_bytes()).hexdigest(),
                    "smart_report_sha256": hashlib.sha256(smart_report.read_bytes()).hexdigest(),
                    "canonical_lyrics_sha256": [hashlib.sha256(lyric.read_bytes()).hexdigest()],
                    "reviewer_model": "GPT-5.6 Sol",
                    "proposals": [
                        {
                            "action": "apply_canonical_gap",
                            "cue_ordinals": [1],
                            "canonical_span": [1, 3],
                            "expected_texts": ["Dance the night away"],
                            "reason": "model_suggested_extra_interjection",
                            "confidence": "high",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        values, report = _apply_text_adjudication(
            cue_texts=["Left anchor", "Dance the night away", "Right anchor"],
            smart_srt=smart,
            smart_report=smart_report,
            canonical_lyrics=[lyric],
            proposal_path=proposal,
            task_fingerprint=FP,
        )
        self.assertEqual(values, ["Left anchor", "Dance the night away", "Right anchor"])
        self.assertEqual(report["applied_region_count"], 0)
        self.assertEqual(report["rejected_region_count"], 1)
        self.assertTrue(report["rejected"][0]["reason"].startswith("deterministic_multi_line_"))

    def test_text_adjudication_keeps_smart_when_normalized_stream_is_already_equal(self):
        smart = self.root / "text-smart-presentation.srt"
        write_srt(
            smart,
            [
                (1, 1000, 2000, "Left anchor"),
                (2, 2100, 3100, "Hello world"),
                (3, 3200, 4200, "Right anchor"),
            ],
        )
        lyric = self.root / "TextTrackPresentation.lrc"
        lyric.write_text(
            "[00:01.000]Left anchor\n[00:02.000]Hello, world\n[00:03.000]Right anchor\n",
            encoding="utf-8",
        )
        smart_report = self.root / "text-smart-presentation.json"
        smart_report.write_text(
            json.dumps(
                {
                    "text_decisions": [
                        {"cue_ordinal": 0, "action": "replace", "canonical_span": [0, 1]},
                        {"cue_ordinal": 1, "action": "review", "canonical_span": [1, 2]},
                        {"cue_ordinal": 2, "action": "replace", "canonical_span": [2, 3]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        proposal = self.root / "text-adjudication-presentation.json"
        proposal.write_text(
            json.dumps(
                {
                    "schema_version": "best-safe-text-adjudication-1.0",
                    "task_fingerprint_sha256": FP,
                    "smart_srt_sha256": hashlib.sha256(smart.read_bytes()).hexdigest(),
                    "smart_report_sha256": hashlib.sha256(smart_report.read_bytes()).hexdigest(),
                    "canonical_lyrics_sha256": [hashlib.sha256(lyric.read_bytes()).hexdigest()],
                    "reviewer_model": "GPT-5.6 Sol",
                    "proposals": [
                        {
                            "action": "apply_canonical_gap",
                            "cue_ordinals": [1],
                            "canonical_span": [1, 2],
                            "expected_texts": ["Hello world"],
                            "reason": "presentation_only",
                            "confidence": "high",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        values, report = _apply_text_adjudication(
            cue_texts=["Left anchor", "Hello world", "Right anchor"],
            smart_srt=smart,
            smart_report=smart_report,
            canonical_lyrics=[lyric],
            proposal_path=proposal,
            task_fingerprint=FP,
        )
        self.assertEqual(values, ["Left anchor", "Hello world", "Right anchor"])
        self.assertEqual(report["applied_region_count"], 0)
        self.assertEqual(report["rejected"][0]["reason"], "deterministic_normalized_stream_already_equal_keep_smart_presentation")

    def test_text_adjudication_rejects_freeform_and_stale_smart(self):
        smart = self.root / "text-smart.srt"
        write_srt(
            smart,
            [
                (1, 1000, 2000, "Left anchor"),
                (2, 2100, 3100, "Corret words"),
                (3, 3200, 4200, "Right anchor"),
            ],
        )
        lyric = self.root / "TextTrack.lrc"
        lyric.write_text(
            "[00:01.000]Left anchor\n[00:02.000]Correct words\n[00:03.000]Right anchor\n",
            encoding="utf-8",
        )
        smart_report = self.root / "text-smart.json"
        smart_report.write_text(
            json.dumps(
                {
                    "text_decisions": [
                        {"cue_ordinal": 0, "action": "replace", "canonical_span": [0, 1]},
                        {"cue_ordinal": 1, "action": "review", "canonical_span": [1, 2]},
                        {"cue_ordinal": 2, "action": "replace", "canonical_span": [2, 3]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        proposal = self.root / "text-adjudication.json"
        base = {
            "schema_version": "best-safe-text-adjudication-1.0",
            "task_fingerprint_sha256": FP,
            "smart_srt_sha256": hashlib.sha256(smart.read_bytes()).hexdigest(),
            "smart_report_sha256": hashlib.sha256(smart_report.read_bytes()).hexdigest(),
            "canonical_lyrics_sha256": [hashlib.sha256(lyric.read_bytes()).hexdigest()],
            "reviewer_model": "GPT-5.6 Sol",
            "proposals": [
                {
                    "action": "apply_canonical_gap",
                    "cue_ordinals": [1],
                    "canonical_span": [1, 2],
                    "expected_texts": ["Corret words"],
                    "reason": "bounded_semantic_correction",
                    "confidence": "high",
                }
            ],
        }
        stale = dict(base)
        stale["smart_srt_sha256"] = "b" * 64
        proposal.write_text(json.dumps(stale), encoding="utf-8")
        with self.assertRaises(BestSafeError):
            _apply_text_adjudication(
                cue_texts=["Left anchor", "Corret words", "Right anchor"],
                smart_srt=smart,
                smart_report=smart_report,
                canonical_lyrics=[lyric],
                proposal_path=proposal,
                task_fingerprint=FP,
            )
        stale_report = json.loads(json.dumps(base))
        stale_report["smart_report_sha256"] = "c" * 64
        proposal.write_text(json.dumps(stale_report), encoding="utf-8")
        with self.assertRaises(BestSafeError):
            _apply_text_adjudication(
                cue_texts=["Left anchor", "Corret words", "Right anchor"],
                smart_srt=smart,
                smart_report=smart_report,
                canonical_lyrics=[lyric],
                proposal_path=proposal,
                task_fingerprint=FP,
            )
        stale_canonical = json.loads(json.dumps(base))
        stale_canonical["canonical_lyrics_sha256"] = ["d" * 64]
        proposal.write_text(json.dumps(stale_canonical), encoding="utf-8")
        with self.assertRaises(BestSafeError):
            _apply_text_adjudication(
                cue_texts=["Left anchor", "Corret words", "Right anchor"],
                smart_srt=smart,
                smart_report=smart_report,
                canonical_lyrics=[lyric],
                proposal_path=proposal,
                task_fingerprint=FP,
            )
        freeform = json.loads(json.dumps(base))
        freeform["proposals"][0]["text"] = "invented replacement"
        proposal.write_text(json.dumps(freeform), encoding="utf-8")
        with self.assertRaises(BestSafeError):
            _apply_text_adjudication(
                cue_texts=["Left anchor", "Corret words", "Right anchor"],
                smart_srt=smart,
                smart_report=smart_report,
                canonical_lyrics=[lyric],
                proposal_path=proposal,
                task_fingerprint=FP,
            )

    def test_best_safe_output_tree_cannot_live_inside_lyrics_input(self):
        self._transition("resolved_clear")
        lyrics_root = self.root / "lyrics"
        lyrics_root.mkdir()
        lyric = lyrics_root / "A.lrc"
        lyric.write_text("[00:00.000]A smart\n", encoding="utf-8")
        unsafe = lyrics_root / "out"
        with self.assertRaises(ValueError):
            build_best_safe(
                smart_srt=self.smart,
                max_srt=self.max,
                max_audit=self.audit,
                semantic_sync=self.semantic,
                song_list=self.songs,
                transition_authorization=self.transitions,
                canonical_lyrics=[lyric],
                canonical_lyrics_root=lyrics_root,
                out_srt=unsafe / "FINAL.srt",
                out_audit=unsafe / "FINAL.audit.csv",
                out_qa=unsafe / "QA.json",
                out_artifact=unsafe / "ARTIFACT.json",
                expected_task_fingerprint=FP,
            )

    def test_semantic_fingerprint_mismatch_fails_closed(self):
        self._transition("resolved_clear")
        payload = json.loads(self.semantic.read_text(encoding="utf-8"))
        payload["task_fingerprint_sha256"] = "b" * 64
        self.semantic.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(BestSafeError):
            self._run()

    def test_output_cannot_overwrite_input(self):
        self._transition("resolved_clear")
        with self.assertRaises(BestSafeError):
            build_best_safe(
                smart_srt=self.smart,
                max_srt=self.max,
                max_audit=self.audit,
                semantic_sync=self.semantic,
                song_list=self.songs,
                transition_authorization=self.transitions,
                out_srt=self.smart,
                out_audit=self.out / "audit.csv",
                out_qa=self.out / "qa.json",
                out_artifact=self.out / "artifact.json",
                expected_task_fingerprint=FP,
            )


if __name__ == "__main__":
    unittest.main()
