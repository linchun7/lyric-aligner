# Lyric Aligner v4 实施记录与关键代码说明

> 只记录已经进入代码的生产能力、契约和迁移边界。当前开发版本：`4.0.0a5`。

## 1. 当前正式分层

```text
lyric_aligner/
  assets/       # TrackAsset / TrackOccurrence / fail-closed resolution
  audio/        # features / Coarse / TimeWarp / Fine / Transition
  contracts/    # immutable artifact lineage
  io/           # strict task text input
  pipeline/     # PipelineContext / production planning
  qa/           # final integrity
  review/       # task/base-run scoped replayable review decisions
  text/         # canonical lyrics / normalization / language spans
  timeline/     # source→mix projection + final cue composition
```

生产 CLI：

```text
v4_run.py
v4_review.py
v4_render.py
v4_validate_release.py
```

单 stage CLI 主要用于诊断、calibration 和 artifact 重现。

## 2. Asset / Canonical Single Truth

`TrackAsset` 表示具体录音 + canonical lyric interpretation；`TrackOccurrence` 表示该资产在 mix 中的一次出现。

TrackAsset semantic identity：

```text
source audio SHA-256
raw lyric SHA-256
canonical same-timestamp selection SHA-256
```

`ResolvedAssetBinding` 形成后，下游 stage 禁止重新 fuzzy resolve source/LRC。Canonical parser 支持 line LRC、Enhanced LRC、QRC、same-timestamp alternatives、token/word timing。ASR/Editor 不拥有 canonical text 权限。

## 3. Source-to-Mix Mapping

### Coarse

```text
mix/source audio
 → HPSS harmonic
 → Chroma CENS + MFCC
 → multi-slope / multi-source candidates
 → NMS + top1/top2 margin
 → global monotonic path
```

强 click/metronome 不是主对齐依据。

### AFFINE first

默认：

```text
source_time = intercept + slope * mix_time
```

BPM 仅作 slope soft prior。

### Continuous PIECEWISE_RATE

只有 AFFINE residual/drift 明显失败、复杂模型显著改善且获得足够独立 feature-family 支持时才升级：

```text
source_time = intercept + base_slope * mix_time
            + Σ delta_i * max(0, mix_time - breakpoint_i)
```

source position 连续；local slope 可突然变化。

### Cut

`rate change != cut`。只有 source-position jump 超出连续倍率 envelope 才产生 discontinuity candidate。Middle cut 无论 task 声明 false/true/unknown 都不能自动 confirmed；未解决 discontinuity 必须 BLOCK。

### Selective Fine

高置信 AFFINE 不跑 Fine。ambiguous/complex/blocked case 才在 coarse source neighbourhood 高分辨率精修，避免整条长 mix 重算。

## 4. Transition

Primary occurrence interval 负责主单曲 timeline，但 nominal start 不是真实声学硬边界。

相邻 A/B 使用：

```text
boundary ± transition.search_margin_seconds
```

左右 source 在同一 mix 区间独立取证。双侧强 evidence -> overlap candidate；ambiguous repeated occurrence -> uncertain interval。二者都 BLOCK/review，不自动成为 overlap truth。

## 5. Canonical Timeline Projection

`timeline/projector.py`：

```text
CanonicalLine / CanonicalToken source time
               +
Effective TimeWarp (Fine if applied, else Coarse)
               ↓
Global mix timeline
```

输出 result 明确携带：

- occurrence_id；
- ordinal；
- track_id；
- canonical_selection_sha256；
- occurrence window；
- projected lines/tokens。

这些字段是 final renderer 再验证 single-truth lineage 的正式接口。

## 6. Production Orchestrator

`v4_run.py`：

```text
Task Manifest
 → Asset Resolution
 → Primary Coarse
 → Selective Fine
 → Effective TimeWarp
 → Canonical Timeline Projection
 → Shared-boundary LEFT/RIGHT Coarse
 → Transition Probe
 → production_orchestration artifact
```

状态：

```text
ready_for_render
review_required
```

并固定：

```text
legacy_fallback_used = false
```

## 7. Final Timeline Composer

`timeline/composer.py` 把 review-free occurrence canonical timelines 组合成最终 cue stream。

关键规则：

