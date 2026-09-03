# Lyric Aligner v4 关键变更记录

> 2026-08-22 PR #70 前的完整当前记录已无损归档到 `references/archive/2026-08-22-pre-max-authority-v4-change-record.md`。P3 前更早历史仍见 `references/archive/2026-08-19-pre-p3-v4-change-record.md`。生产设计基线见 `references/production-requirements.md`。

## 当前产品责任分层

```text
Standard = Text Repair V2.1
Smart    = Canonical Sequence Reconciliation + Anchor Timeline Repair v1.2.10（no-audio）
Pro      = Selective Audio Repair v1.2.6（局部 audio evidence；no auto write-back）
Max      = Full V4 Alignment（完整 audio / heavy fallback）
```

共同 authority：

```text
Canonical lyric -> final text/order truth
Jianying timing / cue boundary -> strong but rebuttable prior
LRC line break -> grouping/onset evidence, not final subtitle segmentation authority
Timed canonical -> primary no-audio timing evidence for Smart
Source-to-Mix -> primary acoustic timing truth for Pro/Max
ASR / forced -> auxiliary acoustic evidence
```

---

## 2026-09-03 — Calibration-only ablation review

完成一轮只使用 calibration truth 的 Max 消融，未读取未观察 blind truth。325 个 coarse windows 上 Chroma-only 与当前 fused top-1 不同 `47/325 (14.46%)`，MFCC-only 不同 `28/325 (8.62%)`，因此双特征融合保留。K110 完整 no-Fine 全链在 composer 层保持 17/17 occurrence 可见 cue identity 完全一致，但 strict calibration reference 评分从 full-Fine 的 boundary MAE/P95 `16.265/7 ms` 退化到 `22.939/25 ms`；文本/行级指标不变。结论：Fine 对 timing tail quality 有明确贡献，本轮否决删除 Fine，也不基于当前有限 corpus 新增 skip/selective gate。

Piecewise TimeWarp 当前缺独立 `piecewise_rate` calibration 正例，不能用“现有样本都选 AFFINE”反推其无用；该能力继续保留。完整实验方法、指标与清理结论见 `references/ablation-review-2026-09-03.md`。

## 2026-09-03 — Legacy diagnostic helper retired

`scripts/karaoke_subtitle_pipeline.py` 的 1569 行 pre-v4 diagnostic/draft 实现已退役。仓库内没有当前代码、测试或权威工作流依赖该实现，且其旧 SRT/LRC/ASR authority 规则与当前 Standard/Smart/Pro/Max contract 不等价；继续保留可执行实现只会形成第二套入口。为避免旧命令静默生成 authority 不明的字幕，文件名保留为 fail-closed 迁移提示：`--help` 展示当前工作流映射，其余调用退出 2。历史实现仍可从 Git history 恢复，不做自动语义转发。

同时统一评估文档入口：`references/dataset-protocol.md` 不再把低层 `evaluate_dataset.py` 展示为生产验收命令，正式 calibration/blind-test 统一指向 `v4_calibration_workflow.py` strict contract；新增 `references/local-artifact-retention.md`，明确本地 `private/output/cache` 的 KEEP / ARCHIVE / REGENERABLE 边界，禁止按版本号或目录新旧直接删除 truth、release 或生产证据。

## 2026-09-03 — Structural truth-discovery closeout

在 r4 QA bridge 完成后，没有直接继续实现 `piecewise_rate / hard_cut / true_overlap` detector，而是先对当前 real corpus 做独立 truth-discovery。`piecewise_rate` 最强候选使用原曲与最终调速 stem 直接做局部 source↔adjusted rate 测量，不读取 Max timeline/Fine/TimeWarp：10/10 预设窗口均可靠，local-rate 总跨度 `0.0125`，最强相邻两-regime split 仅差 `0.005`，低于预先冻结的 `0.015` truth threshold，因此制作上“手工调 BPM”不能被扩大解释为 benchmark-level piecewise-rate truth。

`hard_cut` 最强候选已有历史人工确认 source omission，但新的 task-bound waveform-only 双 branch audit 在结果读取前固定窗口/步长/rate/score/dominance/dual-support 门槛；执行后稳定左右 dominance、abrupt switch、material-dual-support-absent 四项均未满足，并出现约 `0.40s` ambiguous dual-support。由于同曲重复结构可造成 branch 高相似，这既不证明 crossfade，也不证明 abrupt cut；该 evidence 只保留 confirmed omission 身份，不升级为 hard-cut truth，且不调 threshold 重跑。

`true_overlap` 则核对了当前 real corpus 全部 3 个 production `confirmed_overlap` decision；独立人工 rationale 分别明确写为 `Confirmed crossfade` / `Confirmed short crossfade` / `Confirmed crossfade`。因此它们继续构成 `crossfade` truth，不得为了补 taxonomy 覆盖而重标为 non-crossfade `true_overlap`。结论：当前 corpus 对这三类均仍是 truth gap；在出现新的独立制作/编辑 truth 之前，不实现、不调参对应 detector，研究优先级转为 event-driven real failure、regression/provenance/release-authority hardening。

## 2026-09-03 — Read-only structural evidence QA bridge

fresh-blind 通过的 `reorder / detached_tail` detector 没有直接进入 Max mutation path，而是先增加 `lyric_aligner/qa/structural_evidence.py` + `scripts/v4_audit_structural.py` 只读生产 QA bridge。输出固定 `authority=diagnostic_only`，同时明确 `automatic_timing_change_allowed=false`、`automatic_content_end_change_allowed=false`、`automatic_review_resolution_allowed=false`、`release_gate_eligible=false`、`publish_ready=false`；发现事件不会改变 production run、review 或 release。

`detached_tail` 只读取 task manifest 已绑定音频；`reorder` 只有在调用方显式提供 `v4-editor-source-map-1.0` 且 map 绑定同 task fingerprint、同 editor SRT SHA、`mapping_authority=source_occurrence_verified` 和 repository-relative 上游 source-mapping artifact path + 现场 SHA 校验时才运行。缺少 map 时明确 `not_run_missing_source_mapping_authority`，不会退回 raw-SRT heuristic；map 路径逃逸、artifact 缺失/SHA drift、cue-count/position 异常均 fail closed，上游 mapping artifact 同时进入 output collision protection。

