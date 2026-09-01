# Lyric Aligner v4 当前实施状态

更新日期：2026-09-01
主线算法版本：`4.0.0a8`

> PR #70 前的完整状态说明已无损归档到 `references/archive/2026-08-22-pre-max-authority-v4-status.md`。P3 前状态见 `references/archive/2026-08-19-pre-p3-v4-status.md`。生产基线见 `references/production-requirements.md`；Smart / Pro 细节见 `references/smart-pro-v1-1.md`。

## 1. 当前四档产品路径

```text
Standard -> Text Repair V2.1
Smart    -> Canonical Sequence Reconciliation + Anchor Timeline Repair v1.2.10（no-audio）
Pro      -> Selective Audio Repair v1.2.6（bounded audio evidence）
Max      -> Full V4 Alignment
```

共同事实：

- Canonical lyric 是最终**文字/顺序** truth；
- LRC 行换行不是最终 subtitle cue segmentation authority；
- Jianying/editor timing 与 cue boundary 是强但可推翻的先验；
- 只有更强的 token/word/audio boundary evidence 才能推翻可信 editor segmentation；
- Higher mode 可以增加证据、减少 review，但不能在没有更强反证时破坏 lower-mode 已安全成立的 text / cue ownership / timing；
- Canonical truth 构建前会过滤明确的 timed provider metadata：中英文制作/工程 credits 与严格的 multi-instrument section marker 不能进入 canonical text/order；普通含 instrument/credit-like 词汇的歌词仍保留，explicit selection 也不能把已判定 metadata 的行重新引入。

## 2. Standard

Standard = Text Repair V2.1，适用于 timing 已可信、只修文字：

- no audio；
- cue count / number / start / end 冻结；
- deterministic canonical text repair + bounded 1↔N / N↔1 / N↔N；
- canonical 连续内容相同但 LRC/editor 分句不同，保留 editor cue ownership；
- ambiguous/mixed/unsafe layout fail closed；
- production auto threshold `>=0.72`；report schema `2.1`。

## 3. Smart v1.2.10

Smart 是日常主力 no-audio 模式。当前 facade 使用 v1.2.10，并继续满足：

- canonical text/order authority；
- four-A primary timing model gate 不降低；
- BPM-derived 只做 soft plausibility，exact DAW 才可作为 hard prior；
- Sequence/BPM/A-bounded recovery 都是 text-only evidence，不倒灌成 A/B timing authority；
- split-line 内部 editor cue 只有在 exact normalized token partition + reliable later token timestamp 下才允许使用内部 boundary onset；否则 `segmentation_internal_boundary_unvalidated` 且无 timing proposal；
- manual actionable timing queue 与 Pro high-value budget subset 分离；actionable suspicion 不会因 ranking 消失；
- output path collision、overlap safety、stale-policy rejection 继续 fail closed。

## 4. Pro v1.2.6

Pro 只处理 Smart 明确 unresolved 的 bounded regions：

```text
timing review -> local source<->mix acoustic first
text/identity review -> bounded ASR + word timestamps
source identity needs help -> auxiliary forced alignment
unmapped review -> bounded ASR
```

当前 contract：

```text
automatic_timing_change_allowed = false
automatic_text_change_allowed = false
timing_mutation_performed = false
```

Acoustic schema 1.4 同时审计 slope 与 source-start 搜索边界；命中/接近任一搜索边界的 optimum 只能作为 diagnostic，不参与 timing fusion。ASR 只在 canonical-local language 与已知 source language 一致时固定语言；code-switch/mixed/unknown/source-auto 保持 backend auto-detect。

## 5. Max — Full V4 Alignment

Max 是 heavy fallback，用于整体 timeline/mapping 不可信、复杂 cut/overlap/reorder 或 Smart/Pro 无法安全收敛的任务。当前 primary chain 包括 TrackAsset、coarse/Fine/TimeWarp、canonical projection、transition/cut/overlap/review 等完整 reconstruction evidence。

### 5.1 Primary coarse terminal coverage

