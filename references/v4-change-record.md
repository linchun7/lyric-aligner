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

## 冻结与回滚

Smart/Pro production freeze tag 继续固定在：

```text
prod-smart-v1.2.5-pro-v1.1.4-20260821
56841c40d6a90101efe1da568e2d5c2e5e67a0a2
```

Max #68/#70 不移动该 tag，不改变冻结 Smart/Pro 的行为。回滚依赖 Git commit/tag + artifact lineage，不维护第二套静默 fallback。
