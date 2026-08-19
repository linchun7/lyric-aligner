---
name: lyric-aligner
description: Reconstruct, review, materialize, diagnose and render multilingual canonical lyric subtitles for edited music mixes using fingerprinted TrackAssets, Source-to-Mix TimeWarp, cut/overlap-safe canonical timelines, editor/ASR/forced-alignment evidence, fail-closed shadow fusion, and immutable release lineage.
---

# Lyric Aligner

当前算法版本为 **v4.0.0a8 production-first**。当前开发链已经实现到 P9，并补齐真实数据生产前的只读 doctor、runtime snapshot、逐 family calibration evaluator，以及高频 **Text Repair V2**：生产重建、cut/overlap materialization、editor evidence、local ASR first/second pass、external source forced alignment、forced source→mix projection、editor/ASR/forced 三 family shadow fusion。**main/PR 的最新合并状态以 `references/v4-status.md` 为准。**

这个项目的生产原则不是“让 ASR 重写歌词”，而是：canonical lyric 决定最终文字与顺序，Source-to-Mix 决定主要时间；其他信号只提供可审计 evidence。任何无法由现有证据安全证明的情况继续 review/BLOCK，**不得静默回退 v3.9，不得手工拼/改 artifact 绕过 lineage。**

## Codex 开始任何真实任务前必须先读

```text
SKILL.md
references/v4-runtime-guide.md
references/v4-status.md
references/v4-implementation.md
references/dataset-protocol.md
```

如果正在改生产代码，再读：

```text
references/documentation-contract.md
references/v4-change-record.md
```

## Codex 真实任务开场纪律

真实生产任务与代码开发必须分开。开始真实歌曲任务时：

```text
1. checkout/fetch 到最新 main；不要从旧 agent/* / codex/* 分支恢复实现；
2. 先运行 scripts/v4_runtime_snapshot.py 固化当前 runtime identity；
3. 已有任务产物时，把 payload 与对应 *.artifact.json 成对交给 scripts/v4_doctor.py，并要求 --require lineage；
4. 先根据 doctor 的 BLOCK / recommended_next_action 续跑，不要无条件从 reconstruction 重跑；
5. 真实生产任务本身不得顺手修改 tracked production code。
```

如果真实数据暴露代码 bug：停止该生产 run，保存最小复现和 doctor/lineage 证据；从**最新 main** 新建独立 bugfix 分支修复、补测试、走 CI/PR。修复合入后再以新的 runtime snapshot/algorithm lineage 重跑受影响阶段。不要在生产任务分支边跑数据边改算法，也不要为了过任务手改 formal artifact。

## 不可违反的原则

1. **Canonical lyric 是最终文字与顺序真源。** ASR、编辑器、forced aligner 都不能替换 final text/order。
2. **Source-to-Mix audio mapping 是主要时间真源。** editor/ASR/forced timing 默认都不是 final authority。
3. `rate change != cut`；forward source-position discontinuity 才能进入 candidate-level cut review。
4. `confirmed_cut` 仍必须经过 local cut locator → CUT_AWARE mapping → cut-aware canonical timeline。
5. line-LRC 只有整个可推断行区间都位于 source gap 才可整行删除；partial-line 一律 review。
6. confirmed overlap 保持左右两条独立 canonical cue stream，跨轨实际交集必须完整位于 exact confirmed region。
7. cut/overlap 两边先从同一个 `review_resolution` 独立物化，再由 composition stage 合并；不得互相改写 materializer。
8. cut + overlap 只有两层都安全时自动组合：overlap mix interval 不穿 localized cut boundary；overlap delta canonical source interval 不与 confirmed source gap 相交。
9. overlap delta 缺 canonical source provenance、open source interval 不能证明未穿 gap时继续 BLOCK。
10. `TrackAsset / TrackOccurrence / ResolvedAssetBinding` 确定后，下游不得重新猜 source/LRC/canonical selection。
11. Review Decision 必须 task-scoped + exact base-run-scoped；所有 materialization/evidence 必须绑定 exact source run/artifact lineage。
12. P7 forced alignment 只在 **source time** 产生 auxiliary evidence；进入 fusion 前必须经 P8 exact Source-to-Mix projection。
13. P7 protocol `1.0` single-job 与 optional protocol `1.1` batch 只改变 external aligner 进程组织；两者必须产生同一类 source-time auxiliary evidence，batch 不能提升 authority。
14. P8 `CUT_AWARE` line 跨 confirmed gap/cut 必须 `unprojectable`，不得 bridge；spans 可独立保留合法局部证据。
15. P9 fusion 只接受 mix-time editor/ASR/forced evidence；任意可用 auxiliary pair 超阈值就是 `CONFLICT`，不得用 2-of-3 多数票隐藏 outlier。
16. P9 的 `LOW/MEDIUM/HIGH/CONFLICT` 都是 **uncalibrated shadow state**；`HIGH` 也不得自动改 authoritative timing 或视为 release confidence。
17. Final renderer 只接受 `ready_for_render + issues=[] + legacy_fallback_used=false`，并验证 exact task/profile/artifact lineage。
18. 所有 stage 都绑定 task fingerprint、algorithm version、upstream IDs、materialized SHA-256；涉及模型的 evidence 还必须绑定 backend/model revision。
19. 所有实质性更新必须同步 owning docs；CI 不通过不得合并。
20. Runtime snapshot / doctor / family evaluator 都是可复现与诊断层，不改变 Source-to-Mix authority；没有独立 blind-test 结果不得把 auxiliary family 提升为自动 timing/release authority。
21. **Text Repair V2 只在时间轴明确冻结时使用。** 它可处理错字、漏字、多字以及 bounded 1↔N / N↔1 / N↔N 断句差异；普通 span 保守处理，3–4 段只在近乎完全一致的高置信文本证据下使用。任何情况下都不得改变 cue 数、编号或 timing；部分时间轴修复必须走后续声学/Source-to-Mix 路径。

