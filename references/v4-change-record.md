# Lyric Aligner v4 关键变更记录

> P3 前的完整历史保存在 `references/archive/2026-08-19-pre-p3-v4-change-record.md`；本文件记录当前生产责任与 Partial Timeline Repair P1–P5。

## 当前 authority

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
Editor / ASR / forced -> auxiliary evidence
P9 fusion      -> uncalibrated shadow diagnostics
P4 trust lock  -> calibrated cue-trust proposal eligibility only
P5 Doctor      -> read-only readiness diagnostics only
```

全局固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

公共 CI、P9 HIGH、synthetic calibration 或 Doctor `proposal_inputs_ready` 都不能提升 timing/release authority。

---

## 2026-08-19 — Partial Timeline Repair P1

新增 `lyric_aligner/timeline/partial_repair.py`：建立 `trusted / untrusted / unknown` cue trust、trusted timing hard lock、Source-to-Mix-only candidate、`AFFINE / PIECEWISE_RATE / CUT_AWARE`、`rate change != cut`、trusted-neighbor 与 candidate-overlap fail-closed guards。输出 proposal-only。

## 2026-08-19 — Partial Timeline Repair P2

新增 `lyric_aligner/timeline/partial_repair_evidence.py`：P9 `LOW/MEDIUM/HIGH/CONFLICT` 仅做 diagnostics；`HIGH` 不自动 trusted，`CONFLICT` 不自动 untrusted；editor cue 必须唯一绑定 canonical line；candidate 只使用 authoritative `source_timeline_boundary_ms`；open-end 1ms sentinel 不可作为 repair interval。

## 2026-08-19 — Partial Timeline Repair P3

新增 `partial_repair_context.py` 与 `partial_repair_production.py`：正式生产要求 effective-run payload/artifact + P9 fusion payload/artifact 四件套；从 exact coarse/Fine/cut lineage 派生 `AFFINE / PIECEWISE_RATE / CUT_AWARE`；Fine identity 必须与 coarse 一致；blocked mapping 不可用；CUT_AWARE 只认 materialized `cut_timewarp_rebuild` lineage；fusion output SHA/size/self-signature/upstream 均严格校验；tampered fusion JSON fail closed。

## 2026-08-19 — Partial Timeline Repair P4 calibrated trust lock

新增 `partial_repair_trust.py`、`partial_repair_trust_production.py` 与 `scripts/v4_build_partial_trust_lock.py`。P4 不发明公开 threshold，而是复用 strict calibration + independent blind workflow；只允许 blind policy 明确覆盖并通过的 `language:*` scope 生成 calibrated cue-trust proposal。正式 decision 必须绑定 exact trust lock、candidate ID/revision/runtime、P9 fusion artifact，并通过 formal decision artifact 的 output hash/self-signature/upstream/config 验证。P9 CONFLICT 不能被自动提升为 trusted；human review 优先。

## 2026-08-19 — Partial Timeline Repair P5 Doctor readiness

新增：

```text
lyric_aligner/timeline/partial_repair_readiness.py
lyric_aligner/doctor_partial.py
```

并扩展 `scripts/v4_doctor.py`。P5 是只读 readiness 层，不生成或修改 SRT、trust lock、decision、timing candidate 或 release state。

P5 复用与正式生产相同的 P3/P4 validator，而不是重新实现一套较宽松检查：

- P3 lineage：验证 effective run/artifact 与 P9 fusion/artifact；统计 run stage、AFFINE/PIECEWISE_RATE/CUT_AWARE、unavailable occurrence、confirmed cut、P9 CONFLICT 与 language scopes；
- P4 trust lock：验证 strict calibration/blind lock 是否真实有效，以及是否具有显式 blind-gated `language:*` actionable scope；
- P4 decisions：要求 decision payload + formal artifact 成对，验证 exact lock/candidate/runtime/fusion identity、artifact output hash/self-signature/upstream，再计算 trusted/untrusted/unknown 等计数；
- readiness detail 会清理 POSIX/Windows 本地绝对路径，不输出 raw lyric/subtitle text 或 artifact output path。

新增 Doctor requirements：

```text
partial_repair:lineage
partial_repair:trust_lock
partial_repair:actionable_scope
partial_repair:decisions
partial_repair:proposal_inputs
```

readiness 状态 fail closed：

```text
not_requested
blocked
human_review_or_calibration_required
human_review_required
calibrated_decisions_required
proposal_inputs_ready
```

`proposal_inputs_ready` 仅表示 P3/P4 formal inputs 已可进入 proposal-only local repair；它明确不表示 automatic timing write-back、publish 或 release gate 已获授权。