#68 允许结构上有界的 terminal disconnect 保留已证明 prefix：

- 断点前至少三个连续 anchors；
- 只允许 terminal suffix；
- suffix 上限由现有 window/step 结构决定；
- leading/interior disconnect、超限、证据不足仍 hard fail；
- `path_coverage` 记录 selected/excluded coverage；
- excluded suffix 不获得 affine extrapolation timing authority。

Shared-boundary transition activity 使用 retrieval-only purpose，保留完整 windows 但不生成 TimeWarp。该机制不确认 transition/outro/cut/overlap，也不改变 transition review threshold。

### 5.2 Projection/content-integrity gate

若 canonical timeline 报告：

```text
projection_coverage.authority_omitted_line_count > 0
```

则 composer 拒绝 render。被 proven coverage 排除的 canonical 内容可以留作 unresolved evidence，但不能被静默丢失后生成“完整 final”。malformed/negative omitted count 同样 fail closed。

### 5.3 Evaluation render != production release

当前 `scripts/v4_render.py` 仍直接 materialize canonical timeline lines，因此它现在被明确定位为 **evaluation renderer**：

```text
publish_ready = false
segmentation_authority = canonical_line_evaluation_only
release_blocked_reason = editor_cue_reconciliation_required
```

`ready_for_render` 只表示 reconstruction/review 已足以生成结构/评估输出，**不等于 publish_ready**。

V4 release validator 必须看到唯一、hash-bound 的 final-render artifact 明确声明 `editor_reconciled`，并且 production authority 必须在三层一致：

```text
final_render.normalized_config.segmentation_authority = editor_reconciled
final_render.evidence.segmentation_authority          = editor_reconciled
final_render.evidence.publish_ready                   = true
exact QA.segmentation_authority                       = editor_reconciled
exact QA.publish_ready                                = true
```

artifact evidence 或 QA 只要仍有非空 `release_blocked_reason`，release 也必须失败。不能出现“config 已 production，但 evidence/QA 仍 evaluation-only”的半升级状态。

没有这组一致 authority 时，production release 必须失败。人工清完 transition/cut/overlap review 也不能自动获得该 authority。

### 5.4 Editor-Cue Reconciliation evaluation bridge

已新增首版 evaluation-only bridge：

```text
lyric_aligner/timeline/editor_cue_reconcile.py
scripts/v4_editor_cue_reconcile.py
```

它只消费 #70 的 `canonical_line_evaluation_only` final-render artifact，并与 task manifest 中 exact source/editor SRT 对照；不重新推导 Max timeline，不修改 editor cue count/number/start/end，也不生成 production SRT。

逐 editor cue 状态：

```text
resolved       -> canonical interval(s) 完整落入唯一 editor cue，且同 cue 内 canonical material 不互相 overlap
still_review   -> canonical 跨 editor boundary、落入多个重叠 editor cue，或同 editor cue 内 canonical material overlap
rebutted       -> schema 保留；首版不自动产生
not_evaluable  -> 没有 canonical temporal evidence
```

输出 stage：

```text
editor_cue_reconciliation_evaluation
segmentation_authority = editor_reconciliation_evaluation_only
production_authority_granted = false
```

`full_topology_candidate=true` 也**不**等于 production authority。它只表示在当前 evaluation render 下，所有 canonical cue 可以不改变 editor cue topology 地获得唯一 ownership，并且 editor SRT 文件时间顺序单调。若 editor file order 有时间回退，单 cue 诊断仍保留，但 `full_topology_candidate=false`。

因此当前 Max 应描述为：**强 reconstruction/evidence engine + canonical evaluation renderer + editor-topology reconciliation evaluator；production subtitle path 仍未闭环。** 下一步必须先用私有任务验证 evaluator 的 coverage/review 分布，再设计真正可授予 `editor_reconciled` 的 materialization contract；不能直接把 evaluation result 改名为 production authority。

### 5.5 Artifact-writer path safety

Max 下一步实际会使用的 review/materializer/render/reconciliation/release CLI 共享 fail-closed 输入所有权 contract：

