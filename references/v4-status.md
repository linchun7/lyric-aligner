# Lyric Aligner v4 当前实施状态

更新日期：2026-09-03
主线算法版本：`4.0.0a14`

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

### 5.0 a14 task-local semantic run config

`4.0.0a14` 起，新任务由 `init_task.py` 同时创建 `qa/v4_run_config.json`。它不替代 raw-input `task_manifest.json`，而是单独绑定后补且会改变 Max asset-resolution 语义的 `profile / language_map / middle_cut_map / lyric_role_map`。config 自身绑定 exact task fingerprint，并为每个非空语义文件记录 repository-relative path、size 与 SHA-256，再生成独立 `run_config_fingerprint_sha256`。

`v4_run.py`、direct optimized 与 direct legacy 三个 public run entrypoint 都会在第一次 output mutation 前自动发现 task-local config、验证 task/file identity，并把缺失的语义参数展开到既有 production parser。调用者显式参数与 config 路径不一致、config 记录 null 却临时塞入新 map、被绑定文件内容变化或 config 绑定了另一 task 时全部 fail closed。不存在 config 的 legacy task 保持原显式 CLI 兼容；旧任务可用 `scripts/init_v4_run_config.py` 有意识迁移，语义变化必须显式 `--replace`。

该层解决“同一 task 因调用者漏传 language/role map 而得到不同 raw run”的可复现性问题，不修改 Source-to-Mix、Fine、transition、review 或 release threshold。正式 asset artifact 继续记录实际 profile/map SHA，因此 production lineage 仍由真实语义输入身份约束；workers/resume/out-dir/git metadata 属于执行策略，不进入 run config。

### 5.1 Primary coarse terminal coverage

#68 允许结构上有界的 terminal disconnect 保留已证明 prefix：

- 断点前至少三个连续 anchors；
- 只允许 terminal suffix；
- suffix 上限由现有 window/step 结构决定；
- leading/interior disconnect、超限、证据不足仍 hard fail；
- `path_coverage` 记录 selected/excluded coverage；
- excluded suffix 不获得 affine extrapolation timing authority。

Shared-boundary transition activity 使用 retrieval-only purpose，保留完整 windows 但不生成 TimeWarp。该机制不确认 transition/outro/cut/overlap，也不改变 transition review threshold。

Max run 同时区分物理 `mix_duration` 与保守 `content_end`。只有尾部至少 30 秒解码样本**精确为数字 0**时，`content_end` 才缩到最后一个非零样本之后；普通 fade/近静音/底噪不会被裁掉。该值只约束最后 occurrence 的 production interval/terminal clamp，完整容器时长继续保留作 provenance。

`4.0.0a13` 起，若任务存在经过 QA 明确证明的 detached export tail（例如主节目结束后出现长数字零区间，再出现短小孤立音频残片），可把 `mix_content_extent` JSON 作为可选 task input 纳入 fingerprint。该 override 必须绑定同一 audio SHA、提供非空 reason，并且**只能缩短**自动 `content_end`，不能延长；未提供该输入的任务保持原自动判定。该机制保留原音频文件与物理时长，不通过复制/截短 mix 绕过 provenance。

`4.0.0a12` 起 coarse/Fine 只解码当前请求的 mix interval 加 2 秒上下文，而不是把长 mix 作为完整工作波形。压缩容器到达物理尾端时，若实际可解码终点与声明终点只差不超过 5ms，可保守 clamp 到真实终点；中段 short-read、更大的尾差以及未覆盖请求区间的 decode 仍 hard fail。该优化不改变 retrieval window、candidate pool、TimeWarp、review 或 release authority。

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

`full_topology_candidate=true` 仍**不**等于 production authority。它只表示在当前 evaluation render 下，所有 canonical cue 可以不改变 editor cue topology 地获得唯一 ownership，并且 editor SRT 文件时间顺序单调；保留 editor topology 的 production materializer 仍需独立实现。

