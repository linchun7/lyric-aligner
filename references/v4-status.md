# Lyric Aligner v4 当前实施状态

更新日期：2026-08-17  
当前开发分支：`agent/v4-final-renderer`  
基线：`main` 已合入 v4.0.0a3（squash commit `cfa43f4c...`）  
当前开发版本：`4.0.0a4`  
TrackAsset schema：`1.1`

## 1. production-first 不变

新真实任务继续优先 v4：

```text
Task Manifest
 → Source-to-Mix / Timeline / Transition
 → ready_for_render | review_required
```

不确定性进入 review，不静默回退 v3.9。v3.9 只保留历史比较/仓库级 rollback 价值，不再是正常任务必需路径。

## 2. a3 已进入 main 的能力

`main` 当前已拥有：

- TrackAsset / TrackOccurrence / ResolvedAssetBinding single truth；
- LRC / Enhanced LRC / QRC canonical selection；
- HPSS harmonic + Chroma CENS + MFCC coarse mapping；
- monotonic source path；
- AFFINE-first TimeWarp；
- evidence-driven PIECEWISE_RATE；
- selective Fine Alignment；
- shared-boundary transition evidence；
- canonical timeline projector；
- `v4_run.py` production orchestrator；
- artifact/release integrity；
- evaluator upgrades；
- CI Documentation Contract。

PR #2 已在 base=`main` 的 CI #267 全绿后 squash merge。

## 3. a4 当前新增：package-native final rendering

### Final Timeline Composer

新增：

```text
lyric_aligner/timeline/composer.py
```

它只消费 canonical projected timelines，不从 Jianying/ASR 重造歌词。

当前规则：

- 裁剪到 occurrence window；
- last/open line 使用有限 duration；
- 超长 line-LRC gap 有 maximum line duration；
- Enhanced LRC/QRC word timing 加小 tail；
- 过短 cue BLOCK；
- same-occurrence 异常 overlap BLOCK；
- 未确认 cross-track overlap BLOCK。

### Final Renderer

新增：

```text
scripts/v4_render.py
```

它只接受：

```text
v4_run.status == ready_for_render
issues == []
legacy_fallback_used == false
```

并重新验证：

- task fingerprint；
- current v4 algorithm version；
- calibration profile identity；
- run artifact；
- TrackAsset artifact；
- 每个 canonical timeline artifact 的 materialized hash；
- timeline artifact 必须属于 run upstream lineage。

输出：

```text
FINAL.srt
FINAL.csv
FINAL.qa.json
FINAL.render.artifact.json
```

现有 `v4_validate_release.py` 可继续消费 `final_render` artifact，生成最终 `release.artifact.json`。

因此 a4 的正常无-review任务已经不需要 v3.9 `build/finalize/qa` 来生成最终文件。

## 4. a4 Render Calibration

`V4CalibrationProfile` 新增：

```json
"render": {
  "minimum_cue_duration_ms": 250,
  "maximum_line_duration_ms": 12000,
  "open_line_duration_ms": 5000,
  "word_timing_tail_ms": 120
}
```

profile version：

```text
production-bootstrap-2026-08-17-a4
```

这些值只是 bootstrap，后续由真实 calibration 调整。

### 迁移边界

由于 profile complete content 已改变：

- a3 profile_id != a4 profile_id；
- a3 TrackAsset/profile artifacts 不允许直接给 a4 renderer；
- 应从 Asset Resolution / `v4_run` 重跑 a4 chain；
- 禁止手工给旧 JSON 补 `render` 字段绕过身份验证。

## 5. 当前完整无-review路径

```text
Task Manifest
 → v4_run
 → ready_for_render
 → v4_render
 → FINAL.srt + FINAL.csv + FINAL.qa.json
 → v4_validate_release
 → release.artifact.json
```

这里第一次形成 package-native 的 v4 run→final files→release 路径。

`review_required` 仍不得进入 renderer。

## 6. 合成验收

新增：

- `scripts/test_v4_timeline_composer.py`
- `scripts/test_v4_render_end_to_end.py`

后者使用完全合成的 WAV / 虚构 LRC / schema-2.0 Task Manifest，通过真实 subprocess 执行：

```text
v4_run
 → v4_render
 → v4_validate_release
```

并验证：

- final canonical text；
- cue timing 正数且受 mix/window 限制；
- audit/QA；
- `publish_ready=true` 只在 review-free renderer 输出；
- release manifest stage/version/fingerprint/lineage。

当前分支尚需通过完整 CI 后才能认为 a4 该里程碑可合入 main。

## 7. 仍未解决的 review 路径

当前最大的生产缺口已从“没有 renderer”转为“review decision 不能自动重放”。

仍需：

1. task-scoped + fingerprinted Review Decision artifact；
2. transition `resolved_clear` 后安全解除 review；
3. `confirmed_overlap` 后生成双路 transition-aware canonical timeline；
4. middle-cut confirmed/rejected decision 对 TimeWarp/timeline 的可重放应用；
5. Editor Evidence + LanguageSpan 最终 cue fusion；
6. real private calibration / blind-test。

## 8. 当前不能宣称

- a4 已 stable；
- bootstrap render duration 已最优；
- review_required 可以跳过；
- overlap candidate 可以直接输出两路字幕；
- 真实任务准确率已提升固定百分比。

当前正确表述：

> **v4.0.0a4 正在完成 package-native final rendering，使 review-free 任务可以从 Task Manifest 全程走 v4 到 release manifest；复杂 cut/overlap 的可重放人工决策仍是下一优先级。**
