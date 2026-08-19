# Lyric Aligner v4 当前实施状态

更新日期：2026-08-19  
主线算法版本：`4.0.0a8`  
当前 authority：canonical lyrics = final text/order truth；Source-to-Mix = primary timing truth。

> 2026-08-19 P3 前的完整历史状态快照保存在 `references/archive/2026-08-19-pre-p3-v4-status.md`。本文件从 P3 起收敛为“当前生产状态”，避免持续重复累积历史段落。

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
Partial Timeline Repair P1-P3 shadow path
```

当前固定：

```text
policy_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

任何 `HIGH` shadow state 都不是自动改时间轴的授权。

## 2. Text Repair V2

适用于“规范歌词可信、剪映 cue 数量/编号/全部时间冻结，只修文字”的高频任务：

```text
lyric_aligner/text_repair.py
scripts/v4_text_repair.py
scripts/v4_text_repair_batch.py
```

硬边界：

- 不读取 audio；
- cue count / number / start / end 全部 immutable；
- 支持确定性的错字、漏字、多字修复；
- 支持 bounded segmentation span，包括常见 `1↔1 / 1↔2 / 2↔1 / 2↔2`，以及仅在近乎完全一致时放行的 3–4 span；
- canonical gap、subtitle gap、重复歌词歧义、会清空已有 cue 的重分配继续 `review_required`；
- LRC timestamp 间距/BPM 不用于覆盖 SRT timing。

## 3. Partial Timeline Repair P1：结构安全 planner

```text
lyric_aligner/timeline/partial_repair.py
scripts/test_v4_partial_timeline_repair.py
```

- cue trust 显式为 `trusted / untrusted / unknown`；
- trusted cue timing 硬锁；
- 只有 explicit untrusted cue 可接收 Source-to-Mix candidate；
- mapping kind 只接受 `AFFINE / PIECEWISE_RATE / CUT_AWARE`；
- `rate change != cut`；
- candidate 不能穿越 trusted neighbor，也不能与其他 repair candidate 重叠；
- 结果固定 proposal-only，不自动写 authoritative SRT。

## 4. Partial Timeline Repair P2：P9 evidence bridge

```text
lyric_aligner/timeline/partial_repair_evidence.py
scripts/test_v4_partial_timeline_repair_evidence.py
```

- P9 必须保持 `shadow_only`、uncalibrated、不可 release、不可自动 timing mutation；
- `LOW / MEDIUM / HIGH / CONFLICT` 只作为 diagnostics；
- `HIGH` 不自动变 trusted，`CONFLICT` 不自动断言 editor cue 一定错误；
- trust 只来自 `human_review` 或未来独立 blind-lock 的 `calibrated_policy`；
- untrusted cue 只有在 editor family 唯一绑定一个 canonical line 时才生成候选；
- candidate 只读 `source_timeline_boundary_ms`，不用 editor/ASR/forced auxiliary boundary；
- open-end `start+1ms` sentinel 永不成为 repair interval。

P2 内部仍保留低层 mapping/cut 参数接口用于兼容与测试，但生产入口从 P3 起不再要求调用方提供这些标签。

## 5. Partial Timeline Repair P3：effective-run + fusion artifact lineage

```text
lyric_aligner/timeline/partial_repair_context.py
lyric_aligner/timeline/partial_repair_production.py
scripts/test_v4_partial_timeline_repair_context.py
scripts/test_v4_partial_timeline_repair_context_identity.py
scripts/test_v4_partial_timeline_repair_production.py
```

P3 正式生产入口只接受 formal artifact 四件套：

```text
effective run + exact run artifact
P9 fusion + exact fusion artifact
explicit cue trust
```

不再要求调用方提供：

```text
mapping_kind_by_occurrence
confirmed_cut_occurrence_ids
```

派生与 lineage 规则：

- 支持 `production_orchestration / review_resolution / overlap_recomposition / cut_rebuild / combined_recomposition`；
- continuous mapping 直接读取 effective coarse 或已应用 Fine 的正式 `TimeWarp.mapping.mode`，只接受 `AFFINE / PIECEWISE_RATE`；
- Fine 必须与 effective coarse 的 occurrence、track、canonical selection identity 完全一致，并满足 `fine_applied=true`、Fine `applied=true`、Fine→coarse artifact lineage；
- `mapping_blocked=true` 只得到 unavailable，不会因为 review 中出现 `confirmed_cut` 就自动升级为 CUT_AWARE；
- CUT_AWARE 只认已经 materialize 的 `cut_timewarp_rebuild` payload/artifact，且 cut count、confirmed candidate IDs、source review artifact 与 effective-run upstream 必须一致；
- cut+overlap combined run 继续沿正式 cut mapping lineage 得到 CUT_AWARE；overlap-only 保留原始 coarse/fine continuous mapping kind；
- production bridge 验证 fusion artifact 的 stage/role/self-signature/output SHA/size，且 exact effective-run artifact 必须同时出现在 fusion payload、fusion artifact config 与 fusion upstream；
- 手工改动 fusion JSON 导致 artifact output hash 不一致时 fail closed，不能改变 `source_timeline_boundary_ms` 后继续进入 repair；
- report 不记录 coarse/fine/cut 的本地文件路径。

低层 in-memory bridge 继续用于单元测试/组合调用；生产必须走 `partial_repair_production.py` 的 artifact-verified bridge。

## 6. 当前验证边界

Synthetic/CI 可以证明：

```text
schema / lineage / artifact hash
mapping-mode derivation
Fine/coarse identity binding
fusion payload/artifact integrity
trusted timing locks
rate-change/cut separation
candidate monotonicity / non-overlap
privacy / docs / multi-Python compatibility
```

Synthetic/CI 不能证明：

```text
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

## 7. 下一阶段

P3 通过 exact-head CI 后，下一阶段应把 private calibration + independent blind-test 产生的锁定 policy identity 接到 cue trust 生成层。不得用 synthetic fixtures 直接发明自动 trust/timing threshold；在真实 blind 数据证明前，生产自动写回仍保持关闭。