- task manifest 与所有 manifest-bound files 都是 protected inputs；directory input 保护整棵 subtree；
- review 保护 run/run artifact/decisions；
- cut/overlap/combined materializer 在首次 `mkdir`、Fine 子进程或 write 前保护直接输入及 run payload 递归声明的全部 `*_path` lineage；`--out-dir` 与这些输入必须双向不相交；
- materializer 的公开 `v4_*.py` 是唯一支持的 CLI entrypoint；原算法 blob 以 `_v4_*_impl.txt` internal source resource 保存并由安全 wrapper 加载，不作为第二套 CLI；
- render 额外保护 TrackAssets/asset artifact，以及 run 实际读取的每个 canonical timeline/timeline artifact；四个 render outputs 必须彼此不同；
- reconciliation 保护 canonical evaluation SRT/audit/QA/final-render artifact；
- release manifest 不能覆盖 final SRT/audit/QA、upstream artifact 或任一 task input。

所有 collision 在第一次 materialization 前 fail closed。该机制只保护文件 ownership，不改变 review、cut/overlap、timing、text、segmentation 或 release authority。

Release/reconciliation 对 `review_candidate_count` 要求真正 JSON integer `0`；render eligibility 的 review/cut/overlap/combined count 同样不能靠 Python coercion。完整 CLI 规则见 `references/v4-cli-contract.md`。

### 5.6 Production orchestration output-tree safety

`v4_run.py`、`v4_run_optimized.py` 与 `v4_run_legacy.py` 现在也受同一 output-tree ownership contract 约束，而且检查发生在 orchestration 的第一次写操作之前：

- canonical `v4_run.py` 在 `OutputRunLock` 创建 output directory / `.v4-run.lock` 前检查；
- direct optimized entrypoint 在 `cache/`、verified-input session 或 stage directory 创建前检查；
- direct legacy entrypoint 在 `assets/primary/transitions/timelines` 创建前检查；
- task manifest、全部 manifest-bound input roots/subtrees，以及显式 profile/language/middle-cut/lyric-role config 都是 protected inputs；
- `--out-dir` 不得位于 protected input 内，也不得反向包住 protected input。

Legacy/optimized orchestration implementation blob 保持不变，仅由安全 public wrapper 在 preflight 后进入。该 gate 不改变算法或任何 readiness/release authority；它只阻止 run orchestration 自己污染已经 fingerprint 的输入树。

## 6. Legacy Partial Timeline Repair

旧 P1–P5 bridge 继续固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

Smart/Pro/Max 不借用 legacy P9/P4 flags 获得新的 mutation authority。

## 7. 验证与隐私边界

Public CI 必须继续证明：

- lower-mode segmentation monotonicity；
- Smart text recovery 不提升 timing authority；
- Pro no-write contract；
- Max bounded terminal coverage 只缩小/记录 authority，不扩张；
- omitted canonical lines 不能静默 render；
- canonical-line Max output 不能通过 production release gate；
- production release 的 final-render config/evidence/exact QA authority 必须一致；
- reconciliation evaluator 不移动 editor boundaries、不自动产生 `rebutted`、不授予 production authority；
- Max artifact writers/materializers 不覆盖 task/upstream/lineage inputs，动态 output tree 不得包住输入；
- canonical / optimized / legacy 三个 run entrypoint 在 output tree 与 task/config inputs 相交时必须在首次写入前失败；
- malformed release/evaluation QA types fail closed；
- artifact/task/version/hash lineage 完整；
- Python/ASR environment 与 legacy regressions 不回归。

真实任务失败模式只能转化成 generic synthetic regression；不得把私有歌曲名、歌词、cue 编号、真实时间戳或音频写入 production algorithm/public tests。

## 8. 冻结 Smart/Pro

Production freeze tag 保持不动：

```text
prod-smart-v1.2.5-pro-v1.1.4-20260821
56841c40d6a90101efe1da568e2d5c2e5e67a0a2
```

后续 Max 工程不得移动或重写该 tag。
