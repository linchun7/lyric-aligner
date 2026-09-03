# V4 Structural Benchmark Coverage

Updated: 2026-09-03

This document is the public, privacy-safe coverage map for P1 structural evaluation. It records what kinds of structural truth are currently supported by independent evidence, what is only historical/diagnostic, and what is still missing before a new detector may be selected.

The governing rule is simple: **prediction output must not be promoted into truth**. A category is considered benchmark-ready only when the structural fact comes from an independent production/editor/user/QA source and is frozen before candidate selection. Existing blind results must not be reused for threshold tuning.

## 1. Canonical scenario semantics

The schema-1.1 `structural_scenarios` labels are case-level truth metadata. A case may carry multiple labels when distinct structural facts coexist. `none` is exclusive.

- `none`: no targeted structural event is present in the evaluation window.
- `hard_cut`: an abrupt source handoff/cut with no material crossfade interval. A detector prediction alone is not enough to establish this label.
- `same_track_splice`: source position jumps within the same logical track/occurrence, including removed/repeated source material. It does not imply whether the splice itself is abrupt or crossfaded.
- `crossfade`: two source branches are simultaneously active during a bounded gain handoff. This label is used only when independent evidence supports a crossfade rather than a boundary sliver.
- `true_overlap`: sustained simultaneous source activity that is not merely the gain-handoff interval of a simple crossfade. `crossfade` and `true_overlap` are intentionally distinct so one fact is not double-counted by default.
- `sequential_transition`: adjacent occurrences transition without independently supported persistent simultaneous-source activity.
- `piecewise_rate`: one logical occurrence contains independently known rate regimes that cannot be represented by one stable affine rate. Algorithmic `PIECEWISE_RATE_ACCEPTED` is not truth.
- `reorder`: source/editor chronology is non-monotonic; material appears in a different source-order sequence or returns to an earlier source region.
- `detached_tail`: material after the authoritative main-program end is separated by a long inactive/zero region and is independently classified as export residue rather than program content.

The labels describe truth; they do not grant timing, cut, overlap, review, or release authority.

## 2. Current evidence coverage

| Scenario | Real calibration truth | Fresh locked blind truth | Current status |
| --- | --- | --- | --- |
| `none` | available through clear/sequential real boundaries, but not frozen as a dedicated `none` real case | yes: r3 has four synthetic negative cases | benchmark-ready for negative control |
| `same_track_splice` | yes: KPOP110 Gee has independently audited source-offset handoff/removal within one occurrence | yes: r3 has four unseen synthetic positive splice cases | covered |
| `crossfade` | yes: KPOP110 production review contains independently confirmed crossfade boundaries | yes: r3 positive cases use a locked linear crossfade between source-offset branches | covered |
| `sequential_transition` | yes: KPOP130 and 快乐健走140 production review explicitly resolve representative boundaries clear with no persistent dual-source support | no dedicated fresh blind category | calibration-ready; blind gap |
| `reorder` | yes: the private 190 regression truth explicitly documents non-monotonic mapped source/editor order and the affected review region | yes: r4 has four fresh locked hidden reorder cases | evaluation detector passed fresh blind; production promotion not yet granted |
| `detached_tail` | yes: Walk120 QA freezes the main-program end, long exact-zero region, and later detached audio island as export residue | yes: r4 has four fresh locked hidden detached-tail cases | evaluation detector passed fresh blind; production promotion not yet granted |
| `hard_cut` | no independently frozen clean case yet | no | gap |
| `true_overlap` | no independently frozen non-crossfade overlap case yet | no | gap |
| `piecewise_rate` | production history/user editing facts suggest candidates, but no independent immutable benchmark truth currently separates real rate regimes from detector inference | no | gap; do not use algorithm selection as truth |

## 3. Evidence already verified

### 3.1 KPOP110 real calibration

The first r2 calibration window contains an independently audited same-track Gee splice and a production-reviewed adjacent transition. The production review marks the adjacent boundary `confirmed_overlap` and explicitly describes it as a confirmed crossfade supported by independent mapping-constrained acoustic evidence.

The second KPOP110 calibration window also contains a production-reviewed `confirmed_overlap` boundary whose rationale explicitly states `Confirmed short crossfade`.

These facts are suitable calibration evidence for `same_track_splice` and `crossfade`. They do **not** automatically create `hard_cut`, `true_overlap`, or `piecewise_rate` truth.

### 3.2 Sequential-transition real calibration