六项真实 production regression：190 使用冻结 Smart `timing_decisions.source_ordinal` 作为 hash-bound mapping authority，仅报告 1 个已知 reorder、0 detached-tail；Walk120 不授予 reorder mapping，但报告 1 个已知 detached-tail；快乐健走140、KPOP110、KPOP130、KPOP200 均报告 0 structural event，且无 mapping authority 的 reorder 全部明确不运行。该阶段只证明 QA evidence 可安全泛化，不授予自动修复 authority。

## 2026-09-03 — P1 r4 fresh-blind structural detector evidence

在 typed-event metric contract 冻结之后，新增 evaluation-only `lyric_aligner/evaluation/structural_detectors.py`，只研究两个已有独立 truth 的正交结构事实，不接入 production Max authority。`reorder` 不再用“任意 SRT 文件顺序回跳”作为真值，而要求已有 source/occurrence mapping authority 的 editor cue 才能建立/触发 chronology frontier；unmapped overlay/口播不能单独生成 reorder。`detached_tail` 只检测后段长 exact-digital-zero gap 后重新出现的短孤立 active island。

r4 calibration 使用 2 个真实正例（190 reorder、Walk120 detached tail）和 3 个显式 negative controls，opaque SRT 仅作评估载体；真实 production SRT/audio/QA/Smart mapping 通过 SHA 绑定 input/truth。candidate revision `11b2443c59aa5a14b8b1c8950a9eaf0c103fc6f48d958711208bc7f3ad5c5183` 在 calibration 上得到 typed-event precision/recall/F1=`1/1/1`、negative clean rate=`1.0`、interval IoU=`1.0`，随后 selection payload `71c5f5ab9b40603a095b526dea016435ceffcff49a0183038a3d0f3c2ff38745` 被锁定。

在读取任何 blind metric 前，fresh blind policy 同样冻结。12 个一次性、隐藏 case（4 reorder / 4 detached-tail / 4 none）首次 materialize 后执行唯一一次 gate：overall precision/recall/F1=`1/1/1`、interval mean IoU=`0.999696`，两类 positive recall 均为 `1.0`，`none` clean-case rate=`1.0`，gate PASS；blind gate payload SHA=`53bef3698a1dc60da6d3aa605c4e126aea5cea231dc6b680ef95e8c044fcbe04`。该 r4 blind 自 observation 起永久不可用于继续调 threshold。此结果只证明 evaluation candidate 泛化，不直接修改 coarse/Fine/TimeWarp、transition/review/release authority。

## 2026-09-03 — P1 typed structural-event evaluation contract

在 taxonomy/reporting foundation 之后继续补齐“事件是否真的被检测到”的 strict gate 能力，而不是直接写新 detector。schema `1.1` 可选 `expected_structural_events / predicted_structural_events`：point event (`hard_cut / same_track_splice / sequential_transition`) 使用 `kind + time_ms`，interval event (`crossfade / true_overlap / piecewise_rate / reorder / detached_tail`) 使用 `kind + start_ms + end_ms`。point 采用单调 maximum-cardinality/minimum-total-error matching，interval 采用单调 maximum-cardinality/maximum-total-IoU matching。

Expected events 及其 `structural_event_tolerance_ms / structural_event_min_iou` 属于 truth-side identity；predicted events 永不进入 ground-truth SHA。prediction-only metadata、truth/scenario 不一致、`none` 配非空 truth、错误 shape、重复事件、非法时间/阈值全部 fail closed。strict evaluator 新增 aggregate `structural_event_precision / recall / f1`、FP/miss、point MAE、interval mean IoU；evaluation 输出仍只保留 aggregate/opaque case，不泄漏事件位置。

兼容验收：冻结 r3 calibration/blind SHA 分别仍为 `737e83697f1e577bbf9c8473e21b54ad304c33d1c6f09404fc45abe10853e330` / `2e9c49321ac3541d2d5f3fdb953ddbdecab1f0c09f3ed80a6249aae83bbdc886`，旧 case 的 typed-event annotation/clean counts 均为 0；剥离新增 `structural_event_*` 指标后，旧/新两 split 递归全等。另新增 privacy-safe coverage map `references/structural-benchmark-coverage.md`，确认下一阶段优先构造 `reorder / detached_tail` 的 typed calibration 与全新 locked blind，不复用已观察 r3 blind，不先实现 detector。

## 2026-09-03 — P1 structural benchmark taxonomy / reporting contract

结构算法继续研究前先补齐评估层，而不是继续调 ordinary timing threshold。既有 strict calibration / locked blind workflow 保持唯一方法学主线；dataset schema `1.1` 新增可选、truth-side `structural_scenarios`，canonical 类别固定为 `none / hard_cut / same_track_splice / crossfade / true_overlap / sequential_transition / piecewise_rate / reorder / detached_tail`。1.1 未标注 case 只在报表层归入 `structural:unspecified`；显式标签 canonical 排序后进入 ground-truth identity，非法值/重复值/`none` 混用 fail closed。schema `1.0` 不接受该字段，并保持旧 report shape，不新增 structural scope。

公共 metric engine 与 canonical `v4_calibration_workflow.py` 现在都可按 `structural:<scenario>` 输出 aggregate metrics，cut-boundary metrics 同样支持 structural scope；这使后续 calibration policy 能区分不同结构失败类型，同时保持 opaque case IDs / aggregate-only privacy contract。benchmark 专用 `language=synthetic` 仅在 generic metric engine 内映射为 `generic` tokenization，不扩 production language profile。该变化只扩 evaluation metadata/reporting，不修改 Max 算法版本、coarse/Fine/TimeWarp、transition threshold、review 或 release authority。

冻结 r3 兼容回归精确复现 calibration SHA `737e83697f1e577bbf9c8473e21b54ad304c33d1c6f09404fc45abe10853e330` 和 blind SHA `2e9c49321ac3541d2d5f3fdb953ddbdecab1f0c09f3ed80a6249aae83bbdc886`；去掉新增 structural-only report fields 后，历史 calibration / blind evaluation 与新评估递归全等。

## 2026-09-03 — Max a14 task-local semantic run configuration

