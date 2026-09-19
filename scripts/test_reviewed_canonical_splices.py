from __future__ import annotations
import copy
import json
from pathlib import Path
import tempfile
import unittest

from lyric_aligner.review.canonical_splices import SCHEMA_VERSION, apply_reviewed_splices, file_ref, inspect_splice_context


class ReviewedCanonicalSplicesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.floor = self.root / "floor.srt"
        self.floor.write_bytes(b"\xef\xbb\xbf" + "1\r\n00:00:01,000 --> 00:00:03,000\r\nhey wrong tail\r\n\r\n3\r\n00:00:04,000 --> 00:00:06,000\r\nkeep exactly\r\n".encode())
        self.canonical = self.root / "canonical.lrc"
        self.canonical.write_text("[00:01.00]앞부분 안녕 뒷부분\n[00:04.00]keep exactly\n", encoding="utf-8")
        self.smart = self.root / "smart.json"
        self.smart.write_text(json.dumps({"schema_version": "smart-1.1", "output_srt_sha256": file_ref(self.floor)["sha256"],
                                          "text_decisions": [{"cue_ordinal": 0, "action": "review"}, {"cue_ordinal": 1, "action": "keep"}]}), encoding="utf-8")
        self.observation = self.root / "observation.json"
        self.observation.write_text('{"raw_observation":"안녕","human_gold":false}', encoding="utf-8")
        self.plan = {"schema_version": SCHEMA_VERSION, "floor": file_ref(self.floor), "smart_srt": file_ref(self.floor),
                     "smart_report": file_ref(self.smart), "canonical": [file_ref(self.canonical)], "proposals": [{
                         "cue_ordinal": 0, "cue_number": "1", "timing": "00:00:01,000 --> 00:00:03,000", "before": "hey wrong tail",
                         "edits": [{"span": [4, 9], "segments": [{"canonical_ordinal": 0, "span": [4, 6]}]}],
                         "observations": [file_ref(self.observation)]}]}
        self.review = {"schema_version": SCHEMA_VERSION, "plan_sha256": "", "reviewer": "test task reviewer; no acoustic truth claim",
                       "decisions": [{"cue_ordinal": 0, "decision": "apply", "scope": "partial_text", "wording_reason": "reviewed against external observation",
                                      "ownership_reason": "only the middle fragment belongs here", "evidence_indices": [0]}]}

    def freeze(self):
        plan = self.root / "plan.json"
        plan.write_text(json.dumps(self.plan, ensure_ascii=False), encoding="utf-8")
        self.review["plan_sha256"] = file_ref(plan)["sha256"]
        review = self.root / "review.json"
        review.write_text(json.dumps(self.review, ensure_ascii=False), encoding="utf-8")
        return plan, review

    def run_case(self):
        plan, review = self.freeze()
        return apply_reviewed_splices(plan_path=plan, review_path=review, out_dir=self.root / "out")

    def assert_blocked(self):
        before = self.floor.read_bytes()
        with self.assertRaises(ValueError):
            self.run_case()
        self.assertFalse((self.root / "out").exists())
        self.assertEqual(before, self.floor.read_bytes())

    def test_subline_preserves_true_english_and_skipped_cue_number(self):
        result = self.run_case()
        text = (self.root / "out/FINAL.srt").read_text(encoding="utf-8")
        self.assertIn("hey 안녕 tail", text)
        self.assertIn("3\n00:00:04,000 --> 00:00:06,000\nkeep exactly", text)
        self.assertEqual(len(result["ledger"]), 2)
        self.assertFalse(result["automatic_lexical_authority"])
        self.assertNotIn(b"\r", (self.root / "out/FINAL.srt").read_bytes())

    def test_empty_plan_preserves_exact_floor_bytes(self):
        self.plan["proposals"] = []
        self.review["decisions"] = []
        self.run_case()
        self.assertEqual((self.root / "out/FINAL.srt").read_bytes(), self.floor.read_bytes())

    def test_context_uses_current_floor_and_exposes_actual_gap_without_authority(self):
        plan, _ = self.freeze()
        packet = inspect_splice_context(plan)
        row = packet["rows"][0]
        self.assertEqual(row["next"]["text"], "keep exactly")
        self.assertEqual(row["next"]["cue_number"], "3")
        self.assertEqual(row["next_gap_ms"], 1000)
        self.assertIsNone(row["previous"])
        self.assertFalse(packet["ownership_authority"])
        self.assertFalse((self.root / "out").exists())

    def test_explicit_keep_preserves_exact_floor_bytes(self):
        self.review["decisions"][0]["decision"] = "keep"
        self.run_case()
        self.assertEqual((self.root / "out/FINAL.srt").read_bytes(), self.floor.read_bytes())

    def test_signed_partial_review_cannot_silently_become_whole_line(self):
        plan, review = self.freeze()
        self.plan["proposals"][0]["edits"][0]["segments"][0]["span"] = [0, 10]
        plan.write_text(json.dumps(self.plan), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "different plan"):
            apply_reviewed_splices(plan_path=plan, review_path=review, out_dir=self.root / "out")
        self.assertFalse((self.root / "out").exists())

    def test_stale_floor_fails_before_writing(self):
        self.floor.write_text(self.floor.read_text(encoding="utf-8-sig").replace("wrong", "user edit"), encoding="utf-8")
        self.assert_blocked()

    def test_changed_canonical_fails(self):
        self.canonical.write_text("[00:01.00]different\n", encoding="utf-8")
        self.assert_blocked()

    def test_changed_observation_fails(self):
        self.observation.write_text('{}', encoding="utf-8")
        self.assert_blocked()

    def test_missing_ownership_review_fails(self):
        self.review["decisions"][0]["ownership_reason"] = ""
        self.assert_blocked()

    def test_no_external_evidence_fails(self):
        self.plan["proposals"][0]["observations"] = []
        self.assert_blocked()

    def test_unreviewed_proposal_fails(self):
        self.review["decisions"] = []
        self.assert_blocked()

    def test_duplicate_proposal_fails(self):
        self.plan["proposals"] *= 2
        self.assert_blocked()

    def test_duplicate_review_fails(self):
        self.review["decisions"] *= 2
        self.assert_blocked()

    def test_wrong_ordinal_cannot_bypass_resolved_cue_floor(self):
        p = self.plan["proposals"][0]
        p.update(cue_ordinal=1, cue_number="3", timing="00:00:04,000 --> 00:00:06,000", before="keep exactly")
        self.review["decisions"][0]["cue_ordinal"] = 1
        self.assert_blocked()

    def test_timing_mutation_fails(self):
        self.plan["proposals"][0]["timing"] = "00:00:01,500 --> 00:00:03,000"
        self.assert_blocked()

    def test_free_replacement_text_is_rejected(self):
        self.plan["proposals"][0]["after"] = "invented text"
        self.assert_blocked()

    def test_presentation_cannot_change_lexical_truth(self):
        self.plan["proposals"][0]["edits"][0]["segments"][0]["display"] = "다른말"
        self.assert_blocked()

    def test_overlapping_splices_fail(self):
        self.plan["proposals"][0]["edits"] *= 2
        self.assert_blocked()

    def test_negative_or_boolean_indices_fail(self):
        for index in (-1, True):
            with self.subTest(index=index):
                self.plan["proposals"][0]["edits"][0]["segments"][0]["canonical_ordinal"] = index
                self.assert_blocked()

    def test_cannot_overwrite_prior_delivery(self):
        (self.root / "out").mkdir()
        marker = self.root / "out/existing.srt"
        marker.write_text("protected", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.run_case()
        self.assertEqual(marker.read_text(), "protected")


    def test_report_cannot_be_rebound_to_another_same_timing_srt(self):
        report = json.loads(self.smart.read_text())
        report["output_srt_sha256"] = "0" * 64
        self.smart.write_text(json.dumps(report), encoding="utf-8")
        self.plan["smart_report"] = file_ref(self.smart)
        self.assert_blocked()

    def test_missing_report_output_binding_fails(self):
        report = json.loads(self.smart.read_text())
        del report["output_srt_sha256"]
        self.smart.write_text(json.dumps(report), encoding="utf-8")
        self.plan["smart_report"] = file_ref(self.smart)
        self.assert_blocked()

    def test_reordered_or_repeated_canonical_segments_fail(self):
        segments = self.plan["proposals"][0]["edits"][0]["segments"]
        segments[:] = [{"canonical_ordinal": 0, "span": [4, 6]},
                       {"canonical_ordinal": 0, "span": [0, 3]}]
        self.assert_blocked()
        segments[1] = copy.deepcopy(segments[0])
        self.assert_blocked()

    def test_cross_source_segments_fail(self):
        other = self.root / "other.lrc"
        other.write_text("[00:01.00]다른 노래\n", encoding="utf-8")
        self.plan["canonical"].append(file_ref(other))
        self.plan["proposals"][0]["edits"][0]["segments"].append({"canonical_ordinal": 2, "span": [0, 2]})
        self.assert_blocked()

    def test_chinese_homophone_missing_and_extra_characters_preserve_timeline(self):
        from lyric_aligner.text_repair import parse_srt_text, timeline_signature
        for n, before in enumerate(("我再看天空", "我看天空", "我在在看天空")):
            with self.subTest(before=before):
                after = "我在看天空"
                self.floor.write_text("1\n00:00:01,000 --> 00:00:03,000\n" + before + "\n\n3\n00:00:04,000 --> 00:00:06,000\nkeep exactly\n", encoding="utf-8")
                self.canonical.write_text("[00:01.00]" + after + "\n[00:04.00]keep exactly\n", encoding="utf-8")
                report = json.loads(self.smart.read_text())
                report["output_srt_sha256"] = file_ref(self.floor)["sha256"]
                self.smart.write_text(json.dumps(report), encoding="utf-8")
                self.plan.update(floor=file_ref(self.floor), smart_srt=file_ref(self.floor), smart_report=file_ref(self.smart), canonical=[file_ref(self.canonical)])
                self.plan["proposals"][0].update(before=before, edits=[{"span": [0, len(before)], "segments": [{"canonical_ordinal": 0, "span": [0, len(after)]}]}])
                plan, review = self.freeze()
                out = self.root / ("zh-" + str(n))
                apply_reviewed_splices(plan_path=plan, review_path=review, out_dir=out)
                _, original = parse_srt_text(self.floor.read_text(encoding="utf-8"))
                _, result = parse_srt_text((out / "FINAL.srt").read_text(encoding="utf-8"))
                self.assertEqual(result[0].text, after)
                self.assertEqual(timeline_signature(original), timeline_signature(result))
                self.assertEqual(result[1].text, original[1].text)

    def test_standard_untimed_canonical_uses_same_materializer(self):
        self.canonical.write_text("앞부분 안녕 뒷부분\nkeep exactly\n", encoding="utf-8")
        report = json.loads(self.smart.read_text())
        report.update(schema_version="2.2", mode="text_only_preserve_timeline", timeline_unchanged=True, cue_count_unchanged=True)
        report["decisions"] = report.pop("text_decisions")
        self.smart.write_text(json.dumps(report), encoding="utf-8")
        self.plan.update(smart_report=file_ref(self.smart), canonical=[file_ref(self.canonical)])
        self.run_case()
        self.assertIn("hey 안녕 tail", (self.root / "out/FINAL.srt").read_text(encoding="utf-8"))

    def test_canonical_display_is_not_automatically_simplified(self):
        self.canonical.write_text("[00:01.00]風中的聲音\n[00:04.00]keep exactly\n", encoding="utf-8")
        self.plan["canonical"] = [file_ref(self.canonical)]
        segment = self.plan["proposals"][0]["edits"][0]["segments"][0]
        segment.update(span=[0, 5], display="风中的声音")
        self.assert_blocked()
        del segment["display"]
        self.run_case()
        self.assertIn("風中的聲音", (self.root / "out/FINAL.srt").read_text(encoding="utf-8"))

    def test_prior_reviewed_hangul_is_inherited_from_current_floor(self):
        baseline = self.root / "baseline.srt"
        baseline.write_bytes(self.floor.read_bytes())
        self.plan["smart_srt"] = file_ref(baseline)
        self.floor.write_text(self.floor.read_text(encoding="utf-8-sig").replace("keep exactly", "keep 안녕 exactly"), encoding="utf-8")
        self.plan["floor"] = file_ref(self.floor)
        self.run_case()
        self.assertIn("keep 안녕 exactly", (self.root / "out/FINAL.srt").read_text(encoding="utf-8"))

    def test_file_reference_hashing_is_streaming(self):
        from unittest.mock import patch
        with patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read forbidden")):
            self.assertEqual(len(file_ref(self.observation)["sha256"]), 64)

    def test_input_changed_during_render_fails_before_output(self):
        from unittest.mock import patch
        from lyric_aligner.text_repair import render_repaired_srt
        def mutate(*args):
            self.canonical.write_text("[00:01.00]changed\n", encoding="utf-8")
            return render_repaired_srt(*args)
        with patch("lyric_aligner.review.canonical_splices.render_repaired_srt", side_effect=mutate):
            self.assert_blocked()


    def test_disjoint_edits_cannot_reverse_canonical_order_between_edits(self):
        self.plan["proposals"][0]["edits"] = [
            {"span": [0, 3], "segments": [{"canonical_ordinal": 0, "span": [4, 6]}]},
            {"span": [4, 9], "segments": [{"canonical_ordinal": 0, "span": [0, 3]}]},
        ]
        self.assert_blocked()


if __name__ == "__main__":
    unittest.main()