Representative KPOP130 and 快乐健走140 boundaries are production-reviewed `resolved_clear`; their rationales state that strict/local dual-source evidence did not show persistent simultaneous activity and that the high raw ambiguity score is attributable to repetitive source/rhythmic similarity rather than a confirmed crossfade.

These are independent real calibration examples for `sequential_transition`.

### 3.3 Reorder real calibration

The private 190 regression dataset documents manually confirmed non-monotonic source/editor order: the editor-cue region for one source ordinal appears after later source ordinals, and the cues after the return remain review-only. This is calibration data, not blind data. It is valid real `reorder` truth but is not yet represented by a category-specific detection metric in the strict evaluator.

### 3.4 Detached-tail real calibration

Walk120 QA records an authoritative main-program end at `2727.582s`, followed by `279.594s` of exact digital zero and a later approximately `6.526s` active island. The task-level content-extent evidence explicitly classifies that later island as export residue, not playlist program content. This is strong real `detached_tail` truth.

### 3.5 r3 fresh blind

The r3 fresh-blind generator was locked before candidate selection. Its eight unseen cases consist of four positive cases and four negative cases. Positive cases linearly crossfade between two source offsets of the same synthetic stem; negative cases remain on one source offset with gain variation/noise. Therefore the r3 blind set directly covers `same_track_splice + crossfade` positives and `none` negatives. It does not cover `hard_cut`, `true_overlap`, `piecewise_rate`, `reorder`, `sequential_transition`, or `detached_tail` as dedicated blind categories.

The already observed r3 blind result is frozen historical evidence and must not be retuned against.

## 4. Typed structural-event metric contract

The strict evaluator now has a privacy-safe typed event layer in addition to the existing cut/overlap metrics. Truth uses `expected_structural_events`; candidate output uses `predicted_structural_events`. A typed prediction is rejected unless the case already contains a frozen expected-event list, including an explicit empty list for negative controls.

Point events are `hard_cut / same_track_splice / sequential_transition` and use `kind + time_ms` with monotonic maximum-cardinality/minimum-error matching. Interval events are `crossfade / true_overlap / piecewise_rate / reorder / detached_tail` and use `kind + start_ms + end_ms` with monotonic maximum-cardinality/maximum-IoU matching. The default point tolerance is 500 ms and the default interval threshold is IoU 0.5; if a case freezes different thresholds, those truth-side thresholds enter ground-truth identity.

Expected events and their matching thresholds enter the immutable dataset ground-truth SHA. Predicted events never do. Event kind must be present in the case's explicit `structural_scenarios`; `none` requires an explicit empty expected list. Wrong event shape, duplicate event, non-finite/negative time, prediction-only event metadata, or truth/scenario mismatch all fail closed.

Strict evaluation now exposes aggregate `structural_event_precision / recall / f1`, false-positive/miss counts, point MAE and interval mean IoU in overall/language/structural scopes. Event locations remain private and are not copied into the evaluation report.

Compatibility was verified against the frozen r3 calibration and locked blind view. Both historical ground-truth SHAs remain exact; legacy cases have `structural_event_annotation_case_count=0` and are not treated as event-level clean truth. After stripping only the newly added `structural_event_*` fields, the old and new r3 evaluations are recursively equal.

## 5. Current detector-readiness

`reorder` and `detached_tail` have now completed the full real-calibration -> candidate-selection -> predeclared-policy -> fresh-blind cycle. The selected evaluation-only candidate passed r4 fresh blind with overall typed-event precision/recall/F1 `1/1/1`, interval mean IoU `0.999696`, category recalls `1.0`, and `none` clean-case rate `1.0`. The r4 blind is now permanently observed and must not be used for retuning.

Remaining categories are still benchmark-limited:

1. add independent `piecewise_rate`, `hard_cut`, and non-crossfade `true_overlap` truth before researching those detectors;
2. keep r3 and r4 observed blind sets out of future threshold tuning;
3. treat the current `reorder / detached_tail` detector as evaluation evidence until a separate production-authority design and real-task regression prove it can be integrated fail-closed.

## 6. Current research priority

The next algorithm target should not be chosen by convenience. Priority is determined by production impact and availability of independent truth:

1. production-promotion design for the fresh-blind-passed `reorder / detached_tail` evidence, without granting raw-SRT heuristics new authority;
2. `piecewise_rate` — important for manually edited songs, but truth must first be frozen independently of Max predictions;
3. `hard_cut` / non-crossfade `true_overlap` — obtain clean truth cases before detector work;
4. ordinary timing refinement remains lower priority because current calibration timing/text performance is already near saturation.