真实回归验收暴露出一个与 alignment threshold 无关的可复现性缺口：旧任务的 `language_map / middle_cut_map / lyric_role_map / profile` 虽然会被 asset artifact 分别记录 SHA，但调用者仍可能在另一次 `v4_run.py` 中漏传某个 CLI 参数，从而让“同一 task manifest”在没有显式迁移动作的情况下进入不同 asset-resolution 语义。典型表现是已固定语言的任务因漏传 map 回到 `auto`，或更严格的同时间戳 lyric-role preflight 因漏传 role map 重新 BLOCK。

`4.0.0a14` 新增 task-local `qa/v4_run_config.json`（schema `v4-run-config-1.0`），与 raw-input `task_manifest.json` 分层。它绑定 exact task fingerprint，并记录 `profile / language_map / middle_cut_map / lyric_role_map` 每个非空文件的 repository-relative path、size 与 SHA-256，同时计算独立 `run_config_fingerprint_sha256`。`init_task.py` 默认创建该配置；旧任务可用 `scripts/init_v4_run_config.py` 建立/迁移，已有语义变化必须显式 `--replace`。

canonical `v4_run.py`、direct optimized、direct legacy 三个 public run entrypoint 都会在第一次 output mutation 前自动发现并验证 task-local config，再把缺失 semantic flags 展开给原 production parser。显式 CLI 与 config 漂移、绑定文件变更、task fingerprint 不匹配、config 为 null 却临时增加新语义输入全部 fail closed；没有 run config 的 legacy task 保持旧 CLI 兼容。run config 本身也进入 output-tree protected inputs。该变更不修改 TrackAsset 选择算法、coarse/Fine/TimeWarp、transition/review/release threshold；正式 asset artifact 仍以实际 semantic file SHA 记录 lineage。

## 2026-09-03 — Max a13 explicit detached-tail content extent closeout

走路带风120真实任务暴露出自动 trailing-zero content extent 的边界：主节目在 `2727.582s` 结束，随后连续 `279.594s` 为逐样本 exact digital zero，但文件在 `3007.176s` 后又包含约 `6.526s` 的孤立音频残片。因为物理文件末端并非完整 trailing zero，旧自动规则必须保守保留整个容器，进而把最后 occurrence 错误扩到 50 分钟附近并在空白区产生 coarse disconnect。

`4.0.0a13` 不扩大自动 silence/island 推断，而是增加可选、fingerprint-bound 的 `mix_content_extent` task input。该 JSON 必须绑定同一 task audio SHA、使用 `mix-content-extent-1.0` schema、提供正且有限的 `content_end_seconds` 和非空 reason，并且只能把自动 `content_end` 往前缩；任何延长、SHA 不一致、schema/数值异常都 fail closed。原 mix 文件、SHA 与物理 `mix_duration` 保持不变。optimized/legacy Max runner 共享同一 override 语义；无该 input 的任务完全保持 a12 行为。新增 task-fingerprint 与 shorten-only/SHA-binding regression。

## 2026-09-03 — Max a12 bounded mix decode closeout

真实长音频任务暴露出 coarse/Fine 子阶段仍会围绕当前 occurrence 反复解码远大于实际检索窗口的 mix 区间，并且压缩容器在物理文件尾端可能出现少量“声明时长略长于实际可解码样本”的 short-read。`4.0.0a12` 将 coarse/Fine 的 mix 输入统一改为 conservative bounded decode：只解码当前检索区间加固定 2 秒上下文，不改变 source feature、candidate pool、coarse/Fine/TimeWarp 阈值或 review/release authority。

尾端 short-read 只在请求本身已经到达物理文件尾、且缺失不超过 5ms（另保留历史 one-sample rounding tolerance）时允许 clamp 到真实可解码终点；中段 short-read、超过容差的尾差、没有覆盖请求区间的 decode 仍 hard fail。调用方必须继续使用真实 `effective_mix_end`，不得用零填充或虚构尾部 timing。新增 synthetic regression 覆盖小尾差、mid-file short-read、大尾差和 one-sample compatibility；coarse/Fine/end-to-end/interval/content-end 回归保持原语义。

## 2026-09-03 — Max a11 reference-retime renderer source-stage closeout

真实 KPOP200 再次验证了 a10 的 direct-review reference-retime 入口本身可用，但 renderer 在验证 `reference_retime` run 后仍无条件把 materialization source 当成 `overlap_recomposition`，导致合法的 `review_resolution -> reference_retime -> render` lineage 被错误要求提供不存在的 overlap metadata。`4.0.0a11` 只修正这一处 renderer 分支：reference-retimed run 后续 materialization validation 现在使用已在 reference-retime metadata 中严格验证过的 `source_run_stage`；review 来源不再虚构 overlap lineage，overlap 来源仍执行原有 overlap metadata / artifact identity 校验。coarse/Fine/TimeWarp、review decisions、retained-segment 映射、cut/overlap 判定、render composition 和 release gate 均未改变。新增 source-stage regression，review/overlap 两条路径都显式覆盖。

## 2026-09-02 — Max a10 direct-review reference-retime closeout

真实 KPOP200 复跑暴露出一个 production contract 缺口：任务没有 confirmed overlap，13 个 transition review 已全部闭合，但历史 waveform + lyric context 已确认单曲内部存在 source cut，需要用 `retained_segments` 删除 cut gap 内 canonical event 并截断跨 cut cue。现有 `v4_retime_reference.py` 的映射能力已经能正确完成该操作，却把 source stage 硬性限制为 `overlap_recomposition`，迫使无-overlap任务先制造一个没有语义的 overlap stage。

`4.0.0a10` 只扩展 lineage 入口，不修改 retained-segment 映射、coarse/Fine/TimeWarp、cut/overlap 判定或 release gate：reference-retime source stage 允许 `review_resolution` 或原有 `overlap_recomposition`。直接从 review 进入时仍要求 source run `ready_for_render`、issues 为空、非 legacy，并把 source run artifact 自身作为 source review artifact；overlap 路径仍校验 overlap metadata 的 source-review identity。`v4_render.py` 同步按两种 source stage fail-closed 验证，未知 stage、review identity 不一致仍拒绝。新增 source-stage contract regression，原 overlap/reference-retime/release/path-safety 回归保持通过。

## 2026-08-22 — Max coarse terminal coverage / transition activity closeout (#68)

Full V4 primary coarse 可在已证明至少三个连续 anchors、且不可连接区域只位于结构上有界的 terminal suffix 时保留 proven prefix 并继续 TimeWarp；leading/interior disconnect、超限 suffix、证据不足仍 fail closed。coarse artifact 记录 `path_coverage` 与 excluded terminal centers；Fine 只消费与 proven path 对齐的 prefix。

