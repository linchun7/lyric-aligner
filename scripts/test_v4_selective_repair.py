from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from lyric_aligner.alignment.asr_executor import (
    FasterWhisperExecutionConfig,
    execute_faster_whisper_jobs,
)
from lyric_aligner.alignment.local_acoustic_match import (
    execute_local_source_match_jobs,
)
from lyric_aligner.alignment.selective_repair import (
    SelectiveRepairPlanningError,
    build_selective_repair_plan,
    canonical_text_by_job_id,
)
from lyric_aligner.text_repair import SubtitleCue
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        info = SimpleNamespace(language="en", language_probability=0.9)
        return iter([]), info


def _cue(ordinal: int, start_ms: int, end_ms: int, text: str) -> SubtitleCue:
    def clock(ms: int) -> str:
        hour, rem = divmod(ms, 3_600_000)
        minute, rem = divmod(rem, 60_000)
        second, millis = divmod(rem, 1000)
        return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"

    return SubtitleCue(
        ordinal=ordinal,
        number=str(ordinal + 1),
        timing=f"{clock(start_ms)} --> {clock(end_ms)}",
        text=text,
        normalized=text,
        raw_block_index=ordinal * 2,
    )


def _canonical(
    ordinal: int,
    source_ordinal: int,
    time_ms: int,
    text: str,
) -> TimedCanonicalOccurrence:
    return TimedCanonicalOccurrence(
        ordinal=ordinal,
        source=f"{source_ordinal + 1:02d}.lrc",
        source_ordinal=source_ordinal,
        time_ms=time_ms,
        text=text,
        normalized=text,
    )


