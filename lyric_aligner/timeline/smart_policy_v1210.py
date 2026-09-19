"""Smart v1.2.10 split-line timing safety binding.

The v1.2.9 text and role decisions remain unchanged.  This policy records the
timing correction that prevents multiple editor cues mapped to one canonical
line from reusing that line's single onset.  An internal editor boundary gets
a timing hypothesis only when it aligns exactly with reliable canonical token
timing; otherwise it remains explicitly unvalidated.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from lyric_aligner.text_repair import (
    DEFAULT_AUTO_THRESHOLD,
    CanonicalLine as RepairCanonicalLine,
)
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.timeline.smart_policy_v129 import smart_repair_srt_text_v129

SMART_POLICY_ID = "smart-validation-policy-2026-08-22-v1.2.10"


def smart_repair_srt_text_v1210(
    source_text: str,
    timed_canonical: Sequence[TimedCanonicalOccurrence],
    repair_canonical: Sequence[RepairCanonicalLine],
    *,
    auto_threshold: float = DEFAULT_AUTO_THRESHOLD,
    rate_prior_by_source: Mapping[int, float] | None = None,
    rate_prior_metadata_by_source: Mapping[int, Mapping[str, object]] | None = None,
) -> tuple[str, dict[str, object]]:
    rendered, base_report = smart_repair_srt_text_v129(
        source_text,
        timed_canonical,
        repair_canonical,
        auto_threshold=auto_threshold,
        rate_prior_by_source=rate_prior_by_source,
        rate_prior_metadata_by_source=rate_prior_metadata_by_source,
        _segmentation_internal_boundary_guard=True,
    )
    report = dict(base_report)
    report["policy_id"] = SMART_POLICY_ID
    report["canonical_role_context_semantics"] = (
        "bare_cjk_role_requires_same_file_explicit_multi_cast_or_strict_ensemble_context"
    )
    report["segmentation_internal_timing_semantics"] = (
        "multi_cue_single_line_internal_onset_requires_exact_word_token_boundary"
    )
    return rendered, report


__all__ = (
    "SMART_POLICY_ID",
    "smart_repair_srt_text_v1210",
)