这项恢复**不确认**尾段 source activity、cut、crossfade 或 overlap，也不授予超出 proven coverage 的 canonical projection authority。`bounded_terminal_disconnect` 只允许内部 mapping 继续求解。

Shared-boundary transition probe 使用独立 `transition_activity` purpose：保留完整 retrieval windows，但不请求连续 TimeWarp，输出 `path_coverage.status=retrieval_only` / `timewarp.selection=NOT_REQUESTED`。transition score/margin/ambiguity/review authority 不变；retrieval-only artifact 不能被当作 primary mapping。

随后 authority review 进一步要求：当 bounded terminal recovery 排除了 suffix 时，canonical projection 不能依靠 affine extrapolation 在该 suffix 获得普通 timing authority。该边界已经进入 projection artifact/lineage，并保持 complete-path 行为不变。

## 2026-08-22 — Max render/release authority fail-closed closeout (#70)

对 #68 后完整 Max 路径的独立复核发现两个产品 authority 漏洞：

1. canonical timeline 可能正确地把 proven projection coverage 外的行留作 unresolved；这些行不能被静默丢失后仍生成“正常 final”；
2. 当前 `v4_render.py` 直接把 canonical timeline line materialize 成字幕 cue，而产品合同明确规定 LRC line break 不是最终 subtitle segmentation authority。

本轮只收紧下游 authority，不改 reconstruction、transition、acoustic、ASR、forced alignment、Smart/Pro 阈值或 mutation 权限：

- `lyric_aligner/timeline/composer.py` 检查 `projection_coverage.authority_omitted_line_count`；非整数、负数或 `>0` 都 fail closed。partial-prefix timeline 可继续作为 upstream evidence，但不能静默变成缺行的 final subtitle。
- 当前 canonical-line renderer 明确降为 evaluation-only；QA/stdout/final-render artifact 写入：

```text
publish_ready = false
segmentation_authority = canonical_line_evaluation_only
release_blocked_reason = editor_cue_reconciliation_required
```

- `scripts/v4_validate_release.py` 除原有 task/version/hash/upstream binding 外，必须看到唯一 final-render artifact 明确声明：

```text
normalized_config.segmentation_authority = editor_reconciled
```

否则 release fail closed。完成 transition/cut/overlap review、甚至 run 已是 `ready_for_render`，都不能替代 segmentation authority。

因此 #70 合并后的 Max 能力边界是：**完整 reconstruction/evidence + evaluation render 可用；production subtitle release 仍需独立 Editor-Cue Reconciliation。** 下一步是单独实现 evaluation-only reconciliation bridge，而不是通过降低 transition/acoustic threshold 绕过 gate。

Public regression 全部使用 generic synthetic fixtures：覆盖 omitted-line render block、malformed coverage、canonical evaluation render、release lineage/segmentation gate，以及 review/cut/overlap/combined 路径不会误获 publish authority。私有歌词、音频、cue 编号与真实时间戳不进入仓库。

## 2026-08-23 — Editor-Cue Reconciliation evaluation bridge

新增 `lyric_aligner/timeline/editor_cue_reconcile.py` 与 `scripts/v4_editor_cue_reconcile.py`，用于评估 #70 canonical-line evaluation render 能否在**不改变原 editor cue topology** 的前提下回填 canonical text/order。

首版刻意不重新解析或重建 Max timeline，而是消费已经经过 task/version/hash/upstream binding 的 `final_render` evaluation artifact，再与 task manifest 中 exact `source_srt` 对照。这样 canonical timing/occurrence lineage 仍只有 #70 render 一套真源。

结构判定固定为 fail-closed：

- canonical cue 完整 interval 被唯一 editor cue 包含 -> 该 ownership 可标 `resolved`；
- canonical cue 跨 editor boundary -> 涉及 cue 均 `still_review`；
- canonical cue 同时完整落入多个重叠 editor cue -> `still_review`；
- 同一 editor cue 内被分配的 canonical cues 彼此 overlap -> `still_review`，禁止静默压平成单 cue；
- 没有 canonical temporal evidence 的 editor cue -> `not_evaluable`；
- `rebutted` 保留为 schema 状态，但首版**永不自动产生**，直到以后有独立 token/word/audio boundary evidence。

stage 输出：

```text
stage = editor_cue_reconciliation_evaluation
segmentation_authority = editor_reconciliation_evaluation_only
production_authority_granted = false
```

即使 `full_topology_candidate=true`、所有 editor cue 均为 `resolved`，也只代表“现有 editor topology 与 canonical render 在结构上可兼容”的评估结果；它不修改 `v4_render.py`、不生成 production SRT、不改变 `v4_validate_release.py`，也绝不等价于 `editor_reconciled`。

额外记录 `editor_file_order_monotonic`。若 source SRT 文件顺序存在时间回退，单 cue 诊断仍保留，但 `full_topology_candidate=false`，避免复杂 reorder 在首版被误当成已闭环 production segmentation。

Public synthetic regression 覆盖唯一包含、1 editor cue 承载多条非重叠 canonical cue、跨边界、重叠 editor ambiguity、canonical overlap、无 evidence、非单调 file order、audit identity，以及 CLI 对 source render authority / QA / artifact lineage 的 fail-closed 检查。

## 2026-08-23 — Max artifact-writer path safety / strict QA types

在进入私有 Max review/render/reconciliation 校准前，对公开 CLI 做输入所有权复核，确认 `v4_review.py`、`v4_render.py`、`v4_validate_release.py` 原先没有统一 output-path collision gate。误填输出参数时，理论上可覆盖 task input、run/artifact、timeline evidence 或 final 文件。

本轮只收紧 artifact writer 安全边界，不改任何 Smart/Pro/Max 算法、threshold、review action 或 segmentation/release authority：

