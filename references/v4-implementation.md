# Lyric Aligner v4 实施记录与关键代码说明

> 2026-08-19 P3 前的完整实施/架构记录保存在 `references/archive/2026-08-19-pre-p3-v4-implementation.md`。本文件从 P3 起聚焦当前 responsibility graph 与生产接口。

## 1. 当前 authority graph

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
TrackAsset     -> source/canonical identity truth
Editor SRT     -> auxiliary mix-time evidence
ASR            -> auxiliary mix-time acoustic evidence
Forced P7/P8   -> auxiliary acoustic evidence
P9 fusion      -> uncalibrated multi-family shadow diagnostics
P4 trust lock  -> calibrated cue-trust proposal eligibility only
```

禁止：

```text
ASR/forced text -> final canonical lyric
source-ms evidence -> direct mix-ms comparison
rate change -> implicit cut
review confirmed_cut flag -> CUT_AWARE without materialization
P9 HIGH -> automatic trust/timing mutation
P9 CONFLICT -> automatic trusted editor cue
mutable fusion/decision JSON without artifact validation -> production input
synthetic CI -> claim real calibration/accuracy
calibrated cue trust -> automatic timing write-back
```

## 2. Text Repair V2

冻结 timeline 的文字修复层，不读取 audio、不改变 cue count/number/start/end。需要 timing 判断的任务进入 Source-to-Mix/Partial Timeline Repair。

## 3. Partial Timeline Repair 分层

```text
P1  partial_repair.py
    explicit cue trust + local structural guards

P2  partial_repair_evidence.py
    P9 editor/canonical identity bridge

P3a partial_repair_context.py
    effective-run/artifact lineage -> mapping/cut context

P3b partial_repair_production.py
    formal fusion payload/artifact verification

P4a partial_repair_trust.py
    strict calibration/blind trust lock + low-level decision semantics

P4b partial_repair_trust_production.py
    formal cue-trust decision artifact verification
