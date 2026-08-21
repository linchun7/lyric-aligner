from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one occurrence, found {count}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: Path, marker: str, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    path.write_text(text.rstrip() + "\n\n" + block.strip() + "\n", encoding="utf-8")


bpm = ROOT / "lyric_aligner/timeline/bpm_sequence_reconcile.py"
smart = ROOT / "lyric_aligner/timeline/smart_policy.py"
test = ROOT / "scripts/test_v4_smart_bpm_bounded_stream_v123.py"
policy_test = ROOT / "scripts/test_v4_smart_bpm_bounded_stream_policy_v123.py"
skill = ROOT / "SKILL.md"
status = ROOT / "references/v4-status.md"
implementation = ROOT / "references/v4-implementation.md"
change_record = ROOT / "references/v4-change-record.md"

replace_once(
    bpm,
    "def _pairwise_rate(anchors: Sequence[_Anchor]) -> float | None:\n",
    '''def _is_unmapped_span(decision: MatchDecision | None) -> bool:\n    """Treat absent and zero-width canonical spans as the same unmapped state."""\n\n    if decision is None or decision.canonical_span is None:\n        return True\n    start, end = decision.canonical_span\n    return int(start) == int(end)\n\n\ndef _pairwise_rate(anchors: Sequence[_Anchor]) -> float | None:\n''',
)

replace_once(
    bpm,
    '''    for decision in block_decisions:\n        if decision.canonical_span is None:\n            continue\n''',
    '''    for decision in block_decisions:\n        if _is_unmapped_span(decision):\n            continue\n''',
)

replace_once(
    bpm,
    '''        if decision.canonical_span is None:\n            if (\n                len(cue.normalized) < 4\n''',
    '''        if _is_unmapped_span(decision):\n            if (\n                len(cue.normalized) < 4\n''',
)

replace_once(
    bpm,
    '''    if not block_cues or not gap or len(block_cues) > _BOUNDED_STREAM_MAX_CUES:\n        return None\n''',
    '''    if not block_cues or not gap or len(block_cues) > _BOUNDED_STREAM_MAX_CUES:\n        return None\n    # Latin/mixed canonical rows need token-boundary-aware layout reconstruction.\n    # The current character-owner repartitioner preserves editor whitespace and can\n    # therefore split canonical words even when normalized text is identical.  Keep\n    # the new multi-cue tier Chinese/CJK-only until token-aware rendering exists; the\n    # older mapped 1:1 BPM tier remains available for English/mixed lyrics.\n    if any(_LATIN_TOKEN_RE.search(item.text) for item in gap):\n        return None\n''',
)

replace_once(
    bpm,
    '''    spans = _stream_canonical_spans(rendered, gap)\n    if spans is None:\n        return None\n    return rendered, spans\n''',
    '''    spans = _stream_canonical_spans(rendered, gap)\n    if spans is None:\n        return None\n\n    # A mapped review may be corrected inside its existing canonical claim, but the\n    # broader bounded tier may not enlarge that claim into adjacent canonical rows.\n    # Enlarging a mapped span is exactly the cross-cue ownership failure that can\n    # make one editor cue absorb the previous/next lyric fragment.  Unmapped cues\n    # are the only cues allowed to acquire a new span in this tier.\n    for decision, span in zip(block_decisions, spans):\n        if decision.action != "review" or _is_unmapped_span(decision):\n            continue\n        assert decision.canonical_span is not None\n        old_start, old_end = decision.canonical_span\n        if span[0] < int(old_start) or span[1] > int(old_end):\n            return None\n    return rendered, spans\n''',
)

replace_once(
    bpm,
    '''                if original.canonical_span is None:\n                    region_unmapped += 1\n''',
    '''                if _is_unmapped_span(original):\n                    region_unmapped += 1\n''',
)

# Version/policy bump: the production acceptance behavior changed materially.
replace_once(
    smart,
    'SMART_POLICY_ID = "smart-validation-policy-2026-08-21-v1.2.3"',
    'SMART_POLICY_ID = "smart-validation-policy-2026-08-21-v1.2.4"',
)
text = smart.read_text(encoding="utf-8").replace("Smart v1.2.3 also", "Smart v1.2.4 also", 1)
smart.write_text(text, encoding="utf-8")

# Existing bounded-stream regressions should now use the production-shaped zero-width
# unmatched span rather than the synthetic-only None representation.
replace_once(
    test,
    '''        cues, canonical, decisions, metadata = _ready_fixture(\n            middle_two=("山河错碎", "汉到底都不同"),\n            middle_two_spans=(1, None),\n        )\n\n        replacements, updated, summary, models = recover_text_reviews_from_bpm_projection(\n''',
    '''        cues, canonical, decisions, metadata = _ready_fixture(\n            middle_two=("山河错碎", "汉到底都不同"),\n            middle_two_spans=(1, None),\n        )\n        # Production Text Repair represents an unmatched cue with a zero-width\n        # canonical span rather than canonical_span=None.\n        decisions[2] = replace(decisions[2], canonical_span=(2, 2))\n\n        replacements, updated, summary, models = recover_text_reviews_from_bpm_projection(\n''',
)
replace_once(test, "import unittest\n", "import unittest\nfrom dataclasses import replace\n")