- 新增共享 `protected_task_input_paths()`：保护 task manifest、所有 file inputs，并把 manifest directory input 展开到每个 fingerprinted 文件成员；
- `v4_review.py` 的 template/apply 在写入前保护 task inputs、run/run artifact、decisions，并保证多个输出互不重合；
- `v4_render.py` 在第一次 materialization 前同时保护 task inputs、run/TrackAssets/asset artifact，以及 run 实际读取的每个 canonical timeline/timeline artifact；四个 render outputs 必须 pairwise distinct；
- `v4_editor_cue_reconcile.py` 复用同一 shared task-path contract；
- `v4_validate_release.py` 保护 final SRT/audit/QA、所有 upstream artifacts 与 task inputs，release manifest 不得覆盖任何输入；
- release/reconciliation 的 `review_candidate_count` 不再使用 Python `int(...)` 宽松强转，`false`、float、string、null 都不能冒充整数 0；
- release upstream artifact 与 `normalized_config` 必须确实是 JSON object，畸形 artifact 受控 fail closed。

这些变更不产生新的 timing/text/segmentation authority。`canonical_line_evaluation_only`、`editor_reconciliation_evaluation_only` 和 `editor_reconciled` 三层语义保持不变。

CLI 安全契约集中到 `references/v4-cli-contract.md`，并加入文档同步 owner 集合。

## 2026-08-23 — Max release authority consistency hardening

继续复核 production release gate 时发现：`v4_validate_release.py` 已要求 `final_render.normalized_config.segmentation_authority=editor_reconciled`，但没有同时验证同一个 final-render artifact 的 `evidence` 与其 hash-bound QA 是否声明相同 production authority。若未来 materializer 产生内部自相矛盾的 artifact，单看 config 可能形成 false-ready。

本轮只收紧 release consistency，不新增任何 production authority：

- final-render `normalized_config.segmentation_authority` 仍必须是 `editor_reconciled`；
- final-render `evidence.segmentation_authority` 也必须是 `editor_reconciled`，且 `evidence.publish_ready=true`；
- exact hash-bound QA 同样必须声明 `segmentation_authority=editor_reconciled` 与 `publish_ready=true`；
- artifact evidence 或 QA 仍携带非空 `release_blocked_reason` 时 release fail closed；
- 任一层缺失、evaluation-only、not-publish-ready 或彼此矛盾都不能进入 release manifest。

当前 canonical-line renderer 和 reconciliation evaluator 均继续是 evaluation-only，因此行为保持 blocked；本变更只保证未来 production materializer 必须在 config/evidence/QA 三层形成一致、可审计的 authority contract。

## 2026-08-23 — Max cut/overlap/combined materializer output-tree safety

继续审查 review 后的 Max writer chain 时发现，`v4_rebuild_cut.py`、`v4_recompose_overlap.py`、`v4_compose_materializations.py` 会在 `--out-dir` 动态创建 Fine/mapping/timeline artifact，但此前没有像 review/render/release 一样的 output-tree ownership gate。错误的 `--out-dir` 可包住 task input 或已存在的 coarse/Fine/transition/timeline provenance，并在后续 `mkdir`/子进程/materialization 时污染或覆盖输入。

本轮只改变 CLI 文件所有权边界，不改变 materializer 算法：

- output tree 与所有 protected input 双向不相交；
- task input subtree、直接 run/artifact、TrackAssets，以及输入 payload 中递归声明的全部 `*_path` lineage 都在首次 `mkdir`/子进程/write 前保护；
- 三条公开 `v4_*.py` 变成薄安全 entrypoint；原 cut/overlap/combined implementation 以 blob-identical `_v4_*_impl.txt` internal source resource 保存，由通过 preflight 的 wrapper 以非 `__main__` 名称加载，不暴露第二个 `v4_*_impl.py` CLI；
- `--help` 保持原行为，原 E2E 仍实际执行相同实现 blob。

该修复不改变 review decisions、cut/overlap detection、mapping、timeline reconstruction、render/release authority；只防止 materializer 在取得不安全 filesystem ownership 后再开始写入。

## 2026-08-23 — V4 orchestration output-tree ownership gate

最终 P0/P1 收口扫描发现，顶层 `v4_run.py` 会在证明 output-tree ownership 之前进入 `OutputRunLock`，而 direct `v4_run_optimized.py` / `v4_run_legacy.py` 也会先创建 cache/session/stage 目录。若 `--out-dir` 落入已 fingerprint 的 task input subtree，这些 orchestration 写入本身就可能先污染受保护输入。

本轮把同一双向 output-tree gate 前移到三条公开 run entrypoint 的第一次写操作之前：保护 task manifest、所有 manifest-bound input roots/subtrees，以及显式 profile/language/middle-cut/lyric-role config inputs；output tree 既不能位于这些输入内，也不能反向包住它们。canonical `v4_run.py` 必须在创建 `.v4-run.lock` 前完成检查，direct optimized/legacy entrypoint 也必须在 cache/session/stage `mkdir` 前完成检查。

为避免安全修复混入 orchestration 算法 diff，legacy 与 optimized 原实现继续以 blob-identical internal source resource 保存：

```text
legacy    a20afb27ca7030033e86618cebea6414eea36ceb
optimized c7838ac50ab2b2202ee93bda5bd22801ec5d8d9a
```

公开 regression 必须覆盖 canonical / optimized / legacy 三种直接调用，在 unsafe output 位于 fingerprinted input subtree 时证明输出目录和 `.v4-run.lock` 均未被创建；同时覆盖 output tree 反向包住 task inputs 和显式 config input 的情况。该变更只收紧 filesystem ownership，不改变 alignment、evidence、render、release authority 或 Smart/Pro 策略。

## 2026-08-23 — V4 primary-stage writer ownership gate

继续从 production orchestrator 向下枚举真实 child process 后，确认 `v4_resolve_assets.py`、`v4_coarse_align.py`、`v4_fine_align.py`、`v4_probe_transition.py` 仍可被直接执行，并在没有 ownership preflight 的情况下写 `--out` / `--artifact-out`；coarse 还会写显式或由 production layout 推导出的 feature-cache tree。仅保护顶层 run 因而不足以阻止 direct stage CLI 污染已 fingerprint 的 task input 或 upstream lineage。

本轮把相同 fail-closed ownership contract 放到四条 public stage entrypoint 的原实现首次 write 之前：

- task manifest、全部 manifest-bound input roots/subtrees 与 direct upstream/config inputs 均受保护；
- TrackAssets/coarse 等 JSON 输入中递归声明的 `*_path` provenance 也进入 protected input 集合；
- `--out` 与 `--artifact-out` 必须 pairwise distinct，且不得覆盖/进入 protected input；
- coarse `--feature-cache-dir`（包括默认推导 cache）按动态 output tree 处理，与 protected inputs 双向不相交；
- 普通 `--out`/`--artifact-out` 只拥有各自文件，不把合法 stage artifact 共处的整个父目录误判成 writer-owned tree。

