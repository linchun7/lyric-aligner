# Smart / Pro production policy

Date: 2026-08-22

Normative workload baseline remains `references/production-requirements.md`.
Smart and Pro remain the daily primary modes; this change does not expand
Max/Full V4 as the default path.

## Smart v1.2.7

Smart still reads no audio. The primary v1 A-anchor affine timing engine remains
unchanged from the established base policy. v1.2.0 added a separate canonical-
sequence text layer so severe editor ASR cannot permanently block text recovery
merely because lexical similarity is low; later v1.2.x releases hardened BPM,
ownership and bounded recovery without increasing timing authority. v1.2.7 runs
the frozen v1.2.6 policy to completion, freezes its final timing decisions, then
adds exact-adjacency cross-script vocalization recovery and no-audio hypothesis
quality/value stratification.

Primary timing hardenings remain:

- `preserve` means timing was actually validated strongly enough to keep;
- `timing_model_not_ready`, missing unique timed-canonical identity, and C-grade
  identities are unresolved and must escalate to Pro instead of making the
  whole Smart task look `ready`;
- B-grade identity cannot build the primary timing model, but may be secondarily
  confirmed by an already-ready A-anchor model;
- automatic timing repair may not create a new subtitle overlap or increase an
  existing overlap in the combined proposal;
- rate prior provenance remains explicit: `exact_daw`, `bpm_derived`, or
  `anchor_estimated`; exact DAW stretch remains stronger than BPM-derived prior;
- Smart report schema remains `smart-1.1`; current policy id is
  `smart-validation-policy-2026-08-22-v1.2.7`.

### Segmentation authority and mode monotonicity

Canonical lyric is final **text/order** authority, but canonical LRC line breaks
are not final subtitle cue authority.

```text
canonical lyric text/order -> authority
LRC line break             -> grouping/onset evidence only
trusted Jianying cue       -> display segmentation strong prior
word/token/audio evidence  -> may rebut editor boundary when independently strong
```

Therefore a higher mode must not regress a lower-mode safe result merely because
canonical lines are grouped differently. When editor cues concatenate to the
same canonical text/order, Smart keeps editor cue ownership. Moving words
between otherwise-correct cues requires stronger boundary evidence than a line
LRC break.

### Why v1.2.0 adds Sequence Projection

v1.1.x still had a severe-ASR bootstrap deadlock:

```text
bad editor ASR
-> lexical matcher cannot form the correct canonical span
-> fewer than four A timing anchors
-> primary timing model is not ready
-> ready-model text recovery cannot start
-> wrong editor text remains in output
```

Lowering Text Repair thresholds or the four-A primary timing gate would increase
false-auto risk. v1.2.0 instead introduced a **text-only Sequence Projection**
with lower authority than the primary timing model.

Without an exact hard rate prior, Sequence Projection requires:

- at least three unique/exact/unchanged 1:1 A text anchors;
- at least one additional `score >= 0.92` A/B strong text anchor;
- at least 8 seconds useful span in both source and mix time;
- a robust affine rate in `[0.5, 2.0]`;
- median absolute strong-anchor residual `<= 450ms`;
- `750ms` inlier fraction `>= 0.75`.

With an exact DAW hard rate prior, a narrower two-A text projection may be used.
BPM-derived rate remains soft and is not allowed to become a hard text-projection
rate merely to make a job pass.

### Strongly bounded canonical sequence recovery

When two model-consistent strong anchors of the same song bound a weak/review
region, Smart may use the complete canonical gap plus timed-LRC projection to
recover text even when the middle editor strings are nearly unrelated.

The recovery preserves existing editor cue count/start/end. It may assign
multiple canonical LRC rows to one editor cue when projected onsets show that
those rows belong inside that display interval. The partition is chosen from:

- current cue first-onset agreement;
- next canonical onset versus next editor cue start;
- a small text-length ownership penalty.

