# Lyric Aligner v4 当前实施状态

更新日期：2026-08-17  
当前开发分支：`agent/v4-review-decisions`  
基线：`main` 已合入 v4.0.0a4（squash commit `236d9d717229147ee1d1a8755d712e54db47a751`）  
当前开发版本：`4.0.0a5`  
TrackAsset schema：`1.1`

## 1. production-first 不变

新真实任务继续优先 v4：

```text
Task Manifest
 → Source-to-Mix / Timeline / Transition
 → ready_for_render | review_required
```

不确定性进入 review，不静默回退 v3.9。v3.9 只保留历史比较/仓库级 rollback 价值，不再是正常任务必需路径。

## 2. a4 已进入 main

PR #3 在 GitHub Billing/Actions 恢复后，对最新 head `a9ab9bc5...` 重新运行 validate #303。

最终全绿：

- Python 3.10 / 3.12 / 3.14 full unittest discovery；
- synthetic `v4_run → v4_render → v4_validate_release` E2E；
- Documentation Contract；
- `compileall lyric_aligner + scripts`；
- Skill validation；
- privacy scan；
- environment validation；
- `git diff --check`；
- ASR environment。

随后 PR #3 squash merge 到 main，commit：

```text
236d9d717229147ee1d1a8755d712e54db47a751
```

因此 main 当前已拥有完整 review-free 路径：

```text
Task Manifest
 → v4_run
 → ready_for_render
 → v4_render
 → FINAL.srt + FINAL.csv + FINAL.qa.json + FINAL.render.artifact.json
 → v4_validate_release
 → release.artifact.json
```

以及严格 final-render/release lineage。

## 3. a5 当前目标：Replayable Review Decision

之前 `review_required` 只能 BLOCK，人工结论无法成为可重放 artifact。a5 新增：

```text
lyric_aligner/review/
scripts/v4_review.py
```

目标不是“让人工绕过算法”，而是把人工判断变成：

```text
task-scoped
base-run-scoped
fingerprinted
immutable-artifact-backed
replayable
```

的正式生产证据。

## 4. Review issue identity

`v4_review template` 会对当前 `v4_run.issues[]` 规范化并生成 deterministic `issue_id`。

身份只使用逻辑字段，不使用可读 reason 文案：

### Transition

```text
task fingerprint
kind=transition
code=transition_overlap_or_ambiguity
left_occurrence_id
right_occurrence_id
```

### TimeWarp

```text
task fingerprint
kind=timewarp
code=effective_mapping_blocked
occurrence_id
```

因此：

- 同一任务、同一逻辑 issue 的 reason 文案变化不改变 issue ID；
- 不同任务的相同边界不会共享 issue ID。

但 review decision 还必须额外绑定 `base_run_artifact_id`，所以旧 production run 的 decision 不能静默套到新 run。

## 5. Review template / apply

### Template

```text
v4_review.py template
```

输出：

```text
schema_version
algorithm_version
task_fingerprint_sha256
base_run_artifact_id
review_items[]
```

每个 item 包含：

```text
issue_id
issue snapshot
allowed_actions
decision=null
```

### Apply

```text
v4_review.py apply
```

会重新验证：

- Task Manifest；
- production-run artifact stage/version/task/hash；
- decision task fingerprint；
- decision algorithm version；
- exact base run artifact ID；
- issue snapshot 仍与当前 base run 完全一致；
- allowed action 未被篡改；
- rationale 非空；
- 每个 base issue 都必须出现在 decision file。

输出：

```text
reviewed_run.json
reviewed_run.artifact.json
```

artifact stage：

```text
review_resolution
```

同时冻结：

- reviewed-run output hash；
- decision JSON hash；
- base production-run artifact；
- base run 全部 upstream artifact IDs；
- calibration profile identity。

## 6. 当前安全 action 语义

### Transition：`resolved_clear`

人工确认 candidate 是误报/没有真实 overlap：

