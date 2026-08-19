# Lyric Aligner Production Requirements

Status: normative production baseline

This document records the real production workload that should drive product and algorithm decisions. When an implementation choice, optimization target, or edge-case architecture conflicts with this workload, use this document as the design baseline unless a later explicit production decision supersedes it.

## 1. Primary production job

The normal job is not subtitle generation from zero. The normal job starts with a Jianying-exported SRT that is already mostly usable, then repairs it against canonical lyrics.

Typical inputs are:

- one mixed/final program, often around 40–60 minutes;
- a Jianying SRT whose timing is mostly correct;
- canonical lyrics for nearly every song, usually timestamped LRC;
- some canonical files with Enhanced LRC/QRC or other word/token timing;
- original/source song audio for most songs when acoustic fallback is needed;
- song order and, when available, original BPM, target BPM, or the exact time-stretch ratio.

Canonical lyrics are the authority for lyric text and lyric order. Editor ASR, generic ASR, and acoustic models may establish identity or timing evidence, but must not rewrite canonical lyric truth from recognition guesses.

**Canonical text/order authority is not canonical line-break authority.** A line break in LRC/QRC is a grouping/onset representation, not unconditional authority over the final subtitle cue boundary. When editor cue segmentation is already credible, do not move words across cue boundaries merely to mimic canonical line grouping. Re-segmentation requires stronger independent boundary evidence such as word/token timing or audio-derived evidence.

**Text certainty and timing certainty are separate axes.** If canonical sequence can be independently established while cue timing still needs review, repair the known-wrong editor text and keep only the timing question unresolved. A timing review is not permission to preserve editor ASR text that contradicts already-proven canonical lyrics.

**Severe ASR must not create a text-first bootstrap deadlock.** The worse an editor cue is recognized, the less useful raw lexical similarity becomes. When song identity, canonical order, surrounding strong identities, and timed-canonical projection jointly establish a unique sequence, Smart may recover canonical text without lowering lexical thresholds or pretending the recovered cue is a timing anchor.

## 2. Language distribution

The common case is Chinese music with canonical lyrics. English phrases or rap can occur inside Chinese songs.

Korean, Japanese, and other foreign-language songs are less common in ordinary jobs, but a whole 40-minute mix can occasionally contain many such songs. Foreign-language content does **not** automatically require Full V4/Max. It should first use the cheapest mode whose evidence is sufficient; escalate to selective audio or Max only when the text/timing mapping is broadly untrustworthy or local evidence cannot resolve it.

Missing canonical lyrics are uncommon and are a fallback path, not the architecture that should set the cost of ordinary Chinese jobs.

## 3. Tempo and rate reality

Most songs are changed from their source BPM to one target cadence/BPM with one constant time-stretch ratio for the whole used occurrence.

Therefore the primary timing model is affine/constant-rate. If source BPM and target BPM are known, the expected Source-to-Mix slope is:

`rate_prior = target_bpm / source_bpm`

If the exact editor/DAW stretch ratio is available, it is stronger than a BPM-derived prior.

A minority of songs contain multiple source-speed segments inside one used song. Piecewise-rate mapping must be supported, but it is an evidence-triggered exception. A rate change is not itself evidence of a cut.

Design rule: **Affine first; piecewise only when evidence rejects the single-rate model.**

## 4. Jianying timing prior

For normal Chinese jobs, most Jianying cue timing is correct. The common failure pattern is a small number of bad cues inside a largely trustworthy timeline, not a globally broken timeline.

The system must preserve the majority and search for evidence-backed outliers. It must not rebuild an entire timeline merely because a few cues are wrong.

Common high-risk regions include song starts/ends, transitions, English rap/code-switch, fast/stylized/ancient-style singing, repeated chorus identity, and occasional local cuts or special edits.

## 5. Canonical timing evidence

Timestamped LRC line starts are primary non-audio timing evidence.

When Enhanced LRC/QRC or equivalent word/token timing is available, preserve and use it. Word timing can strengthen boundary evidence, distinguish internal lyric structure, and improve local validation. Do not discard word timing merely because line-level matching is sufficient for Text Repair.