## 权威文档

- 运行：`references/v4-runtime-guide.md`
- 状态：`references/v4-status.md`
- 架构：`references/v4-implementation.md`
- 变更：`references/v4-change-record.md`
- forced batch protocol：`references/forced-alignment-batch-protocol.md`
- 文档契约：`references/documentation-contract.md`
- 数据/盲测：`references/dataset-protocol.md`

## 标准生产流程

### 0. Runtime / Resume preflight

```powershell
python scripts/v4_runtime_snapshot.py ...
python scripts/v4_doctor.py ... --require lineage
```

新任务没有任何 formal artifact 时，doctor 的 lineage requirement 可等第一个 run artifact 生成后再启用；中断恢复或已有产物时必须优先验证现有 payload/artifact lineage。具体配对参数见 `references/v4-runtime-guide.md`。

### 0A. Text Repair V2（时间轴冻结时优先）

如果规范歌词可信，而且任务明确要求保留剪映现有 cue 数量、编号及全部起止时间，只修文字，直接运行：

```powershell
python scripts/v4_text_repair.py `
  --source-srt "private/<任务>/input/source.srt" `
  --canonical-lrc "private/<任务>/input/lyrics/01.lrc" `
  --canonical-lrc "private/<任务>/input/lyrics/02.lrc" `
  --out "output/<任务>/TEXT_REPAIRED.srt" `
  --report "output/<任务>/TEXT_REPAIR.json"
```

批量高频生产使用：

```powershell
python scripts/v4_text_repair_batch.py `
  --manifest "private/<任务>/text-repair.batch.json" `
  --summary "output/<任务>/text-repair.batch.summary.json"
```

V2 先用唯一 exact 文本锚点把长字幕切成局部区间，再在区间内运行 bounded monotonic span DP。常见 1↔1 / 1↔2 / 2↔1 / 2↔2 可在高置信下自动处理；3–4 个 cue/lyric 的更极端断句差异只在拼接后几乎完全一致时放行。因此剪映多断一句、少断一句或断句点不同不再天然需要 review，同时不会因为扩大 span 把真正漏掉的歌词轻易吞掉。高置信 span 内允许字符 replace/insert/delete，以修复普通错字、漏字和多字；源 SRT 的标点、空白、换行和常见音乐装饰符继续保留。真实 canonical gap、额外 subtitle cue、近似重复歌词歧义、gap 邻域弱匹配、会把现有 cue 清空的重分配或结构差异过大继续 `review_required`。

这个入口完全不读取音频，也不依据 LRC timestamp 修改 SRT timing，因此 **BPM 加速/减速不会改变 Text Repair V2 的文字修复规则**。下一轮“部分时间轴可信”的局部修复必须把 BPM/rate change 当常态，复用 Source-to-Mix 的 `AFFINE/PIECEWISE_RATE/CUT_AWARE` 映射；不能把原曲 LRC/source absolute time 直接覆盖到 edited mix。

### 1. Reconstruction

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --out-dir "output/<任务>/v4" `
  --git-commit "<commit>"
