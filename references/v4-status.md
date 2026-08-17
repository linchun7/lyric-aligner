# Lyric Aligner v4 当前实施状态

更新日期：2026-08-17  
当前开发分支：`agent/v4-overlap-recomposition`  
基线：`main` 已合入 v4.0.0a5（squash commit `a80a531d6933946484c54d3a589bc55b0cb9e94b`）  
当前开发版本：`4.0.0a6`  
TrackAsset schema：`1.1`

## 1. production-first 不变

新真实任务继续优先 v4。不确定 mapping/cut/transition 进入 review/BLOCK，不静默回退 v3.9。Canonical lyric 仍是最终文字真源，Source-to-Mix 仍是主要时间真源。

## 2. a5 已进入 main

PR #4：`v4.0.0a5: replayable review decisions and reviewed-run rendering`。

最新 head `66ad787266d3ae7539b6362d870437e44c585f16` 的 GitHub Actions validate #321 **SUCCESS**：

- Python 3.10 / 3.12 / 3.14 full unittest/E2E；
- review decision state machine；
- review CLI template/apply；
- synthetic `review_resolution → v4_render`；
- existing run→render→release regressions；
- Documentation Contract；
- compileall / Skill / privacy / environment / diff-check；
- ASR environment。

随后 squash merge 到 main：

```text
a80a531d6933946484c54d3a589bc55b0cb9e94b
```

因此 main 已支持 transition false-positive 通过正式 `review_resolution` artifact 安全解除并进入 renderer；confirmed overlap / blocked TimeWarp 仍 fail-closed。

## 3. a6 当前目标：confirmed-overlap 双路 canonical timeline

a6 解决 a5 的最高优先级缺口：

```text
confirmed_overlap
 → 不直接 render
 → 用 boundary-local Source-to-Mix evidence 重投影左右 canonical lyrics
 → 生成两路独立 overlap timelines
 → 仅在 exact confirmed region 内允许跨 track cues 时间重叠
```

不把两首歌词拼成一行。

## 4. Candidate-level Transition review

发现 a5 的“整条 A→B 一个 transition issue”在同一共享窗口出现多个分离 candidate 时粒度过粗。a6 改为：

```text
每个 overlap / ambiguity interval
 → 独立 candidate_id
 → 独立 issue_id
 → 独立人工 action
```

`v4_run` schema 当前为 `1.1`，transition issue 类型：

```text
transition_overlap
transition_ambiguity
```

Overlap issue 记录：

- candidate_id；
- left/right occurrence；
- exact interval；
- left/right score；
- review status/reason。

Ambiguity issue 同样拥有 candidate_id + interval。

Review Decision schema 升到 `1.1`；candidate-level `issue_id` 包含 candidate_id，因此清除一个候选不会清除同一边界上的另一个候选。

## 5. Confirmed-overlap recomposition

新增：

```text
lyric_aligner/timeline/overlap.py
scripts/v4_recompose_overlap.py
```

### 输入

必须是 `review_resolution` run，且 active issue 中存在：

```text
decision_action = confirmed_overlap
requires_recomposition = true
confirmed_interval
candidate_id
issue_id
```

### Exact evidence binding

每个 region 重新验证：

- issue_id 非空；
- candidate_id 在原 Transition artifact 中唯一存在；
- interval 与原 candidate 毫秒级一致；
- occurrence pair 一致；
- Transition artifact 属于 reviewed-run lineage；
- Transition artifact upstream 到 exact TrackAsset artifact；
- LEFT/RIGHT boundary coarse 分别匹配 exact occurrence/track/canonical-selection；
- 两份 coarse artifact 都由 exact TrackAsset artifact 派生；
- Transition artifact upstream 到这两份 exact coarse artifact；
- 左右 coarse artifact 不得是同一个 artifact。

因此 swapped LEFT/RIGHT mapping 不可进入重组。

### Boundary mapping

