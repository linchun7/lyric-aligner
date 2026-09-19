from __future__ import annotations

import unittest

from lyric_aligner.text.semantic_lexical import build_semantic_request_bundle
from lyric_aligner.text_repair import (
    CanonicalLine,
    _normalize_for_match,
    build_repair_plan_v2,
    build_trusted_lexical_floor_report,
    parse_srt_text,
    repair_srt_text,
)


def canonical(*lines: str) -> list[CanonicalLine]:
    return [
        CanonicalLine(i, "song.lrc", text, _normalize_for_match(text), source_ordinal=0)
        for i, text in enumerate(lines)
    ]


class LexicalFloorTests(unittest.TestCase):
    def test_helper_mapped_scope_does_not_require_raw_coverage(self):
        _, cues = parse_srt_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        _, decisions, _ = build_repair_plan_v2(cues, canonical("Hello", "World"))
        report = build_trusted_lexical_floor_report(cues, decisions, canonical("Hello", "World"), unresolved_canonical_count=1)
        self.assertEqual(report["status"], "complete")
        self.assertFalse(report["complete_canonical_coverage_required"])

    def test_helper_can_require_raw_coverage(self):
        _, cues = parse_srt_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        _, decisions, _ = build_repair_plan_v2(cues, canonical("Hello", "World"))
        report = build_trusted_lexical_floor_report(cues, decisions, canonical("Hello", "World"), unresolved_canonical_count=1, require_complete_canonical_coverage=True)
        self.assertEqual(report["status"], "review_required")

    def test_helper_detects_midword_split_and_cjk_is_not_false_positive(self):
        _, split_cues = parse_srt_text("1\n00:00:01,000 --> 00:00:01,500\nIntoxi\n\n2\n00:00:01,500 --> 00:00:02,000\ncate\n")
        _, split_decisions, _ = build_repair_plan_v2(split_cues, canonical("Intoxicate"))
        split = build_trusted_lexical_floor_report(split_cues, split_decisions, canonical("Intoxicate"))
        self.assertEqual(split["status"], "review_required")
        _, cjk_cues = parse_srt_text("1\n00:00:01,000 --> 00:00:02,000\n我爱你\n")
        _, cjk_decisions, _ = build_repair_plan_v2(cjk_cues, canonical("我爱你"))
        cjk = build_trusted_lexical_floor_report(cjk_cues, cjk_decisions, canonical("我爱你"))
        self.assertEqual(cjk["trusted_region_word_boundary_error_count"], 0)
    def test_safe_text_fix_reaches_complete_floor_without_timing_mutation(self):
        source = "1\n00:00:01,000 --> 00:00:02,000\n我真的爱\n"
        output, report = repair_srt_text(source, canonical("我真的爱你"))
        floor = report["lexical_floor"]
        self.assertEqual(output, "1\n00:00:01,000 --> 00:00:02,000\n我真的爱你\n")
        self.assertEqual(floor["status"], "complete")
        self.assertEqual(floor["trusted_region_lexical_error_count"], 0)
        self.assertEqual(floor["unresolved_cue_count"], 0)
        self.assertEqual(floor["unresolved_canonical_count"], 0)
        self.assertEqual(floor["timeline_mutation_count"], 0)

    def test_missing_canonical_occurrence_prevents_complete_floor(self):
        source = (
            "1\n00:00:01,000 --> 00:00:02,000\n第一句\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\n第三句\n"
        )
        output, report = repair_srt_text(source, canonical("第一句", "第二句", "第三句"))
        self.assertEqual(output, source)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["lexical_floor"]["status"], "review_required")
        self.assertEqual(report["lexical_floor"]["unresolved_canonical_count"], 1)

    def test_low_similarity_review_can_be_exported_as_bounded_model_request(self):
        source = "1\n00:00:01,000 --> 00:00:02,000\nso nyo shi dae\n"
        lines = canonical("소녀시대")
        _, cues = parse_srt_text(source)
        _, decisions, _ = build_repair_plan_v2(cues, lines)
        self.assertTrue(any(item.action == "review" for item in decisions))
        bundle = build_semantic_request_bundle(cues, lines, decisions)
        self.assertEqual(len(bundle["requests"]), 1)
        request = bundle["requests"][0]
        self.assertEqual(request["proposed_canonical_lines"], ["소녀시대"])
        self.assertEqual(request["cue_ordinals"], [0])
        self.assertNotIn("timing", request)

    def test_latin_spacing_repro(self):
        source = (
            "1\n00:00:01,000 --> 00:00:02,000\n"
            "the team will paint the ground\n"
        )
        output, report = repair_srt_text(source, canonical("The team will paint it"))
        self.assertEqual(output.splitlines()[-1], "The team will paint it")
        self.assertEqual(report["lexical_floor"]["status"], "complete")

    def test_internal_newline_mid_word_is_reviewed(self):
        source = "1\n00:00:01,000 --> 00:00:02,000\nIntoxi\ncate\n"
        output, report = repair_srt_text(source, canonical("Intoxicate"))
        self.assertEqual(output, source)
        self.assertEqual(report["status"], "review_required")

    def test_expected_newline_boundary_is_safe(self):
        source = "1\n00:00:01,000 --> 00:00:02,000\nHello,\nworld\n"
        output, report = repair_srt_text(source, canonical("Hello world"))
        self.assertEqual(output.splitlines()[-2:], ["Hello,", "world"])
        self.assertEqual(report["lexical_floor"]["status"], "complete")

    def test_multi_cue_mid_word_is_reviewed(self):
        source = (
            "1\n00:00:01,000 --> 00:00:01,500\nIntoxi\n\n"
            "2\n00:00:01,500 --> 00:00:02,000\ncate\n"
        )
        output, report = repair_srt_text(source, canonical("Intoxicate"))
        self.assertEqual(output, source)
        self.assertEqual(report["status"], "review_required")


if __name__ == "__main__":
    unittest.main()