The LRC line count never becomes the subtitle cue count. A four-cue editor block
may therefore consume eight canonical lines without creating eight subtitle
cues or moving cue boundaries.

Successful reason:

```text
sequence_projection_confirms_bounded_canonical
```

### Cautious song frontier recovery

Outside a song's first/last model-consistent strong anchor, Sequence Projection
may walk one cue at a time only while projected canonical onset stays close to
editor timing. It stops at the first timing discontinuity, non-monotonic cue,
other strong anchor, or lack of a defensible candidate; it does not jump across
a cut/editor-only region to chase later LRC.

Multi-line frontier assignment keeps an extra lexical guard because one-sided
context is weaker than a bounded two-anchor region.

Successful reason:

```text
sequence_projection_confirms_frontier_canonical
```

### Anti-circularity and authority ordering

v1.1.x ready-model recovery remains stronger and runs first:

```text
timing_model_confirms_canonical_sequence
timing_model_confirms_song_edge_canonical
```

Sequence Projection must not overwrite either result. If a stronger ready-model
recovery is present inside a candidate bounded region or frontier, the lower-
authority sequence layer stops/fails closed instead of relabelling it.

All sequence-projected decisions have score capped below `0.92`. Therefore they
remain C-grade for the primary timing engine even after their output text exactly
matches canonical lyrics. A typical valid state is deliberately:

```text
3 original A anchors + 1 strong B
-> text-only Sequence Projection ready
-> severe-ASR canonical text recovered
-> primary timing model still has only 3 A anchors
-> timing remains review / Pro escalation
```

Text certainty and timing certainty are separate axes.

### Existing v1.1 ready-model recovery

The independently-ready four-A timing paths remain:

- bilateral interior recovery: same-song strong anchors on both sides, complete
  canonical gap, model-consistent onsets;
- narrow song-edge recovery: independently-ready model, strict edge scope,
  consecutive one-sided strong anchors, tighter candidate onset guard, and only
  truly unmapped editor-only ad-libs may be transparent.

These results are text authority only and do not become primary timing anchors.

### v1.2.5 A-bounded post-timing recovery

After the frozen v1.2.4 Smart timing plan is final, v1.2.5 may recover a
consecutive **mapped** review region only when immediate resolved neighbours
define the exact same-source canonical gap and a farther ready same-source A/A
timing pair brackets that region. Both A anchors must have absolute residual
`<=750ms`; regional normalized similarity must be `>=0.80`, normalized length
ratio `>=0.85`, and the region must contain at least 12 normalized characters.

Unmapped/zero-width spans, cross-source gaps, pure vocalization, boundary
insertion, empty ownership, multi-cue Latin/mixed repartition, weak residuals,
low similarity, short regions and poor length ratio fail closed. A-bounded
recovered score is capped at `<=0.89`, below B timing authority. Cue count,
numbering, start/end and the frozen v1.2.4 timing decisions remain unchanged;
Smart must not rebuild timing after this text recovery.

### Smart report

Existing fields remain, including:

- `text_review_count_before_timing_recovery`;
- `text_timing_recovery_count`;
- `text_timing_recovery_block_count`;
- `text_edge_timing_recovery_count`;
- `text_edge_timing_recovery_block_count`;
- final `text_review_count`.

v1.2.0 adds:

- `text_sequence_reconciled_cue_count`;
- `text_sequence_reconciled_region_count`;
- `text_sequence_resolved_review_count`;
- `text_sequence_frontier_cue_count`;
- `text_sequence_frontier_run_count`;
- `text_sequence_projection_models`.

v1.2.5 adds:

- `text_a_bounded_recovery_count`;
- `text_a_bounded_region_count`;
- `text_a_bounded_materialized_change_count`.

`text_sequence_projection_models` are text-only diagnostics and must not be
confused with `models`, which remain the primary timing models.

## Pro v1.2.2

Pro stays local and evidence-first. It still performs no timing mutation.

Evidence is routed by the reason Smart escalated the cue:

- timing review with canonical identity -> local source<->mix acoustic first;
- text/identity review -> local ASR + word timestamps;
- source forced alignment is requested only when useful, primarily when
  canonical identity/text needs stronger source-side evidence and no word-timed
  canonical is already available;
- word-timed Enhanced LRC/QRC avoids redundant source forced alignment;
- unmapped review cues remain ASR-only instead of pretending source identity is
  known.

Pro requires the exact current Smart schema + policy. The current accepted
upstream contract is:

```text
schema_version = smart-1.1
policy_id      = smart-validation-policy-2026-08-22-v1.2.7
```

Versioned Smart modules remain historical implementations: `smart_policy.py`
is the frozen v1.2.4 base contract; `smart_policy_v125.py` and
`smart_policy_v126.py` / `smart_policy_v127.py` are versioned wrappers. `smart_current.py` is the **only
current-production facade**; it currently binds schema `smart-1.1`, policy
v1.2.7 and the v1.2.7 repair function. Both the Smart CLI and Pro v1.2.2 import
through this facade.
Therefore a future Smart promotion changes one current binding instead of
independently changing multiple consumers. v1.2.4 and earlier Smart reports are
stale and must be rerun.

Pro only handles Smart-unresolved cues; it does not automatically catch Smart
false-ready results, which is why segmentation/sequence/authority regressions
are Smart release tests.

Nearby acoustic review cues are assigned to merged mix regions. ASR-only jobs do
not widen an acoustic region. Source windows use token timing / next canonical
onset and guarantee enough duration for the planned acoustic slope search.

At the first/last canonical lines of a song, a timing-review job may add one
neighbouring-song shadow competitor. The competitor remains acoustic evidence
only (`shadow_evidence_only=true`) and cannot directly mutate timing. `max_jobs`
continues to limit primary unresolved-cue jobs; an attached boundary competitor
is additive shadow evidence for an already-selected primary job.

The existing external forced-alignment protocol remains callable from
`scripts/v4_pro_selective.py` through explicit backend/model/command arguments.
It remains auxiliary evidence and canonical lyrics remain final text/order
authority.

## Safety boundary

Pro v1.2.2 still reports `timing_mutation_performed=false`. Automatic Pro
writeback must remain disabled until private real-song calibration + independent
blind validation establishes safe evidence combinations and false-repair bounds.

Max also follows the same segmentation authority contract: line-LRC grouping is
not sufficient evidence by itself to resegment a trusted editor subtitle cue.

## Current superseding contract: Smart v1.2.7 / Pro v1.2.2

The current Smart facade binds `smart-1.1` to
`smart-validation-policy-2026-08-22-v1.2.7`. In addition to the frozen v1.2.6
pipeline, it filters conservative CJK/mixed singer-role metadata, applies a
post-timing ownership-preserving final-text recovery, and separates timing
suspected from timing merely unvalidated in product reports. v1.2.7 additionally
recovers only exact-adjacency cross-script vocalizations and stratifies no-audio
timing hypotheses by model strength and text-identity value.

The current Pro policy is
`smart-to-pro-reason-aware-2026-08-22-v1.2.2`. Concrete actionable timing
hypotheses are selected by strong-vs-weak local model quality and value before
display-tolerance and unvalidated work.
Acoustic gates are observations, not authority. Optional `--decision-out`
combines executed evidence into supported, rebutted, conflict and unvalidated
states with exact high-priority positions. Automatic timing mutation remains
disabled.

Local acoustic retrieval synchronizes source music to the mix but does not by
itself measure the vocal onset. Because Smart and this retrieval both consume
the canonical/LRC timeline, their agreement is correlated evidence. v1.2.1
therefore keeps acoustic-only support/conflict at medium priority; the smallest
high queue requires additional value such as resolving a one-to-one canonical
text occurrence. v1.2.2 preserves that boundary while accepting Smart v1.2.7
and retaining anchored cross-script candidates in the smallest high queue.