私有长混剪验证暴露了另一类可严格证明的情况：editor SRT 可能只是稀疏/错误识别出来的时间子集，存在完整 timed canonical cue 与任何 editor cue 都没有时间交集。此时“不移动/不新增 editor cue”与“canonical lyric 完整性”在逻辑上不能同时成立。`v4_materialize_editor_reconciled.py` 因此只增加一个窄 production path：它必须消费 exact hash-bound canonical evaluation render + `editor_cue_reconciliation_evaluation`；要求 `full_topology_candidate=false`、至少一个 `no_editor_temporal_overlap` canonical witness、reconciliation assigned/unassigned/status 计数闭合，并且最终 audit 每一行都来自显式 timed `line_lrc / enhanced_lrc / qrc_word_timing`。editor file order 正常情况下仍要求单调；若存在相邻逆序，只有所有 inversion 都满足 `right.end_ms <= left.start_ms`、即文件顺序错位但时间区间互不重叠时，evaluation 才标记 `editor_file_order_recoverable_nonoverlap_reordering=true`，rebuttal materializer 才可继续。任一逆序存在时间重叠仍 fail closed；`full_topology_candidate` 仍只允许单调文件顺序。满足这些条件时，reconciliation 结论为全局 `rebutted`，exact canonical SRT/audit 可被提升为 `editor_reconciled` production segmentation；否则继续 fail closed。

该 materializer 不修改 canonical text/timing，也不把普通 `canonical_interval_crosses_editor_boundary` 当成 topology rebuttal 证据。它生成新的 production QA/final-render artifact，并在 `normalized_config`、artifact `evidence` 与 exact QA 三层同时声明 `editor_reconciled` / `publish_ready=true`；原 canonical evaluation artifact 仍保持 evaluation-only。`v4_validate_release.py` 不做例外处理，仍按既有三层 production-authority contract 验证。

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

### 5.7 Production display policy

Production timing/segmentation authority 与 viewer-facing presentation 现在明确分层。`scripts/v4_apply_display_policy.py` 只消费已经 `editor_reconciled`、`publish_ready=true` 的 production final-render；cue count/number/start、occurrence identity、track identity 与 canonical line identity 全部冻结。默认不改 timing；只有显式启用 `trim_extreme_unknown_end_v1` 时，允许对 `next_line_start` 推导出的极端长未知 end 做 shorten-only display trim，绝不延长 end 或移动 start。

Canonical lyric 继续作为文字/顺序 evidence truth，不因平台敏感词处理或模型高置信 typo 修订而被覆盖。显式模型修订必须 task-bound，并精确绑定 `occurrence_id + track_id + canonical_line_index + expected_text`；只有 `confidence=high` 才可 materialize，原文不匹配、override 未命中或命中不唯一均 fail closed。输出 audit 同时保留 canonical/display 两层文字、source/display start/end 与 policy/reviewer/reason provenance。

内置 `strong_profanity_v1` 只自动处理明确强脏词（例如 `fuck/fucking -> f*`）。语境相关词如 `sexy`、`shot`、`bullet`、`kill`、`damn` 不自动改写，必须经模型/人工语境判断。`trim_extreme_unknown_end_v1` 只接受 `source_end_basis=next_line_start`，且 `max_display_hold_ms` 必须严格小于 source-duration trigger；`open_end` 和显式 timing 不可被该规则改写。display stage 生成新的 hash-bound `final_render`，继续保持三层 `editor_reconciled` / `publish_ready=true`，随后仍由原 `v4_validate_release.py` 正常验收；release gate 不增加例外。

`4.0.0a10` 补齐无-overlap confirmed-cut 的正式 reference-retime 路径：`scripts/v4_retime_reference.py` 仍只接受已完全 resolved、`ready_for_render`、非 legacy 的 source run，但 source stage 可为 `review_resolution` 或原有 `overlap_recomposition`。直接从 `review_resolution` 进入时，source review artifact 必须就是 source run artifact 自身；从 overlap 进入时仍验证 overlap metadata 中的 source-review identity。`4.0.0a11` 补齐 renderer 的对应 source-stage 分支：reference-retimed run 不再无条件按 overlap materialization 验证，而是沿已验证的 `source_run_stage` 继续；review 来源无需不存在的 overlap metadata，overlap 来源仍保持原严格校验。retained-segment 的删行/截断/fail-closed 语义不变。

### 5.8 Diagnostic final-candidate audit

