"""Smart v1.2.9 contextual canonical-role safety binding.

The v1.2.8 decision/report semantics remain unchanged.  This current-policy
wrapper records the shared-parser promotion that allows a bare CJK role label
only when an explicit multi-person cast row in the same canonical file proves
that exact name.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from lyric_aligner.text_repair import (
    DEFAULT_AUTO_THRESHOLD,
    CanonicalLine as RepairCanonicalLine,
)
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.timeline.smart_policy_v128 import smart_repair_srt_text_v128

SMART_POLICY_ID = "smart-validation-policy-2026-08-22-v1.2.9"


def smart_repair_srt_text_v129(
    source_text: str,
    timed_canonical: Sequence[TimedCanonicalOccurrence],
    repair_canonical: Sequence[RepairCanonicalLine],
    *,
    auto_threshold: float = DEFAULT_AUTO_THRESHOLD,
    rate_prior_by_source: Mapping[int, float] | None = None,
    rate_prior_metadata_by_source: Mapping[int, Mapping[str, object]] | None = None,
) -> tuple[str, dict[str, object]]:
    rendered, base_report = smart_repair_srt_text_v128(
        source_text,
        timed_canonical,
        repair_canonical,
        auto_threshold=auto_threshold,
        rate_prior_by_source=rate_prior_by_source,
        rate_prior_metadata_by_source=rate_prior_metadata_by_source,
    )
    report = dict(base_report)
    report["policy_id"] = SMART_POLICY_ID
    report["canonical_role_context_semantics"] = (
        "bare_cjk_role_requires_same_file_explicit_multi_cast_membership"
    )
    return rendered, report


__all__ = (
    "SMART_POLICY_ID",
    "smart_repair_srt_text_v129",
)