每侧先使用共享边界 Coarse；`should_run_fine_alignment()` 认为难例时自动跑现有 Selective Fine。最终 `effective_timewarp(Fine if applied, else Coarse)` 若仍 blocked，recomposition 失败并保持 fail-closed。

### Canonical re-projection

左右 occurrence 分别：

```text
exact canonical TrackAsset
 + boundary effective TimeWarp
 + confirmed overlap interval
 → overlap canonical lines
 → strict clip to confirmed interval
```

再与 primary canonical timeline 合并，只扩展 occurrence window 到 confirmed region。

新 timeline stage：

```text
overlap_timeline_recomposition
```

新 run stage：

```text
overlap_recomposition
```

Processed confirmed-overlap issues 移除；其他 issue 保留。remaining issues=0 时才 `ready_for_render`。

## 6. Renderer / Composer a6 语义

`v4_render.py` 当前接受：

```text
production_orchestration
review_resolution
overlap_recomposition
```

Overlap run 必须：

- `source_review_artifact_id` 属于 upstream；
- artifact config 与 payload source-review identity 一致；
- remaining issue count=0；
- non-empty confirmed_overlap_regions；
- supplied TrackAsset / recomposed timelines 全部处于 lineage 中。

Composer 不再一律拒绝跨 track cue overlap，但规则极严格：

> 每一个实际跨 track cue intersection，都必须完整落在 exact occurrence pair 的某个 confirmed-overlap region 内。

检查覆盖**所有相交 cue pair**，不是只看排序后相邻 cue，因此长 cue 与后续第二/第三条 cue 的越界也会 BLOCK。

Final SRT 中两路歌词保持独立 cue，可拥有时间重叠；不拼接文本。

QA/Final Render 记录：

```text
source_run_stage=overlap_recomposition
confirmed_overlap_region_count
```

## 7. a6 测试覆盖（当前分支，待 CI）

新增/扩展：

- `test_v4_transition.py`：stable candidate_id、ambiguity candidate identity；
- `test_v4_review_decisions.py`：同边界多 candidate 独立 issue；
- `test_v4_overlap_recomposition.py`：region materialization、strict clipping、timeline merge、confirmed-region composer gate、非相邻 cue 交集；
- `test_v4_overlap_lineage.py`：swapped occurrence/track/canonical/asset binding BLOCK；
- `test_v4_overlap_end_to_end.py`：artifact-level real `v4_recompose_overlap → v4_render`，不依赖声学阈值碰巧触发 overlap；
- CLI bootstrap 加入 `v4_recompose_overlap.py`。

这些测试尚需最新 head 的完整 GitHub Actions 矩阵验证，当前不能声明 a6 可合并。

## 8. Calibration

a6 不调整数值阈值，继续使用 `production-bootstrap-2026-08-17-a4` profile 内容。变化属于 algorithm/review/timeline contract，因此 algorithm version 升为 `4.0.0a6`，a5 artifacts 不可与 a6 artifacts 混入同一 release。

## 9. 当前仍未完成

### P0 — confirmed TimeWarp / middle-cut rebuild

对人工确认的 source discontinuity/cut 生成新的 effective mapping、cut intervals 与 canonical timeline artifact。

### P1 — real-task calibration / blind-test

用真实私有任务评估 mapping residual、onset/offset、review density、cut/overlap P/R、runtime。

### P2 — Editor Evidence + LanguageSpan cue fusion

只在 canonical/source mapping 后作为辅助边界 evidence。

### P3 — Forced Alignment / ASR v2

由真实误差数据决定是否成为下一瓶颈。

## 10. 当前不能宣称

- a6 已通过 CI 或已合并；
- candidate detection 等于真实 overlap；
- confirmed overlap 可以覆盖坏 Source-to-Mix mapping；
- bootstrap profile 已最优；
- 真实任务准确率已提升固定百分比。

当前正确表述：

> **v4.0.0a6 正在把已人工确认的 overlap materialize 成两路独立 canonical timeline，并且只在确认区间内允许跨 track subtitle overlap；所有 mapping/lineage 越界继续 fail-closed。**