1. `mix_start_ms` 裁剪到 occurrence window；
2. 已有 `mix_end_ms` 时使用 projected end；
3. Enhanced/QRC word timing 加 `word_timing_tail_ms`；
4. 没有 end 的最后行使用 `open_line_duration_ms`；
5. cue 受 `maximum_line_duration_ms` 与 occurrence end 限制；
6. 有下一 canonical line 时 end 不越过 next start；
7. duration < `minimum_cue_duration_ms` -> BLOCK；
8. same-occurrence overlap -> BLOCK；
9. cross-track overlap 未经 transition-aware recomposition -> BLOCK。

## 8. Render Calibration

当前 profile 继续使用：

```text
production-bootstrap-2026-08-17-a4
```

`RenderConfig`：

```python
@dataclass(frozen=True)
class RenderConfig:
    minimum_cue_duration_ms: int = 250
    maximum_line_duration_ms: int = 12000
    open_line_duration_ms: int = 5000
    word_timing_tail_ms: int = 120
```

所有数值均属于 bootstrap calibration，不是业务硬编码真理。

a5 没有调整 calibration 参数；只升级 algorithm/review contract 到 `4.0.0a5`。因此 a4 algorithm artifacts 不能与 a5 artifacts 混用，但 profile calibration identity 本身保持不变。

## 9. Replayable Review Decision Layer

新增：

```text
lyric_aligner/review/decisions.py
scripts/v4_review.py
```

目的：把人工 review 从“口头结论/手改 JSON”变成正式 artifact，而不是提供一个绕过 BLOCK 的 override。

### 9.1 Review issue identity

`normalize_review_issue()` 生成 task-scoped deterministic `issue_id`。

Transition identity：

```text
schema_version
task_fingerprint_sha256
kind=transition
code=transition_overlap_or_ambiguity
left_occurrence_id
right_occurrence_id
```

TimeWarp identity：

```text
schema_version
task_fingerprint_sha256
kind=timewarp
code=effective_mapping_blocked
occurrence_id
```

`reason` 等展示字段不参与 hash，因此文案变化不破坏逻辑 identity。

### 9.2 Base-run scope

Review template 除 issue_id 外，还绑定：

```text
algorithm_version
base_run_artifact_id
```

这解决一个重要问题：同一任务再次运行后，即使产生相同 logical issue_id，旧 decision 也不能静默应用到新的 production-run artifact。

### 9.3 Template schema

`scripts/v4_review.py template` 输出：

```text
schema_version = 1.0
algorithm_version
task_fingerprint_sha256
base_run_artifact_id
review_items[]
```

每个 item：

```text
issue_id
issue snapshot
allowed_actions
decision = null | { action, rationale }
```

Apply 时 issue snapshot 必须仍与 base run 的 normalized issue 完全一致，避免 reviewer 在 decision 文件里顺手改 evidence。

### 9.4 Apply validation

`scripts/v4_review.py apply` 重新验证：

- Task Manifest inputs；
- base artifact stage=`production_orchestration`；
- base artifact task/version/materialized output hash；
- run algorithm version / task fingerprint；
- decision task/version/base artifact ID；
- 每个 issue snapshot；
- `allowed_actions` 未被篡改；
- action 属于当前 issue kind；
- rationale 非空；
- decision file 包含全部 base issues；
- 不允许重复 issue item。

### 9.5 Safe decision state machine

#### Transition `resolved_clear`

人工确认 overlap/ambiguity candidate 是误报：

```text
active issue removed
resolved_issues += decision
effective_blocked=false
```

原 transition summary 的原始 `blocked` evidence 不被改写，只新增：

```text
transition.review_resolution
```

如果没有其他 active issue，reviewed run 可变为 `ready_for_render`。

#### Transition `confirmed_overlap`

```text
status=confirmed
requires_recomposition=true
```

issue 继续 active，reviewed run 保持 `review_required`。

#### TimeWarp `confirmed_requires_rebuild`

```text
status=confirmed
requires_timeline_rebuild=true
```

issue 继续 active，reviewed run 保持 `review_required`。

TimeWarp **没有** `resolved_clear`，因为 blocked mapping 可能根本没有合法 canonical timeline。

### 9.6 Review artifact

Apply 生成：

```text
reviewed_run.json
reviewed_run.artifact.json
```

Artifact：

```text
stage = review_resolution
outputs:
  v4_reviewed_run
  review_decisions
```

并记录：

- decision file SHA-256；
- exact base run artifact ID；
- calibration profile identity；
- base run 的所有 upstream artifact IDs；
- base production-run artifact 本身。

所以 review resolution 不会切断 TrackAsset/coarse/fine/timeline/transition provenance。

