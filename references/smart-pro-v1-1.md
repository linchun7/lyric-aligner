# Smart / Pro v1.1 production policy

Date: 2026-08-19

Normative workload baseline remains `references/production-requirements.md`.
This change keeps Smart and Pro as the daily primary modes and does not expand
Max/Full V4 as the default path.

## Smart v1.1.3

Smart still reads no audio. The v1 A-anchor affine engine remains the timing
model, with these production hardenings:

- `preserve` means timing was actually validated strongly enough to keep;
- `timing_model_not_ready`, missing unique timed-canonical identity, and C-grade
  identities are unresolved and must escalate to Pro instead of making the
  whole Smart task look `ready`;
- B-grade identity cannot build the timing model, but may be secondarily
  confirmed by an already-ready A-anchor model;
- automatic timing repair may not create a new subtitle overlap or increase an
  existing overlap in the combined proposal;
- rate prior provenance is explicit: `exact_daw`, `bpm_derived`, or
  `anchor_estimated`; exact DAW stretch remains stronger than BPM-derived prior;
- Smart report schema remains `smart-1.1`; current policy id identifies v1.1.3
  behavior and stale Smart artifacts must be rerun before Pro.

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
same canonical text/order, Smart keeps the editor cue ownership. Moving words
between otherwise-correct cues requires stronger boundary evidence than a line
LRC break.

Public regression uses synthetic but structurally equivalent text:

```text
editor:
  第一段歌词到这里
  下一小句仍在同一画面
  最后几个字继续播放

canonical LRC:
  第一段歌词到这里下一小句
  仍在同一画面最后几个字继续播放
```

The continuous lyric text/order is the same; only grouping differs. Standard and
Smart must retain editor cue ownership.

### Severe-ASR text recovery

A timing/identity review is not permission to keep editor ASR garbage when the
canonical sequence can already be independently proven. Text Repair V2
thresholds remain unchanged; Smart first builds its affine model from original
high-confidence A anchors.

#### Interior bilateral recovery

After a model is ready, Smart may revisit a low-similarity text-review block only
when all of these hold:

- validated single-line text anchors on both sides (`score >= 0.92`);
- both anchors belong to the same source song and each agrees with the ready
  affine model within 750ms;
- the canonical occurrences strictly between those anchors are consecutive and
  same-source;
- the entire canonical gap can be partitioned monotonically onto review cue
  starts, with the first onset for every cue within 750ms of editor start;
- each cue absorbs at most four canonical lines and a block contains at most
  eight cues.

Successful reason:

```text
timing_model_confirms_canonical_sequence
```

#### Song-edge one-sided recovery — v1.1.3

A real production song-edge failure showed that an editor-only ad-lib can remove
the left/right anchor even though the first/last canonical lyric cue is timing-
consistent and the rest of the song supplies a stable affine model. v1.1.3 adds
a narrower one-sided path instead of lowering lexical thresholds.

It requires all of the following:

- the affine model was already `ready` from independent A anchors;
- the candidate is within the first/last four canonical rows of that song;
- the available side contains at least two immediately adjacent, consecutive
  canonical, `score >= 0.92` text anchors;
- those anchors each agree with the model within 750ms;
- the candidate canonical onset agrees with editor cue start within a tighter
  500ms tolerance;
- a skipped review cue may be treated as transparent only when it has no
  canonical claim at all (`canonical_ordinal=None` and `canonical_span=None`),
  i.e. an editor-only ad-lib; at most three such cues may be skipped;
- a weak mapped cue is never transparent and blocks one-sided recovery.

Editor-only ad-libs remain untouched and stay review. Successful reason:

```text
timing_model_confirms_song_edge_canonical
```

Both recovery paths are **text authority only**. Recovered cues do not become A
timing anchors, do not participate in the model that validated them, and do not
receive new automatic timing-write authority.

Smart report records:

- `text_review_count_before_timing_recovery`;
- `text_timing_recovery_count`;
- `text_timing_recovery_block_count`;
- `text_edge_timing_recovery_count`;
- `text_edge_timing_recovery_block_count`;
- final `text_review_count`.

## Pro v1.1

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

Pro requires the exact current Smart schema + policy. After v1.1.3, old Smart
reports are stale and must be rerun. Pro only handles Smart-unresolved cues; it
does not automatically catch Smart false-ready results, which is why
segmentation/lower-mode-monotonicity regressions are release tests at Smart itself.

Nearby acoustic review cues are assigned to merged mix regions. ASR-only jobs do
not widen an acoustic region. Source windows use token timing / next canonical
onset and guarantee enough duration for the planned acoustic slope search.

At the first/last canonical lines of a song, a timing-review job may add one
neighbouring-song shadow competitor. The competitor remains acoustic evidence
only (`shadow_evidence_only=true`) and cannot directly mutate timing.

The existing external forced-alignment protocol remains callable from
`scripts/v4_pro_selective.py` through explicit backend/model/command arguments.
It remains auxiliary evidence and canonical lyrics remain final text/order
authority.

## Safety boundary

Pro v1.1 still reports `timing_mutation_performed=false`. Automatic Pro writeback
must remain disabled until private real-song calibration + independent blind
validation establishes safe evidence combinations and false-repair bounds.

Max also follows the same segmentation authority contract: line-LRC grouping is
not sufficient evidence by itself to resegment a trusted editor subtitle cue.
