# Lyric Aligner v4 关键变更记录

> P3 前完整历史保存在 `references/archive/2026-08-19-pre-p3-v4-change-record.md`。当前生产设计基线见 `references/production-requirements.md`。

## 当前产品责任分层

```text
Standard = Text Repair V2.1
Smart    = Anchor Timeline Repair（no-audio）
Pro      = Selective Audio Repair（局部 audio）
Max      = Full V4 Alignment（完整 audio）
```

共同 authority：

```text
Canonical lyric -> final text/order truth
Jianying timing / cue boundary -> strong but rebuttable prior
LRC line break -> grouping/onset evidence, not final subtitle segmentation authority
Timed canonical -> primary no-audio timing evidence for Smart
Source-to-Mix   -> primary acoustic timing truth for Pro/Max
ASR / forced    -> auxiliary acoustic evidence
```

旧 Partial Timeline Repair P1–P5 的 `proposal_only / automatic_timing_change_allowed=false / release_gate_eligible=false` 约束继续保持，但这些 flags **只约束该 calibration/P9 production bridge**。Smart/Pro 是独立的 staged production path；Pro v1/v1.1 只生成/执行局部 evidence，不因此获得自动 timing write-back 权限。

---

## 2026-08-19 — Smart v1.1.4 parser + text-only evidence hardening

真实生产重跑在 v1.1.3 后继续暴露两类通用问题：

1. 普通消费级 LRC 会把标题、制作人员、歌手角色标签与歌词一样 timestamp；旧 `parse_canonical_lyrics()` 会把 metadata-only timestamp group 当作“零 lexical candidate 的歧义”并直接阻断 Smart；
2. strict A-anchor timing model 为了 mutation 安全会排除重复歌词与复杂 span，但这些 editor/canonical exact mappings 对**文字恢复**仍然是强证据；同时 severe-ASR block 可能只有局部左右边界足够，整首歌却无法建立 strict A model。若 Smart 只依赖 strict model，就会比旧轻量模式漏修更多文字。

本轮不降低 Text Repair V2 `auto-threshold`，也不扩大 automatic timing authority，而是新增明确分离的 **text-only evidence layer**：

### Canonical parser

- `text/normalization.py` 扩展常见 timestamped credits，支持 `:` / `：`；
- 新增 Latin/digit role-label 识别；
- 最前约 1 秒内的常见 `artist - title` timed row 不进入 canonical lyric；
- 没有显式 TrackAsset selection 时，metadata-only timestamp group 直接忽略；
- 同 timestamp 真正存在多个 lexical alternatives 时仍 fail closed；
- 显式 selection 指向 metadata/blank/title 时仍拒绝。

### Local bilateral text recovery

新增 `timeline/text_recovery_consensus.py`：

- bounded review block 最多 8 cue；
- 左右 boundary 必须各是非 review、1↔1、single canonical mapping，score `>=0.80`；
- 两侧同 source，canonical gap 连续同源；
- 只用左右 boundary 构造局部 affine interpolation，rate `0.5–2.0`；该模型只服务当前 block 的 text recovery，不进入 final timing mutation。

优先运行 whole-span ownership projection：当 review block 连续 editor text 与 canonical gap 连续 text grouped score `>=0.55` 时，复用 Text Repair `_assign_targets()`，把 canonical 正确字符按**原 editor cue ownership**回填。一个 canonical line 横跨多个 editor cue 时不制造伪 canonical timing span。

reason：

```text
local_bilateral_span_preserves_editor_segmentation
```

若 lexical evidence 太弱，则用 local affine + canonical onsets 进行 bounded partition：first-onset tolerance `750ms`、boundary guard `500ms`、每 cue 最多 4 canonical lines。

reason：

```text
local_bilateral_timing_confirms_canonical_sequence
```

### Exact-consensus text model

从 **recovery 前 original decisions** 中收集 exact + unchanged + 1↔1 mappings。与 strict A model 不同，重复副歌 exact rows 允许参加 robust fit，因为该模型只确认文字，不写 timing。