## 10. Final Renderer with Reviewed Runs

`scripts/v4_render.py` 现在允许两种 run artifact：

```text
production_orchestration / v4_production_run
review_resolution      / v4_reviewed_run
```

共同门禁：

```text
status == ready_for_render
issues == []
legacy_fallback_used == false
```

对于 `review_resolution` 额外要求：

1. reviewed payload 包含 `review_resolution` object；
2. `base_run_artifact_id` 非空；
3. base run artifact ID 位于 review artifact upstream IDs；
4. review artifact normalized config 的 base-run ID 与 payload 一致；
5. `remaining_issue_count == 0`；
6. supplied TrackAsset artifact 位于 review artifact upstream；
7. 所有 canonical timeline artifact 位于 review artifact upstream；
8. 原有 timeline/TrackAsset identity/hash 检查仍全部执行。

QA 与 final-render artifact 新增：

```text
source_run_stage = production_orchestration | review_resolution
```

因此 downstream 能明确区分“无 review 直接发布”和“通过正式 review artifact 解除安全 transition false-positive 后发布”。

## 11. Strict Release Contract

`v4_validate_release.py` 是独立最后门禁：

```text
FINAL.srt + FINAL.csv + FINAL.qa.json
                 +
exact FINAL.render.artifact.json
                 ↓
release.artifact.json
```

对于 v4：

1. 必须且只能有一个 `final_render` upstream；
2. requested algorithm version = upstream version；
3. upstream calibration profile id/version 必须存在；
4. QA profile id/version 必须与 upstream 完全一致；
5. final-render artifact 的三个 output records 必须分别匹配当前 final SRT / audit / QA 的 size 与 SHA-256；
6. SRT/report 再逐 cue 核对时间、正文、cue id、text hash；
7. QA ready flags 全部 true，review count=0。

## 12. Tests

### Reconstruction E2E

`test_v4_run_end_to_end.py`：synthetic WAV/LRC/task manifest，真实 subprocess 跑 `v4_run.py`。

### Composer

`test_v4_timeline_composer.py`：open line、long gap、word tail、window clipping、short-cue BLOCK、unconfirmed cross-track overlap BLOCK。

### Review decisions

`test_v4_review_decisions.py`：

- issue ID reason-stability + task isolation；
- transition clear；
- confirmed overlap fail-closed；
- TimeWarp cannot resolved_clear；
- confirmed TimeWarp rebuild flag；
- base-run mismatch；
- issue snapshot tamper。

`test_v4_review_cli.py`：

- template → apply；
- review artifact stage/output roles；
- decision SHA/provenance；
- inherited reconstruction upstream IDs。

### Run-to-release E2E

`test_v4_render_end_to_end.py` 现在覆盖两条路径：

```text
v4_run → v4_render → v4_validate_release
```

以及：

```text
production evidence
 → synthetic review_required transition
 → v4_review template/apply resolved_clear
 → review_resolution artifact
 → v4_render
```

第二条断言 reviewed renderer 输出与原 canonical SRT 相同，并在 QA 中记录 `source_run_stage=review_resolution`。

### Release negative tests

`test_v4_release_lineage.py` / `test_v4_release_integrity.py`：final output tamper、wrong/multiple final_render、wrong version/profile、SRT/report mismatch、malformed SRT。

## 13. 文档 / CI

实质性生产变更同步：

- `v4-change-record.md`
- `v4-status.md`
- `v4-runtime-guide.md` / `SKILL.md`
- 本 implementation 文档。

完整 CI：

```text
compileall lyric_aligner scripts
validate_docs_contract.py
unittest discovery
validate_skill.py
privacy_scan.py
check_environment.py
git diff --check
```

Python 3.10 / 3.12 / 3.14；ASR environment 单独验证。

## 14. 下一实现接口

### confirmed overlap recomposition

当前 `confirmed_overlap` 只转成 `requires_recomposition=true`，不会进入 renderer。下一阶段必须生成真正双路、独立 canonical stream 的 transition-aware timeline artifact。

### confirmed TimeWarp / middle-cut rebuild

当前 `confirmed_requires_rebuild` 只冻结人工事实；下一阶段必须据此重建 effective mapping/cut intervals/timeline，而不是清一个布尔值。

这两个阶段完成前，Review Decision 的职责只是**安全地解除可证明的 false-positive block，并冻结其他人工结论**，不是代替声学和时间轴算法。
