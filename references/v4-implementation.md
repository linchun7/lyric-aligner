# Lyric Aligner v4 实施记录与关键代码说明

> P3 前的完整实施/架构记录保存在 `references/archive/2026-08-19-pre-p3-v4-implementation.md`。本文件聚焦当前 responsibility graph 与 Partial Timeline Repair P1–P5。

## 1. Authority graph

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
TrackAsset     -> source/canonical identity truth
Editor SRT     -> auxiliary mix-time evidence
ASR            -> auxiliary mix-time acoustic evidence
Forced P7/P8   -> auxiliary acoustic evidence
P9 fusion      -> uncalibrated multi-family shadow diagnostics
P4 trust lock  -> calibrated cue-trust proposal eligibility only
P5 Doctor      -> read-only readiness diagnostics only
```

禁止：

```text
ASR/forced text -> final canonical lyric
source-ms evidence -> direct mix-ms comparison
rate change -> implicit cut
review confirmed_cut flag -> CUT_AWARE without materialization
P9 HIGH -> automatic trust/timing mutation
mutable fusion/decision JSON without artifact validation -> production input
synthetic CI -> claim real calibration/accuracy
P5 proposal_inputs_ready -> automatic timing write-back
```

## 2. Text Repair V2

冻结 timeline 的文字修复层，不读取 audio、不改变 cue count/number/start/end。需要 timing 判断的任务进入 Source-to-Mix/Partial Timeline Repair。

## 3. Partial Timeline Repair 分层

```text
P1  timeline/partial_repair.py
    explicit cue trust + local structural guards

P2  timeline/partial_repair_evidence.py
    P9 editor/canonical identity bridge

P3a timeline/partial_repair_context.py
    effective-run/artifact lineage -> mapping/cut context

P3b timeline/partial_repair_production.py
    formal run/fusion payload+artifact verification

P4a timeline/partial_repair_trust.py
    strict calibration/blind trust lock + low-level decision semantics

P4b timeline/partial_repair_trust_production.py
    formal cue-trust decision artifact verification

P5a timeline/partial_repair_readiness.py
    read-only P3/P4 readiness composition

P5b doctor_partial.py
    backward-compatible Doctor extension + partial_repair:* requirements
```

正式生产必须复用 P3/P4 formal validators；P5 不重新实现一套较宽松的 lineage/trust 判断。

## 4. P1/P2/P3/P4 responsibility summary

P1：`trusted / untrusted / unknown`，trusted timing hard lock，Source-to-Mix-only candidate，候选不得穿越 trusted neighbor 或相互重叠。

P2：P9 shadow level 不直接生成 trust；editor cue 必须唯一绑定 canonical line；candidate boundary 只使用 authoritative `source_timeline_boundary_ms`。

P3：验证 effective run/artifact exact task/algorithm/stage/output hash；从 coarse/Fine `TimeWarp.mapping.mode` 派生 continuous mapping；CUT_AWARE 只来自 materialized `cut_timewarp_rebuild`；fusion 必须绑定 exact effective-run artifact 并通过 output SHA/size/self-signature/upstream 校验。

P4：trust lock 重新验证 strict calibration/blind selection、candidate revision/runtime、evaluation/policy hashes、source-group isolation、calibration/blind identity separation 和 blind language-scope gates。formal decision artifact 必须绑定 exact lock/candidate/runtime/fusion identity。P9 CONFLICT trusted decision 会安全降级；human review 优先。

## 5. P5a `timeline/partial_repair_readiness.py`

`inspect_partial_timeline_repair_readiness()` 是只读编排器。输入可包含：

```text
run_path / run_artifact_path
fusion_path / fusion_artifact_path
trust_lock_path
decision_path / decision_artifact_path
```

### 5.1 P3 lineage inspection

调用 `inspect_partial_repair_artifacts()` 复用正式 P3 验证，因此 incomplete pair、tampered output、wrong artifact lineage、blocked mapping 等继续 fail closed。成功后只输出 privacy-safe 摘要：

```text
run_stage
artifact ID prefix
fusion CONFLICT count
fusion language scopes
AFFINE / PIECEWISE_RATE / CUT_AWARE counts
unavailable occurrence count
confirmed cut occurrence count
```

### 5.2 P4 lock inspection

调用 `load_calibrated_trust_policy_lock()`。`valid=true` 只说明 strict calibration/blind lock 本身有效；`actionable=true` 还要求 lock 的 `cue_trust_generation_allowed` 为真，即至少存在显式 blind-gated `language:*` scope。

### 5.3 Formal decision inspection

只有 decision payload/artifact 成对、P3 lineage valid、trust lock valid 时才继续。P5 调用 `validate_calibrated_trust_decision_artifact()` 检查 formal artifact，再调用 `calibrated_decisions_to_explicit_trust()` 复用正式 scope/conflict/binding 语义。报告只保留 decision 数和 trusted/untrusted/unknown 等计数。

### 5.4 Status state machine

```text
no partial input                         -> not_requested
invalid/incomplete lineage               -> blocked
valid lineage, no lock                   -> human_review_or_calibration_required
invalid lock                             -> blocked
valid but non-actionable lock            -> human_review_required
actionable lock, no decisions            -> calibrated_decisions_required
invalid decisions                        -> blocked
all formal P3/P4 inputs valid            -> proposal_inputs_ready
```

所有状态均固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

### 5.5 Privacy

readiness 不输出 raw lyric/subtitle text 或 artifact output path；validator exception detail 会把 POSIX/Windows absolute local path 替换为 `<local_path>`。

## 6. P5b `doctor_partial.py`

`build_doctor_report_with_partial_repair()` 包装既有 `build_doctor_report()`，不重写稳定 Doctor core。旧 requirements 仍交给原 Doctor；新增 partial requirements 独立计算后合并结果：

```text
partial_repair:lineage
partial_repair:trust_lock
partial_repair:actionable_scope
partial_repair:decisions
partial_repair:proposal_inputs
```

未知 `partial_repair:*` requirement 继续抛 `DoctorError`。Doctor 原有 API、stage recommendation 与 legacy requirement 语义保持不变。

## 7. CLI extension

`scripts/v4_doctor.py` 改用 `build_doctor_report_with_partial_repair()`，新增：

```text
--partial-trust-lock
--partial-trust-decisions
--partial-trust-decisions-artifact
```

并将五个 `partial_repair:*` requirements 加入允许集合。未提供 partial-repair 输入时，Doctor 仍可按旧方式工作。

## 8. Validation boundary

Public synthetic tests可证明：P3/P4 validator 复用、Doctor requirement wiring、readiness state machine、mapping/conflict/scope 摘要、formal decision pair validation、path redaction、proposal-only authority flags、Python compatibility。

Public CI 不能证明真实 private candidate 已通过 blind gate、真实歌曲准确率、false-auto rate 或 automatic timing write-back safety。因此 P5 只完成 readiness infrastructure，不提升 release authority。
