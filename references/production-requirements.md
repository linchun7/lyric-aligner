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

**Canonical text/order authority is not canonical line-break authority.** A line break in LRC/QRC is a grouping/onset representation, not unconditional authority over the final subtitle cue boundary. When the editor cue segmentation is already credible, do not move words across cue boundaries merely to mimic canonical line grouping. Re-segmentation requires stronger independent boundary evidence such as word/token timing or audio-derived evidence.

**Text certainty and timing certainty are separate axes.** If canonical sequence can be independently established while cue timing still needs review, the system should repair the known-wrong editor text and keep only the timing question unresolved. A timing review is not permission to preserve editor ASR text that contradicts already-proven canonical lyrics.

## 2. Language distribution

The common case is Chinese music with canonical lyrics. English phrases or rap can occur inside Chinese songs.

Korean, Japanese, and other foreign-language songs are less common in ordinary jobs, but a whole 40-minute mix can occasionally contain many such songs. Foreign-language content does **not** automatically require Full V4/Max. It should first use the cheapest mode whose evidence is sufficient; escalate to selective audio or Max only when the text/timing mapping is broadly untrustworthy or local evidence cannot resolve it.

Missing canonical lyrics are uncommon and are a fallback path, not the architecture that should set the cost of ordinary Chinese jobs.

## 3. Tempo and rate reality

Most songs are changed from their source BPM to one target cadence/BPM with one constant time-stretch ratio for the whole used occurrence.

Therefore the primary timing model is affine/constant-rate. If source BPM and target BPM are known, the expected Source-to-Mix slope is:

`rate_prior = target_bpm / source_bpm`

If the exact editor/DAW stretch ratio is available, it is stronger than a BPM-derived prior.

A minority of songs contain multiple source-speed segments inside one used song, for example 120→130 and 125→130 in different regions. Piecewise-rate mapping must be supported, but it is an evidence-triggered exception. A rate change is not itself evidence of a cut.

Design rule: **Affine first; piecewise only when evidence rejects the single-rate model.**

## 4. Jianying timing prior

For normal Chinese jobs, most Jianying cue timing is correct. The common failure pattern is a small number of bad cues inside a largely trustworthy timeline, not a globally broken timeline.

The system must therefore preserve the majority and search for evidence-backed outliers. It must not rebuild an entire timeline merely because a few cues are wrong.

Common high-risk regions include:

- song starts and ends;
- transitions between adjacent songs;
- English rap/code-switch sections inside Chinese songs;
- fast, stylized, ancient-style, or unusually articulated singing;
- repeated chorus text whose occurrence identity is ambiguous;
- occasional local cuts or special edits.

## 5. Canonical timing evidence

Timestamped LRC line starts are primary non-audio timing evidence.

When Enhanced LRC/QRC or equivalent word/token timing is available, preserve and use it. Word timing can strengthen boundary evidence, distinguish internal lyric structure, and improve local validation. Do not discard word timing merely because line-level matching is sufficient for Text Repair.

Word timing is evidence, not an unconditional authority over final subtitle segmentation. Jianying cue boundaries and canonical token boundaries can represent different display semantics. Plain line-LRC grouping is weaker still: it must never, by itself, move otherwise-correct words across trusted editor cue boundaries.

## 6. Product modes

User-facing names may remain simple while internal names stay technical.

### Standard

Internal basis: Text Repair V2.1.

- no audio reads;
- repair lyric text only;
- never change cue count, numbering, start, or end time;
- preserve trusted editor cue ownership when continuous canonical text/order already matches but canonical line grouping differs;
- fail closed to review when text identity is ambiguous.

Use when the editor timeline is trusted.

### Smart

Internal basis: Anchor Timeline Repair.

- normally no audio reads;
- run canonical text matching first;
- inherit Standard-safe text/cue-ownership results unless stronger independent evidence rebuts them;
- use high-confidence cue↔canonical occurrence mappings as timing anchors;
- use LRC line timestamps, available word/token timing, and optional rate/BPM prior;
- model the dominant constant-rate transformation robustly;
- preserve normal cues;
- change only a small number of timing outliers when multiple independent evidence families support the change;
- when editor ASR text is severely wrong, a ready timing model plus canonical-order constraints may resolve canonical text even if lexical similarity is low;
- interior severe-ASR recovery should prefer bilateral canonical anchors; narrowly-scoped song-edge recovery may use stronger one-sided anchor evidence when the model is already independently ready and any intervening cue is a true unmapped editor-only ad-lib;
- text recovered from timing/order evidence must not become a primary timing anchor merely because its text was repaired;
- unresolved timing can remain review/Pro even after text has been safely repaired;
- unresolved cases become review/selective-audio escalation, not guesses.