为避免安全修复混入 asset/coarse/Fine/transition 算法变化，四份 production implementation 直接复用原 Git blob：

```text
resolve_assets    162b1d9dfc25b3ae2e5995d0e790c47dbcc931f8
coarse_align      735c9aa1a98607953206aedbe1264f7680b5c145
fine_align        005ba2744ba299ded2eed4c7ee7a8c9511448706
probe_transition  eabf2b2f10f67d1057adab992b395ee562a1f8c4
```

Public regression 使用 generic synthetic task，直接调用四条 CLI，证明 unsafe output 在任何 stage artifact 被创建前即失败；另覆盖 coarse cache 进入/包住 task input 以及两个固定输出重合。该修复不改变 TrackAsset resolution、Source-to-Mix、Fine、transition score/margin、TimeWarp、readiness 或 release authority。

## 2026-09-01 — Max terminal interval float serialization fix

真实私有任务暴露出一个通用 orchestration 边界 bug：production plan 的最后一个 occurrence 会把 `primary_end` 设为 exact mix duration，但 `_coarse_command()` 原先固定格式化为 6 位小数。若 duration 在第 7 位小数触发向上舍入，序列化后的 `--mix-end` 会极小幅超过真实音频长度，随后被 coarse 的严格 `mix_end > mix_duration` gate 正确拒绝为 `invalid occurrence mix interval`。

修复只改变 run orchestration 的 CLI 浮点序列化：`--mix-start/--mix-end` 使用 Python round-trip float representation，不调整 coarse/Fine/TimeWarp 阈值、mapping 逻辑或任何 authority。新增 generic synthetic regression，专门覆盖“固定 6 位格式会向上越过 terminal duration”的情况；真实任务名称、音频时长和时间戳不进入 public test。

## 2026-09-01 — Max TimeWarp drift diagnostics honor robust inliers

真实私有任务进一步暴露出一个通用诊断一致性问题：robust TimeWarp fitting 已经通过 inlier mask 排除 gross retrieval outlier，但 early/middle/late drift diagnostics 原先仍对全部 residual 直接求位置桶均值。这样已被 robust fit 明确认定为 outlier 的少量错误 retrieval 仍会二次进入 drift authority gate，可能阻断一个其余 inlier 高覆盖、低残差且速率稳定的 affine mapping。

修复不改变 robust inlier threshold、drift threshold、piecewise threshold、feature threshold 或 cut authority。位置 drift 现在优先仅由对应桶内 robust inlier residual 计算；如果一个位置桶完全没有 inlier，则退回该桶全部 residual，继续 fail closed，不把无证据区域伪装成零 drift。新增 generic synthetic regression：仅前部少量 gross outlier、其余连续 anchors 保持稳定 affine 时，不应因为已排除 outlier 再次触发 false drift block。真实任务名称、时间戳、BPM 和音频数据不进入 public regression。

## 2026-09-01 — Canonical timed-credit / instrument-section filtering hardening

真实生产任务暴露出 canonical LRC 预处理的一个通用内容完整性缺口：已有中文制作信息过滤较完整，但英文 timed credits（publisher、instrument/session performer、recording/mixing/mastering、Dolby Atmos 等）仍可能被 TrackAsset/canonical parser 当作歌词；另外，provider 偶尔会把仅由多个乐器名称组成的纯器乐段落标签写成普通时间行。这两类非歌词文本如果进入 canonical timeline，会污染后续 Max render/reconciliation，即使声学 mapping 本身正确也无法产出可信字幕。

本轮只收紧共享 canonical metadata grammar，不改变 source-to-mix、TimeWarp、Fine、transition、cue segmentation 或 release authority：

- 中英文 metadata 继续走同一 `is_metadata_text()` 真源，TrackAsset lyric-role preflight 与 canonical parser 保持一致；
- 英文 credit 只按明确的 production-role grammar 过滤，例如 publisher、`<role> by:` / `<role>:`、recording/mixing/mastering/Dolby Atmos 工程字段；
- 无冒号的 `mixed/mastered/recorded at ...` 仅在后文包含明显 studio/mastering/recording 场所语法时过滤，避免误删普通歌词如 `Mixed at midnight ...`；
- 裸纯器乐标签只在整行由两个及以上乐器名称通过 `and` / `&` / `/` / `+` 连接时过滤，不因普通歌词中出现 bass/drums/guitar 等词而删除；
- explicit TrackAsset selection 仍不能把已判定为 metadata 的行重新引入 canonical truth。

Public regression 全部使用 generic synthetic fixtures，覆盖英文制作 credits、multi-instrument section marker，以及 instrument/credit-like 普通歌词的反例。相关 canonical / lyric-role / asset-resolver / text-repair + 文档版本 identity 回归共 67 项通过；真实任务仅用于 private QA，不把曲名、歌词、人员、时间戳或音频事实写入 public algorithm/test。

同轮审计还发现当前权威 `v4-status.md` / `v4-runtime-guide.md` 曾标为 `4.0.0a9`，但当前 HEAD、`origin/main`、运行时 `__version__` 与真实 artifact 均为 `4.0.0a8`。Git 历史证明 a9 曾存在于一个未进入当前主线的 transition-activity 条件化提交，而当前 legacy/optimized 实现也没有该 a9 行为，因此本轮把**当前权威文档**纠回真实 a8，历史 archive 保持不变；新增 generic identity regression，要求两份权威文档的主线版本始终等于 `lyric_aligner.__version__`，防止后续再次漂移。

兼容性：合法歌词与既有中文 metadata 规则保持不变；受影响的只是此前误进入 canonical truth 的明确 provider metadata。回滚点为本变更前 Git commit；Max artifact 继续通过 `git_commit` + task fingerprint 区分 lineage，算法版本不因这次 parser hardening 单独改号。

## 2026-09-02 — Narrow confirmed-overlap interval + minimum-duration cue repair

私有 Max 成品链路暴露出两个通用下游问题，均在不降低既有门槛的前提下收紧处理：

