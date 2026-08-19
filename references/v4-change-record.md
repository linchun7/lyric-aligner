# Lyric Aligner v4 关键变更记录

> 2026-08-19 P3 前的完整历史变更记录保存在 `references/archive/2026-08-19-pre-p3-v4-change-record.md`。本文件从 P3 起记录当前阶段新增行为；早期 a3–a8、P1–P9、PR21–PR26、Text Repair V2 与 forced-alignment batch protocol 1.1 的完整细节均保留在归档快照和 Git 历史中。

## 当前不变的 authority

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
Editor / ASR / forced -> auxiliary evidence
P9 fusion      -> uncalibrated shadow diagnostics
```

固定：

```text
policy_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

---

## 2026-08-19 — Partial Timeline Repair P1 shadow planner

新增：

```text
lyric_aligner/timeline/partial_repair.py
scripts/test_v4_partial_timeline_repair.py
```

建立局部时间轴修复的 fail-closed 结构骨架：显式 `trusted / untrusted / unknown` cue trust；trusted cue timing 硬锁；candidate 只接受 Source-to-Mix；mapping kind 只接受 `AFFINE / PIECEWISE_RATE / CUT_AWARE`；`rate change != cut`；候选不得穿越 trusted neighbor 或与其他 repair candidate 重叠。输出仍为 proposal-only。

---

## 2026-08-19 — Partial Timeline Repair P2 evidence bridge

新增：

```text
lyric_aligner/timeline/partial_repair_evidence.py
scripts/test_v4_partial_timeline_repair_evidence.py
```

把 P9 canonical/editor line identity 接入 P1，但不提升 P9 authority：

- P9 `LOW/MEDIUM/HIGH/CONFLICT` 只做 diagnostics；
- `HIGH` 不自动生成 trusted；`CONFLICT` 不自动生成 untrusted；
- trust 只接受 `human_review` 或未来独立 blind-lock 的 `calibrated_policy`；
- 一 editor cue 对多 canonical line 时 ambiguous/fail-closed；
- repair candidate 只使用 P9 authoritative `source_timeline_boundary_ms`；
- P9 open-end `start+1ms` sentinel 被拒绝；
- CUT_AWARE 低层接口要求独立 confirmed-cut identity，单独 mapping label 不算 cut 证明。

P2 仍不写 SRT timing。

---

## 2026-08-19 — Partial Timeline Repair P3 formal lineage context

新增：

```text
lyric_aligner/timeline/partial_repair_context.py
lyric_aligner/timeline/partial_repair_production.py
scripts/test_v4_partial_timeline_repair_context.py
scripts/test_v4_partial_timeline_repair_context_identity.py
scripts/test_v4_partial_timeline_repair_production.py
```

### 目标

移除生产调用方对以下标签的责任：

```text
mapping_kind_by_occurrence
confirmed_cut_occurrence_ids
```

并进一步禁止生产入口信任一个脱离 formal artifact 的可变 P9 fusion JSON。P3 正式生产路径要求 effective run/artifact 与 fusion/artifact 四件套全部验证后才进入 P2/P1。

### Continuous mapping

支持 effective run stage：

```text
production_orchestration
review_resolution
overlap_recomposition
cut_rebuild
combined_recomposition
```

对于未 cut-rebuild occurrence：

- `mapping_source=coarse` 时验证 exact coarse payload/artifact、自签名/output hash、task/algorithm/occurrence identity及其为 effective-run upstream；直接读取 `result.timewarp.mapping.mode`；
- `mapping_source=fine` 时除上述检查外，还要求 `fine_applied=true`、Fine `result.applied=true`、Fine artifact upstream 包含 exact coarse artifact；
- Fine 的 occurrence、track、`canonical_selection_sha256` 必须与 effective coarse 完全一致，同 task 内错拼另一 track/canonical 也会 fail closed；
- continuous mode 只允许 `AFFINE / PIECEWISE_RATE`；AFFINE 不得夹带 rate breakpoints，PIECEWISE_RATE 必须有实际 breakpoint；
- 不从 BPM 或 LRC timestamp 推导 mapping kind；
- `mapping_blocked=true` 返回 unavailable context，不产生 timing candidate。

### Confirmed cut / CUT_AWARE

CUT_AWARE 只在以下事实链完整时成立：

```text
occurrence.mapping_source = cut_aware_rebuild
occurrence.cut_rebuilt = true
cut_mapping_path + cut_mapping_artifact_path exist
artifact stage = cut_timewarp_rebuild
artifact role = cut_aware_timewarp
payload result.kind = CUT_AWARE
cut count agrees across occurrence / payload / artifact evidence
confirmed_candidate_ids complete
source_review_artifact_id agrees
cut mapping artifact + source review artifact are upstream of effective run
```

因此 `review_resolution` 里即使已经出现 `decision_action=confirmed_cut`，在 dedicated cut rebuild 尚未 materialize 前仍不会被 P3 当作 CUT_AWARE。

对于 combined cut+overlap run，P3 沿继承的 cut occurrence/cut mapping lineage 得到 CUT_AWARE；overlap-only run 不改变底层 Source-to-Mix mapping kind。

### Formal P9 fusion artifact binding

正式入口 `bridge_effective_artifacts_to_partial_repair()` 要求：

```text
run payload <-> run artifact exact output hash
fusion payload <-> fusion artifact exact output hash
fusion artifact stage = evidence_fusion_shadow
fusion artifact role = evidence_fusion
fusion.task/algorithm/source_run_stage/source_run_artifact_id exact
fusion artifact normalized_config.source_run_artifact_id exact
exact effective run artifact is upstream of fusion artifact
fusion artifact evidence keeps shadow-only / uncalibrated / no-release / no-auto-mutation
```

因此手工修改 P9 `source_timeline_boundary_ms` 后继续使用旧 artifact 会在 output hash 校验处直接失败。低层 payload bridge 继续作为测试/组合 API，但不再是正式生产入口。

### Privacy / authority

P3 report 只记录 occurrence ID、stage、artifact ID、mapping kind、cut count、状态/reason；不输出 coarse/fine/cut 本地路径。Formal production report 额外记录 fusion artifact ID 与 `production_inputs_artifact_verified=true`。

本阶段仍固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
```

### Regression coverage

新增测试覆盖：

- base AFFINE；
- applied Fine PIECEWISE_RATE；
- Fine/coarse track mismatch；
- Fine/coarse canonical-selection mismatch；
- review-only confirmed cut 仍 unavailable；
- materialized cut -> CUT_AWARE；
- combined cut+overlap -> CUT_AWARE；
- overlap-only 保留 continuous mapping；
- 伪造 cut label 无 artifact fail closed；
- mapping artifact 不在 effective-run upstream fail closed；
- production bridge 无需 caller mapping/cut labels；
- tampered fusion payload output hash fail closed；
- fusion artifact 必须把 exact effective run 作为 config binding + upstream；
- P9 payload 必须绑定 exact effective-run stage/artifact。

P3 通过 final exact-head CI 后，下一阶段才接 private calibration + independent blind-test 的 calibrated trust policy；不会用 synthetic CI 宣称真实 timing accuracy。
