# Lyric Aligner v4 实施记录与关键代码说明

> 只记录已经进入代码的生产能力、契约和迁移边界。当前开发版本：`4.0.0a4`。

## 1. 当前正式分层

```text
lyric_aligner/
  assets/       # TrackAsset / TrackOccurrence / fail-closed resolution
  audio/        # feature retrieval / TimeWarp / Fine / Transition
  contracts/    # immutable artifact lineage
  io/           # strict task text input
  pipeline/     # PipelineContext / production planning
  qa/           # final integrity
  text/         # canonical lyrics / normalization / language spans
  timeline/     # source→mix projection + final cue composition
```

生产 CLI：

```text
v4_run.py
v4_render.py
v4_validate_release.py
```

单 stage CLI 主要用于诊断和 calibration。

## 2. Asset / Canonical Single Truth

`TrackAsset` 表示具体录音 + canonical lyric interpretation；`TrackOccurrence` 表示该资产在 mix 中的一次出现。

TrackAsset identity 包含：

```text
source audio SHA-256
raw lyric SHA-256
canonical same-timestamp selection SHA-256
```

因此修改 LRC original selection 会改变资产身份，下游旧 artifact 不可复用。

`ResolvedAssetBinding` 形成后，所有下游 stage 禁止重新 fuzzy resolve source/LRC。

Canonical parser 支持：

- line LRC；
- Enhanced LRC；
- QRC；
- same timestamp alternatives；
- token/word timing。

ASR/Editor 不拥有 canonical text 权限。

## 3. Source-to-Mix Mapping

### 3.1 Coarse retrieval

```text
mix/source audio
 → HPSS harmonic
 → Chroma CENS + MFCC
 → multi-slope / multi-source candidates
 → NMS + top1/top2 margin
 → global monotonic path
```

强 click/metronome 不是主对齐依据。

### 3.2 AFFINE first

默认：

```text
source_time = intercept + slope * mix_time
```

BPM 仅作为 slope soft prior。

### 3.3 Continuous PIECEWISE_RATE

当 AFFINE residual/drift 明显失败、复杂模型显著改善且至少有足够独立 feature family 支持时才升级：

```text
source_time = intercept + base_slope * mix_time
            + Σ delta_i * max(0, mix_time - breakpoint_i)
```

canonical model state：

```text
intercept
base_slope
breakpoints[]
slope_deltas[]
```

source position 保持连续；local slope 可以突然变化。

### 3.4 Cut

`rate change != cut`。

只有连续 mix 时间上的 source-position jump 超出连续倍率 envelope 才产生 discontinuity candidate。

Middle cut 无论 task 声明 false/true/unknown 都不能自动 confirmed。未解决 discontinuity 必须 BLOCK。

### 3.5 Selective Fine

高置信 AFFINE 不跑 Fine。ambiguous/complex/blocked case 才在 coarse source neighbourhood 做高分辨率搜索，避免整条长 mix 重算。

## 4. Transition

Primary occurrence interval 负责主单曲 timeline，但 nominal start 不是真实声学硬边界。

相邻 A/B 另外使用：

```text
boundary ± transition.search_margin_seconds
```

LEFT source 与 RIGHT source 都在同一 mix 区间取证。

Transition profile 当前控制：

```text
min_score
min_margin
min_overlap_seconds
search_margin_seconds
minimum_feature_agreement
merge_gap_seconds
```

双侧强 evidence -> overlap candidate；ambiguous repeated occurrence -> uncertain interval。两者都 BLOCK/review，不自动成为 overlap truth。

## 5. Canonical Timeline Projection

`timeline/projector.py`：

```text
CanonicalLine / CanonicalToken source time
               +
Effective TimeWarp (Fine if applied, else Coarse)
               ↓
Global mix timeline
```

支持 AFFINE 与 continuous PIECEWISE_RATE 的解析反演。

Blocked TimeWarp 不能生成可继续发布的 timeline。

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

## 7. a4 Final Timeline Composer

新增 `timeline/composer.py`。

它把多个 review-free occurrence canonical timelines 组合成最终 cue stream。

关键算法：

