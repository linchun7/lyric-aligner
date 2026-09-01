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

## 冻结与回滚

Smart/Pro production freeze tag 继续固定在：

```text
prod-smart-v1.2.5-pro-v1.1.4-20260821
56841c40d6a90101efe1da568e2d5c2e5e67a0a2
```

Max #68/#70 与后续 reconciliation evaluation / CLI safety / release consistency maintenance 不移动该 tag，不改变冻结 Smart/Pro 的行为。回滚依赖 Git commit/tag + artifact lineage，不维护第二套静默 fallback。
