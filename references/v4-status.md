# Lyric Aligner v4 当前实施状态

更新日期：2026-08-17  
当前开发分支：`agent/v4-final-renderer`  
Draft PR：#3  
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

只消费 canonical projected timelines，不从 Jianying/ASR 重造歌词。

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

只接受：

```text
v4_run.status == ready_for_render
issues == []
legacy_fallback_used == false
```

Renderer 重新验证：

- task fingerprint；
- current v4 algorithm version；
- calibration profile identity；
- production-run artifact；
- supplied TrackAsset artifact 必须就是 production run upstream；
- 每个 canonical timeline artifact 必须属于 production-run upstream；
- timeline artifact 必须由 supplied TrackAsset artifact 派生；
- timeline occurrence / track / ordinal / canonical-selection 必须与 `ResolvedAssetBinding` 一致；
- production run 必须精确覆盖全部 resolved TrackOccurrences；
- materialized output hash 不得漂移。

输出：

```text
FINAL.srt
FINAL.csv
FINAL.qa.json
FINAL.render.artifact.json
```

因此 a4 的正常无-review任务不再需要 v3.9 `build/finalize/qa` 生成最终文件。

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

这些值是 bootstrap，后续必须用真实 calibration 调整。

### a3 → a4 迁移

由于 profile complete content 改变：

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
 → FINAL.srt + FINAL.csv + FINAL.qa.json + FINAL.render.artifact.json
 → v4_validate_release
 → release.artifact.json
```

`review_required` 仍不得进入 renderer。

### Release lineage 已进一步收紧

v4 release 现在必须：

- 至少有一个 upstream artifact；
- **恰好一个 `final_render` upstream**；
- requested release algorithm version == upstream algorithm version；
- QA calibration profile id/version == upstream profile identity；
- `final_render` artifact 中记录的 `final_srt / audit_csv / qa_json` size/SHA 必须与当前实体文件逐一一致。

因此即使 SRT/CSV/QA 三份文件被一起协调修改，只要没有重新生成对应 `final_render` artifact，release 也必须失败。

## 6. 合成与负向验收

新增/扩展：

- `scripts/test_v4_timeline_composer.py`
- `scripts/test_v4_render_end_to_end.py`
- `scripts/test_v4_release_lineage.py`
- `scripts/test_v4_release_integrity.py`

覆盖：

- open last line；
- long instrumental gap；
- word-timing tail；
- occurrence-window clipping；
- too-short cue BLOCK；
- unconfirmed cross-track overlap BLOCK；
- synthetic `v4_run → v4_render → v4_validate_release`；
- modified final SRT 与旧 render artifact 不一致时 BLOCK；
- 多个/错误版本 final_render artifact BLOCK；
- QA calibration profile mismatch BLOCK。

## 7. 当前 CI 状态：外部 GitHub Actions 计费阻断

PR #3 的 Actions run #283 与 #285 **均未启动任何 runner**：

```text
runner_id = 0
steps = []
```

GitHub check annotation 明确返回：

> The job was not started because recent account payments have failed or your spending limit needs to be increased.

因此当前红灯是 GitHub Actions billing / spending-limit 外部阻断，不是 unittest、compile 或 renderer 代码失败。

处理原则：

- 不降低 CI 门禁；
- 不删除测试；
- 不把旧 a3 CI 绿灯冒充 a4 验收；
- PR #3 保持 Draft；
- GitHub Actions 计费/限额恢复后，以**最新 PR #3 head**重新运行 Python 3.10/3.12/3.14 + ASR 全矩阵。

只有最新 a4 head 全绿后才允许合入 main。

## 8. 仍未解决的 review 路径

当前最大的生产缺口已从“没有 renderer”转为“review decision 不能自动重放”。

仍需：

1. task-scoped + fingerprinted Review Decision artifact；
2. transition `resolved_clear` 后安全解除 review；
3. `confirmed_overlap` 后生成双路 transition-aware canonical timeline；
4. middle-cut confirmed/rejected decision 对 TimeWarp/timeline 的可重放应用；
5. Editor Evidence + LanguageSpan 最终 cue fusion；
6. real private calibration / blind-test。

## 9. 当前不能宣称

- a4 已通过 CI；
- a4 已 stable；
- bootstrap render duration 已最优；
- review_required 可以跳过；
- overlap candidate 可以直接输出两路字幕；
- 真实任务准确率已提升固定百分比。

当前正确表述：

> **v4.0.0a4 已实现 package-native final rendering 与更严格的 release lineage，并已具备 synthetic run→render→release 测试；当前唯一无法完成的正式矩阵验收受 GitHub Actions 账户计费/支出上限阻断。**