1. 每行 `mix_start_ms` 裁剪到 occurrence window；
2. 已有 `mix_end_ms` 时使用 projected end；
3. Enhanced/QRC word timing 加 `word_timing_tail_ms`；
4. 没有 end 的最后行使用 `open_line_duration_ms`；
5. 所有 cue 受 `maximum_line_duration_ms` 与 occurrence end 限制；
6. 若有下一 canonical line，则 end 不能越过 next start；
7. duration < `minimum_cue_duration_ms` -> BLOCK；
8. same-occurrence overlap -> BLOCK；
9. cross-track overlap 未经确认 -> BLOCK。

这样不会因为 line LRC 下一句很晚，就让上一句字幕穿过整段乐器间奏持续显示。

## 8. a4 Render Calibration

`V4CalibrationProfile` 新增：

```python
@dataclass(frozen=True)
class RenderConfig:
    minimum_cue_duration_ms: int = 250
    maximum_line_duration_ms: int = 12000
    open_line_duration_ms: int = 5000
    word_timing_tail_ms: int = 120
```

profile version：

```text
production-bootstrap-2026-08-17-a4
```

所有数值均属于 bootstrap calibration，不是业务硬编码真理。

由于 profile complete content 变化，a3 TrackAsset/profile artifacts 不允许静默升级；a4 从 Asset Resolution / `v4_run` 重跑。

## 9. Package-native Final Renderer

`scripts/v4_render.py` 接受：

```text
Task Manifest
v4_run.json + artifact
track_assets.json + artifact
```

然后逐个验证 canonical timeline payload/artifact。

必须满足：

```text
run.status == ready_for_render
run.issues == []
legacy_fallback_used == false
all task/version/profile IDs match
all materialized hashes match
all timeline artifact IDs ∈ run upstream lineage
```

输出：

```text
FINAL.srt
FINAL.csv
FINAL.qa.json
FINAL.render.artifact.json
```

Audit 每 cue 包含：

- cue number/start/end/text；
- occurrence_id / track_id / ordinal；
- canonical_line_index；
- timing format/end basis；
- task fingerprint；
- cue_id / text SHA。

QA 只有在 review-free composer 成功时才写 `publish_ready=true` / `review_candidate_count=0`。

## 10. Release

`v4_validate_release.py` 继续作为独立最后门禁：

```text
FINAL.srt + FINAL.csv + FINAL.qa.json
                 +
FINAL.render.artifact.json
                 ↓
release.artifact.json
```

它重新核对 SRT/report/QA、task、algorithm version、profile、upstream lineage 和 materialized outputs。

因此 a4 review-free 正常路径不再需要 legacy v3.9 `build/finalize/qa`。

## 11. 文档与 CI 契约

实质性生产变更必须同步：

- `v4-change-record.md`
- `v4-status.md`
- owning runtime/implementation/architecture docs

CI 运行：

```text
compileall lyric_aligner scripts
validate_docs_contract.py
unittest discovery
validate_skill.py
privacy_scan.py
check_environment.py
git diff --check
```

Python matrix：3.10 / 3.12 / 3.14；ASR environment 单独验证。

## 12. 当前测试重点

### Reconstruction E2E

`test_v4_run_end_to_end.py`：纯合成 WAV/LRC/task manifest，真实跑 `v4_run.py`。

### Composer

`test_v4_timeline_composer.py`：

- open last line；
- long instrumental gap；
- Enhanced word tail；
- occurrence-window clipping；
- short-cue BLOCK；
- unconfirmed cross-track overlap BLOCK。

### Run-to-release E2E

`test_v4_render_end_to_end.py`：

```text
v4_run
 → v4_render
 → v4_validate_release
```

全部使用 synthetic source/mix、虚构歌词和 schema-2.0 Task Manifest，不提交真实任务素材。

## 13. 下一接口：Review Decision

当前 `review_required` 仍不能 render。

下一层必须把人工结论做成 task-scoped、fingerprinted、可重放 artifact，不能靠口头说明或直接修改 JSON。

计划语义：

- transition false positive -> `resolved_clear`，安全解除该 issue；
- confirmed overlap -> 必须生成 transition-aware 双路 canonical timeline，不能只是把 BLOCK 改 false；
- middle-cut confirmed/rejected -> 必须作用于 TimeWarp/timeline，再生成新 downstream artifact；
- 每个 issue/decision 必须有稳定 identity 和 evidence。

在该层完成前，不允许 renderer 绕过 `review_required`。
