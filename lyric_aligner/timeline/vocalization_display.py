"""Lossless display grouping of reviewed continuous non-lexical vocalizations.

Groups inherit existing outer times and retain every lyric token. Removed
internal display boundaries are not replaced by claimed acoustic boundaries.
"""
import re

POLICY = 'reviewed-vocalization-display-group-1.0'


def _signature(text):
    words = text.casefold().split()
    if len(words) >= 2 and len(set(words)) == 1 and words[0] in {'na', 'la', 'ah', 'oh'}:
        return words[0], len(words)
    if re.fullmatch(r'啊{2,}', text):
        return '啊', len(text)
    return None


def group_display_rows(rows, records):
    """Group at most four consecutive canonical lines with reviewed gap coverage."""
    confirmed = [r for r in records if r.get('human_confirmed') is True and r.get('presence') == 'present']
    output, groups = [], []
    i = 0
    while i < len(rows):
        first = rows[i]
        signature = _signature(first['text'])
        members = [i]
        if signature is not None and str(first.get('lrc_indices', '')).isdigit():
            for j in range(i+1, min(len(rows), i+4)):
                previous, current = rows[j-1], rows[j]
                if (current.get('track') != first.get('track') or _signature(current['text']) != signature or
                    not str(current.get('lrc_indices', '')).isdigit() or
                    int(current['lrc_indices']) != int(previous['lrc_indices'])+1):
                    break
                gap_start, gap_end = int(previous['end_ms']), int(current['start_ms'])
                if not 0 <= gap_end-gap_start <= 1000 or int(current['end_ms'])-int(first['start_ms']) > 30000:
                    break
                # Human presence is used only to justify continuous display over
                # an existing gap. It does not certify the individual line edges.
                if not any(r['track'] == first['track'] and _signature(r['text']) == signature and
                           r['lrc_indices'] in {str(rows[k]['lrc_indices']) for k in range(i,j+1)} and
                           r['start_ms'] <= gap_start <= gap_end <= r['end_ms'] for r in confirmed):
                    break
                # Filling a gap must not cover an unrelated simultaneous cue.
                if any(k < i or k > j for k,r in enumerate(rows)
                       if min(int(r['end_ms']),gap_end) > max(int(r['start_ms']),gap_start)):
                    break
                members.append(j)
        last = rows[members[-1]]
        result = dict(start_ms=int(first['start_ms']),end_ms=int(last['end_ms']),
                      text='\n'.join(rows[k]['text'] for k in members))
        output.append(result)
        if len(members) > 1:
            groups.append(dict(output_position=len(output),member_positions=[k+1 for k in members],
                               canonical_indices=[rows[k]['lrc_indices'] for k in members],
                               start_from_position=i+1,end_from_position=members[-1]+1,
                               start_ms=result['start_ms'],end_ms=result['end_ms']))
        i = members[-1]+1
    return output, groups
