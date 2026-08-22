"""Stable import surface for the current production Smart policy.

Versioned Smart policy modules remain immutable historical implementations.
Production consumers should import through this module so a future Smart
promotion changes one current-policy binding instead of independently updating
the CLI, Pro compatibility gate, and tests.
"""

from __future__ import annotations

from lyric_aligner.timeline.smart_policy import SMART_SCHEMA_VERSION
from lyric_aligner.timeline.smart_policy_v126 import SMART_TIMING_ACTIONABLE_SHIFT_MS
from lyric_aligner.timeline.smart_policy_v127 import (
    SMART_POLICY_ID,
    smart_repair_srt_text_v127,
)

smart_repair_srt_text = smart_repair_srt_text_v127

__all__ = (
    "SMART_POLICY_ID",
    "SMART_SCHEMA_VERSION",
    "SMART_TIMING_ACTIONABLE_SHIFT_MS",
    "smart_repair_srt_text",
)
