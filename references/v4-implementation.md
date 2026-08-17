# Lyric Aligner v4 实施记录与关键代码说明

> 只记录已经进入代码的生产能力、契约和迁移边界。当前开发版本：`4.0.0a4`。

## 1. 当前正式分层

```text
lyric_aligner/
  assets/       # TrackAsset / TrackOccurrence / fail-closed resolution
  audio/        # features / Coarse / TimeWarp / Fine / Transition
  contracts/    # immutable artifact lineage
  io/           # strict task text input
  pipeline/     # PipelineContext / production planning / review identity groundwork
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

单 stage CLI 主要用于诊断、calibration 和 artifact 重现。

## 2. Asset / Canonical Single Truth

`TrackAsset` 表示具体录音 + canonical lyric interpretation；`TrackOccurrence` 表示该资产在 mix 中的一次出现。

TrackAsset semantic identity：

```text
source audio SHA-256
raw lyric SHA-256
canonical same-timestamp selection SHA-256
```

因此修改 LRC original selection 会改变资产身份，下游旧 artifact 不可复用。

`ResolvedAssetBinding` 形成后，所有下游 stage 禁止重新 fuzzy resolve source/LRC。

Canonical parser 支持 line LRC、Enhanced LRC、QRC、same-timestamp alternatives、token/word timing。ASR/Editor 不拥有 canonical text 权限。

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

canonical model state：

```text
intercept
base_slope
breakpoints[]
slope_deltas[]
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

左右 source 在同一 mix 区间独立取证。

Transition profile 控制：

```text
min_score
min_margin
min_overlap_seconds
search_margin_seconds
minimum_feature_agreement
merge_gap_seconds
```

双侧强 evidence -> overlap candidate；ambiguous repeated occurrence -> uncertain interval。二者都 BLOCK/review，不自动成为 overlap truth。

## 5. Canonical Timeline Projection

`timeline/projector.py`：

```text
CanonicalLine / CanonicalToken source time
               +
Effective TimeWarp (Fine if applied, else Coarse)
               ↓
Global mix timeline
```

支持 AFFINE 与 continuous PIECEWISE_RATE 解析反演。

输出 result 明确携带：

- occurrence_id；
- ordinal；
- track_id；
- canonical_selection_sha256；
- occurrence window；
- projected lines/tokens。

这些字段是 a4 final renderer 再验证 single-truth lineage 的正式接口。

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

`timeline/composer.py` 把多个 review-free occurrence canonical timelines 组合成最终 cue stream。

算法：

1. `mix_start_ms` 裁剪到 occurrence window；
2. 已有 `mix_end_ms` 时使用 projected end；
3. Enhanced/QRC word timing 加 `word_timing_tail_ms`；
4. 没有 end 的最后行使用 `open_line_duration_ms`；
5. 所有 cue 受 `maximum_line_duration_ms` 与 occurrence end 限制；
6. 有下一 canonical line 时 end 不越过 next start；
7. duration < `minimum_cue_duration_ms` -> BLOCK；
8. same-occurrence overlap -> BLOCK；
9. cross-track overlap 未确认 -> BLOCK。

这避免 line LRC 下一句很晚时上一句穿过长间奏常驻，也避免为了“凑出结果”自动拉长极短 cue。

## 8. a4 Render Calibration

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

profile complete content 改变，因此 a3 TrackAsset/profile artifacts 不允许静默升级；a4 必须从 Asset Resolution / `v4_run` 重跑。

## 9. Package-native Final Renderer

`scripts/v4_render.py` 接受：

```text
Task Manifest
v4_run.json + artifact
track_assets.json + artifact
```

必须满足：

```text
run.status == ready_for_render
run.issues == []
legacy_fallback_used == false
```

### Renderer lineage checks

1. task / algorithm / calibration profile 完全一致；
2. production-run artifact 的 materialized `v4_run.json` hash 必须匹配；
3. supplied TrackAsset artifact 必须存在于 production-run upstream IDs；
4. 每个 timeline artifact 必须存在于 production-run upstream IDs；
5. 每个 timeline artifact 的 upstream 必须包含同一个 supplied TrackAsset artifact；
6. timeline result 的 occurrence_id、track_id、ordinal、canonical_selection_sha256 必须与 `ResolvedAssetBinding` 相同；
7. run occurrence set 必须精确等于全部 resolved TrackOccurrences；
8. timeline materialized hash 不得漂移。

