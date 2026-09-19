"""Backend-neutral CTC forced alignment for known lyric/phoneme sequences.

This module consumes framewise model scores and a *known* target token sequence.  It
never transcribes or changes canonical text.  Its only job is to find the most likely
monotonic CTC path through that target and expose token intervals/sequence confidence
as observer evidence.

The implementation deliberately has no torch/transformers/model dependency.  A
Mandarin singing CTC model, WavLM observer, or future GPT audio backend only needs to
produce a score matrix plus target class ids.  Production mutation authority lives
above this layer and requires Human-Gold calibration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Sequence

import numpy as np

from lyric_aligner.alignment.boundary_hypotheses import (
    BOUNDARY_HYPOTHESES_AUTHORITY,
    BOUNDARY_HYPOTHESES_SCHEMA_VERSION,
    BoundaryHypothesis,
    BoundaryHypothesisSet,
)


CTC_FORCED_ALIGNMENT_VERSION = "1.0"
CTC_FORCED_ALIGNMENT_AUTHORITY = "observer_evidence_only_never_direct_mutation"


class CTCForcedAlignmentError(ValueError):
    pass


@dataclass(frozen=True)
class CTCTokenInterval:
    target_index: int
    token_id: int
    start_frame: int
    end_frame_exclusive: int
    start_ms: int
    end_ms: int
    mean_emission_probability: float

    def validate(self) -> None:
        if self.target_index < 0 or self.token_id < 0:
            raise CTCForcedAlignmentError("token identity must be nonnegative")
        if self.start_frame < 0 or self.end_frame_exclusive <= self.start_frame:
            raise CTCForcedAlignmentError("token frame interval must be positive")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise CTCForcedAlignmentError("token millisecond interval must be positive")
        if not math.isfinite(self.mean_emission_probability) or not 0.0 <= self.mean_emission_probability <= 1.0:
            raise CTCForcedAlignmentError("token mean emission probability must be within [0,1]")


@dataclass(frozen=True)
class CTCForcedAlignmentResult:
    version: str
    authority: str
    frame_ms: float
    blank_id: int
    target_token_ids: tuple[int, ...]
    token_intervals: tuple[CTCTokenInterval, ...]
    boundary_start_ms: int
    boundary_end_ms: int
    path_log_probability: float
    mean_path_probability: float
    lexical_mean_probability: float
    frame_count: int
    state_path: tuple[int, ...]
    automatic_mutation_allowed: bool = False

    def validate(self) -> None:
        if self.version != CTC_FORCED_ALIGNMENT_VERSION:
            raise CTCForcedAlignmentError("unsupported CTC alignment version")
        if self.authority != CTC_FORCED_ALIGNMENT_AUTHORITY:
            raise CTCForcedAlignmentError("CTC observer authority marker is invalid")
        if not math.isfinite(self.frame_ms) or self.frame_ms <= 0.0:
            raise CTCForcedAlignmentError("frame_ms must be finite and positive")
        if self.blank_id < 0 or not self.target_token_ids:
            raise CTCForcedAlignmentError("CTC target/blank identity is invalid")
        if len(self.token_intervals) != len(self.target_token_ids):
            raise CTCForcedAlignmentError("every target token must have one aligned interval")
        if self.boundary_start_ms < 0 or self.boundary_end_ms <= self.boundary_start_ms:
            raise CTCForcedAlignmentError("CTC outer boundaries are invalid")
        if not math.isfinite(self.path_log_probability):
            raise CTCForcedAlignmentError("path log probability must be finite")
        for label, value in (
            ("mean_path_probability", self.mean_path_probability),
            ("lexical_mean_probability", self.lexical_mean_probability),
        ):
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise CTCForcedAlignmentError(f"{label} must be within [0,1]")
        if self.frame_count != len(self.state_path) or self.frame_count < 1:
            raise CTCForcedAlignmentError("state path length does not match frame_count")
        previous_end = -1
        for expected_index, interval in enumerate(self.token_intervals):
            interval.validate()
            if interval.target_index != expected_index or interval.token_id != self.target_token_ids[expected_index]:
                raise CTCForcedAlignmentError("token interval identity/order mismatch")
            if interval.start_frame < previous_end:
                raise CTCForcedAlignmentError("token intervals overlap or reorder")
            previous_end = interval.end_frame_exclusive
        if self.automatic_mutation_allowed:
            raise CTCForcedAlignmentError("CTC observer cannot directly authorize subtitle mutation")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["target_token_ids"] = list(self.target_token_ids)
        payload["token_intervals"] = [asdict(item) for item in self.token_intervals]
        payload["state_path"] = list(self.state_path)
        return payload


def _log_softmax(scores: np.ndarray) -> np.ndarray:
    values = np.asarray(scores, dtype=np.float64)
    maximum = np.max(values, axis=1, keepdims=True)
    shifted = values - maximum
    normalizer = np.log(np.sum(np.exp(shifted), axis=1, keepdims=True))
    return shifted - normalizer


def _validate_inputs(
    frame_scores: np.ndarray,
    target_token_ids: Sequence[int],
    *,
    blank_id: int,
    frame_ms: float,
    scores_are_log_probs: bool,
) -> tuple[np.ndarray, tuple[int, ...]]:
    scores = np.asarray(frame_scores, dtype=np.float64)
    if scores.ndim != 2 or scores.shape[0] < 1 or scores.shape[1] < 2:
        raise CTCForcedAlignmentError("frame_scores must be a non-empty [frames, classes] matrix")
    if not np.all(np.isfinite(scores)):
        raise CTCForcedAlignmentError("frame_scores contain non-finite values")
    target = tuple(int(value) for value in target_token_ids)
    if not target:
        raise CTCForcedAlignmentError("target token sequence must not be empty")
    class_count = int(scores.shape[1])
    if isinstance(blank_id, bool) or not 0 <= int(blank_id) < class_count:
        raise CTCForcedAlignmentError("blank_id is outside class range")
    if any(value < 0 or value >= class_count for value in target):
        raise CTCForcedAlignmentError("target token id is outside class range")
    if any(value == int(blank_id) for value in target):
        raise CTCForcedAlignmentError("target token sequence must not contain the CTC blank id")
    if not math.isfinite(float(frame_ms)) or float(frame_ms) <= 0.0:
        raise CTCForcedAlignmentError("frame_ms must be finite and positive")

    minimum_frames = len(target) + sum(left == right for left, right in zip(target, target[1:]))
    if scores.shape[0] < minimum_frames:
        raise CTCForcedAlignmentError("too few frames to align target under CTC repeat constraints")

    if scores_are_log_probs:
        # Re-normalize even caller-supplied log probabilities.  A constant offset
        # per frame should not change the alignment, and malformed over-full rows
        # must not become artificial confidence.
        log_probs = _log_softmax(scores)
    else:
        # Treat arbitrary finite model logits as logits; this also accepts positive
        # unnormalized scores without trusting their absolute scale.
        log_probs = _log_softmax(scores)
    return log_probs, target


def _extended_target(target: tuple[int, ...], blank_id: int) -> np.ndarray:
    states = np.full(2 * len(target) + 1, int(blank_id), dtype=np.int64)
    states[1::2] = np.asarray(target, dtype=np.int64)
    return states


def _can_skip(states: np.ndarray, state_index: int, blank_id: int) -> bool:
    if state_index < 2:
        return False
    current = int(states[state_index])
    return current != int(blank_id) and current != int(states[state_index - 2])


def _logsumexp(values: Sequence[float]) -> float:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    if not finite:
        return -math.inf
    maximum = max(finite)
    return float(maximum + math.log(sum(math.exp(value - maximum) for value in finite)))


def _ctc_forward_backward(
    log_probs: np.ndarray,
    target: tuple[int, ...],
    *,
    blank_id: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Return constrained CTC forward/backward tables and total log likelihood."""

    frames = int(log_probs.shape[0])
    states = _extended_target(target, blank_id)
    state_count = int(states.size)
    alpha = np.full((frames, state_count), -np.inf, dtype=np.float64)
    beta = np.full((frames, state_count), -np.inf, dtype=np.float64)

    alpha[0, 0] = log_probs[0, states[0]]
    if state_count > 1:
        alpha[0, 1] = log_probs[0, states[1]]
    for t in range(1, frames):
        for s in range(state_count):
            predecessors = [alpha[t - 1, s]]
            if s > 0:
                predecessors.append(alpha[t - 1, s - 1])
            if _can_skip(states, s, blank_id):
                predecessors.append(alpha[t - 1, s - 2])
            prefix = _logsumexp(predecessors)
            if math.isfinite(prefix):
                alpha[t, s] = prefix + log_probs[t, states[s]]

    beta[-1, -1] = 0.0
    if state_count >= 2:
        beta[-1, -2] = 0.0
    for t in range(frames - 2, -1, -1):
        for s in range(state_count):
            successors = [log_probs[t + 1, states[s]] + beta[t + 1, s]]
            if s + 1 < state_count:
                successors.append(log_probs[t + 1, states[s + 1]] + beta[t + 1, s + 1])
            if s + 2 < state_count and _can_skip(states, s + 2, blank_id):
                successors.append(log_probs[t + 1, states[s + 2]] + beta[t + 1, s + 2])
            beta[t, s] = _logsumexp(successors)

    terminal = [alpha[-1, -1]]
    if state_count >= 2:
        terminal.append(alpha[-1, -2])
    log_z = _logsumexp(terminal)
    if not math.isfinite(log_z):
        raise CTCForcedAlignmentError("target sequence has zero constrained CTC probability")
    return states, alpha, beta, log_z