- transition review 的 `confirmed_overlap` 原先只能把整个候选 review interval 视为重组区。候选窗口通常刻意较宽，用它直接物化会把没有双源证据的前后片段一并扩成 overlap。现在 review decision 可选携带 `confirmed_interval=[start,end]`，且必须严格包含于原候选区间；未提供时保持旧行为。`resolved_clear` 等其他 action 不能携带该字段。overlap materializer 同步接受该候选子区间，并独立验证它不得越出 transition artifact 的原 candidate interval；因此 review 与 materialization 两层都 fail closed。transition activity coarse 继续只承担边界活动/lineage 证据，即使其 `timewarp.selection=NOT_REQUESTED` / `path=[]` 也不会被错误送入 Fine 或冒充连续 TimeWarp；歌词 timing 仍来自已经验证的 primary canonical timeline，confirmed region 只扩展该 timeline 的 occurrence window。若 primary timeline 带 `bounded_terminal_disconnect` projection authority，则任何越过 `mix_end_ms` 的 overlap 扩展直接拒绝。这样 reviewer 可把宽候选缩到证据实际支持的 crossfade 子区间，同时保持 Source-to-Mix 与 projection authority 分层，不改变 transition detector 本身的 score/margin/ambiguity 阈值。
- canonical timeline 偶尔会产生低于 renderer `minimum_cue_duration_ms` 的极短 cue。renderer 不降低全局 250 ms 门槛，而是先利用真实相邻空白扩展；仍不足时，只在相邻 cue 保持同一最小时长的前提下重分配边界。若邻居没有足够 temporal capacity 仍 fail closed。若歌词主体起点位于 occurrence authority window 之外、只剩不足最小时长的 clipped leading fragment，则省略该残片而不是显示闪字；confirmed-overlap recomposition 仍可在有证据时重新引入对应内容。

Public regression 使用 synthetic timeline/review fixtures，覆盖窄 overlap 子区间越界拒绝、错误 action 拒绝、内部极短 cue 邻接重平衡、clipped leading sliver 省略、无足够 donor capacity 时继续阻断；并复跑 timeline composer、review decisions、overlap recomposition、projection/render guard 与 overlap end-to-end。真实任务歌曲、歌词、时间戳与音频不进入 public test。

## 2026-09-02 — Editor topology rebuttal production materializer

私有长混剪的 Editor-Cue Reconciliation 实测表明，某些 editor SRT 并不是可保真的完整 cue topology：canonical timed stream 中会出现与任何 editor cue 都没有时间交集的歌词。此时若强制“不移动/不新增 editor cue”，会必然丢失 canonical truth。项目因此新增 `scripts/v4_materialize_editor_reconciled.py`，但只开放一个窄、可证明的 production rebuttal path。

materializer 必须消费 exact hash-bound canonical evaluation render 与 `editor_cue_reconciliation_evaluation`，要求 editor file order 单调、`full_topology_candidate=false`、至少一个 `no_editor_temporal_overlap` witness、reconciliation assigned/unassigned/status 计数闭合，并且 canonical audit 全部来自 `line_lrc / enhanced_lrc / qrc_word_timing` 显式 timing。普通 `canonical_interval_crosses_editor_boundary` 不能单独触发 rebuttal。成功时 final SRT/audit 与 evaluation SRT/audit exact byte-identical；只有新 QA 与新 `final_render` artifact 获得 `editor_reconciled` / `publish_ready=true`，原 evaluation artifact 保持不变。

Release gate 没有增加例外：production final-render 的 normalized config、artifact evidence 与 exact QA 仍必须三层一致，之后仍由 `v4_validate_release.py` 正常验证。Synthetic regression 覆盖成功 materialize→release、仅 boundary-crossing 继续阻断、unsupported timing format 阻断、reconciliation 内部计数不闭合时阻断；同时复跑 editor reconciliation CLI、release lineage、overlap E2E 与 projection/render guard。真实任务内容不进入 public tests。

## 2026-09-02 — Production display policy / model-reviewed presentation layer

真实成品复核确认需要把“canonical lyric truth”与“平台最终展示”严格分层：规范歌词可能包含高置信 typo/标点问题，同时平台发布还可能要求把明确强脏词做显示打码；line-LRC 又只有行起点，少数歌词会被 `next_line_start` 被动拉成极端长挂字幕。这些展示修订都不应回写 canonical text/order truth，也不重新推导 Max/Fine mapping 或 segmentation authority。

本轮新增 `lyric_aligner/text/display_policy.py` 与 `scripts/v4_apply_display_policy.py`。display stage 只允许消费已经 `editor_reconciled`、`publish_ready=true` 的 production final-render，并冻结 cue count/number/start、occurrence、track 与 canonical-line identity。显式模型修订必须绑定 exact task fingerprint 与 `occurrence_id + track_id + canonical_line_index + expected_text`，只有 `confidence=high` 才可 materialize；expected text 不一致、override 未命中或命中不唯一均 fail closed。输出 audit 同时保存 `canonical_text` / `display_text`、source/display start/end、policy identity、reviewer 与 reason，并重新计算 viewer-facing `text_sha256/cue_id`。

新增窄 `strong_profanity_v1` 自动显示 profile：明确强脏词（例如 `fuck/fucking`）按首字母加星号显示为 `f*`，但 canonical 原文保持不变。`sexy`、`shot`、`bullet`、`trigger`、`fire`、`kill`、`damn` 等语境相关歌词不会自动改写，需模型/人工按具体上下文判断，避免过度净化。

同一 display policy 还可显式启用 `trim_extreme_unknown_end_v1`：只接受 `source_end_basis=next_line_start`，只在源 duration 达到 integer trigger 后，把 viewer-facing end shorten-only 为 `start + max_display_hold_ms`；max hold 必须严格小于 trigger。start 永不移动、end 永不延长，`open_end` 与显式 timing authority 不受影响，原 source timing 必须保存在 audit 中。这是对“未知 end 的展示上限”建模，不把它冒充 vocal-end 检测。

Display stage 生成新的、hash-bound 的 `stage=final_render` production artifact，并以上一层 production final-render 为 upstream；既有 `v4_validate_release.py` 不增加任何例外，发布时仍要求 exactly one final-render 与三层 `editor_reconciled` / `publish_ready=true` authority 一致。Public regression 只使用 synthetic lyrics，验证强脏词窄打码、expected-text mismatch / 非 high-confidence / unmatched override fail-closed、start/identity 冻结、极端未知 end 只缩不伸、`open_end` 不受影响，以及 display materialization 后仍能通过原 release validator。真实歌词与真实任务 timing 审计只保存在 private task policy/QA。