新增 `lyric_aligner/qa/final_candidate_audit.py` + `scripts/v4_audit_final.py`，把此前各私有任务重复做的成品结构审计抽成通用只读 QA。它不会生成 production artifact、不会修改 SRT、不会授予任何 authority；只在 SRT/report exact binding 与 publish-ready QA 基础上检查 final file order、cue duration 分布、occurrence-window containment、`content_end`、以及 same/cross-occurrence overlap。跨 occurrence overlap 必须完整位于 run 已物化的 confirmed-overlap region 才允许；长驻留只产生 presentation warning。audit output 同样受 task/direct/run-declared `*_path` 输入保护，不能覆盖实际 timeline 等 lineage input。

### 5.9 Private calibration baseline

2026-09-02 首轮 r1 private benchmark 已用于验证 workflow，但随后确认其 reference/prediction authority 混合了旧人工 segmentation 与已人工闭合 production 结果，因此只保留为 exploratory history，不再用于正式 candidate selection。

正式基线已升级为 `2026-09-02-r2-auto`：仍为 8 个 opaque case（4 calibration / 4 blind_test）、8 个 source_group 严格隔离，但 reference 固定为已验收 pre-display production SRT，prediction 固定为 raw `v4_run` per-occurrence timeline 按 authoritative occurrence window 物化，禁止应用人工 review、overlap recomposition、reference-retime、editor reconciliation 或 display override。该 baseline 已生成独立 lock；blind prediction/QA 仍未 materialize，blind metrics 仍未读取。

r2 calibration aggregate 为 `unit_f1=0.999221`、`line_exact_f1=0.999167`、`cue_text_exact_match_rate=1.0`、`boundary_mae_ms=17.982`、`boundary_p95_ms=6.0`。4/4 calibration case 的 raw Max SRT 基本已与 production truth 重合，但每个 case 仍保留 1 个 review candidate，`publish_ready_rate=0`；该 calibration 中被人工确认的结构事件在 raw Max 阶段仍有 `cut_recall=0` / `overlap_recall=0`。因此当前主要瓶颈已从普通歌词 timing 转为结构事件与 review authority。

首个 transition Fine-anchored 多尺度自动降噪候选已在 calibration 阶段淘汰：2/2 已确认真实 overlap 均被错误建议为 sequential clear；进一步加入 aligned dual-source STFT/NNLS mixture-gain 证据后，clear 与 overlap 的分数仍明显重叠，无法形成安全阈值。该实验未进入 production/public code，也未触碰 blind。后续不得通过继续堆同源 retrieval 阈值来自动 clear transition；新 candidate 应由 r2 calibration 的结构事件 error breakdown 驱动。

第二个 candidate 使用用户工作流中的 prepared stem 做 same-track splice 正诊断。它在真实 calibration 上能自动发现一例约 6 秒 source-offset handoff，并把 `cut_precision/recall` 从 `0/0` 提升到 `1/1`，因此曾以 commit `1dbf82b` 进入 public candidate。随后发现原 r2 blind manifest 的结构标签已经在人工审计时暴露，原 4 个 blind case 被永久降级为 quarantine，不再用于正式 gate；重新锁定的 r3 fresh blind 在 candidate selection 前固定 8 个未见 synthetic structural case，并在 prediction materialize 后首次执行 gate。结果 candidate 在 fresh blind 上 `cut_precision=0`、`cut_recall=0`，未达到预先设定的 precision=1.0 / recall>=0.75 门禁。按 blind 纪律不再针对该结果调阈值，prepared-stem public core/CLI/test 撤回；private A/B 证据保留用于避免重复走同一路线。

P1 结构 benchmark 现在复用上述 strict workflow，并把 case-level 结构真值显式标准化为 `structural_scenarios`。schema `1.1` 支持 `none / hard_cut / same_track_splice / crossfade / true_overlap / sequential_transition / piecewise_rate / reorder / detached_tail`；schema `1.0` 不接受该字段且保持旧 report shape。1.1 显式标签 canonical 排序后进入 ground-truth identity，未标注 1.1 case 只在 report 中归入 `structural:unspecified`，因此不会重写既有锁。strict evaluator 同时输出 `language:*` 与 `structural:*` aggregate scope；这些标签只用于 evaluation/gating，不增加任何生产 timing authority。