def _outer_boundary_distribution(
    log_probs: np.ndarray,
    target: tuple[int, ...],
    *,
    blank_id: int,
    boundary_kind: str,
) -> list[tuple[int, float]]:
    """Return constrained-path probability mass for each outer boundary frame."""

    if boundary_kind not in {"start", "end"}:
        raise CTCForcedAlignmentError("outer boundary kind must be start or end")
    states, alpha, beta, log_z = _ctc_forward_backward(log_probs, target, blank_id=blank_id)
    frames = int(log_probs.shape[0])
    probabilities: list[tuple[int, float]] = []
    if boundary_kind == "start":
        first_state = 1
        initial_log = log_probs[0, states[first_state]] + beta[0, first_state]
        probabilities.append((0, float(math.exp(initial_log - log_z))))
        for t in range(1, frames):
            transition_log = alpha[t - 1, 0] + log_probs[t, states[first_state]] + beta[t, first_state]
            value = 0.0 if not math.isfinite(transition_log) else math.exp(transition_log - log_z)
            probabilities.append((t, float(value)))
    else:
        last_state = int(states.size) - 2
        final_blank = int(states.size) - 1
        for t in range(frames - 1):
            transition_log = (
                alpha[t, last_state]
                + log_probs[t + 1, states[final_blank]]
                + beta[t + 1, final_blank]
            )
            value = 0.0 if not math.isfinite(transition_log) else math.exp(transition_log - log_z)
            probabilities.append((t + 1, float(value)))
        terminal_log = alpha[-1, last_state]
        terminal_value = 0.0 if not math.isfinite(terminal_log) else math.exp(terminal_log - log_z)
        probabilities.append((frames, float(terminal_value)))

    total = sum(value for _, value in probabilities)
    if not math.isfinite(total) or total <= 0.0:
        raise CTCForcedAlignmentError("outer boundary posterior has no probability mass")
    return [(frame, float(value / total)) for frame, value in probabilities]


