import unittest

from lyric_aligner.review.transition_final_resolution import (
    Policy,
    _canonical_vocal_overlap,
    _editor_gap_clear,
    _local_audio_sequence_clear,
    authorize_final_transitions,
)
from lyric_aligner.text_repair import SubtitleCue, _normalize_for_match

TASK = "1" * 64
RUN_SHA = "2" * 64
SOURCE_SHA = "3" * 64
ARTIFACT_ID = "4" * 64


def cue(ordinal: int, start_ms: int, end_ms: int, text: str) -> SubtitleCue:
    def fmt(ms: int) -> str:
        h, rem = divmod(ms, 3600000)
        m, rem = divmod(rem, 60000)
        s, milli = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"
    return SubtitleCue(
        ordinal=ordinal,
        number=str(ordinal + 1),
        timing=f"{fmt(start_ms)} --> {fmt(end_ms)}",
        text=text,
        normalized=_normalize_for_match(text),
        raw_block_index=ordinal,
    )


def positional_row(*, paired, windows=None, action="unresolved"):
    return {
        "transition_index": 1,
        "left_occurrence_id": "left",
        "right_occurrence_id": "right",
        "nominal_boundary": 100.0,
        "paired_evidence": paired,
        "windows": windows or [],
        "recommendation": {"action": action},
    }


def timeline(occurrence: str, lines):
    return {
        "algorithm_version": "4.0.0a19",
        "result": {"occurrence_id": occurrence, "lines": lines},
    }


def line(index, text, source_start, source_end, mix_start, mix_end):
    return {
        "canonical_line_index": index,
        "text": text,
        "source_start_ms": source_start,
        "source_end_ms": source_end,
        "mix_start_ms": mix_start,
        "mix_end_ms": mix_end,
    }


