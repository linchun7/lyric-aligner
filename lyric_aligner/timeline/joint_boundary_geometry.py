"""Choose compatible, already authorized edge edits across a whole timeline.

This solver supplies geometry, never acoustic or statistical authority. Keeping
every edge is always a candidate. Adjacent corrections can enable each other;
their order in a dictionary or calibration file cannot affect the result.
"""
from __future__ import annotations

from itertools import product
from typing import Mapping, Sequence


def select_compatible_edits(
    rows: Sequence[Mapping], edits: Sequence[Mapping],
) -> set[str]:
    """Maximize (accepted edits, corrected distance), subject to baseline overlap.

    Input edits must already be authorized, with a unique id and one proposal
    per (zero-based row_index, boundary_kind). No end/start interpolation or
    clipping is performed. The active interval frontier also retains non-adjacent nested cues.
    Excessive frontier branching is rejected before producing a partial result.
    """
    indexed = {}
    ids = set()
    for edit in edits:
        ident, index, kind, value = (edit[k] for k in ('id', 'row_index', 'boundary_kind', 'selected_ms'))
        if not isinstance(ident, str) or not ident or ident in ids:
            raise ValueError('edit identities must be unique and nonempty')
        if type(index) is not int or not 0 <= index < len(rows) or kind not in ('start', 'end'):
            raise ValueError('invalid edit target')
        if type(value) is not int or value < 0 or (index, kind) in indexed:
            raise ValueError('invalid or duplicate boundary proposal')
        ids.add(ident)
        indexed[index, kind] = edit
    baseline = [(int(r['start_ms']), int(r['end_ms'])) for r in rows]
    if any(s < 0 or e <= s for s, e in baseline):
        raise ValueError('invalid baseline duration')
    if any(baseline[i][0] > baseline[i+1][0] for i in range(len(rows)-1)):
        raise ValueError('baseline starts are not ordered')
    if not rows:
        return set()
    # A future start may move earlier than its editor value. Keep every earlier
    # interval that could overlap any remaining start, not just the last row.
    earliest = [min(start, indexed.get((i, 'start'), {}).get('selected_ms', start))
                for i, (start, _) in enumerate(baseline)]
    suffix_min = [float('inf')] * (len(rows)+1)
    for i in range(len(rows)-1, -1, -1):
        suffix_min[i] = min(earliest[i], suffix_min[i+1])
    states = {(None, ()): ((0, 0), None)}
    nodes = []
    for i, (start, end) in enumerate(baseline):
        choices = []
        for kind, old in (('start', start), ('end', end)):
            edit = indexed.get((i, kind))
            choices.append([(old, None)] + ([(edit['selected_ms'], edit['id'])] if edit and edit['selected_ms'] != old else []))
        options = [(s, e, tuple(x for x in (sid, eid) if x),
                    (int(sid is not None)+int(eid is not None), abs(s-start)+abs(e-end)))
                   for (s, sid), (e, eid) in product(*choices) if e > s]
        updated = {}
        for (previous_start, active), (score, parent) in states.items():
            for s, e, accepted, utility in options:
                if previous_start is not None and previous_start > s:
                    continue
                if any(max(0, pe-s) > max(0, baseline[j][1]-start) for j, pe in active):
                    continue
                frontier = tuple((j, pe) for j, pe in (*active, (i, e)) if pe > suffix_min[i+1])
                key = (s, frontier)
                value = (score[0]+utility[0], score[1]+utility[1])
                if key not in updated or value > updated[key][0]:
                    nodes.append((parent, accepted))
                    updated[key] = (value, len(nodes)-1)
                if len(updated) > 4096:
                    raise ValueError('too many simultaneously overlapping boundary alternatives')
        states = updated
    _, winner = max(states.values(), key=lambda entry: entry[0])
    accepted = set()
    while winner is not None:
        winner, chosen = nodes[winner]
        accepted.update(chosen)
    return accepted
