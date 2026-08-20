from __future__ import annotations

import unittest

from lyric_aligner.text_repair import MatchDecision, parse_srt_text
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.timeline.bpm_consensus_recovery import (
    build_bpm_consensus_text_models,
    recover_text_reviews_from_bpm_consensus,
)


def _fixture():
    canonical_text = ["春风向前", "河流向东", "星光错落", "灯火明亮", "远山安静", "清晨来到"]
    editor_text = ["春风向前", "河流向东", "新光做落", "登火名亮", "远山安静", "清晨来到"]
    blocks = []
    canonical = []
    decisions = []
    rate = 1.10
    offset = 10_000
    for index, (editor, lyric) in enumerate(zip(editor_text, canonical_text)):
        start_ms = index * 2_000
        end_ms = start_ms + 1_500
        blocks.append(
            f"{index + 1}\n00:00:{start_ms // 1000:02d},000 --> "
            f"00:00:{end_ms // 1000:02d},500\n{editor}"
        )
        source_ms = int(round(offset + rate * start_ms))
        canonical.append(
            TimedCanonicalOccurrence(
                ordinal=index,
                source="synthetic.lrc",
                source_ordinal=0,
                time_ms=source_ms,
                text=lyric,
                normalized=lyric,
            )
        )
        review = index in {2, 3}
        decisions.append(
            MatchDecision(
                cue_ordinal=index,
                canonical_ordinal=index,
                score=0.55 if review else 1.0,
                action="review" if review else "unchanged",
                reason=(
                    "low_or_structurally_unsafe_similarity"
                    if review
                    else "canonical_content_matches_source_segmentation"
                ),
                cue_span=(index, index + 1),
                canonical_span=(index, index + 1),
                source_text=editor,
                canonical_text=lyric,
                output_text=editor if review else lyric,
                edit_operations=(),
            )
        )
    _, cues = parse_srt_text("\n\n".join(blocks) + "\n")
    rate_metadata = {
        0: {
            "value": rate,
            "provenance": "bpm_derived",
            "source_bpm": 100.0,
            "target_bpm": 110.0,
        }
    }
    return cues, canonical, decisions, {0: rate}, rate_metadata


class SmartBpmConsensusV122Tests(unittest.TestCase):
    def test_dense_bpm_offset_consensus_recovers_review_stream_only(self) -> None:
        cues, canonical, decisions, rates, metadata = _fixture()
        changed, updated, summary, models = recover_text_reviews_from_bpm_consensus(
            cues,
            canonical,
            decisions,
            evidence_decisions=decisions,
            replacements={},
            rate_prior_by_source=rates,
            rate_prior_metadata_by_source=metadata,
        )
        self.assertEqual(models[0].status, "ready")
        self.assertEqual(summary.resolved_review_cue_count, 2)
        self.assertEqual(changed[2], "星光错落")
        self.assertEqual(changed[3], "灯火明亮")
        self.assertEqual(updated[2].reason, "sequence_projection_confirms_bpm_consensus_stream")
        self.assertEqual(updated[3].reason, "sequence_projection_confirms_bpm_consensus_stream")
        self.assertEqual(updated[0].action, "unchanged")
        self.assertEqual(updated[0].output_text, "春风向前")

    def test_same_evidence_without_bpm_prior_stays_review(self) -> None:
        cues, canonical, decisions, _, _ = _fixture()
        changed, updated, summary, models = recover_text_reviews_from_bpm_consensus(
            cues,
            canonical,
            decisions,
            evidence_decisions=decisions,
            replacements={},
        )
        self.assertEqual(models[0].status, "no_bpm_soft_prior")
        self.assertEqual(summary.resolved_review_cue_count, 0)
        self.assertEqual(changed, {})
        self.assertEqual(updated[2].action, "review")
        self.assertEqual(updated[3].action, "review")

    def test_sparse_consensus_cannot_recover(self) -> None:
        cues, canonical, decisions, rates, metadata = _fixture()
        sparse = decisions[:4]
        models = build_bpm_consensus_text_models(
            cues[:4],
            canonical,
            sparse,
            rate_prior_by_source=rates,
            rate_prior_metadata_by_source=metadata,
        )
        self.assertEqual(models[0].status, "insufficient_consensus_evidence")


if __name__ == "__main__":
    unittest.main()
