"""Diagnostic LRC-to-recording clock candidates, separate from audio mapping.

A robust clock fit describes timestamp drift; it is not calibrated vocal-boundary
authority. In particular, fitting onsets does not verify acoustic lyric ends.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Iterable

POLICY_ID = "source-lyric-clock-candidate-1.0"


@dataclass(frozen=True)
class LyricClockCandidate:
    rate: float
    offset_ms: float
    first_prior_ms: int
    last_prior_ms: int
    observation_count: int
    residuals_ms: tuple[float, ...]

    def map_ms(self, prior_ms: int) -> int:
        if isinstance(prior_ms, bool) or not isinstance(prior_ms, int) or prior_ms < 0:
            raise ValueError("lyric clock coordinate must be a nonnegative integer")
        value = round(self.rate * prior_ms + self.offset_ms)
        if value < 0:
            raise ValueError("lyric clock candidate maps before recording start")
        return value

    def is_extrapolation(self, prior_ms: int) -> bool:
        return not self.first_prior_ms <= prior_ms <= self.last_prior_ms


def fit_lyric_clock(observations: Iterable[tuple[int, int]]) -> LyricClockCandidate:
    """Fit a Theil-Sen diagnostic clock from preselected onset observations.

    The caller owns lexical/occurrence selection and must retain that selection
    in its report. Validation observations must not be silently added to the fit.
    Duplicate prior coordinates are rejected instead of overweighting a repeat.
    """
    rows = sorted(observations)
    if len(rows) < 4:
        raise ValueError("lyric clock candidate requires at least four observations")
    for prior, observed in rows:
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (prior, observed)):
            raise ValueError("lyric clock observations must be nonnegative integers")
    if len({p for p, _ in rows}) != len(rows):
        raise ValueError("duplicate lyric clock prior coordinate")
    if rows[-1][0] - rows[0][0] < 30000:
        raise ValueError("lyric clock observations must span at least 30 seconds")
    slopes = [(b[1]-a[1])/(b[0]-a[0]) for i,a in enumerate(rows) for b in rows[i+1:]]
    rate = median(slopes)
    offset = median(observed-rate*prior for prior,observed in rows)
    if not math.isfinite(rate) or not math.isfinite(offset) or rate <= 0:
        raise ValueError("lyric clock candidate must be finite and forward")
    return LyricClockCandidate(rate, offset, rows[0][0], rows[-1][0], len(rows),
        tuple(rate*prior+offset-observed for prior,observed in rows))
