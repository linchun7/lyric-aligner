# Lyric Aligner v4 关键变更记录

> 所有实质性生产更新必须按 `documentation-contract.md` 在同一 PR 同步本文件。这里记录“改了什么、为什么、兼容/迁移、验证边界”，不把设计草案写成已实现事实。

## 2026-08-17 — Foundation：测量、多语言与发布可信性

关键能力：

- evaluator 从 bag-of-token 升级为 sequence WER、line exact P/R/F1、missing/extra/order、split/merge、onset/offset、cut、overlap IoU/track attribution；
- Editor Evidence 按语言分级，zh/en direct-text，ko/ja phonetic-hint，yue timing-hint，unknown/generic 不把编辑器文字当 canonical truth；
- strict SRT parser；
- FINAL SRT / audit / QA 严格绑定；
- ArtifactManifest 绑定 task fingerprint、algorithm version、config、upstream IDs、materialized SHA；
- release 拒绝跨 task/version/profile 或未固化 calibration override。

## 2026-08-17 — TrackAsset / Canonical Single Truth

关键代码：`assets/`、`text/canonical_lyrics.py`、`pipeline/context.py`。

- fail-closed TrackAsset/TrackOccurrence/ResolvedAssetBinding；
- artist/title + top1/top2 margin + 文件唯一占用，避免错绑 source/LRC；
- semantic identity 包含 source SHA、raw lyric SHA、same-timestamp canonical selection SHA；
- 普通 LRC、Enhanced LRC、QRC 共用同一 timestamp alternative identity；
- downstream 不得重新 fuzzy resolve source/LRC 或固定选 `alternatives[0]`；
- UTF-8 fail-closed，避免“成功解码乱码”。

## 2026-08-17 — Audio Mapping v2

关键代码：`audio/features.py`、`coarse_mapper.py`、`timewarp.py`、`fine_alignment.py`。

- HPSS harmonic 降低 click/percussive 干扰；
- Chroma CENS + MFCC 多特征检索；
- 每窗多候选、NMS、top1/top2 margin；
- 全局单调 source path，避免 repeated chorus 每窗独立跳 occurrence；
- AFFINE-first；BPM 只作 soft prior；
- residual/drift/feature support 证明 fixed-rate 不足时才升级 continuous PIECEWISE_RATE；
- local rate 可突然变化，`rate change != cut`；
- source-position jump 才产生 discontinuity/cut candidate；
- clean AFFINE 跳过 Fine，难例才局部高分辨率精修；
- Coarse/Fine 只计算当前 occurrence/局部搜索区间，避免每首重复处理整条 40–60 分钟 mix。

## 2026-08-17 — Calibration / Documentation Contracts

- `V4CalibrationProfile` complete content 形成稳定 `profile_id`；
- TrackAsset artifact 嵌入 profile，下游通过 PipelineContext 恢复；
- 临时 CLI override 只能实验，release BLOCK；
- `documentation-contract.md + validate_docs_contract.py` 接入 CI；
- production/status/CLI/schema/architecture 变化必须同步 owning docs。

CI #241 曾因 CLI 已变但 runtime/workflow 文档未同步而正确 FAIL，证明文档门禁有效。

## 2026-08-17 — v4.0.0a3 Production-first（已合入 main）

合入方式：PR #2 squash merge，main commit `cfa43f4c854b699819cd3acb0cfea575cd1a04c8`。

- 新真实任务优先 v4；
- unresolved mapping/cut/transition → `review_required`；
- 禁止静默 fallback v3.9；
- v3.9 只作为 Git 历史/比较/仓库级 rollback 点；
- 新算法不再继续堆入 legacy monolith。

新增 `timeline/projector.py`、`pipeline/production.py`、`scripts/v4_run.py`，形成 Asset → Coarse → Fine → Timeline → Transition 的生产入口。相邻 A/B 在 `boundary ± profile.search_margin_seconds` 同一 mix 窗口独立取证；搜索窗口重叠不等于 overlap 已确认。

状态：`ready_for_render | review_required`，且固定 `legacy_fallback_used=false`。

PR 已 retarget 到 main 后，head `940f0fa...` 的 CI #267 SUCCESS：Python 3.10/3.12/3.14 full tests、synthetic `v4_run` E2E、Documentation Contract、Skill、privacy、environment、diff-check、ASR environment 全部通过。