class SelectiveRepairTests(unittest.TestCase):
    def smart_report(self):
        return {
            "mode": "smart_anchor_timeline_repair_no_audio",
            "audio_read": False,
            "models": [
                {
                    "source_ordinal": 0,
                    "source": "01.lrc",
                    "rate": 1.1,
                    "status": "ready",
                }
            ],
            "timing_decisions": [
                {
                    "cue_ordinal": 0,
                    "canonical_ordinal": 0,
                    "action": "preserve",
                    "reason": "timing_matches_anchor_model",
                },
                {
                    "cue_ordinal": 1,
                    "canonical_ordinal": 1,
                    "action": "review",
                    "reason": "insufficient_independent_neighbor_support",
                },
                {
                    "cue_ordinal": 2,
                    "canonical_ordinal": 2,
                    "action": "review",
                    "reason": "non_A_identity_not_auto_repairable",
                },
            ],
            "text_decisions": [
                {"cue_ordinal": 0, "canonical_ordinal": 0, "action": "unchanged"},
                {"cue_ordinal": 1, "canonical_ordinal": 1, "action": "unchanged"},
                {"cue_ordinal": 2, "canonical_ordinal": 2, "action": "unchanged"},
            ],
        }

    def sample_inputs(self):
        cues = [
            _cue(0, 10_000, 11_000, "中文歌词"),
            _cue(1, 20_000, 21_000, "I keep running"),
            _cue(2, 30_000, 31_000, "中文 and English"),
        ]
        canonical = [
            _canonical(0, 0, 11_000, "中文歌词"),
            _canonical(1, 0, 22_000, "I keep running"),
            _canonical(2, 0, 33_000, "中文 and English"),
        ]
        return cues, canonical

    def test_only_smart_review_cues_become_bounded_pro_jobs(self) -> None:
        cues, canonical = self.sample_inputs()
        plan = build_selective_repair_plan(
            smart_report=self.smart_report(),
            cues=cues,
            canonical=canonical,
            language_by_source={0: "zh"},
        )

        self.assertEqual(plan["summary"]["job_count"], 2)
        self.assertEqual([row["cue_ordinal"] for row in plan["jobs"]], [1, 2])
        english, mixed = plan["jobs"]
        self.assertEqual(english["asr_language_hint"], "auto")
        self.assertTrue(english["asr_force_auto_detect"])
        self.assertEqual(mixed["asr_language_hint"], "auto")
        self.assertTrue(mixed["asr_force_auto_detect"])
        self.assertEqual(english["rate_prior"], 1.1)
        self.assertIn("source_local_acoustic_match", english["requested_capabilities"])
        self.assertLess(english["mix_window_ms"][0], 20_000)
        self.assertGreater(english["mix_window_ms"][1], 21_000)
        self.assertNotIn("I keep running", str(plan))

    def test_unstable_smart_rate_does_not_narrow_pro_search(self) -> None:
        cues, canonical = self.sample_inputs()
        report = self.smart_report()
        report["models"][0]["status"] = "unstable"
        plan = build_selective_repair_plan(
            smart_report=report,
            cues=cues,
            canonical=canonical,
            language_by_source={0: "zh"},
        )
        self.assertIsNone(plan["jobs"][0]["rate_prior"])
        self.assertIsNone(plan["jobs"][1]["rate_prior"])

    def test_smart_report_count_mismatch_fails_closed(self) -> None:
        cues, canonical = self.sample_inputs()
        report = self.smart_report()
        report["cue_count"] = len(cues) + 1
        with self.assertRaisesRegex(
            SelectiveRepairPlanningError,
            "Smart report/SRT cue count mismatch",
        ):
            build_selective_repair_plan(
                smart_report=report,
                cues=cues,
                canonical=canonical,
                language_by_source={0: "zh"},
            )

    def test_private_canonical_lookup_is_reconstructed_outside_plan(self) -> None:
        cues = [_cue(0, 20_000, 21_000, "I keep running")]
        canonical = [_canonical(0, 0, 22_000, "I keep running")]
        report = self.smart_report()
        report["timing_decisions"] = [
            {
                "cue_ordinal": 0,
                "canonical_ordinal": 0,
                "action": "review",
                "reason": "independent_model_not_ready",
            }
        ]
        report["text_decisions"] = [
            {"cue_ordinal": 0, "canonical_ordinal": 0, "action": "unchanged"}
        ]
        plan = build_selective_repair_plan(
            smart_report=report,
            cues=cues,
            canonical=canonical,
            language_by_source={0: "zh"},
        )
        lookup = canonical_text_by_job_id(plan, canonical)
        self.assertEqual(next(iter(lookup.values())), "I keep running")
        self.assertNotIn("I keep running", str(plan))

    def test_asr_uses_local_canonical_language_not_whole_track_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            plan = {
                "mode": "plan_only",
                "backend_execution_performed": False,
                "jobs": [
                    {
                        "job_id": "english-rap",
                        "occurrence_id": "occ-1",
                        "canonical_line_index": 0,
                        "language_profile": "zh",
                        "mix_window_ms": [1000, 6000],
                        "requested_capabilities": ["mix_asr"],
                    },
                    {
                        "job_id": "mixed-line",
                        "occurrence_id": "occ-1",
                        "canonical_line_index": 1,
                        "language_profile": "zh",
                        "mix_window_ms": [7000, 12000],
                        "requested_capabilities": ["mix_asr"],
                    },
                ],
            }
            fake = FakeModel()
            execute_faster_whisper_jobs(
                audio_path=audio,
                plan=plan,
                canonical_text_by_job_id={
                    "english-rap": "I keep running all night",
                    "mixed-line": "继续 running all night",
                },
                config=FasterWhisperExecutionConfig(model_id="test-model"),
                model_factory=lambda *args, **kwargs: fake,
            )

        self.assertEqual(fake.calls[0][1]["language"], "en")
        self.assertIsNone(fake.calls[1][1]["language"])

    def test_local_acoustic_executor_returns_timing_evidence_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mix = root / "mix.wav"
            source = root / "source.wav"
            mix.write_bytes(b"fake")
            source.write_bytes(b"fake")
            plan = {
                "mode": "plan_only",
                "backend_execution_performed": False,
                "jobs": [
                    {
                        "job_id": "local-1",
                        "source_ordinal": 0,
                        "cue_ordinal": 4,
                        "mix_window_ms": [10_000, 15_000],
                        "source_window_ms": [19_000, 30_000],
                        "expected_source_time_ms": 24_500,
                        "editor_cue_start_ms": 12_000,
                        "rate_prior": 1.1,
                        "requested_capabilities": ["source_local_acoustic_match"],
                    }
                ],
            }
            fake_features = SimpleNamespace(duration_seconds=11.0)
            best = SimpleNamespace(
                source_start=3.0,
                estimated_slope=1.1,
                fused_score=0.91,
                chroma_score=0.94,
                mfcc_score=0.80,
                feature_agreement=2,
            )
            retrieval = SimpleNamespace(top1=best, margin=0.08, ambiguous=False)

            with patch(
                "lyric_aligner.alignment.local_acoustic_match.extract_harmonic_features",
                return_value=fake_features,
            ), patch(
                "lyric_aligner.alignment.local_acoustic_match.retrieve_coarse_window",
                return_value=retrieval,
            ):
                result = execute_local_source_match_jobs(
                    mix_audio_path=mix,
                    plan=plan,
                    source_audio_by_source_ordinal={0: source},
                    audio_loader=lambda *args, **kwargs: np.zeros(80_000, dtype=np.float32),
                )

        row = result["jobs"][0]
        self.assertTrue(row["reliable_local_match"])
        self.assertFalse(row["timing_mutation_performed"])
        self.assertEqual(row["matched_source_start_ms"], 22_000)
        self.assertEqual(row["predicted_mix_start_ms"], 12_273)
        self.assertEqual(row["editor_start_residual_ms"], -273)


if __name__ == "__main__":
    unittest.main()
