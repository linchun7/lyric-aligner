from pathlib import Path

bpm_path = Path('lyric_aligner/timeline/bpm_sequence_reconcile.py')
text = bpm_path.read_text(encoding='utf-8')
marker = '''            gap = rows[left_pos + 1 : right_pos]\n            candidate = _bounded_stream_candidate(\n'''
replacement = '''            gap = rows[left_pos + 1 : right_pos]\n\n            # v1.2.3 is additive: a broader bounded stream must never reopen a\n            # cue that v1.2.2 deliberately left review because editor text\n            # already owns an adjacent canonical fragment or a canonical line\n            # is visibly split across existing editor cues. Those ownership\n            # signals remain stronger than LRC row grouping.\n            inherited_guard_blocked = False\n            for guard_cue, guard_decision in zip(block, typed_decisions):\n                if guard_decision.action != "review":\n                    continue\n                guard_ordinal = _single_span(guard_decision)\n                if guard_ordinal is None:\n                    continue\n                guard_occurrence = canonical_by_ordinal.get(guard_ordinal)\n                if guard_occurrence is None:\n                    inherited_guard_blocked = True\n                    break\n                if (\n                    _split_continuation_risk(guard_cue, guard_occurrence, cues)\n                    or _adjacent_lexical_overlap_risk(\n                        guard_occurrence, guard_cue, lexical_rows\n                    )\n                ):\n                    inherited_guard_blocked = True\n                    break\n            if inherited_guard_blocked:\n                continue\n\n            candidate = _bounded_stream_candidate(\n'''
if marker not in text:
    raise SystemExit('bounded stage insertion marker not found')
text = text.replace(marker, replacement, 1)
bpm_path.write_text(text, encoding='utf-8')

test_path = Path('scripts/test_v4_smart_bpm_bounded_stream_v123.py')
test = test_path.read_text(encoding='utf-8')
test = test.replace(
    'self.assertEqual(summary.bounded_stream_cue_count, 2)\n        self.assertEqual(summary.bounded_stream_unmapped_cue_count, 1)\n        self.assertEqual(summary.resolved_review_cue_count, 2)',
    '# The mapped review is intentionally solved first by the unchanged v1.2.2\n        # 1:1 tier; the bounded tier adds only the formerly-unmapped cue.\n        self.assertEqual(summary.bounded_stream_cue_count, 1)\n        self.assertEqual(summary.bounded_stream_unmapped_cue_count, 1)\n        self.assertEqual(summary.resolved_review_cue_count, 2)',
    1,
)
test_path.write_text(test, encoding='utf-8')