Word timing is evidence, not unconditional authority over final subtitle segmentation. Jianying cue boundaries and canonical token boundaries can represent different display semantics. Plain line-LRC grouping is weaker still: it must never, by itself, move otherwise-correct words across trusted editor cue boundaries.

## 6. Product modes

### Standard

Internal basis: Text Repair V2.1.

- no audio reads;
- repair lyric text only;
- never change cue count, numbering, start, or end time;
- preserve trusted editor cue ownership when continuous canonical text/order already matches but canonical line grouping differs;
- fail closed to review when text identity is ambiguous.

Use when the editor timeline is trusted.

### Smart

Internal basis: Canonical Sequence Reconciliation + Anchor Timeline Repair.

- normally no audio reads;
- run Standard/Text Repair first and keep its lexical thresholds unchanged;
- inherit Standard-safe text/cue ownership unless stronger independent sequence/timing evidence rebuts a weak result;
- use exact/unique 1:1 editor identities as primary timing anchors;
- keep the primary timing model four-A gate unchanged;
- use timed LRC, available word/token timing, exact DAW prior, and soft BPM plausibility;
- model the dominant constant-rate transformation robustly;
- preserve normal timing and change only evidence-backed outliers;
- when severe ASR prevents correct lexical span bootstrap, allow an independent **text-only sequence projection** built from baseline strong identities to recover canonical order into existing editor cues;
- without an exact hard rate prior, text-only projection must require at least 3 unique A text anchors plus at least one additional strong A/B anchor, useful source/mix span, and stable affine residuals;
- text-only projection may use complete strong-anchor-bounded canonical gaps to solve 1↔N/N↔N ownership without treating LRC line count as subtitle cue count;
- outer-frontier projection must stop at the first timing discontinuity/cut/ad-lib rather than jumping over the break to chase later LRC;
- sequence/timing-recovered text must remain below B timing grade and must not increase primary timing anchor count;
- unresolved timing can remain review/Pro even after text has been safely repaired;
- unresolved text/identity stays review/selective-audio escalation rather than a guess.

This is the intended default production mode for the common workload.

### Pro

Internal basis: Selective Audio Repair.

- inherit Standard/Smart results;
- read only bounded suspicious audio windows;
- use source↔mix acoustic matching, canonical-constrained forced alignment, and ASR only where useful;
- choose language hints per local canonical span/job rather than blindly using a whole-track language;
- do not rescan already-trusted regions;
- do not assume it can repair a Smart false-ready, because Pro sees only Smart-unresolved work.

Use when Smart cannot safely resolve a small number of regions.

### Max

Internal basis: Full V4 Alignment.

- full/heavy Source-to-Mix reconstruction and acoustic evidence path;
- supports broadly untrusted timelines, complex cuts, transitions, overlaps, and weak anchor coverage;
- must still distinguish canonical text/order authority from final display-segmentation authority.

Max is a fallback, not the default merely because a mix contains Korean/Japanese or other foreign-language songs.

### Cross-mode monotonicity

The mode ladder is a capability ladder, not permission to overwrite lower-mode safe results:

`Standard -> Smart -> Pro -> Max`

A higher mode may add evidence, resolve more reviews, or rebut a **weak** lower-mode mapping. Without stronger independent evidence, it must not regress text correctness, cue ownership/display segmentation, or timing already established safely. In particular, Smart sequence projection is allowed to replace a low-confidence/review lexical mapping only when song-local canonical order and independent projection satisfy their own strict contract; it must not reopen pure Standard-safe segmentation merely because LRC line grouping differs.

## 7. Anchor trust and anti-circularity

Do not let a cue prove itself correct.

Trust classes:

- **A anchor:** original editor text already has a unique/high-confidence 1:1 canonical identity and monotonic context independent of timing model. A anchors may build primary timing model.
- **B evidence:** a small safe text repair was needed but identity remains strong. B may support/check a model but must not establish primary timing authority by itself.
- **C evidence:** span merge/split, gap, repeated occurrence, large edit, sequence/timing-recovered text, or otherwise ambiguous identity. C must not build primary timing model.

Two independent model families are allowed, with different authority:

1. **Primary timing model:** four-A production gate; may support timing validation/repair under existing guards.
2. **Text-only Sequence Projection:** can be built from 3 A + >=1 strong B (or a narrower exact-rate-prior case) solely to recover canonical text/order. It cannot authorize timing mutation and its recovered decisions must remain C-grade.

This separation is mandatory. A sequence-recovered cue may be perfectly canonical after repair and still remain timing review. It must not be promoted to A/B merely because the system itself inserted the correct text.

Outlier timing decisions continue to use robust fitting and leave-one-out/independent-neighbor logic so the candidate cue does not circularly validate its own timing.

## 8. Timing change policy

Jianying timing is a strong prior, but not absolute authority.

A timing change requires multiple independent supports such as canonical occurrence identity, monotonic lyric order, strong anchors before/after, stable per-song affine model, exact/compatible rate evidence, canonical line/token timing, and structural safety after the proposed change.

Interior repairs should prefer evidence from both sides. Edge extrapolation at a song start/end needs stronger one-sided support and/or a known rate prior.

If evidence is insufficient, keep original timing and return review/Pro escalation. This does not prevent separately repairing lyric text when canonical text/order has already been independently established.

## 9. Piecewise and cut behavior

A stable rate with an abrupt offset change across many anchors can indicate a local cut and may justify a piecewise-offset proposal. Different stable rates can indicate a true piecewise-rate edit.

These are minority paths. They must be inferred from evidence, never enabled globally by default. Smart text-only frontier reconciliation must stop on a local timing discontinuity instead of treating a cut as permission to keep extrapolating canonical order.

## 10. Cost escalation principle

Use the cheapest sufficient evidence first:

`Standard -> Smart -> Pro -> Max`

Normal cues must not pay for hard cases. A few difficult cues must not trigger a full-program acoustic scan unless the cheap evidence chain demonstrates that the broad timeline is unreliable.

## 11. Output and safety requirements

MUST:

1. Optimize the main path for Chinese + canonical lyrics + mostly-correct Jianying timing.
2. Treat one constant time-stretch ratio per song as the common case.
3. Preserve and use word/token timing when available.
4. Avoid making ordinary jobs pay the acoustic cost of rare multilingual/no-lyric cases.
5. Never rebuild all timing merely to fix a small number of cues.
6. Treat canonical lyrics as text/order authority, not unconditional line-break/segmentation authority.
7. Treat Jianying timing and credible cue segmentation as strong but rebuttable priors.
8. Keep text certainty separate from timing certainty; do not keep known-wrong editor text merely because timing remains review.
9. Do not let severe-ASR lexical failure permanently block canonical sequence recovery when independent song/order/timing evidence is sufficient.
10. Keep text-only sequence projection authority strictly below primary timing authority; recovered text must not create timing anchors.
11. Require multiple independent supports before automatically changing timing or moving text across a trusted cue boundary.
12. Fail closed to preserve/review/Pro escalation when proof is insufficient.
13. Run expensive acoustic work locally before escalating to Max.
14. Never overwrite original inputs; write separate outputs/artifacts.
15. Never improve benchmarks with song/cue/timestamp-specific hard-coding.
16. Optimize false-repair/false-ready risk before optimizing for fewer reviews.
17. Keep the four product modes semantically distinct even if implementation components are shared.
18. Preserve lower-mode safe results in higher modes unless stronger independent evidence explicitly rebuts them.

## 12. Acceptance direction

Measure at least:

- false text repairs;
- false cross-cue text moves / segmentation regressions;
- canonical-text recovery rate on severely corrupted editor ASR;
- sequence-projection false-auto rate;
- number of primary timing anchors before/after text recovery (must not grow from self-recovered text);
- false timing repairs;
- false-ready decisions;
- percentage of original trusted cues preserved;
- timing error on intentionally corrupted cues;
- review/escalation rate;
- fraction of jobs resolved without audio;
- fraction of audio processed in Pro relative to full duration;
- runtime by mode;
- Chinese ordinary-job performance separately from multilingual hard-set performance.

Private real-song calibration and blind evaluation should decide whether new evidence is safe enough for automatic write-back. Production safety thresholds must not be loosened merely to reduce review count. Every real production failure used for development should be converted to a generic synthetic regression without publishing or hard-coding the real song, cue number, timestamp, or lyric text.
