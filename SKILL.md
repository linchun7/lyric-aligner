---
name: lyric-aligner
description: Reconstruct, review, materialize, diagnose and render multilingual canonical lyric subtitles for edited music mixes using fingerprinted TrackAssets, Source-to-Mix TimeWarp, cut/overlap-safe canonical timelines, editor/ASR/forced-alignment evidence, fail-closed shadow fusion, and immutable release lineage.
---

# Lyric Aligner

当前算法版本为 **v4.0.0a8 production-first**。当前开发链已经实现到 P9：生产重建、cut/overlap materialization、editor evidence、local ASR first/second pass、external source forced alignment、forced source→mix projection、editor/ASR/forced 三 family shadow fusion。**main/PR 的最新合并状态以 `references/v4-status.md` 为准。**

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
13. P8 `CUT_AWARE` line 跨 confirmed gap/cut 必须 `unprojectable`，不得 bridge；spans 可独立保留合法局部证据。
14. P9 fusion 只接受 mix-time editor/ASR/forced evidence；任意可用 auxiliary pair 超阈值就是 `CONFLICT`，不得用 2-of-3 多数票隐藏 outlier。
15. P9 的 `LOW/MEDIUM/HIGH/CONFLICT` 都是 **uncalibrated shadow state**；`HIGH` 也不得自动改 authoritative timing 或视为 release confidence。
16. Final renderer 只接受 `ready_for_render + issues=[] + legacy_fallback_used=false`，并验证 exact task/profile/artifact lineage。
17. 所有 stage 都绑定 task fingerprint、algorithm version、upstream IDs、materialized SHA-256；涉及模型的 evidence 还必须绑定 backend/model revision。
18. 所有实质性更新必须同步 owning docs；CI 不通过不得合并。

## 权威文档

- 运行：`references/v4-runtime-guide.md`
- 状态：`references/v4-status.md`
- 架构：`references/v4-implementation.md`
- 变更：`references/v4-change-record.md`
- 文档契约：`references/documentation-contract.md`
- 数据/盲测：`references/dataset-protocol.md`

## 标准生产流程

### 1. Reconstruction

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --out-dir "output/<任务>/v4" `
  --git-commit "<commit>"
```

输出 `ready_for_render` 或 candidate-level `review_required`。

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

然后执行：

```powershell
python scripts/v4_execute_forced_alignment.py ...
```

P7 formal output 是 source-time：

```text
source_forced_alignment_evidence / forced_alignment_evidence
```

真实 backend 必须记录：backend/package version、model/checkpoint revision、language/G2P resources、runtime/device identity。不要把“executable 找得到”写成“模型准确”。

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

先跑 calibration，再冻结 threshold/model/profile，然后用独立 blind set 验证。没有 blind 结果，不得宣称某个真实 backend 或某套 fusion threshold 已经达到生产准确率目标。

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
3. 只有 blind 数据证明收益后，才设计 calibrated boundary refinement / release-gate integration；
4. 如真实任务证明有必要，再研究 local vocal refinement 与 cut boundary + overlap 同一区域的 joint acoustic composition。