模型必须满足：

```text
anchor_count >= 6
distinct normalized exact texts >= 3
inlier_fraction >= 0.80
median_abs_residual_ms <= 150
```

provenance：

```text
text_exact_consensus_only
```

remaining mapped 1↔1 text review 只有当 projected canonical onset 位于 `editor cue interval ±250ms` 时才能恢复。若 editor cue 以单个常见中文语气词开头，只有 projected canonical onset 比 cue start 至少晚 `300ms` 才保留该 leading ad-lib。

reason：

```text
exact_consensus_timing_confirms_mapped_canonical
```

### Anti-circularity / timing authority

- local bilateral model 与 text-consensus model 永远不作为 final timing mutation model；
- final timing 仍只使用 strict A anchors / exact DAW hard prior；
- 所有 text recovery reasons 进入 timing planner 前强制 score cap `<0.92`，因此 recovered cue 不会因为文字被修对而升为 A/B timing evidence；
- existing strict bilateral/edge text recovery 可以在**文字阶段**消费 strict A-ready model，strict model 不存在时也可消费通过高质量 gate 的 text-consensus model，但仍不能提升 timing authority。

Smart policy id 升到 v1.1.4，schema 保持 `smart-1.1`；Pro 的 current-policy binding 会把旧 Smart report 自动视为 stale。

新增合成 regression 覆盖：timestamped metadata、local whole-span segmentation preservation、severe-ASR local onset partition、high-quality consensus low-sim recovery、onset-outside-cue rejection、leading ad-lib timing condition，以及 recovered reason 不能成为 primary timing grade。

## 2026-08-19 — Smart v1.1.3 segmentation authority + song-edge text recovery

真实生产复核暴露两个可泛化缺口：

1. canonical LRC 与 editor SRT 的连续文字/顺序一致、但换行分组不同，较高模式不能把 LRC line break 错当成 final subtitle cue boundary，导致文字跨原本正确的 editor cue 搬移；
2. severe-ASR lyric 位于歌曲开头/结尾且前后夹有 editor-only ad-lib 时，v1.1.2 的 bilateral-only text recovery 会因缺少一侧紧邻强 anchor 而漏修，即使该歌曲已经有独立 ready affine model 且 lyric onset 与 editor cue 高度吻合。

本轮把这两类问题收口为 Smart v1.1.3 的通用生产合同，不写入歌曲/cue/timestamp hard-code，也不降低 Text Repair V2 阈值：

- 明确 `canonical lyric = text/order authority`，但 `canonical LRC line break != final subtitle cue segmentation authority`；
- Standard/Smart 对连续文字已经一致的 bounded span 保留 editor 原 cue ownership；没有更强 boundary evidence 时，高阶模式不得仅按 LRC 换行跨 cue 搬字；
- 新增能力单调性规则：Higher mode 可以增加证据、减少 review，但无更强反证时不得破坏 lower-mode 已安全成立的 text/cue ownership/timing；
- `timeline/text_recovery.py` 保留 v1.1.2 bilateral interior recovery，并增加严格的 song-edge one-sided recovery；
- one-sided recovery 只允许 source 首/尾 4 条 canonical rows，initial model 必须已由独立 A anchors `ready`；
- 可用一侧至少需要 2 条紧邻、canonical 连续、`score>=0.92` 的强 anchor，且各自与 model residual `<=750ms`；
- candidate predicted onset 与 editor cue start 使用更紧的 `<=500ms`；
- 只能跨过 `canonical_ordinal=None && canonical_span=None` 的真正 unmapped review cue，作为 editor-only ad-lib；最多 3 条；任何 weak mapped cue 都阻断 one-sided recovery；
- ad-lib 本身保持原文和 review；recovered lyric text 不升级为 A timing anchor，也不获得 timing 自动写回权限；
- Smart report 新增 `text_edge_timing_recovery_count` 与 `text_edge_timing_recovery_block_count`；schema 继续 `smart-1.1`，policy id 升为 v1.1.3；
- Pro 继续通过共享 `SMART_POLICY_ID` 精确绑定当前 Smart，因此旧 v1.1.2 report 自动 stale，必须重跑 Smart。