```

P1/P2/P3a/P4a 的 payload API 可用于单元测试与受控组合。正式生产到 P4 后使用 P4b；所有层当前仍不直接写 authoritative SRT。

## 4. P1/P2 summary

P1：`trusted / untrusted / unknown`，trusted timing hard lock，Source-to-Mix-only candidate，`AFFINE / PIECEWISE_RATE / CUT_AWARE`，candidate 不得穿越 trusted neighbor 或互相重叠。

P2：P9 shadow level 不直接生成 trust；editor cue 必须唯一绑定 canonical line；candidate boundary 只使用 `source_timeline_boundary_ms`；open-end 1ms sentinel 不可修。

## 5. P3 effective-run / fusion lineage

P3 正式生产入口验证：

- effective run/artifact exact task/algorithm/stage/output hash；
- continuous mapping 直接读 coarse/Fine `TimeWarp.mapping.mode`；
- Fine occurrence/track/canonical-selection identity 与 coarse 完全一致；
- CUT_AWARE 只来自 materialized `cut_timewarp_rebuild` artifact；
- P9 fusion payload/artifact 绑定 exact effective-run artifact；
- fusion output SHA/size、自签名、upstream 完整；
- 正式生产只接受当前 algorithm version。

## 6. P4a `timeline/partial_repair_trust.py`

### 6.1 Trust-policy lock builder

`build_calibrated_trust_policy_lock()` 消费 canonical strict workflow 产物：

```text
selection.json
baseline.calibration.eval.json
selected.calibration.eval.json
calibration.policy.json
blind.gate.json
baseline.blind.eval.json
selected.blind.eval.json
blind.policy.json
```

它重新验证而不是只看 `passed=true`：

1. `load_selection()` 校验 selection schema/self-hash；
2. calibration baseline/candidate 必须是 calibration split，并保持 dataset/revision、baseline/selected candidate ID/revision/runtime locks；
3. evaluation 与 policy file SHA 必须和 selection 中冻结值一致；
4. 对 baseline vs selected candidate 重新执行 `evaluate_gates()`，结果必须通过且与 `selection.selection.selected_gate` 完全一致；
5. blind baseline/candidate 必须是 blind_test split，并通过 `validate_blind_baseline_lock()` / `validate_blind_lock()`；
6. blind gate self-hash、selection/evaluation/policy file SHA 必须一致；
7. 对 blind baseline vs selected candidate 重新执行 `evaluate_gates()`，结果必须通过且与 blind gate 完全一致；
8. 四份 strict evaluation 都必须声明 `source_group_isolation_enforced=true`；
9. calibration 与 blind ground-truth SHA / case-ID SHA 必须不同；
10. selected runtime `algorithm_version` 必须等于当前代码版本。

Lock 保存 opaque identities/hashes，不保存歌词、reference SRT 或本地路径。

### 6.2 Actionable scope

P4 不把 overall blind gate 外推成全语言安全。`eligible_language_scopes` 只来自 blind policy 中实际声明且 blind gate 通过的 `language:*` gate scopes。

```text
blind gates only overall
-> valid audit lock
-> eligible_language_scopes=[]
-> cue_trust_generation_allowed=false
```

只有具体 `language:zh` / `language:ko` 等被 blind policy 明确覆盖时，该语言才有资格接受 selected-candidate cue-trust decision。

### 6.3 Low-level decision semantics

私有 selected candidate 的 decision payload 必须绑定：

```text
trust_policy_lock_sha256
candidate_id
candidate_revision
runtime_identity
source_fusion_artifact_id
```

每个 row：

```text
cue_number
scope = language:*
status = trusted | untrusted | unknown
reason_code
```

P4 将 decision 与 P9 editor cue binding / `language_profile` 交叉验证：

- cue binding 缺失/多义 -> unknown；
- scope 与 P9 language 不一致 -> invalid/fail closed；
- language 未进入 lock coverage -> unknown；
- P9 `CONFLICT` + decision `trusted` -> unknown；
- P9 HIGH 没有 decision -> 不生成 trust；
- human_review 对相同 cue 覆盖 calibrated decision。

这里的 `calibrated_policy` 只决定 P1/P2 的 trust 输入，不决定最终 timing 写回。

## 7. P4b `timeline/partial_repair_trust_production.py`

正式生产不接受只有 self-hash 的 decision payload。它要求：

```text
decision payload
decision formal artifact
trust lock
fusion payload/artifact
run payload/artifact
```

Decision artifact contract：

```text
stage = partial_timeline_trust_decisions
role = cue_trust_decisions
algorithm_version = current
```

Artifact validation：

- formal output SHA/size + artifact self-signature；
- task fingerprint 与 P9 fusion artifact 相同；
- exact fusion artifact ID 必须在 decision artifact upstream；
- normalized config exact bind trust lock SHA、candidate ID/revision/runtime、fusion artifact ID；
- evidence 必须：

```text
policy_calibrated = true
independent_blind_gate_passed = true
automatic_timing_change_allowed = false
release_gate_eligible = false
```

验证后才调用 P4a low-level semantics，再进入 P3 artifact-verified mapping/fusion bridge。

## 8. CLI

`scripts/v4_build_partial_trust_lock.py` 是本地真实 calibration/blind 完成后的 lock builder。它不生成/猜测 threshold，只把已经通过 strict workflow 的 exact artifacts 重新验证并冻结成 trust lock。

仓库**不提交一个 synthetic “生产已校准 lock”**。真正的 lock 应在 private real-song workspace 生成。

## 9. Validation boundary

Public CI 可以证明：

```text
strict selection/blind lock mechanics
candidate revision/runtime lock
calibration/blind identity separation
language scope coverage
trust decision self-hash/identity
formal decision artifact/output/upstream
CONFLICT trusted downgrade
human override precedence
P1-P3 existing lineage/structural guards
```

Public CI 不能证明：

```text
真实 candidate 已通过 private blind gate
真实歌曲 trust false-positive rate
真实 timing repair false-auto rate
automatic timing write-back safety
```

因此 P4 仍固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

下一步不是在代码里编一个 threshold，而是在真实 private 数据上构建第一个 trust lock；只有 lock 实际覆盖的语言 scope 才能生成 calibrated trust proposal。