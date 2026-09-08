"""Restore immutable editor cue topology with verified canonical text content.

This is a baseline choice, not an audio-boundary refinement. It never obtains
timing authority from a lyric clock or from an ASR confidence score.
"""
from __future__ import annotations

from dataclasses import replace
from difflib import SequenceMatcher

from lyric_aligner.srt import Cue
from lyric_aligner.text_repair import _normalize_for_match

POLICY_ID = "immutable-editor-canonical-stream-1.0"
REGION_POLICY_ID = "immutable-editor-unique-canonical-region-1.0"


def canonical_editor_regions(editor_cues: list[Cue], canonical_lines: list[str]) -> list[dict]:
    """Find exact unique regions with whole editor cues and canonical lines.

    Differences outside a region remain unresolved. A repeated region is never
    assigned to the first matching chorus. Returns half-open positional ranges.
    """
    def stream(parts):
        text = ''; boundaries = {0: 0}
        for index, part in enumerate(parts):
            key = _normalize_for_match(part)
            if not key:
                raise ValueError('empty normalized content cannot identify a region')
            text += key
            boundaries[len(text)] = index + 1
        return text, boundaries
    left, cue_edges = stream(c.text for c in editor_cues)
    right, line_edges = stream(canonical_lines)
    regions = []
    for block in SequenceMatcher(None, left, right, autojunk=False).get_matching_blocks():
        edges = sorted(p for p in cue_edges if block.a <= p <= block.a + block.size
                       and p - block.a + block.b in line_edges)
        if len(edges) < 2:
            continue
        first, last = edges[0], edges[-1]
        text = left[first:last]
        if not text or left.find(text) != left.rfind(text) or right.find(text) != right.rfind(text):
            continue
        regions.append(dict(editor_cue_range=[cue_edges[first], cue_edges[last]],
                            canonical_line_range=[line_edges[first-block.a+block.b],
                                                  line_edges[last-block.a+block.b]]))
    return regions


def canonical_editor_cues(editor_cues: list[Cue], repaired_cues: list[Cue],
                          canonical_lines: list[str]) -> tuple[list[Cue], list[dict]]:
    """Keep every exact editor interval, reflow an identical canonical stream.

    Text identity must already hold, including order and repetitions. This
    function does not guess missing words or allocate them to neighbouring cues.
    Canonical punctuation/spacing is rendered inside the existing text ownership.
    """
    if not editor_cues or len(editor_cues) != len(repaired_cues) or not canonical_lines:
        raise ValueError("editor preservation requires paired cues and canonical text")
    for source, repaired in zip(editor_cues,repaired_cues):
        if (source.number,source.start_ms,source.end_ms)!=(repaired.number,repaired.start_ms,repaired.end_ms):
            raise ValueError("repaired text changed immutable editor identity/timing")
    canonical = ' '.join(canonical_lines)
    keys = [_normalize_for_match(c.text) for c in repaired_cues]
    if any(not key for key in keys) or ''.join(keys) != _normalize_for_match(canonical):
        raise ValueError("repaired editor content is not the complete canonical stream")
    # Preserve exact glyphs while indexing normalized content. Refuse uncommon
    # cross-glyph normalization instead of cutting a composed character in half.
    content = []
    positions = []
    for index,char in enumerate(canonical):
        normalized = _normalize_for_match(char)
        content.extend(normalized)
        positions.extend([index]*len(normalized))
    if ''.join(content) != _normalize_for_match(canonical):
        raise ValueError("canonical normalization crosses glyph boundaries")
    line_spans=[];line_offset=0
    for index,text in enumerate(canonical_lines):
        line_end=line_offset+len(_normalize_for_match(text))
        line_spans.append((index,line_offset,line_end));line_offset=line_end
    output=[];ownership=[];offset=0
    for source,key in zip(editor_cues,keys):
        end=offset+len(key)
        if end<len(positions) and positions[end-1]==positions[end]:
            raise ValueError("editor cue boundary splits a canonical glyph")
        first=0 if offset==0 else positions[offset]
        last=len(canonical) if end==len(positions) else positions[end]
        text=canonical[first:last].strip()
        if _normalize_for_match(text)!=key:
            raise ValueError("canonical text ownership changed during rendering")
        output.append(replace(source,text=text))
        onset_lines=[index for index,a,b in line_spans if a==offset and b>a]
        ownership.append(dict(original_cue=source.number,canonical_content_start=offset,
            canonical_content_end=end,timing_basis='immutable_editor',
            canonical_line_index=onset_lines[0] if len(onset_lines)==1 else None,
            canonical_line_indices=[index for index,a,b in line_spans if a<end and b>offset]))
        offset=end
    return output,ownership


def resolve_single_cue_suffix(editor_text: str, canonical_text: str) -> str | None:
    """Resolve one ASCII word suffix in an already bound one-cue/one-line span.

    Other words must agree exactly after representation normalization. This does
    not resolve a span identity; the caller must bind it before invoking this.
    """
    import re
    words=lambda text: re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)*",text)
    old,new=words(editor_text),words(canonical_text)
    if len(old)!=len(new) or len(old)<4:
        return None
    if _normalize_for_match(''.join(old))!=_normalize_for_match(editor_text):
        return None
    if _normalize_for_match(''.join(new))!=_normalize_for_match(canonical_text):
        return None
    diffs=[(a,b) for a,b in zip(old,new) if _normalize_for_match(a)!=_normalize_for_match(b)]
    if len(diffs)!=1:return None
    a,b=(_normalize_for_match(v) for v in diffs[0])
    if len(a)<4 or not b.startswith(a) or not 1<=len(b)-len(a)<=2:
        return None
    return canonical_text