## 2026-08-17 — v4.0.0a4 Package-native Final Render（当前开发）

目标：让 review-free 任务不再需要 v3.9 `build/finalize/qa`，从 v4 canonical timeline 直接生成最终 SRT/audit/QA/release chain。

### Version / Profile

- package：`4.0.0a4`；
- profile：`production-bootstrap-2026-08-17-a4`；
- 新增 `RenderConfig`：

```text
minimum_cue_duration_ms = 250
maximum_line_duration_ms = 12000
open_line_duration_ms = 5000
word_timing_tail_ms = 120
```

以上均为 bootstrap 参数，必须由真实 calibration 验证。

### Final Timeline Composer

新增 `lyric_aligner/timeline/composer.py`：

- 只消费 canonical projected timeline；
- 不从 Editor/ASR 生成最终歌词文本；
- cue 裁剪到 occurrence window；
- last/open line 使用有限显示时长；
- next-line gap 很长时应用 maximum line duration；
- Enhanced/QRC word timing 只增加短 profile tail；
- 过短 cue、same-occurrence 异常 overlap、未确认 cross-track overlap 均 BLOCK。

### Package-native Renderer

新增 `scripts/v4_render.py`。

只接受：

```text
run.status == ready_for_render
run.issues == []
legacy_fallback_used == false
```

并验证：

- task / algorithm / profile；
- production run artifact；
- supplied TrackAsset artifact 必须属于该 run upstream；
- timeline artifacts 必须属于 run upstream 且从同一 TrackAsset artifact 派生；
- timeline occurrence/track/ordinal/canonical-selection 与 binding 一致；
- run occurrence set 必须精确等于 resolved occurrence set。

输出：

```text
FINAL.srt
FINAL.csv
FINAL.qa.json
FINAL.render.artifact.json
```

### Strict Release Binding

`v4_validate_release.py` 在 a4 进一步 fail-closed：

- v4 release 至少需要一个 upstream artifact；
- **必须且只能有一个 `final_render` upstream**；
- release `--algorithm-version` 必须等于 upstream algorithm version；
- upstream calibration profile id/version 必须存在且与 QA 完全一致；
- `final_render` artifact 中记录的 `final_srt`、`audit_csv`、`qa_json` size/SHA 必须分别匹配当前实体文件。

这样即使三份最终文件被一起协调修改、彼此仍一致，只要未重新生成对应 `final_render` artifact，也不能形成 release。

### a3 → a4 Migration

profile complete content 已变化，所以 a3 TrackAsset/profile artifacts 不允许直接给 a4 renderer 静默复用。必须从 Asset Resolution / `v4_run` 重跑 a4 chain，禁止手工给旧 JSON 补 `render` 字段。

### Tests

- `test_v4_timeline_composer.py`：open line、长间奏、word tail、window clipping、短 cue、未确认 cross-track overlap；
- `test_v4_render_end_to_end.py`：纯合成 WAV/LRC/Task Manifest，真实 subprocess 执行 `v4_run → v4_render → v4_validate_release`；
- `test_v4_release_lineage.py`：modified final file、多个 render artifact、wrong algorithm version 等负向 lineage；
- `test_v4_release_integrity.py`：QA calibration profile mismatch 等最终完整性回归。

### Current CI Blocker

PR #3 的 Actions #283/#285 均在 runner 分配前失败，`runner_id=0`、`steps=[]`。GitHub annotation 明确为账户付款失败或 spending limit 需要提高，不是代码测试失败。

因此：

- 不降低 CI；
- 不把 a3 旧绿灯当作 a4 验收；
- PR #3 保持 Draft；
- Actions billing 恢复后，以最新 head 重新跑完整 3.10/3.12/3.14 + ASR 矩阵；
- 最新 a4 head 未正式跑绿前不合 main。

### a4 Still Next

- Review Decision artifact；
- confirmed overlap 双路 transition-aware timeline；
- confirmed/rejected middle cut 决策重放；
- Editor Evidence + LanguageSpan 最终 cue fusion；
- real private calibration/blind-test。

Forced Alignment / ASR v2 继续以后续真实误差决定优先级。

## 验证纪律

任何“测试通过/可合并”结论必须绑定具体 head/CI。旧 head 绿灯不能继承到新代码；GitHub runner 未启动也不能称为代码测试失败或通过。
