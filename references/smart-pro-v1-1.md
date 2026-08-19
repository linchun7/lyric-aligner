# Smart / Pro v1.1 production policy

Date: 2026-08-19

Normative workload baseline remains `references/production-requirements.md`.
This change keeps Smart and Pro as the daily primary modes and does not expand
Max/Full V4 as the default path.

## Smart v1.1

Smart still reads no audio. The v1 A-anchor affine engine remains the timing
model, with these production hardenings:

- `preserve` now means timing was actually validated strongly enough to keep;
- `timing_model_not_ready`, missing unique timed-canonical identity, and C-grade
  identities are unresolved and must escalate to Pro instead of making the
  whole Smart task look `ready`;
- B-grade identity still cannot build the timing model, but may be secondarily
  confirmed by an already-ready A-anchor model;
- automatic timing repair may not create a subtitle overlap that did not exist
  in the editor SRT;
- rate prior provenance is explicit: `exact_daw`, `bpm_derived`, or
  `anchor_estimated`; exact DAW stretch remains stronger production evidence
  than a BPM-derived prior;
- Smart report schema is `smart-1.1` and includes
  `pro_escalation_required` plus validated-preserve/review counts.

## Pro v1.1

Pro stays local and evidence-first. It still performs no timing mutation.

Evidence is now routed by the reason Smart escalated the cue:

- timing review with canonical identity -> local source<->mix acoustic first;
- text/identity review -> local ASR + word timestamps;
- source forced alignment is requested only when useful, primarily when
  canonical identity/text needs stronger source-side evidence and no word-timed
  canonical is already available;
- word-timed Enhanced LRC/QRC avoids redundant source forced alignment where its
  timing evidence is already sufficient for routing;
- unmapped review cues remain ASR-only instead of pretending source identity is
  known.

Nearby review cues are assigned to merged mix regions. The v1.1 acoustic
executor decodes/extracts mix features once per region while retaining
individual cue/source retrieval queries.

At the first/last two canonical lines of a song, timing-review jobs may add one
shadow competitor from the neighbouring song. The competitor is acoustic
evidence only (`shadow_evidence_only=true`) and cannot directly mutate timing.
This is intended for joins/crossfades where a local mix window can contain both
songs.

Source windows are adaptive:

- use token timing when available;
- otherwise use the next canonical lyric onset to avoid clipping long rap lines
  and to avoid unnecessarily wide searches for short lines;
- bounded fallback remains for the final lyric line.

The existing external forced-alignment protocol is now callable directly from
`scripts/v4_pro_selective.py` through explicit backend/model/command arguments.
It remains auxiliary evidence and canonical lyrics remain final text/order
authority.

## Safety boundary

Pro v1.1 still reports `timing_mutation_performed=false`. Automatic Pro writeback
must remain disabled until private real-song calibration + independent blind
validation establishes safe evidence combinations and false-repair bounds.