class TransitionFinalResolutionHelpersTest(unittest.TestCase):
    def test_clean_local_audio_handoff_clears_without_nearby_fine_anchor(self):
        row = positional_row(paired=[
            {"mix_center": 94.0, "left_strong": True, "right_strong": False, "overlap_like": False},
            {"mix_center": 97.0, "left_strong": True, "right_strong": False, "overlap_like": False},
            {"mix_center": 100.0, "left_strong": False, "right_strong": True, "overlap_like": False},
            {"mix_center": 103.0, "left_strong": False, "right_strong": True, "overlap_like": False},
        ])
        clear, detail = _local_audio_sequence_clear(row, boundary=100.0, policy=Policy())
        self.assertTrue(clear)
        self.assertEqual(detail["pre_left_only_centers"], [94.0, 97.0])
        self.assertEqual(detail["post_right_only_centers"], [100.0, 103.0])

    def test_local_audio_overlap_never_clears(self):
        row = positional_row(paired=[
            {"mix_center": 97.0, "left_strong": True, "right_strong": False, "overlap_like": False},
            {"mix_center": 100.0, "left_strong": True, "right_strong": True, "overlap_like": True},
            {"mix_center": 103.0, "left_strong": False, "right_strong": True, "overlap_like": False},
        ])
        clear, _ = _local_audio_sequence_clear(row, boundary=100.0, policy=Policy())
        self.assertFalse(clear)

    def test_editor_lyric_gap_with_right_onset_witness_clears(self):
        cues = [
            cue(0, 80000, 85000, "left lyric"),
            cue(1, 114000, 116000, "Party Rock Rock Rock Rock"),
        ]
        row = positional_row(paired=[
            {"mix_center": 94.0, "left_strong": True, "right_strong": False, "overlap_like": False},
            {"mix_center": 97.0, "left_strong": True, "right_strong": False, "overlap_like": False},
            {"mix_center": 100.0, "left_strong": False, "right_strong": False, "overlap_like": False},
            {"mix_center": 103.0, "left_strong": False, "right_strong": False, "overlap_like": False},
        ])
        lexical = {
            "identity_support": {"left_unique_cue_count": 1},
            "order_support": {"last_left_editor_end_ms": 85000},
        }
        right = timeline("right", [line(0, "Party Rock", 1000, 3000, 114300, 116300)])
        clear, detail = _editor_gap_clear(
            editor_cues=cues,
            boundary=100.0,
            positional_row=row,
            lexical_row=lexical,
            right_timeline=right,
            policy=Policy(),
        )
        self.assertTrue(clear)
        self.assertEqual(detail["right_timeline_start_ms"], 114300)

    def test_editor_gap_rejects_right_text_mismatch(self):
        cues = [cue(0, 80000, 85000, "left lyric"), cue(1, 114000, 116000, "not the song")]
        row = positional_row(paired=[
            {"mix_center": 97.0, "left_strong": True, "right_strong": False, "overlap_like": False},
            {"mix_center": 100.0, "left_strong": False, "right_strong": False, "overlap_like": False},
        ])
        lexical = {"identity_support": {"left_unique_cue_count": 1}, "order_support": {"last_left_editor_end_ms": 85000}}
        right = timeline("right", [line(0, "Party Rock", 1000, 3000, 114300, 116300)])
        clear, detail = _editor_gap_clear(
            editor_cues=cues, boundary=100.0, positional_row=row, lexical_row=lexical,
            right_timeline=right, policy=Policy(),
        )
        self.assertFalse(clear)
        self.assertEqual(detail["reason"], "editor_gap_right_timeline_editor_text_disagree")

    def test_dual_source_windows_inside_both_canonical_vocals_confirm_overlap(self):
        paired = [
            {"mix_center": 100.0, "left_strong": True, "right_strong": True, "overlap_like": True},
            {"mix_center": 103.0, "left_strong": True, "right_strong": True, "overlap_like": True},
        ]
        windows = []
        for center, left_source, right_source in [(100.0, 5.0, 2.0), (103.0, 8.0, 5.0)]:
            windows.extend([
                {"side": "left", "global_mix_center": center, "strong": True, "local": {"top1": {"source_center": left_source}}},
                {"side": "right", "global_mix_center": center, "strong": True, "local": {"top1": {"source_center": right_source}}},
            ])
        row = positional_row(paired=paired, windows=windows, action="overlap_candidate_advisory")
        left = timeline("left", [line(1, "whooa oh", 0, 10000, 98000, 105000)])
        right = timeline("right", [line(2, "right lyric", 0, 10000, 99000, 106000)])
        interval, detail = _canonical_vocal_overlap(
            row, left_timeline=left, right_timeline=right,
            issue_start=90.0, issue_end=110.0, policy=Policy(),
        )
        self.assertEqual(interval, (99.0, 105.0))
        self.assertEqual(detail["centers"], [100.0, 103.0])

    def test_dual_source_instrumental_match_does_not_confirm_vocal_overlap(self):
        paired = [
            {"mix_center": 100.0, "left_strong": True, "right_strong": True, "overlap_like": True},
            {"mix_center": 103.0, "left_strong": True, "right_strong": True, "overlap_like": True},
        ]
        windows = []
        for center in (100.0, 103.0):
            windows.extend([
                {"side": "left", "global_mix_center": center, "strong": True, "local": {"top1": {"source_center": 50.0}}},
                {"side": "right", "global_mix_center": center, "strong": True, "local": {"top1": {"source_center": 2.0}}},
            ])
        row = positional_row(paired=paired, windows=windows, action="overlap_candidate_advisory")
        left = timeline("left", [line(1, "vocal", 0, 10000, 98000, 105000)])
        right = timeline("right", [line(2, "right lyric", 0, 10000, 99000, 106000)])
        interval, detail = _canonical_vocal_overlap(
            row, left_timeline=left, right_timeline=right,
            issue_start=90.0, issue_end=110.0, policy=Policy(),
        )
        self.assertIsNone(interval)
        self.assertEqual(detail["reason"], "dual_source_support_not_inside_unique_canonical_vocal")