```

输出 `ready_for_render` 或 candidate-level `review_required`。

生产 coarse/fine CLI 会在内部只解码当前 occurrence / coarse retrieval windows 所需的 mix 区间并保留少量保护 padding；仍使用完整 mix SHA、absolute mix coordinates 与原有 Source-to-Mix 决策规则。该 bounded decode 不需要额外 CLI 参数，也不是低精度模式：cut、ambiguity、review/fail-closed 与 threshold 语义不变。

`v4_run.py` 下的 coarse 调用还会把同一原曲重复需要的 harmonic source features 缓存在当前 V4 输出树的 `cache/features`。cache key 绑定 source audio SHA-256、采样参数、feature implementation 与 librosa 版本；损坏、缺失或 identity 不匹配一律按 cache miss 从原曲重算。这个 cache 只是可删除的本地性能层，不进入 formal artifact lineage，也不能绕过 source SHA、task fingerprint、threshold、cut/review 或 release 验证。

### 2. Review

```powershell
python scripts/v4_review.py template ...
python scripts/v4_review.py apply ...
```

Review Decision schema=`1.2`。主要 actions：

```text
transition candidate: resolved_clear | confirmed_overlap
timewarp discontinuity: confirmed_cut | rejected_requires_remap
generic blocked timewarp: confirmed_requires_rebuild
```

### 3. Materialization

Confirmed overlap：

```powershell
python scripts/v4_recompose_overlap.py ...
```

Confirmed cut：

```powershell
python scripts/v4_rebuild_cut.py ...
```

同一任务同时有 confirmed cut + confirmed overlap 时，两条 materializer 必须从同一个 reviewed run 启动，再执行：

```powershell
python scripts/v4_compose_materializations.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --cut-run "output/<任务>/v4/cut_rebuilt_run.json" `
  --cut-artifact "output/<任务>/v4/cut_rebuilt_run.artifact.json" `
  --overlap-run "output/<任务>/v4/recomposed_run.json" `
  --overlap-artifact "output/<任务>/v4/recomposed_run.artifact.json" `
  --track-assets "output/<任务>/v4/assets/track_assets.json" `
  --asset-artifact "output/<任务>/v4/assets/track_assets.artifact.json" `
  --out-dir "output/<任务>/v4/combined" `
  --out "output/<任务>/v4/combined_run.json" `
  --artifact-out "output/<任务>/v4/combined_run.artifact.json" `
  --git-commit "<commit>"
```

有效 authoritative run 可能是：

```text
production_orchestration
review_resolution
overlap_recomposition
cut_rebuild
combined_recomposition
```

后续 evidence 必须绑定你最终选择的 **同一个 effective run artifact**。

### 4. Editor / ASR evidence

```powershell
python scripts/v4_editor_evidence.py ...
python scripts/v4_alignment_backends.py
python scripts/v4_plan_alignment.py ...
python scripts/v4_execute_asr_evidence.py ...
python scripts/v4_plan_asr_second_pass.py ...
python scripts/v4_execute_asr_second_pass.py ...
```

ASR 只在 planner/routing 指定的 bounded region 使用；不要为了“看起来更智能”全曲无条件跑昂贵模型。

### 5. External forced alignment（需要时）

先检查 external command readiness：

```powershell
python scripts/v4_alignment_backends.py `
  --external-forced-aligner-command '"<executable>" <adapter-args>'
```

默认协议 1.0 保持 one-process-per-job：

```powershell
python scripts/v4_execute_forced_alignment.py ...
```

如果真实 backend 每次进程启动都会重新加载大模型，并且 adapter 实现 `references/forced-alignment-batch-protocol.md` 的 protocol 1.1，可以显式启用：

```powershell
python scripts/v4_execute_forced_alignment.py ... `
  --execution-mode batch `
  --timeout-seconds <整个 batch 可接受的上限秒数>
```

`batch` 会把所有 selected jobs 放入一个临时 request，并只启动一次 external process。Response job IDs 必须与 request 精确一致，每个 job 仍走 P7 原有 source-window/boundary/span fail-closed validator。显式 `--job-id` 为空列表的程序化调用仍表示 zero-work，不会解析/启动 command。`--timeout-seconds` 在 batch 模式覆盖整个 subprocess，因此大任务需要显式设置足够值；不会自动取消 timeout。

P7 formal output 无论 single/batch 都是 source-time：

```text
source_forced_alignment_evidence / forced_alignment_evidence
```

真实 backend 必须记录：backend/package version、model/checkpoint revision、language/G2P resources、runtime/device identity。不要把“executable 找得到”写成“模型准确”。Batch protocol 本身不等于某个 WhisperX/SOFA/MFA adapter 已获生产批准；adapter 仍需对当前 upstream runtime 单独 review，并用 private calibration/blind 验证。

### 6. Forced evidence Source→Mix projection

```powershell
python scripts/v4_project_forced_alignment.py ...
```

只有输出：

```text
forced_alignment_mix_projection / forced_alignment_mix_evidence
```

才允许进入 P9 fusion。

### 7. Multi-family shadow fusion

```powershell
python scripts/v4_fuse_evidence.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/<effective-run>.json" `
  --run-artifact "output/<任务>/v4/<effective-run>.artifact.json" `
  --editor-evidence "<editor.json>" `
  --editor-evidence-artifact "<editor.artifact.json>" `
  --asr-evidence "<asr.json>" `
  --asr-evidence-artifact "<asr.artifact.json>" `
  --forced-mix-evidence "<forced_mix.json>" `
  --forced-mix-evidence-artifact "<forced_mix.artifact.json>" `
  --out "output/<任务>/v4/evidence/fusion.json" `
  --artifact-out "output/<任务>/v4/evidence/fusion.artifact.json"
