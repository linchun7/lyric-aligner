#!/usr/bin/env python3
"""Compatibility adapter for the package-owned v4 editor evidence policy.

New production/shadow work must use ``lyric_aligner.evidence.editor``.  This
module remains for older diagnostic imports and intentionally cannot grant more
trust than the package policy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from language_profiles import language_code
from lyric_aligner.evidence.editor import trust_profile


@dataclass(frozen=True)
class EditorEvidenceProfile:
    language: str
    mode: str
    text_weight: float
    timing_weight: float
    allow_direct_canonical_match: bool
    allow_phonetic_hint: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def editor_evidence_profile(language: str) -> EditorEvidenceProfile:
    """Return a conservative line-level compatibility view of v4 shadow policy."""

    code = language_code(language)
    mode = (
        "direct_text"
        if code in {"en", "zh"}
        else "phonetic_hint"
        if code in {"ko", "ja"}
        else "timing_hint"
    )
    # mixed/auto/generic are routed per span by the package implementation.  A
    # line-level compatibility caller receives timing-only trust, never a text
    # escalation.
    package_language = code if code in {"en", "zh", "yue", "ko", "ja"} else "generic"
    profile = trust_profile(package_language, mode)
    return EditorEvidenceProfile(
        language=code,
        mode=mode,
        text_weight=profile.text_weight,
        timing_weight=profile.timing_weight,
        allow_direct_canonical_match=mode == "direct_text" and profile.text_weight > 0,
        allow_phonetic_hint=mode == "phonetic_hint" and profile.text_weight > 0,
    )
