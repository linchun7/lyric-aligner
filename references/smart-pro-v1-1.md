# Smart / Pro v1.1 production policy

Date: 2026-08-19

Normative workload baseline remains `references/production-requirements.md`.
This change keeps Smart and Pro as the daily primary modes and does not expand
Max/Full V4 as the default path.

## Smart v1.1.2

Smart still reads no audio. The v1 A-anchor affine engine remains the timing
model, with these production hardenings:

- `preserve` now means timing was actually validated strongly enough to keep;
- `timing_model_not_ready`, missing unique timed-canonical identity, and C-grade
  identities are unresolved and must escalate to Pro instead of making the
  whole Smart task look `ready`;
- B-grade identity still cannot build the timing model, but may be secondarily
  confirmed by an already-ready A-anchor model;
- automatic timing repair may not create a subtitle overlap that did not exist
  in the editor SRT or increase an existing overlap in the combined proposal;
- rate prior provenance is explicit: `exact_daw`, `bpm_derived`, or
  `anchor_estimated`; exact DAW stretch remains stronger production evidence
  than a BPM-derived prior;
- Smart report schema remains `smart-1.1`; current policy id identifies the
  v1.1.2 behavior and stale Smart artifacts must be rerun before Pro.

### Severe-ASR text recovery

Canonical lyric remains final text/order authority. A timing/identity review is
not permission to keep editor ASR garbage when canonical sequence can already
be independently proven.

Text Repair V2 thresholds remain unchanged. Smart first builds its affine model
from the same high-confidence A anchors as before. Only after a model is ready
may Smart revisit a low-similarity text-review block, and only when all of these
conditions hold:

- the review block is interior and has validated single-line text anchors on
  both sides (`score >= 0.92`);
- both anchors belong to the same source song and each agrees with the ready
  affine model within 750ms;
- the canonical occurrences strictly between those anchors are consecutive and
  belong to that same source;
- the entire canonical gap can be partitioned monotonically onto the review cue
  starts, with the first onset for every cue within 750ms of editor start;
- each cue absorbs at most four canonical lines and a recovery block contains at
  most eight cues.

If those conditions hold, Smart replaces the editor text with canonical text.
This recovery is **text authority only**: the recovered low-similarity cue is not
promoted to an A timing anchor, does not participate in building the model that
validated it, and does not receive new automatic timing-write authority.
Multi-line recovered cues may therefore have correct final text while their
timing identity remains review/Pro material.

The following remain fail-closed text reviews: one-sided song-edge blocks,
cross-song blocks, canonical-gap-zero ad-libs, non-ready/unstable models,
non-monotonic cue starts, or timing residuals outside the recovery tolerance.

Smart report additionally records:

- `text_review_count_before_timing_recovery`;
- `text_timing_recovery_count`;
- `text_timing_recovery_block_count`;
- final `text_review_count` after recovery.

## Pro v1.1

Pro stays local and evidence-first. It still performs no timing mutation.

Evidence is routed by the reason Smart escalated the cue:

- timing review with canonical identity -> local source<->mix acoustic first;
- text/identity review -> local ASR + word timestamps;
- source forced alignment is requested only when useful, primarily when
  canonical identity/text needs stronger source-side evidence and no word-timed
  canonical is already available;
- word-timed Enhanced LRC/QRC avoids redundant source forced alignment where its
  timing evidence is already sufficient for routing;
- unmapped review cues remain ASR-only instead of pretending source identity is
  known.

Nearby acoustic review cues are assigned to merged mix regions. ASR-only jobs do
not widen an acoustic region. The v1.1 acoustic executor decodes/extracts mix
features once per region while retaining individual cue/source retrieval
queries.

At the first/last two canonical lines of a song, timing-review jobs may add one
shadow competitor from the neighbouring song. The competitor is acoustic
evidence only (`shadow_evidence_only=true`) and cannot directly mutate timing.
This is intended for joins/crossfades where a local mix window can contain both
songs.

Source windows are adaptive:

- use token timing when available;
- otherwise use the next canonical lyric onset to avoid clipping long rap lines
  and to avoid unnecessarily wide searches for short lines;
- guarantee enough source duration for the planned acoustic query/slope search;
- bounded fallback remains for the final lyric line.

The existing external forced-alignment protocol is callable directly from
`scripts/v4_pro_selective.py` through explicit backend/model/command arguments.
It remains auxiliary evidence and canonical lyrics remain final text/order
authority.

## Safety boundary

Pro v1.1 still reports `timing_mutation_performed=false`. Automatic Pro writeback
must remain disabled until private real-song calibration + independent blind
validation establishes safe evidence combinations and false-repair bounds.
