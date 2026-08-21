from pathlib import Path

bpm_path = Path('lyric_aligner/timeline/bpm_sequence_reconcile.py')
text = bpm_path.read_text(encoding='utf-8')
pre = '''            # v1.2.3 is additive: a broader bounded stream must never reopen a\n            # cue that v1.2.2 deliberately left review because editor text\n            # already owns an adjacent canonical fragment or a canonical line\n            # is visibly split across existing editor cues. Those ownership\n            # signals remain stronger than LRC row grouping.\n            inherited_guard_blocked = False\n            for guard_cue, guard_decision in zip(block, typed_decisions):\n                if guard_decision.action != "review":\n                    continue\n                guard_ordinal = _single_span(guard_decision)\n                if guard_ordinal is None:\n                    continue\n                guard_occurrence = canonical_by_ordinal.get(guard_ordinal)\n                if guard_occurrence is None:\n                    inherited_guard_blocked = True\n                    break\n                if (\n                    _split_continuation_risk(guard_cue, guard_occurrence, cues)\n                    or _adjacent_lexical_overlap_risk(\n                        guard_occurrence, guard_cue, lexical_rows\n                    )\n                ):\n                    inherited_guard_blocked = True\n                    break\n            if inherited_guard_blocked:\n                continue\n\n            candidate = _bounded_stream_candidate(\n'''
post = '''            candidate = _bounded_stream_candidate(\n'''
if pre not in text:
    raise SystemExit('old inherited guard block not found')
text = text.replace(pre, post, 1)
marker = '''            candidate_texts, candidate_spans = candidate\n            region_changed = False\n'''
replacement = '''            candidate_texts, candidate_spans = candidate\n\n            # Preserve every v1.2.2 ownership/split fail-closed decision unless\n            # the bounded proof validates the existing editor-owned text exactly.\n            # This permits a canonical/LRC row to span multiple editor cues when\n            # their combined stream is already right, but never uses the broader\n            # v1.2.3 path to delete or move an adjacent fragment that v1.2.2\n            # deliberately protected.\n            inherited_guard_blocked = False\n            for guard_cue, guard_decision, candidate_text in zip(\n                block, typed_decisions, candidate_texts\n            ):\n                if guard_decision.action != "review":\n                    continue\n                guard_ordinal = _single_span(guard_decision)\n                if guard_ordinal is None:\n                    continue\n                guard_occurrence = canonical_by_ordinal.get(guard_ordinal)\n                if guard_occurrence is None:\n                    inherited_guard_blocked = True\n                    break\n                guarded = (\n                    _split_continuation_risk(guard_cue, guard_occurrence, cues)\n                    or _adjacent_lexical_overlap_risk(\n                        guard_occurrence, guard_cue, lexical_rows\n                    )\n                )\n                current_text = _decision_working_text(guard_cue, guard_decision)\n                if guarded and (\n                    _normalize_for_match(candidate_text)\n                    != _normalize_for_match(current_text)\n                ):\n                    inherited_guard_blocked = True\n                    break\n            if inherited_guard_blocked:\n                continue\n\n            region_changed = False\n'''
if marker not in text:
    raise SystemExit('candidate marker not found')
text = text.replace(marker, replacement, 1)
bpm_path.write_text(text, encoding='utf-8')

test_path = Path('scripts/test_v4_smart_bpm_bounded_stream_v123.py')
test = test_path.read_text(encoding='utf-8')
test = test.replace(
    'self.assertTrue(updated[1].reason.startswith("sequence_projection_confirms_bpm_bounded_stream"))\n        self.assertTrue(updated[2].reason.startswith("sequence_projection_confirms_bpm_bounded_stream"))',
    'self.assertEqual(updated[1].reason, "bpm_projection_confirms_mapped_canonical")\n        self.assertTrue(updated[2].reason.startswith("sequence_projection_confirms_bpm_bounded_stream"))',
    1,
)
test_path.write_text(test, encoding='utf-8')
