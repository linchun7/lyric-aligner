#!/usr/bin/env python3
"""Reliability policy for subtitle-editor evidence by canonical language."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from language_profiles import language_code


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


_PROFILES = {
    "en": EditorEvidenceProfile("en", "direct_text", 0.85, 0.75, True, False),
    "zh": EditorEvidenceProfile("zh", "direct_text", 0.75, 0.70, True, False),
    "yue": EditorEvidenceProfile("yue", "timing_hint", 0.05, 0.25, False, False),
    "ko": EditorEvidenceProfile("ko", "phonetic_hint", 0.10, 0.30, False, True),
    "ja": EditorEvidenceProfile("ja", "phonetic_hint", 0.10, 0.30, False, True),
    "mixed": EditorEvidenceProfile("mixed", "timing_hint", 0.15, 0.35, False, True),
    "auto": EditorEvidenceProfile("auto", "timing_hint", 0.0, 0.20, False, False),
    "generic": EditorEvidenceProfile("generic", "timing_hint", 0.0, 0.20, False, False),
}


def editor_evidence_profile(language: str) -> EditorEvidenceProfile:
    """Return a conservative policy for editor text/timing evidence.

    The profile is deliberately a policy layer only. Production alignment can
    consume it incrementally without silently changing legacy v3.9 decisions.
    """

    return _PROFILES[language_code(language)]
