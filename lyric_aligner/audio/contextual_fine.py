"""Context-aware attack matching; mapping evidence, not lyric boundary truth."""
from __future__ import annotations

import math
import numpy as np

from lyric_aligner.audio.independent_fine import (
    INDEPENDENT_FINE_AUTHORITY, INDEPENDENT_FINE_CORRELATION_GROUP,
    _flattened_cosine, _resample_columns, retrieve_independent_onset_window,
)

POLICY_VERSION = 'contextual-independent-fine-1.1.1'


def retrieve_contextual_onset_window(mix, source, *, mix_start, mix_end, slopes,
                                    source_search_start, source_search_end,
                                    candidate_step_seconds=.05):
    """Nominate locally and with context, then compare all on identical support.

    The median of local/left/right scores tolerates one corrupted patch without
    letting one perfect local decoy dominate. Context is never another family.
    The original observer remains unchanged for historical calibrated callers.
    """
    values=(mix_start,mix_end,source_search_start,source_search_end,candidate_step_seconds)
    if not all(math.isfinite(v) for v in values) or candidate_step_seconds <= 0:
        raise ValueError('invalid contextual alignment coordinates')
    slopes=tuple(float(slope) for slope in slopes)
    if not slopes or any(not math.isfinite(slope) or slope<=0 for slope in slopes):
        raise ValueError('contextual alignment slopes must be finite and positive')
    for features in (mix,source):
        if (not math.isfinite(features.sr) or not math.isfinite(features.hop_length)
                or features.sr<=0 or features.hop_length<=0
                or not math.isfinite(features.duration_seconds) or features.duration_seconds<=0):
            raise ValueError('invalid contextual feature sampling')
        onset=np.asarray(features.onset);flux=np.asarray(features.multiband_flux)
        if (onset.ndim!=2 or flux.ndim!=2 or onset.shape[0]!=1 or flux.shape[0]<2
                or onset.shape[1]!=flux.shape[1] or flux.shape[1]<4
                or not np.isfinite(onset).all() or not np.isfinite(flux).all()):
            raise ValueError('contextual features must be finite compatible matrices')
    common=dict(slopes=slopes,source_search_start=source_search_start,
                source_search_end=source_search_end,candidate_step_seconds=candidate_step_seconds,
                top_k=20)
    local=retrieve_independent_onset_window(mix,source,mix_start=mix_start,mix_end=mix_end,**common)
    frame=mix.frame_seconds
    first=round(mix_start/frame);last=round(mix_end/frame)
    pad=min(round(min(2.0,(mix_end-mix_start)/2)/frame),first,mix.frame_count-last)
    base=dict(version=POLICY_VERSION,authority=INDEPENDENT_FINE_AUTHORITY,
              correlation_group=INDEPENDENT_FINE_CORRELATION_GROUP,
              automatic_mutation_allowed=False,mix_start=mix_start,mix_end=mix_end)
    if pad*frame < .5:
        return dict(**base,context_available=False,reason='insufficient_mix_context',
                    top1=local.top1.to_dict(),top2=None if local.top2 is None else local.top2.to_dict(),
                    candidates=[c.to_dict() for c in local.candidates],margin=local.margin,
                    ambiguous=local.ambiguous,local_top1=local.top1.to_dict())
    max_slope=max(slopes)
    try:
        wide=retrieve_independent_onset_window(mix,source,
            mix_start=(first-pad)*frame,mix_end=(last+pad)*frame,
            **{**common,'source_search_start':max(0,source_search_start-pad*frame*max_slope),
               'source_search_end':min(source.duration_seconds,source_search_end+pad*frame*max_slope)})
    except ValueError as exc:
        if str(exc)!='independent onset retrieval produced no candidates':raise
        return dict(**base,context_available=False,reason='insufficient_source_context',
                    top1=local.top1.to_dict(),top2=None if local.top2 is None else local.top2.to_dict(),
                    candidates=[c.to_dict() for c in local.candidates],margin=local.margin,
                    ambiguous=True,local_top1=local.top1.to_dict())
    nominations=[(c.source_start/frame,c.estimated_slope) for c in local.candidates]
    nominations += [(c.source_start/frame+pad*c.estimated_slope,c.estimated_slope) for c in wide.candidates]
    # Refine every nominated position onto the feature-frame grid at every
    # supplied rate. Otherwise coarse grid phase can manufacture peak margins.
    radius=max(1,math.ceil(candidate_step_seconds/frame))
    nominations=[(math.floor(start)+delta,slope) for start,_ in nominations
                 for delta in range(-radius,radius+1) for slope in slopes]
    incomplete_local_support=any(c.fused_score>=max(.48,local.top1.fused_score-local.min_margin) and (
        c.source_start/frame-pad*c.estimated_slope<0 or
        c.source_start/frame+(last-first+pad)*c.estimated_slope>source.frame_count)
        for c in local.candidates)
    candidates=[];seen=set()
    for start,slope in nominations:
        key=(round(start,8),slope)
        if key in seen:continue
        seen.add(key)
        length=last-first
        if start*frame < source_search_start or (start+length*slope)*frame > source_search_end:
            continue
        if start-pad*slope < 0 or start+(length+pad)*slope > source.frame_count:
            continue
        scores=[]
        for left,right in ((first,last),(first-pad,first),(last,last+pad)):
            source_first=start+(left-first)*slope
            n=right-left
            onset=_flattened_cosine(mix.onset[:,left:right],_resample_columns(source.onset,source_first,n*slope,n))
            flux=_flattened_cosine(mix.multiband_flux[:,left:right],_resample_columns(source.multiband_flux,source_first,n*slope,n))
            scores.append(.45*onset+.55*flux)
        score=float(np.median(scores))
        if not math.isfinite(score):
            raise ValueError('nonfinite contextual match score')
        candidates.append(dict(source_start=start*frame,source_end=(start+length*slope)*frame,
            source_center=(start+length*slope/2)*frame,estimated_slope=slope,
            fused_score=score,local_score=scores[0],left_context_score=scores[1],right_context_score=scores[2]))
    if not candidates:
        return dict(**base,context_available=False,reason='no_candidate_with_complete_source_context',
                    top1=local.top1.to_dict(),top2=None if local.top2 is None else local.top2.to_dict(),
                    candidates=[c.to_dict() for c in local.candidates],margin=local.margin,
                    ambiguous=True,local_top1=local.top1.to_dict())
    candidates.sort(key=lambda c:c['fused_score'],reverse=True)
    selected=[]
    for candidate in candidates:
        if any(abs(candidate['source_center']-c['source_center'])<.2 for c in selected):continue
        selected.append(candidate)
        if len(selected)==5:break
    top=selected[0];second=selected[1] if len(selected)>1 else None
    margin=top['fused_score']-(second['fused_score'] if second else 0)
    return dict(**base,context_available=True,context_seconds=pad*frame,reason='joint_local_context_score',
        top1=top,top2=second,candidates=selected,margin=margin,
        incomplete_local_support=incomplete_local_support,
        ambiguous=bool(top['fused_score']<.48 or margin<.025 or incomplete_local_support),local_top1=local.top1.to_dict())