def ctc_outer_boundary_hypothesis_set(
    frame_scores: np.ndarray,
    target_token_ids: Sequence[int],
    *,
    blank_id: int,
    frame_ms: float,
    boundary_kind: str,
    window_start_ms: int,
    observer_id: str,
    observer_revision: str,
    correlation_group: str,
    boundary_id: str,
    audio_basis: str,
    language: str,
    max_hypotheses: int = 5,
    min_separation_frames: int = 1,
    scores_are_log_probs: bool = False,
) -> BoundaryHypothesisSet:
    """Expose constrained CTC outer-boundary uncertainty through the N-best contract."""

    if max_hypotheses < 1 or min_separation_frames < 1:
        raise CTCForcedAlignmentError("hypothesis count/separation must be positive")
    try:
        offset = int(round(float(window_start_ms)))
    except (TypeError, ValueError) as exc:
        raise CTCForcedAlignmentError("window_start_ms must be numeric") from exc
    if offset < 0:
        raise CTCForcedAlignmentError("window_start_ms must be nonnegative")
    log_probs, target = _validate_inputs(
        frame_scores,
        target_token_ids,
        blank_id=int(blank_id),
        frame_ms=float(frame_ms),
        scores_are_log_probs=scores_are_log_probs,
    )
    distribution = _outer_boundary_distribution(
        log_probs,
        target,
        blank_id=int(blank_id),
        boundary_kind=boundary_kind,
    )
    ranked = sorted(distribution, key=lambda item: (-item[1], item[0]))
    selected: list[tuple[int, float]] = []
    for frame, probability in ranked:
        if any(abs(frame - kept_frame) < min_separation_frames for kept_frame, _ in selected):
            continue
        selected.append((frame, probability))
        if len(selected) >= max_hypotheses:
            break
    selected_mass = sum(probability for _, probability in selected)
    residual = max(0.0, 1.0 - selected_mass)
    hypotheses = tuple(
        BoundaryHypothesis(
            hypothesis_id=f"ctc-{boundary_kind}-frame-{frame}",
            boundary_ms=offset + int(round(frame * float(frame_ms))),
            probability=float(probability),
            uncertainty_ms=max(1, int(round(float(frame_ms) / 2.0))),
            rank=rank,
            evidence_label="constrained_ctc_boundary_posterior",
        )
        for rank, (frame, probability) in enumerate(selected, start=1)
    )
    # This observer has the complete constrained boundary posterior, so entropy must
    # reflect the full frame distribution. Treating all omitted frames as one residual
    # bucket would systematically understate diffuse/multimodal uncertainty.
    entropy = 0.0
    for _, probability in distribution:
        if probability > 0.0:
            entropy -= probability * math.log2(probability)
    margin = None if len(hypotheses) < 2 else hypotheses[0].probability - hypotheses[1].probability
    result = BoundaryHypothesisSet(
        schema_version=BOUNDARY_HYPOTHESES_SCHEMA_VERSION,
        authority=BOUNDARY_HYPOTHESES_AUTHORITY,
        observer_id=str(observer_id or "").strip(),
        observer_revision=str(observer_revision or "").strip(),
        correlation_group=str(correlation_group or "").strip(),
        boundary_id=str(boundary_id or "").strip(),
        boundary_kind=boundary_kind,
        audio_basis=str(audio_basis or "").strip(),
        language=str(language or "").strip(),
        hypotheses=hypotheses,
        residual_probability=float(residual),
        posterior_entropy_bits=float(entropy),
        top1_top2_margin=None if margin is None else float(margin),
        automatic_mutation_allowed=False,
    )
    result.validate()
    return result