冻结 r3 复放继续得到 calibration SHA `737e83697f1e577bbf9c8473e21b54ad304c33d1c6f09404fc45abe10853e330`、blind SHA `2e9c49321ac3541d2d5f3fdb953ddbdecab1f0c09f3ed80a6249aae83bbdc886`；去除新增 structural-only report 字段后，新旧两 split 评估递归全等。

P1 已进一步增加 evaluation-only typed structural-event contract。point event 为 `hard_cut / same_track_splice / sequential_transition`，按 `time_ms` 容差做 maximum-cardinality/minimum-error matching；interval event 为 `crossfade / true_overlap / piecewise_rate / reorder / detached_tail`，按区间 IoU 做 maximum-cardinality/maximum-IoU matching。truth 使用 `expected_structural_events`，prediction 使用 `predicted_structural_events`；typed prediction 若没有预先冻结的 expected list（负例也必须显式为空）立即 fail closed。expected events、point tolerance 和 interval IoU threshold 进入 ground-truth identity，prediction 不进入。strict evaluator 现在可在 overall/language/structural scope 输出 event precision/recall/F1、FP/miss、point MAE 和 interval mean IoU，且不把 event 位置写入公开 evaluation。

冻结 r3 在 typed-event 层再次精确复现同一 calibration/blind SHA；历史 r3 没有 event truth 字段，因此 `structural_event_annotation_case_count=0`、`clean_case_count=0`，不会被误当成新 event-level clean truth。剥离新增 `structural_event_*` 指标后，新旧 r3 评估仍递归全等。

随后按该方法学完成 r4 `reorder / detached_tail / none` 研究闭环。真实 calibration 使用独立 production/QA truth，并把评估文本载体改为 opaque SRT；`reorder` detector 只允许已有 source/occurrence mapping authority 的 editor cue 建立或触发 chronology frontier，unmapped overlay/口播不能单独获得 reorder authority；`detached_tail` detector 只读取长 exact-zero gap 后重新出现的短孤立 active island。5-case calibration 在预先固定 gate 下得到 typed-event precision/recall/F1=`1/1/1`、3/3 negative controls clean、interval IoU=`1.0`，candidate revision 锁为 `11b2443c59aa5a14b8b1c8950a9eaf0c103fc6f48d958711208bc7f3ad5c5183`。

candidate selection 与 blind policy 均在读取 blind metrics 前冻结；随后首次且唯一一次 materialize 12 个 fresh locked blind case（4 reorder / 4 detached-tail / 4 none）并执行 gate。结果 overall typed-event precision/recall/F1=`1/1/1`、interval mean IoU=`0.999696`、`structural:reorder` recall=`1.0`、`structural:detached_tail` recall=`1.0`、`structural:none` clean-case rate=`1.0`，blind gate PASS。该 r4 blind 从首次 observation 起永久禁止用于后续 threshold tuning；private case-level truth/prediction 继续不进入 public repo。该 fresh-blind 结果本身仍只证明 evaluation 泛化，不自动授予 Max timing/review/release authority。

fresh-blind 通过后只提升到新的只读 `scripts/v4_audit_structural.py` QA bridge，而不是直接接入 Max mutation authority。`detached_tail` 可直接对 task-manifest 绑定音频做 diagnostic；`reorder` 必须额外提供 `v4-editor-source-map-1.0`，并要求 `mapping_authority=source_occurrence_verified`、同 task fingerprint、同 editor SRT SHA，以及 repository-relative 的上游 source-mapping artifact path + 现场 SHA 复核；缺 authority 时明确 `not_run_missing_source_mapping_authority`。该 bridge 固定 `diagnostic_only`，并显式禁止 automatic timing/content_end/review resolution、release-gate eligibility 和 publish-ready。真实六项任务回归得到：190 唯一 1 个 reorder、Walk120 唯一 1 个 detached-tail；快乐健走140、KPOP110、KPOP130、KPOP200 均 0 structural event，且无 source map 的任务 reorder 均明确不运行。该结果只建立生产 QA evidence，不授予自动修复 authority。

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