这防止同一 task/profile 下另一轮合法但不同的 Asset Resolution 或 timeline 被误混入 final render。

### Renderer outputs

```text
FINAL.srt
FINAL.csv
FINAL.qa.json
FINAL.render.artifact.json
```

Audit 每 cue 包含：

- number/start/end/text；
- occurrence_id / track_id / ordinal；
- canonical_line_index；
- timing format/end basis；
- task fingerprint；
- cue_id / text SHA。

QA 只有 review-free composer 成功时才写：

```text
passed=true
structurally_valid=true
fully_reviewed=true
publish_ready=true
review_candidate_count=0
```

并绑定 calibration profile id/version、source run artifact、source asset artifact。

## 10. Strict Release Contract

`v4_validate_release.py` 是独立最后门禁：

```text
FINAL.srt + FINAL.csv + FINAL.qa.json
                 +
exact FINAL.render.artifact.json
                 ↓
release.artifact.json
```

对于 v4：

1. 必须至少提供 upstream artifact；
2. 必须且只能有一个 `final_render` upstream；
3. requested release algorithm version 必须等于 upstream version；
4. upstream calibration profile id/version 必须存在；
5. QA calibration profile id/version 必须与 upstream 完全一致；
6. `final_render` artifact 的三个 output records 必须分别匹配当前 final SRT / audit / QA 的 size 与 SHA-256；
7. SRT/report 再逐 cue 核对时间、正文、cue id、text hash；
8. QA ready flags 必须全部为 true，review count 必须为 0。

关键安全性质：

> 即使 FINAL.srt、FINAL.csv、FINAL.qa.json 三者被一起协调修改、彼此仍一致，只要没有重新生成对应 final_render artifact，也不能通过 release。

因此 a4 review-free 正常路径不再需要 legacy v3.9 `build/finalize/qa`。

## 11. Review Issue Identity Groundwork

`pipeline/production.py` 已增加 stable review issue identity helper，为下一阶段 replayable Review Decision artifact 建接口。

当前尚未把该机制接入 v4_run 的正式 decision replay，因此不能宣称 Review Decision 已实现。

计划：

- transition false positive -> `resolved_clear`；
- confirmed overlap -> 生成 transition-aware 双路 canonical timeline；
- middle-cut confirmed/rejected -> 对 TimeWarp/timeline 产生新 downstream artifact；
- 每个 decision 必须绑定 task、issue_id、evidence、upstream artifact。

在该层完成前，renderer 不允许绕过 `review_required`。

## 12. Tests

### Reconstruction E2E

`test_v4_run_end_to_end.py`：synthetic WAV/LRC/task manifest，真实 subprocess 跑 `v4_run.py`。

### Composer

`test_v4_timeline_composer.py`：open line、long gap、word tail、window clipping、short-cue BLOCK、unconfirmed cross-track overlap BLOCK。

### Run-to-release E2E

`test_v4_render_end_to_end.py`：

```text
v4_run → v4_render → v4_validate_release
```

全部使用 synthetic source/mix 与虚构歌词。

### Release negative tests

`test_v4_release_lineage.py` / `test_v4_release_integrity.py`：

- final output materialized change；
- wrong/multiple final_render；
- wrong algorithm version；
- mixed profile；
- QA profile mismatch；
- SRT/report mismatch；
- malformed SRT。

## 13. 文档 / CI

实质性生产变更同步 `v4-change-record.md`、`v4-status.md` 及 owning runtime/implementation docs。

完整 CI 目标：

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

### 当前外部 blocker

PR #3 当前 GitHub Actions runner 因账户 payment/spending-limit 问题未启动，run 显示 `runner_id=0 / steps=[]`。这不是代码测试结果。

处理原则：不降低门禁、不合并未验证 a4；Billing 恢复后以最新 head 重跑完整矩阵。
