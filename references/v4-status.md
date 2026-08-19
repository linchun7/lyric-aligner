# Lyric Aligner v4 当前实施状态

更新日期：2026-08-19  
主线算法版本：`4.0.0a8`  
当前 authority：canonical lyrics = final text/order truth；Source-to-Mix = primary timing truth。

> 2026-08-19 P3 前的完整历史状态快照保存在 `references/archive/2026-08-19-pre-p3-v4-status.md`。本文件从 P3 起收敛为“当前生产状态”。

## 1. 当前生产能力

```text
V4 production reconstruction / render / review
AFFINE / PIECEWISE_RATE Source-to-Mix
confirmed cut -> CUT_AWARE rebuild
confirmed overlap -> overlap recomposition
cut + overlap -> combined recomposition
editor / ASR / forced alignment shadow evidence
P8 forced source->mix projection
P9 multi-family shadow fusion
production doctor / runtime snapshot / family evaluator
bounded mix decode / source feature cache / safe resume / bounded workers
Text Repair V2
Partial Timeline Repair P1-P4 shadow/proposal path
```

全局生产边界仍固定：

```text
release_gate_eligible = false
automatic_timing_change_allowed = false
```

P9 自身仍 `policy_calibrated=false`；P4 允许一个独立、严格 blind-validated 的 trust-policy lock 标记自身 `policy_calibrated=true`，但它只授权“产生 cue trust proposal”，**不授权 timing write-back 或 release**。

## 2. Text Repair V2

适用于“规范歌词可信、剪映 cue 数量/编号/全部时间冻结，只修文字”的高频任务：

```text
lyric_aligner/text_repair.py
scripts/v4_text_repair.py
scripts/v4_text_repair_batch.py
```

硬边界：不读取 audio；cue count/number/start/end immutable；支持确定性的错字、漏字、多字与 bounded segmentation-span repair；canonical/subtitle gap、重复歌词歧义、cue ownership 不确定继续 `review_required`；LRC timestamp/BPM 不用于覆盖 SRT timing。

## 3. Partial Timeline Repair P1：结构安全 planner

```text
lyric_aligner/timeline/partial_repair.py
```

- cue trust = `trusted / untrusted / unknown`；
- trusted cue timing 硬锁；
- explicit untrusted cue 才可接收 Source-to-Mix candidate；
- mapping kind 只接受 `AFFINE / PIECEWISE_RATE / CUT_AWARE`；
- `rate change != cut`；
- candidate 不能穿越 trusted neighbor，也不能与其他 repair candidate 重叠；
- proposal-only，不自动写 authoritative SRT。

## 4. Partial Timeline Repair P2：P9 evidence bridge

```text
lyric_aligner/timeline/partial_repair_evidence.py
```

- P9 `LOW / MEDIUM / HIGH / CONFLICT` 只做 diagnostics；
- `HIGH` 不自动 trusted，`CONFLICT` 不自动断言 editor 错；
- trust 只接受 `human_review` 或经过独立 blind lock 的 `calibrated_policy`；
- editor cue 必须唯一绑定 canonical line；
- candidate 只读 authoritative `source_timeline_boundary_ms`；
- open-end `start+1ms` sentinel 不可作为 repair interval。

## 5. Partial Timeline Repair P3：effective-run + fusion artifact lineage

```text
lyric_aligner/timeline/partial_repair_context.py
lyric_aligner/timeline/partial_repair_production.py
```

正式生产入口要求：

```text
effective run + exact run artifact
P9 fusion + exact fusion artifact
explicit cue trust
```

- mapping kind 从 exact coarse/Fine TimeWarp `mapping.mode` 派生，不从 BPM 猜；
- Fine 必须与 coarse 的 occurrence/track/canonical-selection identity 完全一致；
- `mapping_blocked=true` -> unavailable；
- CUT_AWARE 只认 materialized `cut_timewarp_rebuild` lineage；
- P9 fusion payload/artifact 必须绑定 exact effective-run artifact；
- tampered fusion JSON 通过 formal output hash fail closed；
- 正式 production inputs 必须使用当前 algorithm version。

## 6. Partial Timeline Repair P4：strict calibration/blind trust lock（开发中）

新增：

```text
lyric_aligner/timeline/partial_repair_trust.py
lyric_aligner/timeline/partial_repair_trust_production.py
scripts/v4_build_partial_trust_lock.py
scripts/test_v4_partial_timeline_repair_trust.py
scripts/test_v4_partial_timeline_repair_trust_production.py
```

P4 **不定义任何公开自动阈值**，而是复用 `scripts/v4_calibration_workflow.py` / `strict_workflow.py` 的既有隔离流程：

1. 校验 calibration baseline/candidate、candidate revision/runtime、selection self-hash、evaluation/policy file SHA，并重新计算 selected candidate calibration gates；
2. 校验 independent blind baseline/candidate、selection lock、blind policy SHA、blind gate self-hash，并重新计算 blind gates；
3. calibration 与 blind 的 ground-truth identity / case IDs 必须不同；source-group isolation 必须在 strict evaluation 中已强制；
4. selected runtime 的 algorithm version 必须是当前版本；
5. 只有 blind policy **显式声明并通过**的 `language:*` scope 才进入 `eligible_language_scopes`。只测 overall 时 lock 仍可审计，但 `cue_trust_generation_allowed=false`；
6. trust lock 固定 `automatic_timing_change_allowed=false`、`release_gate_eligible=false`。

私有 selected-candidate 的 cue-trust decision 还必须：

- self-hash；
- bind exact trust-policy lock、candidate ID/revision/runtime、exact P9 fusion artifact；
- decision scope 必须与 P9 line `language_profile` 一致；
- 未覆盖语言自动降为 `unknown`；
- P9 `CONFLICT` 即使 decision 写 `trusted` 也强制降为 `unknown`；
- P9 `HIGH` 本身不会产生任何 trust；
- human review 可以覆盖 calibrated decision。

正式生产不只读 decision JSON，还必须提供 `partial_timeline_trust_decisions` formal artifact：验证 decision output SHA/size、自签名、trust lock/candidate/fusion binding，并要求 exact fusion artifact 为 upstream。低层 self-hash API 仅用于测试/受控组合。

## 7. 当前验证边界

Synthetic/CI 可以证明：

```text
schema / lineage / artifact hash
mapping-mode derivation
Fine/coarse identity binding
fusion payload/artifact integrity
strict calibration/blind lock mechanics
calibration/blind identity separation
language-scope coverage enforcement
decision/fusion/lock identity binding
CONFLICT cannot auto-trust
human override precedence
trusted timing locks
rate-change/cut separation
candidate monotonicity / non-overlap
privacy / docs / multi-Python compatibility
```

Synthetic/CI 不能证明：

```text
任何真实 trust candidate 已经通过私有 blind gate
真实歌曲边界准确率
各 evidence family 的统计独立性
语言/风险桶自动阈值
自动 timing repair 的生产安全性
```

因此当前 Partial Timeline Repair 仍固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
```

## 8. 下一阶段

P4 通过 exact-head CI 后，应在本地 private real-song 数据上实际构建 trust lock。只有真实 blind policy 对具体 `language:*` scope 通过后，该 scope 才可生成 calibrated cue trust proposal。自动 timing write-back 仍应作为更后面的独立 promotion，并以真实 blind false-positive/false-auto-repair 指标为门槛。