inject = '''\n    def test_mapped_review_cannot_expand_into_adjacent_canonical_rows(self) -> None:\n        from lyric_aligner.timeline.bpm_sequence_reconcile import _is_unmapped_span\n\n        mapped = _decision(1, 1, action="review", score=0.2, reason="low_or_structurally_unsafe_similarity", source_text="错词", canonical_text="山河破碎")\n        self.assertFalse(_is_unmapped_span(mapped))\n        zero_width = replace(mapped, canonical_span=(1, 1))\n        self.assertTrue(_is_unmapped_span(zero_width))\n\n    def test_latin_bounded_stream_fails_closed_before_layout_repartition(self) -> None:\n        cues = [\n            _cue(0, 0, 1500, "start anchor"),\n            _cue(1, 2000, 3500, "alpha rong"),\n            _cue(2, 4000, 5500, "beta rong"),\n            _cue(3, 6000, 7500, "end anchor"),\n            _cue(4, 12000, 13500, "far anchor"),\n        ]\n        canonical = [\n            _canonical(0, 0, "start anchor"),\n            _canonical(1, 2000, "alpha right"),\n            _canonical(2, 4000, "beta right"),\n            _canonical(3, 6000, "end anchor"),\n            _canonical(4, 12000, "far anchor"),\n        ]\n        decisions = [\n            _decision(0, 0, action="unchanged", score=1.0, reason="canonical_content_matches_source_segmentation", source_text="start anchor", canonical_text="start anchor"),\n            _decision(1, 1, action="review", score=0.5, reason="low_or_structurally_unsafe_similarity", source_text="alpha rong", canonical_text="alpha right"),\n            _decision(2, 2, action="review", score=0.5, reason="low_or_structurally_unsafe_similarity", source_text="beta rong", canonical_text="beta right"),\n            _decision(3, 3, action="unchanged", score=1.0, reason="canonical_content_matches_source_segmentation", source_text="end anchor", canonical_text="end anchor"),\n            _decision(4, 4, action="unchanged", score=1.0, reason="canonical_content_matches_source_segmentation", source_text="far anchor", canonical_text="far anchor"),\n        ]\n        replacements, _, summary, _ = recover_text_reviews_from_bpm_projection(\n            cues, canonical, decisions, rate_prior_metadata_by_source={0: {"provenance": "bpm_derived", "value": 1.0}}\n        )\n        self.assertEqual(summary.bounded_stream_region_count, 0)\n        self.assertNotIn(1, replacements)\n        self.assertNotIn(2, replacements)\n'''
replace_once(test, "\n\nif __name__ == \"__main__\":\n", inject + "\n\nif __name__ == \"__main__\":\n")

replace_once(
    policy_test,
    '"smart-validation-policy-2026-08-21-v1.2.3",',
    '"smart-validation-policy-2026-08-21-v1.2.4",',
)

# Keep the main SKILL current without rewriting historical rationale.
for old, new in (
    ("Smart    -> Sequence Reconciliation + Anchor Timeline Repair v1.2.3", "Smart    -> Sequence Reconciliation + Anchor Timeline Repair v1.2.4"),
    ("smart-validation-policy-2026-08-21-v1.2.3", "smart-validation-policy-2026-08-21-v1.2.4"),
    ("Smart v1.2.3 仍然不读音频", "Smart v1.2.4 仍然不读音频"),
    ("#### v1.2.3 BPM-validated text recovery", "#### v1.2.4 BPM-validated text recovery"),
    ("Smart policy 已升到 v1.2.3", "Smart policy 已升到 v1.2.4"),
):
    text = skill.read_text(encoding="utf-8")
    if old in text:
        skill.write_text(text.replace(old, new, 1), encoding="utf-8")

append_once(
    change_record,
    "Smart v1.2.4 production acceptance hardening",
    '''## 2026-08-21 - Smart v1.2.4 production acceptance hardening\n\n- A private 578-cue rerun exposed three generic gaps in the newly added v1.2.3 bounded-stream tier; no real song/cue/lyric identifiers are committed.\n- Treat `canonical_span=None` and zero-width `[x,x]` spans as the same unmatched state so production-shaped Text Repair output can enter the intended bounded unmapped path.\n- A mapped review may not expand its canonical span into adjacent rows; only truly unmapped cues may acquire a new canonical span from bounded-stream evidence.\n- Multi-cue bounded recovery now fails closed when the target gap contains Latin text because the current character-owner renderer preserves editor whitespace and is not token-boundary-aware. Existing mapped 1:1 BPM recovery remains available for English/mixed lyrics.\n- Add production-shaped synthetic regressions for zero-width unmatched semantics, mapped-span ownership, and Latin bounded fail-closed behavior.\n''',
)
append_once(
    implementation,
    "Smart v1.2.4 bounded-stream production guards",
    '''### Smart v1.2.4 bounded-stream production guards\n\n`timeline/bpm_sequence_reconcile.py` normalizes absent and zero-width canonical claims into one unmapped semantic state. The v1.2.3 bilateral stream path is further constrained so a previously mapped review cannot expand beyond its existing canonical span; this prevents canonical correctness at region level from overriding editor cue ownership. Until token-boundary-aware Latin rendering exists, the new multi-cue bounded tier rejects gaps containing Latin text; the older mapped 1:1 BPM text path remains unchanged.\n''',
)
append_once(
    status,
    "Smart v1.2.4 production-acceptance closeout",
    '''## Smart v1.2.4 production-acceptance closeout\n\nSmart v1.2.4 hardens the bounded-stream tier after a private 578-cue acceptance rerun: production zero-width unmatched spans are recognized, mapped reviews cannot absorb adjacent canonical rows, and Latin/mixed bounded repartition fails closed until token-aware display layout is implemented. These changes do not increase timing authority, do not alter cue count/timing, and do not lower the v1.2.2 mapped 1:1 recovery thresholds.\n''',
)

print("v1.2.4 hardening patch applied")
