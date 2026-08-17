# Lyric Aligner v4 关键变更记录

> 所有实质性生产更新必须按 `documentation-contract.md` 在同一 PR 同步本文件。这里记录已进入代码的行为、兼容/迁移和验证边界，不把设计草案写成已实现事实。

## 2026-08-17 — Foundation：测量、多语言与发布可信性

- evaluator 增加 sequence WER、line exact P/R/F1、missing/extra/order、split/merge、onset/offset、cut、overlap IoU/track attribution；
- Editor Evidence 按语言分级，canonical text 不由 ASR/Editor 决定；
- strict SRT parser；
- FINAL SRT/audit/QA 严格绑定；
- ArtifactManifest 绑定 task fingerprint、algorithm version、config、upstream IDs、materialized SHA；
- release 拒绝跨 task/version/profile 或未固化 calibration override。

## 2026-08-17 — TrackAsset / Canonical Single Truth

- fail-closed TrackAsset/TrackOccurrence/ResolvedAssetBinding；
- source/LRC resolution 使用 score + margin + identity + 文件唯一占用；
- semantic identity 包含 source SHA、raw lyric SHA、canonical same-timestamp selection SHA；
- line LRC / Enhanced LRC / QRC 进入统一 canonical parser；
- downstream 不得重新 fuzzy resolve source/LRC 或重选 canonical original。

## 2026-08-17 — Audio Mapping v2

- HPSS harmonic + Chroma CENS + MFCC；
- 每窗多候选、NMS、top1/top2 margin、全局单调 source path；
- AFFINE-first；BPM 只作 soft prior；
- evidence-driven continuous PIECEWISE_RATE；
- `rate change != cut`；source-position jump 才产生 discontinuity；
- Selective Fine 只处理难例/局部区间。

## 2026-08-17 — Calibration / Documentation Contracts

- 完整 `V4CalibrationProfile` 内容形成稳定 profile_id；
- TrackAsset artifact 嵌入 profile；
- 临时 CLI overrides 只允许实验，release BLOCK；
- `documentation-contract.md + validate_docs_contract.py` 进入 CI；
- production/status/CLI/schema/architecture 变化必须同步 owning docs。

## 2026-08-17 — v4.0.0a3 Production-first（已合入 main）

PR #2 squash merge：

```text
cfa43f4c854b699819cd3acb0cfea575cd1a04c8
```

新增 `timeline/projector.py`、`pipeline/production.py`、`v4_run.py`，形成 Asset → Coarse → Fine → Timeline → Transition 的 v4 reconstruction。新任务优先 v4，unresolved evidence → `review_required`，无 silent v3.9 fallback。

最终 base=main CI #267 全绿后合并。

## 2026-08-17 — v4.0.0a4 Package-native Final Render（已合入 main）

PR #3 squash merge：

```text
236d9d717229147ee1d1a8755d712e54db47a751
```

新增 `timeline/composer.py` / `v4_render.py`，review-free 任务形成：

```text
v4_run → v4_render → v4_validate_release
```

严格 release binding 要求 exact final_render upstream、algorithm/profile 一致，并重新验证 FINAL SRT/CSV/QA materialized hashes。

GitHub Billing 恢复后最新 head 的 #303 在 Python 3.10/3.12/3.14 + ASR + docs/Skill/privacy/environment/diff-check 全绿后才合并。

## 2026-08-17 — v4.0.0a5 Replayable Review Decision（已合入 main）

PR #4 squash merge：

```text
a80a531d6933946484c54d3a589bc55b0cb9e94b
```

新增：

```text
lyric_aligner/review/decisions.py
scripts/v4_review.py
```

人工 review 升级为 task-scoped + exact base-run-scoped + fingerprinted artifact。

安全语义：

- transition `resolved_clear`：解除 false-positive candidate；
- `confirmed_overlap`：仍 review_required + requires_recomposition；
- TimeWarp `confirmed_requires_rebuild`：仍 review_required；
- blocked TimeWarp 没有 `resolved_clear`。

Renderer 接受合法 `review_resolution` run，并保留 base TrackAsset/timeline lineage。

PR head `66ad787...` 的 validate #321 在 Python 3.10/3.12/3.14、ASR、review→render E2E、Documentation Contract、Skill/privacy/environment/diff-check 一次全绿后合并。