## 2026-09-02 — Trailing digital-zero content extent + recoverable editor file order

真实长混剪导出暴露了两个与声学阈值无关的通用边界。第一，容器末尾可能附带数百秒纯数字零；若仍把物理 duration 作为最后 occurrence 的搜索终点，会无意义扩大 terminal window。新增 `lyric_aligner/audio/content_extent.py`：只在尾部 digital-zero run 至少 30 秒时把 `content_end` 缩到最后一个非零样本之后，普通 fade/近静音/底噪全部保留；`mix_duration` 继续作为物理 provenance。production plan 只用 `content_end` 限制最后 occurrence 与 end clamp。Public regression 覆盖长数字零尾、短零尾不裁、content_end 不得早于 nominal start/超过物理 duration。

第二，editor SRT 偶尔只是文件块顺序错位，而各 cue 的真实时间区间并不重叠。Reconciliation 现在同时记录原始 `editor_file_order_monotonic` 与窄 `editor_file_order_recoverable_nonoverlap_reordering`：只有每一个相邻 inversion 都满足 `right.end_ms <= left.start_ms` 才可恢复；任一 inversion 时间重叠仍 fail closed。Topology rebuttal materializer 可接受这类明确非重叠的文件顺序错误，但 `full_topology_candidate` 仍要求原始 file order 单调。Public regression 覆盖 recoverable inversion 与 overlapping inversion 拒绝，避免把“文件行顺序错误”误当成“时间 topology 冲突”。

## 2026-09-02 — Read-only final candidate audit

多次私有任务验收重复实现了相同的 final SRT 几何检查，因此抽出 `lyric_aligner/qa/final_candidate_audit.py` 与 `scripts/v4_audit_final.py`。该工具 strictly diagnostic-only：不写 production artifact、不修改字幕、不授予 authority；在 exact SRT/report + publish-ready QA 基础上，从同 task run/timeline 读取 occurrence windows、`content_end` 与 confirmed-overlap regions，统一检查 final file order、非正 cue、occurrence/content-end 越界、same-occurrence overlap 与未确认 cross-occurrence overlap，并报告短 cue/长驻留分布。长驻留只告警；已确认 overlap 只有 cue 交集完整落入同 TrackOccurrence pair 的 confirmed region 才合法。

Audit output 也受 fail-closed path ownership 保护：task/direct 输入以及 run 递归声明的所有 `*_path` lineage（包括实际 timeline）都不能被 `--out` 覆盖。Synthetic core regression 覆盖 clean candidate、window 越界、confirmed/unconfirmed overlap、same-occurrence overlap、长驻留 warning、非单调 final order 与 count mismatch；真实 production final 另做 private smoke，证明通用 audit 可直接替代任务专属结构审计。

## 2026-09-02 — Prepared-stem splice candidate：calibration 通过、fresh blind 淘汰

Private r2 calibration 显示 raw Max 普通歌词 timing/text 已接近 production truth，但同曲内部手工 splice 仍可能被单一 Fine source trajectory 吞掉。prepared-stem candidate 尝试使用剪辑前调速/预处理单曲 stem 扫描多 lag mode，并用局部双源 waveform OLS 验证 same-track splice。真实 calibration 中它能无需人工 lag 重建一例约 6 秒 source-offset handoff，自动 crossover 与 production truth 相差 170 ms，且在保持 prediction SRT/QA 完全不变时把 `cut_precision/recall` 从 `0/0` 提升到 `1/1`。

候选曾以 commit `1dbf82b` 临时进入 public，以便把 calibration selection 绑定真实 revision。随后审计发现原 r2 blind manifest 的结构标签已经暴露，原 blind split 作废并 quarantine；新建 r3 fresh blind，在 selection 前用 deterministic seed 锁定 8 个未见 structural case，并在 candidate 锁定后首次 materialize prediction。blind gate 的预设要求为 cut precision=1.0、recall>=0.75，同时 timing/text/review 零回归。实际 fresh blind 中 candidate 没有产生任何 cut prediction，`cut_precision=0`、`cut_recall=0`，gate fail。

按照 blind-test 纪律，不允许在看到该结果后继续调 threshold 或 fixture 以挽救 candidate。因此 public prepared-stem core/CLI/test 被撤回，项目继续维持现有 Max review/recomposition authority；private calibration/blind 产物保留为负证据。该结论只说明当前实现缺乏已证明的泛化能力，不否定 prepared stem 在特定人工复核中的辅助价值。

## 2026-09-02 — a9：抑制亚阈值 backward retrieval jitter 的假结构阻断

真实华语男声190 a8 复跑暴露了一个 TimeWarp 判定不对称：forward source jump 只有在超过连续速率包络且 `excess_source_jump >= min_excess_source_jump` 时才升级为结构 discontinuity，但 backward source jump 原实现对任何负 delta 都直接 block。结果两个整体 affine/Fine 质量良好的 occurrence 仅因尾部低-margin ambiguous retrieval 出现 96 ms / 276 ms 小幅回摆，就整首进入 `AFFINE_WITH_DISCONTINUITY_REVIEW`。

a9 让 backward jump 与已有结构阈值使用同一最小幅度语义：`abs(source_delta) < min_excess_source_jump` 视为局部 retrieval jitter，不生成 discontinuity；达到或超过阈值的 backward reorder 仍保持 fail-closed block。默认阈值继续为 1.5 秒，没有修改 calibration profile、forward cut envelope、piecewise selection 或 review authority。Public regression 覆盖亚阈值 backward jitter 被忽略，以及 >=1.5 秒 backward jump 继续阻断。

## 冻结与回滚

Smart/Pro production freeze tag 继续固定在：

```text
prod-smart-v1.2.5-pro-v1.1.4-20260821
56841c40d6a90101efe1da568e2d5c2e5e67a0a2
```

Max #68/#70 与后续 reconciliation evaluation / CLI safety / release consistency maintenance 不移动该 tag，不改变冻结 Smart/Pro 的行为。回滚依赖 Git commit/tag + artifact lineage，不维护第二套静默 fallback。
