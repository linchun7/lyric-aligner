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

## 冻结与回滚

Smart/Pro production freeze tag 继续固定在：

```text
prod-smart-v1.2.5-pro-v1.1.4-20260821
56841c40d6a90101efe1da568e2d5c2e5e67a0a2
```

Max #68/#70 与后续 reconciliation evaluation / CLI safety / release consistency maintenance 不移动该 tag，不改变冻结 Smart/Pro 的行为。回滚依赖 Git commit/tag + artifact lineage，不维护第二套静默 fallback。