## 2026-08-17 — v4.0.0a6 Confirmed-overlap Dual-track Recomposition（当前开发）

目标：把人工确认的 cross-track overlap 从一个“仍然 BLOCK 的事实”materialize 成两路独立 canonical timeline，使最终 SRT 可以在**确认区间内**保留两条同时存在的歌词 cue，而不是拼成一行。

### Candidate-level transition identity

发现 a5 的边界级 transition issue 在同一 A→B window 出现多个分离 candidate 时过粗。a6 增加：

```text
candidate_id = hash(candidate_type, left_occurrence, right_occurrence, start_ms, end_ms)
```

`v4_run` 对每个 overlap/ambiguity interval 单独创建：

```text
transition_overlap
transition_ambiguity
```

Review Decision schema 升为 `1.1`，candidate-level issue identity 包含 candidate_id。清除一个 candidate 不影响同边界其他 candidate。

### Transition provenance for recomposition

`v4_run` transition summary 现在固定记录：

```text
left_coarse_path / artifact
right_coarse_path / artifact
transition_path / artifact
```

下游禁止重新猜文件。

### New overlap timeline layer

新增：

```text
lyric_aligner/timeline/overlap.py
scripts/v4_recompose_overlap.py
```

对每个 `confirmed_overlap`：

1. 要求非空 issue_id/candidate_id；
2. candidate_id/interval/pair 必须与原 Transition artifact 唯一一致；
3. Transition artifact 必须属于 reviewed-run lineage；
4. Transition artifact 必须 upstream 到 exact TrackAsset artifact；
5. LEFT/RIGHT boundary coarse 分别验证 exact occurrence、track、canonical selection、asset identity；
6. 两侧 coarse artifact 必须都属于 reviewed-run 和 Transition lineage，且不能是同一 artifact；
7. `should_run_fine_alignment()` 判断难例时自动执行 Selective Fine；
8. effective boundary TimeWarp Fine 后仍 blocked → recomposition 失败；
9. canonical lyrics 通过 boundary mapping 在 exact confirmed interval 重新投影；
10. projected lines/tokens 严格 clip 到 confirmed interval；
11. 与 primary occurrence timeline 合并，只扩展到 confirmed region。

生成：

```text
overlap_timeline_recomposition  # per occurrence timeline artifact
overlap_recomposition           # recomposed run artifact
```

Processed overlap issues 移除，其他 review issues 原样保留。只有 remaining issues=0 才 `ready_for_render`。

### Composer / Renderer

`v4_render.py` 新增支持 `overlap_recomposition` run。

Final composer 对**所有实际跨 track cue 交集**逐对检查，不只看排序后的相邻 cue。只有：

```text
exact occurrence pair
AND entire pairwise intersection ⊆ confirmed region
```

才允许 cue 时间重叠。任何越界仍 BLOCK。

最终输出保持两个独立 canonical cues，不把左右歌词拼文本。

### a6 regression coverage

新增/扩展：

- stable transition candidate_id；
- same-boundary multi-candidate independent review；
- confirmed issue_id materialization；
- strict overlap clipping / primary+overlap timeline merge；
- confirmed-region-only composer gate；
- non-adjacent cross-track intersection regression；
- swapped boundary occurrence/track/canonical/asset lineage BLOCK；
- artifact-level `v4_recompose_overlap → v4_render` E2E，避免把测试稳定性绑在声学阈值是否碰巧触发 overlap；
- CLI bootstrap。

### Calibration / migration

a6 不修改 bootstrap calibration 数值，继续使用 `production-bootstrap-2026-08-17-a4` profile 内容。变化属于 algorithm/review/timeline contract，因此 package version 升为 `4.0.0a6`；a5 artifacts 不可与 a6 artifacts 混入同一 release。

### 尚未完成

- confirmed TimeWarp/middle-cut mapping + timeline rebuild；
- real private calibration / blind-test；
- Editor Evidence + LanguageSpan final cue fusion；
- Forced Alignment / ASR v2 由真实误差决定优先级。

## 验证纪律

任何“测试通过/可合并”结论必须绑定具体 head/CI。a6 当前尚未完成最新 head 的 GitHub Actions 验证，因此不能声明可合并。