def ctc_viterbi_force_align(
    frame_scores: np.ndarray,
    target_token_ids: Sequence[int],
    *,
    blank_id: int,
    frame_ms: float,
    scores_are_log_probs: bool = False,
) -> CTCForcedAlignmentResult:
    """Viterbi-align one known target sequence to framewise CTC model scores.

    ``frame_scores`` is shaped ``[T, C]``.  Values may be arbitrary logits; they are
    normalized frame-wise before decoding.  ``target_token_ids`` must already be in
    the observer model's vocabulary and must not contain the CTC blank id.

    The returned boundaries are observer evidence only.  Human-Gold calibration and
    the Max risk/authority layers decide whether a timing mutation is ever allowed.
    """

    log_probs, target = _validate_inputs(
        frame_scores,
        target_token_ids,
        blank_id=int(blank_id),
        frame_ms=float(frame_ms),
        scores_are_log_probs=scores_are_log_probs,
    )
    frames, _ = log_probs.shape
    states = _extended_target(target, int(blank_id))
    state_count = int(states.size)

    dp = np.full((frames, state_count), -np.inf, dtype=np.float64)
    back = np.full((frames, state_count), -1, dtype=np.int32)
    dp[0, 0] = log_probs[0, states[0]]
    if state_count > 1:
        dp[0, 1] = log_probs[0, states[1]]

    for t in range(1, frames):
        for s in range(state_count):
            predecessors = [(dp[t - 1, s], s)]
            if s > 0:
                predecessors.append((dp[t - 1, s - 1], s - 1))
            if _can_skip(states, s, int(blank_id)):
                predecessors.append((dp[t - 1, s - 2], s - 2))
            best_score, best_state = max(predecessors, key=lambda item: item[0])
            if not math.isfinite(float(best_score)):
                continue
            dp[t, s] = best_score + log_probs[t, states[s]]
            back[t, s] = int(best_state)

    terminal_states = [state_count - 1]
    if state_count >= 2:
        terminal_states.append(state_count - 2)
    terminal = max(terminal_states, key=lambda s: dp[-1, s])
    terminal_score = float(dp[-1, terminal])
    if not math.isfinite(terminal_score):
        raise CTCForcedAlignmentError("target sequence is not decodable under CTC constraints")

    path = np.empty(frames, dtype=np.int32)
    state = int(terminal)
    for t in range(frames - 1, -1, -1):
        path[t] = state
        if t == 0:
            break
        previous = int(back[t, state])
        if previous < 0:
            raise CTCForcedAlignmentError("CTC Viterbi backtrack is incomplete")
        state = previous

    token_intervals: list[CTCTokenInterval] = []
    lexical_probabilities: list[float] = []
    probabilities = np.exp(log_probs)
    for target_index, token_id in enumerate(target):
        state_index = 2 * target_index + 1
        token_frames = np.flatnonzero(path == state_index)
        if token_frames.size == 0:
            raise CTCForcedAlignmentError("CTC Viterbi path skipped a required target token")
        start_frame = int(token_frames[0])
        end_frame_exclusive = int(token_frames[-1]) + 1
        frame_values = probabilities[token_frames, token_id]
        mean_probability = float(np.mean(frame_values))
        lexical_probabilities.extend(float(value) for value in frame_values)
        token_intervals.append(
            CTCTokenInterval(
                target_index=target_index,
                token_id=int(token_id),
                start_frame=start_frame,
                end_frame_exclusive=end_frame_exclusive,
                start_ms=int(round(start_frame * float(frame_ms))),
                end_ms=int(round(end_frame_exclusive * float(frame_ms))),
                mean_emission_probability=mean_probability,
            )
        )

    path_symbols = states[path]
    path_frame_probabilities = probabilities[np.arange(frames), path_symbols]
    result = CTCForcedAlignmentResult(
        version=CTC_FORCED_ALIGNMENT_VERSION,
        authority=CTC_FORCED_ALIGNMENT_AUTHORITY,
        frame_ms=float(frame_ms),
        blank_id=int(blank_id),
        target_token_ids=target,
        token_intervals=tuple(token_intervals),
        boundary_start_ms=token_intervals[0].start_ms,
        boundary_end_ms=token_intervals[-1].end_ms,
        path_log_probability=terminal_score,
        mean_path_probability=float(np.mean(path_frame_probabilities)),
        lexical_mean_probability=float(np.mean(lexical_probabilities)),
        frame_count=frames,
        state_path=tuple(int(value) for value in path),
        automatic_mutation_allowed=False,
    )
    result.validate()
    return result


__all__ = [
    "CTC_FORCED_ALIGNMENT_VERSION",
    "CTC_FORCED_ALIGNMENT_AUTHORITY",
    "CTCForcedAlignmentError",
    "CTCTokenInterval",
    "CTCForcedAlignmentResult",
    "ctc_outer_boundary_hypothesis_set",
    "ctc_viterbi_force_align",
]