class TransitionFinalResolutionEndToEndTest(unittest.TestCase):
    def test_nonlexical_dual_vocal_overlap_emits_official_confirmed_overlap(self):
        run = {
            "task_fingerprint_sha256": TASK,
            "status": "review_required",
            "legacy_fallback_used": False,
            "transitions": [{"left_occurrence_id": "left", "right_occurrence_id": "right", "nominal_boundary": 100.0}],
            "issues": [{
                "kind": "transition_ambiguity", "code": "ambiguous_source_occurrence",
                "candidate_id": "candidate", "left_occurrence_id": "left", "right_occurrence_id": "right",
                "interval_start": 90.0, "interval_end": 110.0, "status": "review",
                "reason": "high audio score but ambiguous source occurrence",
            }],
        }
        lexical = {
            "schema_version": "adjacent-transition-editor-lexical-evidence-1.0",
            "task_fingerprint_sha256": TASK, "source_srt_sha256": SOURCE_SHA,
            "run_sha256": RUN_SHA, "run_artifact_id": ARTIFACT_ID,
            "automatic_authority_granted": False, "timing_mutation_performed": False,
            "transitions": [{
                "issue_candidate_id": "candidate", "left_occurrence_id": "left", "right_occurrence_id": "right",
                "identity_support": {"left_unique_cue_count": 0, "right_unique_cue_count": 2,
                    "left_unique_normalized_chars": 0, "right_unique_normalized_chars": 20,
                    "ambiguous_cue_count": 0, "unmatched_cue_count": 0, "enough_identity": False},
                "order_support": {"last_left_editor_end_ms": None, "first_right_editor_start_ms": 100000,
                    "sequential_gap_ms": None, "lexical_order_clear": False},
                "recommendation": "review", "automatic_authority_granted": False,
                "confirmed_overlap_authority_granted": False, "timing_mutation_performed": False,
            }],
        }
        paired = [
            {"mix_center": 100.0, "left_strong": True, "right_strong": True, "overlap_like": True},
            {"mix_center": 103.0, "left_strong": True, "right_strong": True, "overlap_like": True},
        ]
        windows = []
        for center, left_source, right_source in [(100.0, 5.0, 2.0), (103.0, 8.0, 5.0)]:
            windows.extend([
                {"side": "left", "global_mix_center": center, "strong": True, "local": {"top1": {"source_center": left_source}}},
                {"side": "right", "global_mix_center": center, "strong": True, "local": {"top1": {"source_center": right_source}}},
            ])
        positional = {
            "schema_version": "adjacent-transition-positional-v2-report-1",
            "task_fingerprint_sha256": TASK, "run_sha256": RUN_SHA, "run_artifact_id": ARTIFACT_ID,
            "authority": {"shadow_only": True, "automatic_review_decision": False, "timing_mutation_performed": False},
            "evidence": [positional_row(paired=paired, windows=windows, action="overlap_candidate_advisory")],
        }
        left = timeline("left", [line(1, "whooa oh", 0, 10000, 98000, 105000)])
        right = timeline("right", [line(2, "right lyric", 0, 10000, 99000, 106000)])
        decisions, report = authorize_final_transitions(
            run=run, base_run_artifact_id=ARTIFACT_ID, run_sha256=RUN_SHA,
            source_srt_sha256=SOURCE_SHA, lexical=lexical, positional=positional,
            timelines={"left": left, "right": right}, editor_cues=[cue(0, 100000, 102000, "right lyric")],
        )
        self.assertEqual(report["confirmed_overlap_count"], 1)
        self.assertEqual(report["remaining_unresolved_count"], 0)
        decision = decisions["review_items"][0]["decision"]
        self.assertEqual(decision["action"], "confirmed_overlap")
        self.assertEqual(decision["confirmed_interval"], [99.0, 105.0])


if __name__ == "__main__":
    unittest.main()