```text
issue resolved
→ effective review block cleared
→ 若无其他 issue，可 ready_for_render
```

原 transition evidence 的 `blocked` 不被改写；reviewed run 新增 `review_resolution` 说明人工覆盖原因。

### Transition：`confirmed_overlap`

```text
issue.status = confirmed
requires_recomposition = true
status 仍为 review_required
```

确认 overlap **不等于可以发布**。必须等待 transition-aware 双路 timeline composition。

### TimeWarp：`confirmed_requires_rebuild`

```text
issue.status = confirmed
requires_timeline_rebuild = true
status 仍为 review_required
```

blocked TimeWarp 没有 `resolved_clear` action。因为该 occurrence 可能没有合法 canonical timeline，不能靠人工布尔 override 直接进入 renderer。

## 7. Renderer 已接受合法 reviewed-run

`v4_render.py` 当前接受两种 run artifact：

```text
production_orchestration
review_resolution
```

两者都必须：

```text
status == ready_for_render
issues == []
legacy_fallback_used == false
```

review_resolution 还必须证明：

- `base_run_artifact_id` 存在并属于 upstream；
- artifact normalized config 与 reviewed payload 的 base-run identity 一致；
- `remaining_issue_count == 0`；
- supplied TrackAsset / canonical timeline artifacts 仍属于 review artifact upstream lineage。

QA/Final Render artifact 会记录：

```text
source_run_stage = production_orchestration | review_resolution
```

## 8. a5 Calibration 状态

a5 没有修改 calibration 数值；继续使用 a4 profile 内容：

```text
production-bootstrap-2026-08-17-a4
```

变化是 algorithm/review contract，因此 algorithm version 升到 `4.0.0a5`，新任务仍应重跑整条 a5 artifact chain，不能混用 a4 stage artifacts。

## 9. a5 回归覆盖

新增：

- `scripts/test_v4_review_decisions.py`
- `scripts/test_v4_review_cli.py`
- existing `test_v4_render_end_to_end.py` 扩展 reviewed-run path
- CLI bootstrap 加入 `v4_review.py`

关键断言：

- issue ID 对 reason 文案稳定、对 task fingerprint 隔离；
- transition `resolved_clear` 可让 reviewed run ready；
- `confirmed_overlap` 仍 BLOCK + `requires_recomposition`；
- TimeWarp 不允许 `resolved_clear`；
- confirmed TimeWarp 仍 BLOCK + `requires_timeline_rebuild`；
- decision 不能跨 base-run artifact 重放；
- issue snapshot 被改立即 BLOCK；
- review artifact 继承 base run 的 asset/timeline upstream IDs；
- synthetic E2E 证明 `review_resolution → v4_render` 可生成同一 canonical SRT。

## 10. 当前还未完成

### P0 — confirmed overlap recomposition

把已确认 overlap 变成两路独立 canonical subtitle streams，而不是合并文本或简单解除 BLOCK。

### P1 — TimeWarp / middle-cut rebuild

对已确认 source discontinuity/cut 生成新的 effective mapping、cut intervals 和 canonical timeline artifact。

### P2 — real-task calibration / blind-test

真实素材验证：mapping residual、onset/offset、review density、cut/overlap P/R、runtime。

### P3 — Editor Evidence + LanguageSpan cue fusion

只在 canonical/source mapping 之后作为辅助边界证据。

### P4 — Forced Alignment / ASR v2

由真实误差数据决定是否成为下一瓶颈。

## 11. 当前不能宣称

- a5 已 stable；
- confirmed overlap 已能自动发布；
- blocked TimeWarp 可以人工强制放行；
- bootstrap profile 已最优；
- 真实任务准确率已提升固定百分比。

当前正确表述：

> **v4.0.0a5 正在把人工 review 从“临时结论”升级为可重放 artifact：transition false-positive 可以安全解除并进入原生 renderer；confirmed overlap / blocked TimeWarp 仍 fail-closed，等待对应的 timeline recomposition/rebuild。**
