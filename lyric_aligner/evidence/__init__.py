"""Non-authoritative evidence layers for Lyric Aligner v4.

Evidence modules may support review/calibration but do not own canonical lyric
text or Source-to-Mix timeline truth.
"""

from lyric_aligner.evidence.editor import (
    EDITOR_SHADOW_POLICY_ID,
    EditorEvidenceError,
    build_editor_evidence,
    evidence_for_line,
)

__all__ = [
    "EDITOR_SHADOW_POLICY_ID",
    "EditorEvidenceError",
    "build_editor_evidence",
    "evidence_for_line",
]
