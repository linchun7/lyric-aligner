# Lyric Aligner v4 关键变更记录

> 2026-08-19 P3 前的完整历史变更记录保存在 `references/archive/2026-08-19-pre-p3-v4-change-record.md`。本文件从 P3 起记录当前阶段新增行为；更早历史保留在归档快照和 Git 历史中。

## 当前 authority

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
Editor / ASR / forced -> auxiliary evidence
P9 fusion      -> uncalibrated shadow diagnostics
```

全局固定：

```text
release_gate_eligible = false
automatic_timing_change_allowed = false
```

P9 本身继续 `policy_calibrated=false`。P4 新增的 trust-policy lock 可以证明“某个 cue-trust candidate 已经过 strict calibration + independent blind gate”，但只允许生成 cue trust proposal，不提升 timing/release authority。

---

## 2026-08-19 — Partial Timeline Repair P1

新增 `lyric_aligner/timeline/partial_repair.py`。建立 `trusted / untrusted / unknown` cue trust、trusted timing hard lock、Source-to-Mix-only candidate、`AFFINE / PIECEWISE_RATE / CUT_AWARE`、`rate change != cut`、trusted-neighbor 与 candidate-overlap fail-closed guards。输出 proposal-only。

---

## 2026-08-19 — Partial Timeline Repair P2

新增 `lyric_aligner/timeline/partial_repair_evidence.py`。P9 `LOW/MEDIUM/HIGH/CONFLICT` 只做 diagnostics；`HIGH` 不自动 trusted，`CONFLICT` 不自动 untrusted；一 editor cue 对多 canonical line 时 ambiguous；candidate 只使用 `source_timeline_boundary_ms`；open-end 1ms sentinel 被拒绝。

---

## 2026-08-19 — Partial Timeline Repair P3

新增：

```text
lyric_aligner/timeline/partial_repair_context.py
lyric_aligner/timeline/partial_repair_production.py
```

P3 正式生产路径要求 effective run/artifact + P9 fusion/artifact 四件套：

- continuous mapping 从 exact coarse/Fine `TimeWarp.mapping.mode` 派生；
- Fine occurrence/track/canonical-selection identity 与 coarse 完全一致；
- `mapping_blocked=true` -> unavailable；
- CUT_AWARE 只认 materialized `cut_timewarp_rebuild` lineage；
- fusion artifact 验证 exact output SHA/size/self-signature，并绑定 exact effective-run artifact；
- tampered fusion JSON fail closed；
- 正式 production bridge 只接受当前 algorithm version。

P3 仍不自动写 timing。

---

## 2026-08-19 — Partial Timeline Repair P4 strict calibrated trust lock

新增：

```text
lyric_aligner/timeline/partial_repair_trust.py
lyric_aligner/timeline/partial_repair_trust_production.py
scripts/v4_build_partial_trust_lock.py
scripts/test_v4_partial_timeline_repair_trust.py
scripts/test_v4_partial_timeline_repair_trust_production.py
```

### 1. 不发明公开阈值

P4 不把 synthetic test 或 P9 `HIGH` 当成校准依据，而是直接复用 canonical strict workflow：

```text
scripts/v4_calibration_workflow.py
lyric_aligner/evaluation/strict_workflow.py
```

Trust lock builder 重新验证：

- selection self-hash；
- calibration baseline/candidate split、dataset/revision、candidate ID/revision/runtime identity；
- calibration evaluation/policy file SHA；
- selected candidate 的 calibration gate 重新计算结果；
- independent blind baseline/candidate 与 selection candidate/baseline lock；
- blind gate self-hash、blind policy/evaluation/selection file SHA；
- blind gate 重新计算结果；
- strict evaluation `source_group_isolation_enforced=true`；
- calibration 与 blind 的 ground-truth identity / case IDs 必须不同；
- selected runtime algorithm version 必须等于当前版本。

### 2. Scope coverage fail closed

只有 blind policy 中显式声明、且 blind gate 实际通过的 `language:*` scope 才写入：

```text
eligible_language_scopes
```

如果 blind policy 只约束 `overall`，trust lock 仍可生成用于审计，但：

```text
cue_trust_generation_allowed = false
```

因此总体指标不能自动外推到未单独验证的语言。

Trust lock 始终：

```text
policy_calibrated = true
independent_blind_gate_passed = true
automatic_timing_change_allowed = false
release_gate_eligible = false
```

这里的 calibrated 只表示 cue-trust policy 的 calibration/blind identity 已锁定，不表示 P9 自身阈值已校准，也不表示可自动写 SRT。

### 3. Calibrated decision contract

私有 selected candidate 可以输出 cue-level decision，但低层 payload 必须：

- self-hash；
- bind exact trust-policy lock SHA；
- bind selected candidate ID/revision/runtime；
- bind exact P9 fusion artifact ID；
- 每个 cue number 唯一；
- scope 为 `language:*` 且与 P9 `language_profile` 完全一致；
- status 只允许 `trusted / untrusted / unknown`；
- 不允许 `automatic_timing_change_allowed=true`。

安全降级：

- blind 未覆盖该 language scope -> `unknown`；
- editor→canonical binding 缺失/多义 -> `unknown`；
- P9 `CONFLICT` + candidate 决策 `trusted` -> 强制 `unknown`；
- P9 `HIGH` 在没有 calibrated decision 时不会生成任何 trust；
- explicit `human_review` 对同 cue 覆盖 calibrated decision。

### 4. Formal production decision artifact

正式生产不接受只有 self-hash 的 decision JSON。`partial_repair_trust_production.py` 额外要求 formal artifact：

```text
stage = partial_timeline_trust_decisions
role = cue_trust_decisions
```

并验证：

- task fingerprint / current algorithm version；
- artifact self-signature；
- decision output SHA/size；
- normalized config 中 exact trust lock SHA、candidate ID/revision/runtime、fusion artifact ID；
- exact fusion artifact 是 upstream；
- evidence 明确 `policy_calibrated=true`、`independent_blind_gate_passed=true`，但 timing mutation/release 均 false。

随后才进入 P3 artifact-verified bridge，因此 P4 只改变“trust 从哪里来”的可审计性，不改变 Source-to-Mix timing authority。

### 5. Public CI 边界

Public synthetic tests 只证明 strict lock/identity/scope/artifact mechanics。仓库不会提交一个假的“已经校准好的生产 trust lock”，也不会因为这些测试通过就宣称真实歌曲 accuracy 或 automatic repair safety。