```

优先检查：

```text
shadow_level == CONFLICT
max_auxiliary_boundary_disagreement_ms
forced_alignment_line_counts.unprojectable
missing family / unavailable reasons
```

**不要把 `HIGH` 直接写回 timeline。** 第一次真实生产阶段，fusion 是定位风险和收集 calibration 数据的工具。

### 8. Final Render / Release

```powershell
python scripts/v4_render.py ...
python scripts/v4_validate_release.py `
  ... `
  --algorithm-version "4.0.0a8" `
  --upstream-artifact "output/<任务>/v4/final/FINAL.render.artifact.json"
```

Render/release 仍以 authoritative canonical timeline 为准，不读取未校准 shadow fusion 来偷偷改 timing。

### 9. Real-data family evaluation

真实人工 truth 到位后：

```powershell
python scripts/v4_evaluate_evidence_families.py ...
```

它比较 Source-to-Mix/editor/ASR/forced 的真实边界误差与 coverage，但仍然只是 calibration evidence。Dataset/runtime/fusion-policy identity 与 blind-test 纪律见 `references/v4-runtime-guide.md` 和 `references/dataset-protocol.md`。

## 第一次真实数据生产纪律

先把真实数据当成 calibration/verification，同时仍可用 authoritative Source-to-Mix 结果正常产出。每种主要语言建议先准备 3–5 个 30–90 秒片段，并覆盖：

```text
normal global-rate
dynamic local stretch
cut附近
overlap
弱人声/强伴奏
editor识别差语言
```

对每条人工 ground truth 记录：

```text
Source-to-Mix boundary error
Editor boundary error
ASR boundary error
Forced boundary error
family coverage
CONFLICT / unprojectable
language/risk bucket
```

先跑 calibration，再冻结 threshold/model/profile/runtime identity，然后用独立 blind set 验证。没有 blind 结果，不得宣称某个真实 backend 或某套 fusion threshold 已经达到生产准确率目标。

## 当前仍 BLOCK 的边界

- overlap interval 与 localized cut boundary 相交；
- overlap delta canonical source interval 与 confirmed source gap 相交；
- overlap delta 缺 source provenance；
- line-LRC partial-line cut；
- timed token 本身被 cut 穿过；
- 任一 cut/overlap mapping 或 lineage 不确定；
- relevant forced mapping/provenance 不完整；
- forced line 跨 confirmed cut/gap；
- auxiliary families 明显冲突；
- runtime/payload/artifact identity 不一致；
- real private calibration / blind-test 尚未完成时尝试提升 auxiliary timing authority。

## 回归纪律

```powershell
python -m compileall -q lyric_aligner scripts
python scripts/validate_docs_contract.py
python -m unittest discover -s scripts -p "test_*.py"
python scripts/validate_skill.py .
python scripts/privacy_scan.py
python scripts/check_environment.py
git diff --check
```

如果 CI 与本地结果冲突，以 **latest-head、相同 Python/dependency、完整日志** 为准调查；不得为了合并而删除失败测试。

## 后续优先级

1. 用用户真实私有数据做 multi-language calibration / blind-test；
2. 根据真实误差选择/锁定 forced-aligner backend、checkpoint、G2P 与运行环境；
3. 设计 Partial Timeline Repair：锁死可信 cue，只修不可信局部；BPM 加速/减速作为默认场景，复用 Source-to-Mix `AFFINE/PIECEWISE_RATE/CUT_AWARE`，不得把 rate change 当 cut；
4. 只有 blind 数据证明收益后，才设计 calibrated boundary refinement / release-gate integration；
5. 如真实任务证明有必要，再研究 local vocal refinement 与 cut boundary + overlap 同一区域的 joint acoustic composition。
