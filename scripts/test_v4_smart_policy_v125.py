from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from lyric_aligner.text_repair import _normalize_for_match
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.timeline.smart_policy_v125 import (
    SMART_POLICY_ID,
    smart_repair_srt_text_v125,
)


def _srt(texts: list[str]) -> str:
    blocks: list[str] = []
    for index, text in enumerate(texts):
        start = index * 2
        blocks.append(
            f"{index + 1}\n"
            f"00:00:{start:02d},000 --> 00:00:{start + 1:02d},500\n"
            f"{text}\n"
        )
    return "\n".join(blocks) + "\n"


def _text_decision(
    cue: int,
    span: tuple[int, int],
    *,
    action: str,
    score: float,
) -> dict[str, object]:
    return {
        "cue_ordinal": cue,
        "canonical_ordinal": span[0],
        "cue_span": [cue, cue + 1],
        "canonical_span": list(span),
        "score": score,
        "action": action,
        "reason": (
            "low_or_structurally_unsafe_similarity"
            if action == "review"
            else "canonical_content_matches_source_segmentation"
        ),
    }


def _timing(cue: int, canonical: int) -> dict[str, object]:
    return {
        "cue_ordinal": cue,
        "source_ordinal": 0,
        "canonical_ordinal": canonical,
        "anchor_grade": "A",
        "action": "preserve",
        "reason": "timing_matches_anchor_model",
        "old_start_ms": cue * 2_000,
        "old_end_ms": cue * 2_000 + 1_500,
        "proposed_start_ms": None,
        "proposed_end_ms": None,
        "residual_ms": 100.0,
        "model_status": "ready",
        "evidence": ["canonical_identity", "affine_model"],
    }


class SmartPolicyV125Tests(unittest.TestCase):
    def test_wrapper_changes_only_text_after_v124_timing_is_frozen(self) -> None:
        source_texts = [
            "左侧锚点",
            "春风吹过山何",
            "星光落在常街",
            "局部边界",
            "远端锚点",
        ]
        canonical_texts = [
            "左侧锚点",
            "春风吹过山河",
            "星光落在长街",
            "局部边界",
            "远端锚点",
        ]
        rendered_v124 = _srt(source_texts)
        timed = [
            TimedCanonicalOccurrence(
                ordinal=index,
                source="synthetic.lrc",
                source_ordinal=0,
                time_ms=index * 2_000,
                text=text,
                normalized=_normalize_for_match(text),
            )
            for index, text in enumerate(canonical_texts)
        ]
        text_decisions = [
            _text_decision(0, (0, 1), action="unchanged", score=1.0),
            _text_decision(1, (1, 2), action="review", score=0.70),
            _text_decision(2, (2, 3), action="review", score=0.70),
            _text_decision(3, (3, 4), action="unchanged", score=1.0),
            _text_decision(4, (4, 5), action="unchanged", score=1.0),
        ]
        timing_decisions = [_timing(0, 0), _timing(4, 4)]
        base_report: dict[str, object] = {
            "policy_id": "smart-validation-policy-2026-08-21-v1.2.4",
            "text_decisions": text_decisions,
            "timing_decisions": timing_decisions,
            "timing_review_count": 0,
            "text_review_count": 2,
            "text_mapped_review_count": 2,
            "text_unmapped_review_count": 0,
            "text_review_reason_counts": {"low_or_structurally_unsafe_similarity": 2},
            "text_status": "review_required",
            "text_replacement_count": 0,
            "text_decision_replacement_count": 0,
            "text_materialized_change_count": 0,
            "text_semantic_change_count": 0,
            "pro_text_escalation_required": True,
            "pro_timing_escalation_required": False,
            "pro_escalation_required": True,
            "status": "review_required",
        }
        frozen_timing = copy.deepcopy(timing_decisions)

        with patch(
            "lyric_aligner.timeline.smart_policy_v125.smart_repair_srt_text_v124",
            return_value=(rendered_v124, base_report),
        ):
            rendered, report = smart_repair_srt_text_v125(
                rendered_v124,
                timed,
                [],
            )

        self.assertIn("春风吹过山河", rendered)
        self.assertIn("星光落在长街", rendered)
        self.assertEqual(report["policy_id"], SMART_POLICY_ID)
        self.assertEqual(report["text_a_bounded_recovery_count"], 2)
        self.assertEqual(report["text_a_bounded_region_count"], 1)
        self.assertEqual(report["text_a_bounded_materialized_change_count"], 2)
        self.assertEqual(report["text_review_count"], 0)
        self.assertEqual(report["text_replacement_count"], 2)
        self.assertEqual(report["text_materialized_change_count"], 2)
        self.assertEqual(report["text_semantic_change_count"], 2)
        self.assertEqual(report["timing_decisions"], frozen_timing)
        self.assertEqual(report["status"], "ready")


if __name__ == "__main__":
    unittest.main()
