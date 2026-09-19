from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from lyric_aligner.review.canonical_splices import SCHEMA_VERSION as SPLICE_SCHEMA, file_ref
from lyric_aligner.review.safe_final_review import SCHEMA_VERSION, build_candidates, verify_gap_review


class SafeFinalReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.floor = self.root / "floor.srt"
        self.canonical = self.root / "canonical.lrc"
        self.report = self.root / "baseline.json"
        self.plan = self.root / "plan.json"
        self.audio = self.root / "mix.wav"
        with wave.open(str(self.audio), "wb") as audio:
            audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * (15 * 8000))
        self.observation = self.root / "observation.json"
        self.observation.write_text('{"synthetic_fixture":true,"human_gold":false}', encoding="utf-8")
        self.floor.write_text("1\n00:00:01,000 --> 00:00:02,000\n开始向前\n\n3\n00:00:03,000 --> 00:00:04,000\n我再看天空\n\n4\n00:00:05,000 --> 00:00:06,000\nhey an nyong friend\n\n6\n00:00:07,000 --> 00:00:08,000\nkeep moving\n\n7\n00:00:11,000 --> 00:00:12,000\n抵达远方\n", encoding="utf-8")
        self.canonical.write_text("[00:01.00]开始向前\n[00:03.00]我在看天空\n[00:05.00]hey 안녕 friend\n[00:07.00]keep moving\n[00:09.00]在風中轉身\n[00:11.00]抵达远方\n", encoding="utf-8")
        self.rows = [{"cue_ordinal": i, "canonical_ordinal": ci, "cue_span": [i, i + 1],
                      "canonical_span": [ci, ci + 1], "action": "review" if i in (1, 2) else "keep"}
                     for i, ci in enumerate((0, 1, 2, 3, 5))]
        self.bind()

    def bind(self, count=6):
        report = {"schema_version": "smart-1.1", "output_srt_sha256": file_ref(self.floor)["sha256"],
                  "canonical_line_count": count, "text_decisions": self.rows,
                  "inputs": {"canonical_lyrics": [{"name": self.canonical.name, "sha256": file_ref(self.canonical)["sha256"]}]}}
        self.report.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        self.plan_data = {"schema_version": SPLICE_SCHEMA, "floor": file_ref(self.floor), "smart_srt": file_ref(self.floor),
                          "smart_report": file_ref(self.report), "canonical": [file_ref(self.canonical)], "proposals": []}
        self.plan.write_text(json.dumps(self.plan_data, ensure_ascii=False), encoding="utf-8")

    def freeze_gap(self, with_audio=True):
        pack = build_candidates(self.plan, final_mix=self.audio if with_audio else None)
        self.pack_path = self.root / "pack.json"
        self.pack_path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
        self.review_path = self.root / "review.json"
        self.review = {"schema_version": SCHEMA_VERSION, "kind": "gap-decisions", "pack_sha256": file_ref(self.pack_path)["sha256"],
                       "reviewer": "synthetic reviewer; not human Gold", "decisions": [
                           {"gap_id": g["gap_id"], "classification": "missing_subtitle", "text_confirmed": True,
                            "ownership_confirmed": True, "reason": "synthetic geometry/evidence fixture",
                            "timing": {"start_ms": 8500, "end_ms": 10000, "basis": "local_asr_observation", "observation": file_ref(self.observation)},
                            "evidence": [file_ref(self.observation)]} for g in pack["gap_candidates"]]}
        return pack

    def verify(self):
        self.review_path.write_text(json.dumps(self.review, ensure_ascii=False), encoding="utf-8")
        return verify_gap_review(self.pack_path, self.review_path)

    def test_korean_and_chinese_candidates_keep_complete_context_and_english(self):
        before = self.floor.read_bytes()
        pack = build_candidates(self.plan)
        rows = {r["current"]["cue_ordinal"]: r for r in pack["lexical_candidates"]}
        self.assertEqual(set(rows), {1, 2})
        self.assertIn("han_mismatch_or_editor_split", rows[1]["reasons"])
        self.assertIn("korean_latin_requires_lexical_review", rows[2]["reasons"])
        latin = {r["text"]: r["canonical_english_token_present"] for r in rows[2]["latin_spans"]}
        self.assertEqual(latin, {"hey": True, "an": False, "nyong": False, "friend": True})
        self.assertEqual(rows[2]["previous"]["text"], "我再看天空")
        self.assertEqual(rows[2]["next_gap_ms"], 1000)
        self.assertTrue(rows[2]["phonetic_hints"])
        self.assertFalse(rows[2]["automatic_replacement_allowed"])
        self.assertIn(3, [r["cue_ordinal"] for r in pack["equal_controls"]])
        self.assertEqual(self.floor.read_bytes(), before)
        self.assertFalse(pack["audio_decoded"])
        self.assertFalse(pack["production_writeback_permitted"])

    def test_gap_detects_only_wholly_uncovered_lines_in_real_empty_interval(self):
        pack = build_candidates(self.plan)
        self.assertEqual(len(pack["gap_candidates"]), 1)
        gap = pack["gap_candidates"][0]
        self.assertEqual(gap["interval_ms"], [8000, 11000])
        self.assertEqual(gap["canonical_span"], [4, 5])
        self.assertEqual(gap["segments"], [{"canonical_ordinal": 4, "span": [0, 5]}])
        self.assertEqual(pack["canonical"][4]["text"], "在風中轉身")
        self.assertEqual(gap["classification"], "uncertain")

    def test_repeated_chorus_exposes_all_occurrences_without_selecting_one(self):
        self.canonical.write_text(self.canonical.read_text(encoding="utf-8") + "[00:13.00]hey 안녕 friend\n", encoding="utf-8")
        self.bind(7)
        pack = build_candidates(self.plan)
        self.assertEqual(pack["canonical"][2]["same_source_equal_occurrences"], [2, 6])
        row = next(r for r in pack["lexical_candidates"] if r["current"]["cue_ordinal"] == 2)
        self.assertIn("repeated_occurrence_requires_context", row["reasons"])
        self.assertFalse(row["automatic_replacement_allowed"])
        self.assertEqual(json.loads(self.plan.read_text())["proposals"], [])

    def test_comparison_equal_does_not_hide_unresolved_ownership(self):
        self.floor.write_text(self.floor.read_text(encoding="utf-8").replace("hey an nyong friend", "hey 안녕 friend"), encoding="utf-8")
        self.bind()
        pack = build_candidates(self.plan)
        self.assertIn(2, [r["current"]["cue_ordinal"] for r in pack["lexical_candidates"]])
        self.assertIn(2, [r["cue_ordinal"] for r in pack["equal_controls"]])

    def test_changed_canonical_version_cannot_reuse_baseline_mapping(self):
        self.canonical.write_text(self.canonical.read_text(encoding="utf-8").replace("我在看天空", "另一种版本"), encoding="utf-8")
        self.plan_data["canonical"] = [file_ref(self.canonical)]
        self.plan.write_text(json.dumps(self.plan_data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "version mismatch"):
            build_candidates(self.plan)

    def test_missing_canonical_lineage_fails_closed(self):
        report = json.loads(self.report.read_text())
        del report["inputs"]
        self.report.write_text(json.dumps(report), encoding="utf-8")
        self.plan_data["smart_report"] = file_ref(self.report)
        self.plan.write_text(json.dumps(self.plan_data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "lineage required"):
            build_candidates(self.plan)

    def test_no_gap_when_timing_overlaps(self):
        self.floor.write_text(self.floor.read_text(encoding="utf-8").replace("00:00:11,000", "00:00:07,900"), encoding="utf-8")
        self.bind()
        self.assertEqual(build_candidates(self.plan)["gap_candidates"], [])

    def test_an_earlier_long_cue_can_own_apparent_adjacent_gap(self):
        self.floor.write_text(self.floor.read_text(encoding="utf-8").replace("00:00:02,000", "00:00:10,000"), encoding="utf-8")
        self.bind()
        self.assertEqual(build_candidates(self.plan)["gap_candidates"], [])

    def test_split_canonical_line_is_not_a_whole_line_gap_anchor(self):
        self.rows[3]["cue_span"] = [2, 4]
        self.bind()
        self.assertEqual(build_candidates(self.plan)["gap_candidates"], [])

    def test_an_existing_mapping_blocks_duplicate_gap_coverage(self):
        self.rows[1]["canonical_span"] = [1, 5]
        self.bind()
        self.assertEqual(build_candidates(self.plan)["gap_candidates"], [])

    def test_valid_proposed_gap_interval_still_has_no_timing_authority(self):
        before = self.floor.read_bytes()
        self.freeze_gap()
        result = self.verify()
        self.assertEqual(result["ledger"][0]["state"], "timing_authority_unavailable")
        self.assertTrue(result["contract_valid"])
        self.assertFalse(result["production_writeback_permitted"])
        self.assertEqual(result["subtitle_mutation_count"], 0)
        self.assertEqual(self.floor.read_bytes(), before)
        self.assertFalse((self.root / "FINAL.srt").exists())

    def test_text_and_ownership_confirmed_without_timing_stays_review(self):
        self.freeze_gap(with_audio=False)
        self.review["decisions"][0]["timing"] = None
        self.assertEqual(self.verify()["ledger"][0]["state"], "timing_review")

    def test_cut_version_repeat_and_not_sung_keep_floor(self):
        self.freeze_gap()
        for classification in ("cut", "version_mismatch", "occurrence_uncertain", "not_sung", "instrumental", "overlap"):
            with self.subTest(classification=classification):
                self.review["decisions"][0].update(classification=classification, timing=None)
                self.assertEqual(self.verify()["ledger"][0]["state"], "keep_floor")

    def test_forged_insertion_authority_is_rejected(self):
        self.freeze_gap()
        self.review["decisions"][0]["timing_authority"] = True
        with self.assertRaisesRegex(ValueError, "insertion authority"):
            self.verify()

    def test_canonical_timestamp_and_out_of_gap_boundaries_rejected(self):
        self.freeze_gap()
        original = copy.deepcopy(self.review)
        for change in ({"basis": "canonical_timestamp"}, {"start_ms": 7999}, {"end_ms": 11001}, {"end_ms": 8500}, {"start_ms": True}):
            with self.subTest(change=change):
                self.review = copy.deepcopy(original)
                self.review["decisions"][0]["timing"].update(change)
                with self.assertRaises(ValueError):
                    self.verify()

    def test_gap_evidence_must_exist_and_be_bound(self):
        self.freeze_gap()
        self.observation.write_text('{}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "stale bound file"):
            self.verify()

    def test_gap_audio_must_be_bound_before_review(self):
        self.freeze_gap(with_audio=False)
        with self.assertRaisesRegex(ValueError, "frozen final mix"):
            self.verify()

    def test_stale_floor_and_edited_pack_cannot_be_approved(self):
        self.freeze_gap()
        pack = json.loads(self.pack_path.read_text(encoding="utf-8"))
        pack["gap_candidates"][0]["interval_ms"][0] -= 1
        self.pack_path.write_text(json.dumps(pack), encoding="utf-8")
        self.review["pack_sha256"] = file_ref(self.pack_path)["sha256"]
        with self.assertRaisesRegex(ValueError, "modified candidate pack"):
            self.verify()
        self.freeze_gap()
        self.floor.write_text(self.floor.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "stale bound file"):
            self.verify()

    def test_review_cannot_omit_duplicate_or_coerce_gap_decisions(self):
        self.freeze_gap()
        original = copy.deepcopy(self.review)
        for decisions in ([], self.review["decisions"] * 2, [{**self.review["decisions"][0], "text_confirmed": 1}]):
            with self.subTest(decisions=decisions):
                self.review = copy.deepcopy(original)
                self.review["decisions"] = decisions
                with self.assertRaises(ValueError):
                    self.verify()

    def test_cli_cannot_overwrite_floor_or_prior_pack(self):
        from scripts.v4_review_safe_final import main
        before = self.floor.read_bytes()
        args = ["review", "scan", "--plan", str(self.plan), "--out", str(self.floor)]
        with patch("sys.argv", args), patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(main(), 1)
        self.assertEqual(self.floor.read_bytes(), before)


    def test_wrong_baseline_line_does_not_hide_nearby_genuine_english(self):
        # The canonical mixed-language line is next to the erroneous mapping.
        self.rows[2]["canonical_ordinal"] = 3
        self.rows[2]["canonical_span"] = [3, 4]
        self.bind()
        pack = build_candidates(self.plan)
        row = next(r for r in pack["lexical_candidates"] if r["current"]["cue_ordinal"] == 2)
        tokens = {r["text"]: r["canonical_english_token_present"] for r in row["latin_spans"]}
        self.assertTrue(tokens["hey"])
        self.assertTrue(tokens["friend"])
        self.assertFalse(row["automatic_replacement_allowed"])


    def test_bool_int_substitution_cannot_survive_pack_reconstruction(self):
        self.freeze_gap()
        pack = json.loads(self.pack_path.read_text(encoding="utf-8"))
        pack["production_writeback_permitted"] = 0
        self.pack_path.write_text(json.dumps(pack), encoding="utf-8")
        self.review["pack_sha256"] = file_ref(self.pack_path)["sha256"]
        with self.assertRaisesRegex(ValueError, "modified candidate pack"):
            self.verify()

    def test_input_changed_during_scan_fails_closed(self):
        from lyric_aligner.review.safe_final_review import _comparison
        mutated = False
        def mutate(text):
            nonlocal mutated
            if not mutated:
                mutated = True
                self.canonical.write_text("[00:01.00]changed version\n", encoding="utf-8")
            return _comparison(text)
        with patch("lyric_aligner.review.safe_final_review._comparison", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "stale bound file"):
                build_candidates(self.plan)

    def test_changed_review_during_verification_fails_closed(self):
        self.freeze_gap()
        self.review_path.write_text(json.dumps(self.review), encoding="utf-8")
        from lyric_aligner.review.safe_final_review import build_candidates as real_build
        def mutate(*args, **kwargs):
            self.review_path.write_text("{}", encoding="utf-8")
            return real_build(*args, **kwargs)
        with patch("lyric_aligner.review.safe_final_review.build_candidates", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "stale bound file"):
                verify_gap_review(self.pack_path, self.review_path)

    def test_resolved_english_split_is_not_reviewed_just_because_line_repeats(self):
        self.canonical.write_text("[00:01.00]first phrase\n[00:03.00]second phrase\n[00:05.00]third phrase\n[00:07.00]keep moving forward\n[00:09.00]fourth phrase\n[00:11.00]keep moving forward\n", encoding="utf-8")
        self.bind()
        pack = build_candidates(self.plan)
        self.assertNotIn(3, [r["current"]["cue_ordinal"] for r in pack["lexical_candidates"]])


if __name__ == "__main__":
    unittest.main()
