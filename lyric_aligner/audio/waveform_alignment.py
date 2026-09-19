"""Local signal registration for audio already rendered at approximately mix speed.

This observes waveform correspondence, not lyric boundaries or lyric identity.
Pitch-preserving stretches, edits and repeated signals may have no unique match.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import correlate

POLICY_VERSION = "local-waveform-registration-1.0"


def _unique_match(template, search, sr):
    template = np.asarray(template, dtype=np.float64)
    search = np.asarray(search, dtype=np.float64)
    n = len(template)
    if n < sr // 2 or len(search) <= n or not np.isfinite(template).all() or not np.isfinite(search).all():
        return None
    template = template - template.mean()
    energy = float(template @ template)
    if energy / n < 1e-10:
        return None
    sums = np.r_[0.0, np.cumsum(search)]
    squares = np.r_[0.0, np.cumsum(search * search)]
    variance = np.maximum(squares[n:] - squares[:-n] - (sums[n:] - sums[:-n]) ** 2 / n, 0)
    scores = np.abs(correlate(search, template, mode="valid", method="fft"))
    scores /= np.sqrt(np.maximum(variance * energy, 1e-20))
    scores[variance / n < 1e-10] = 0
    peak = int(np.argmax(scores))
    # A boundary peak may be a truncated search rather than a complete match.
    if peak == 0 or peak == len(scores) - 1:
        return None
    score = float(scores[peak])
    competitors = scores.copy()
    exclusion = max(1, round(sr * .04))
    competitors[max(0, peak-exclusion):peak+exclusion+1] = 0
    margin = score - float(competitors.max())
    if score < .90 or margin < .10:
        return None
    return peak, min(1.0, score), margin


def refine_waveform_window(mix_audio, source_audio, *, sr, mix_start, mix_end,
                           source_center, slope, radius=1.25, mix_audio_start=0.0):
    """Return a local mapping candidate supported by three patches, or None.

    Three non-overlapping patches must agree on one near-unit-rate mapping.
    Unsampled gaps can contain edits. These anchors do not certify a whole
    interval, source occurrence identity, or lyric boundaries.
    """
    if sr <= 0 or radius <= 0 or not np.isfinite([mix_start, mix_end, source_center, slope, radius, mix_audio_start]).all():
        raise ValueError("invalid waveform alignment coordinates")
    if np.ndim(mix_audio) != 1 or np.ndim(source_audio) != 1:
        raise ValueError("waveform alignment requires mono audio")
    duration = mix_end - mix_start
    if duration < 3.0 or not .99 <= slope <= 1.01:
        return None
    patch_seconds = min(1.5, duration / 5)
    center = (mix_start + mix_end) / 2
    anchors = []
    support = []
    for fraction in (.2, .5, .8):
        target = mix_start + duration * fraction
        first = round((target - patch_seconds / 2 - mix_audio_start) * sr)
        last = first + round(patch_seconds * sr)
        if first < 0 or last > len(mix_audio):
            return None
        observed_mix_center = mix_audio_start + (first + last) / (2 * sr)
        expected = source_center + slope * (observed_mix_center - center)
        source_first = max(0, round((expected - patch_seconds/2 - radius) * sr))
        source_last = min(len(source_audio), round((expected + patch_seconds/2 + radius) * sr))
        if source_last <= source_first:
            return None
        match = _unique_match(mix_audio[first:last], source_audio[source_first:source_last], sr)
        if match is None:
            return None
        offset, score, margin = match
        observed_source_center = (source_first + offset + (last-first)/2) / sr
        anchors.append((observed_mix_center, observed_source_center, score, margin))
        support.append(dict(mix_start=mix_audio_start+first/sr, mix_end=mix_audio_start+last/sr,
                            source_start=(source_first+offset)/sr, source_end=(source_first+offset+last-first)/sr))
    points = np.asarray(anchors)
    # Center the fit to avoid loss of precision on hour-long absolute timelines.
    design = np.column_stack((np.ones(3), points[:, 0] - center))
    refined_center, refined_slope = np.linalg.lstsq(design, points[:, 1], rcond=None)[0]
    residual = float(np.max(np.abs(design @ [refined_center, refined_slope] - points[:, 1])))
    if residual > .003 or not .99 <= refined_slope <= 1.01 or abs(refined_center-source_center) > radius:
        return None
    return {"policy_version": POLICY_VERSION, "source_center": float(refined_center),
            "slope": float(refined_slope), "score": float(points[:, 2].min()),
            "margin": float(points[:, 3].min()), "max_residual_seconds": residual,
            "support_intervals": support,
            "anchors": [dict(mix_time=a, source_time=b, score=c, margin=d) for a,b,c,d in anchors]}