This is the intended default production mode for the common workload.

### Pro

Internal basis: Selective Audio Repair.

- inherit Standard/Smart results;
- read only bounded suspicious audio windows;
- use source↔mix acoustic matching, canonical-constrained forced alignment, and ASR only where useful;
- choose language hints per local canonical span/job rather than blindly using a whole-track language;
- do not rescan already-trusted regions.

Use when Smart cannot safely resolve a small number of regions.

### Max

Internal basis: Full V4 Alignment.

- full/heavy Source-to-Mix reconstruction and acoustic evidence path;
- supports broadly untrusted timelines, complex cuts, transitions, overlaps, and weak anchor coverage;
- must still distinguish canonical text/order authority from final display-segmentation authority.

Max is a fallback, not the default merely because a mix contains Korean/Japanese or other foreign-language songs.

### Cross-mode monotonicity

The mode ladder is a **capability ladder, not permission to overwrite lower-mode safe results**:

`Standard -> Smart -> Pro -> Max`

A higher mode may add evidence, resolve more reviews, or rebut a prior result. Without stronger independent evidence, it must not regress text correctness, cue ownership/display segmentation, or timing that a lower mode already established safely. This invariant matters especially because Pro only sees Smart-unresolved regions; a Smart false-ready cannot be assumed to be repaired later by Pro.

## 7. Anchor trust and anti-circularity

Do not let a cue prove itself correct.

Suggested trust classes:

- **A anchor:** original editor text already has a unique/high-confidence 1:1 canonical identity and monotonic context independent of the timing model. A anchors may build the timing model.
- **B evidence:** a small safe text repair was needed, but identity remains strong. B evidence may support/check a model but should not establish the primary model by itself.
- **C evidence:** span merge/split, gap, repeated occurrence, large edit, or otherwise ambiguous identity. C evidence must not build the primary timing model.

A low-similarity editor cue may have its **text** recovered from a timing model only if that model was built independently from A anchors and canonical-order constraints bind the candidate span. Interior recovery should prefer bilateral constraints. A narrowly-scoped song-edge exception may use stronger one-sided consecutive anchors only when the candidate lies at the actual source edge, the model is already ready, and intervening editor-only cues have no competing canonical claim. Such recovery does not promote the cue into A/B timing evidence and must not create a circular proof path.

Outlier decisions should use robust fitting and leave-one-out/independent-neighbor logic so that the candidate cue does not circularly validate its own timing.

## 8. Timing change policy

Jianying timing is a strong prior, but not absolute authority.

A timing change requires multiple independent supports such as:

- canonical occurrence identity;
- monotonic lyric order;
- strong anchors before and/or after the cue;
- a stable per-song affine model;
- an exact/known stretch ratio or compatible BPM prior;
- canonical line/token timing;
- structural safety after the proposed change.

Interior repairs should prefer evidence from both sides. Edge extrapolation at a song start/end needs stronger one-sided support and/or a known rate prior.

If evidence is insufficient, keep the original timing and return review/Pro escalation. This does not prevent separately repairing lyric text when canonical text/order has already been independently established.

## 9. Piecewise and cut behavior

A stable rate with an abrupt offset change across many anchors can indicate a local cut and may justify a piecewise-offset proposal. Different stable rates can indicate a true piecewise-rate edit.

These are minority paths. They must be inferred from evidence, never enabled globally by default.

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
9. Require multiple independent supports before automatically changing timing or moving text across a trusted cue boundary.
10. Fail closed to preserve/review/Pro escalation when proof is insufficient.
11. Run expensive acoustic work locally before escalating to Max.
12. Never overwrite original inputs; write separate outputs/artifacts.
13. Never improve benchmarks with song/cue/timestamp-specific hard-coding.
14. Optimize false-repair/false-ready risk before optimizing for fewer reviews.
15. Keep the four product modes semantically distinct even if implementation components are shared.
16. Preserve lower-mode safe results in higher modes unless stronger independent evidence explicitly rebuts them.

## 12. Acceptance direction

The key production metrics are not only raw alignment accuracy. Measure at least:

- false text repairs;
- false cross-cue text moves / segmentation regressions;
- canonical-text recovery rate on severely corrupted editor ASR;
- false timing repairs;
- false-ready decisions;
- percentage of original trusted cues preserved;
- timing error on intentionally corrupted cues;
- review/escalation rate;
- fraction of jobs resolved without audio;
- fraction of audio processed in Pro relative to full program duration;
- runtime by mode;
- Chinese ordinary-job performance separately from multilingual hard-set performance.

Private real-song calibration and blind evaluation should decide when Smart or Pro evidence is safe enough for automatic write-back. Production safety thresholds must not be loosened merely to reduce review count.