新增 public regression 使用**合成同构文本**覆盖：Standard/Smart segmentation preservation、unmapped ad-lib + severe-ASR lyric + 一侧连续强 anchors、weak mapped cue 不可跳过，以及 recovered text 不形成 circular timing proof。

## 2026-08-19 — Smart v1.1.2 severe-ASR text recovery

Text Repair V2 因 editor ASR 严重错误而 lexical review 时，旧 Smart 会把错误 editor text 原样留在输出。本轮不降低 Text Repair 阈值、不引入 audio，而增加严格第二阶段 text recovery：

- 先用原有高可信 A anchors 建 ready affine model；review cue 不参与建模；
- interior review block 只在双侧高可信 single-line anchors、同歌、model-compatible、连续 canonical gap、onset 单调与 bounded span 全部成立时恢复；
- recovery 只替换文字，不提升 timing authority。

Smart report 新增 `text_review_count_before_timing_recovery`、`text_timing_recovery_count`、`text_timing_recovery_block_count`。

## 2026-08-19 — Smart / Pro v1.1.1 repair-only收口

- Smart 最终组合时间轴禁止新 overlap，也禁止扩大编辑器原本已有 overlap；
- `bpm_derived` 为 soft plausibility，`exact_daw` 才可 hard-lock rate；
- Enhanced LRC final token `end_ms=None` 可安全进入 Pro；
- Pro 只接受 current Smart schema + policy；
- adaptive source window 覆盖 acoustic query × candidate slope；
- ASR-only job 不扩大 acoustic region；
- `max_jobs` 约束最终 jobs 总数，包括 shadow competitor；
- Smart/Pro output path collision fail closed；
- Pro 只 hash/bind plan 真正需要的 source audio。

## 2026-08-19 — Smart / Pro v1.1 daily-production hardening

Smart 新增 production semantics：未验证 timing、无唯一 identity、C-grade identity 均 review；B-grade 不能建立 timing model；rate provenance 显式区分 exact DAW / BPM-derived / anchor-estimated。

Pro 增加 reason-aware routing、局部 acoustic region reuse、自适应 source window、歌曲边界 shadow competitor、external forced protocol 入口；仍 `timing_mutation_performed=false`。

## 2026-08-19 — Pro / Selective Audio Repair v1 evidence bridge

新增 `alignment/selective_repair.py` 与 `alignment/local_acoustic_match.py`：只把 Smart unresolved cue 转成 bounded Pro jobs；局部 decode source/mix；有 Smart rate 时窄 slope search；只输出 acoustic evidence，不直接改 SRT。

`scripts/v4_pro_selective.py` 支持计划生成、bounded acoustic、bounded faster-whisper 与 external forced；mixed-language ASR routing 以 local canonical span 为优先 language hint。

## 2026-08-19 — Smart / Anchor Timeline Repair v1

新增生产基线 `references/production-requirements.md`。Smart 不读 audio；复用 Text Repair deterministic alignment；保留 LRC/Enhanced LRC/QRC timing；A/B/C identity 分级；每歌优先 dominant affine model；leave-one-out 防循环；只修证据充分的小量 timing outlier；rare multi-rate/cut 升级而不污染普通主路径。

## 2026-08-19 — Text Repair V2.1 hardening

Text Repair 保持 frozen timeline：不读 audio、不改变 cue count/number/start/end。metadata filtering、mixed timed/untimed fail-closed、boundary insertion review、coverage/report 语义、O(n log n) exact anchor chain 与 production threshold floor 均已收口。

## 2026-08-19 — Partial Timeline Repair P1–P5

P1–P5 formal calibration/proposal/readiness chain 继续固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```
