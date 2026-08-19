# Lyric Aligner v4 实施记录与关键代码说明

> 2026-08-19 P3 前的完整实施/架构记录保存在 `references/archive/2026-08-19-pre-p3-v4-implementation.md`。本文件从 P3 起聚焦当前 responsibility graph 与可维护的生产接口。

## 1. 当前 authority graph

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
TrackAsset     -> source/canonical identity truth
Editor SRT     -> auxiliary mix-time evidence
ASR            -> auxiliary mix-time acoustic evidence
Forced P7      -> auxiliary source-time acoustic evidence
Forced P8      -> forced evidence projected to mix time
P9 fusion      -> uncalibrated multi-family shadow diagnostics
```

禁止：

```text
ASR/forced text -> final canonical lyric
source-ms evidence -> direct mix-ms comparison
rate change -> implicit cut
review confirmed_cut flag -> CUT_AWARE without materialization
P9 HIGH -> automatic timing mutation
P9 CONFLICT -> automatic declaration that editor is wrong
mutable fusion JSON without artifact validation -> production timing candidate
synthetic CI -> claim real singing accuracy
```

## 2. Text Repair V2 responsibility

`lyric_aligner/text_repair.py` 是冻结 timeline 的文字修复层：不读取 audio，不改变 cue count/number/start/end。它负责 deterministic typo/missing/extra-char 与 bounded segmentation-span repair；无法确定的 canonical/subtitle gap、重复歌词竞争、cue ownership 歧义进入 review。

任何需要改 timing 的任务不能通过 Text Repair V2 偷渡，必须进入 Source-to-Mix/Partial Timeline Repair 路径。

## 3. Partial Timeline Repair 分层

```text
P1  partial_repair.py
    explicit cue trust + local structural guards

P2  partial_repair_evidence.py
    P9 editor/canonical identity bridge
    no shadow-level -> trust promotion

P3a partial_repair_context.py
    effective-run/artifact lineage -> mapping/cut context

P3b partial_repair_production.py
    formal fusion payload/artifact verification
    production entry -> P3a + P2 + P1
```

P1/P2/P3a 的 payload API 保留给单元测试与受控组合；正式生产使用 P3b artifact bridge。当前所有层都不直接写 authoritative SRT。

## 4. P1 `timeline/partial_repair.py`

核心类型：

```text
CueTrust
TimingCandidate
CueRepairDecision
```

责任：

- trust 为 `trusted / untrusted / unknown`；
- trusted cue timing 是 hard lock；
- candidate source 固定 `source_to_mix`；
- mapping kind 只接受 `AFFINE / PIECEWISE_RATE / CUT_AWARE`；
- candidate 不得穿越最近 trusted neighbor；
- 多个 repair candidate 必须保持非重叠单调；
- CUT_AWARE unprojectable interval block；
- `propose_repair` 只表示结构可用，不是 publish approval。

## 5. P2 `timeline/partial_repair_evidence.py`

P2 消费当前 P9 fusion line identity。

P9 必须保持：

```text
mode = shadow_only
policy_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
authority.canonical_text = canonical_lyrics_only
authority.primary_timing = source_to_mix_only
```

P2 规则：

- `LOW/MEDIUM/HIGH/CONFLICT` 不生成 trust；
- trust 只来自显式 human review 或未来 separately calibrated policy；
- editor cue 必须唯一绑定一个 canonical line；
- candidate boundary 只取 `source_timeline_boundary_ms`；
- open-end 1ms sentinel 不可修；
- P2 的 mapping/cut 参数仅作为低层兼容 API，生产调用从 P3 起不直接提供它们。

## 6. P3a `timeline/partial_repair_context.py`

### 6.1 Effective run contract

P3 支持：

```text
production_orchestration   -> v4_production_run
review_resolution          -> v4_reviewed_run
overlap_recomposition      -> v4_recomposed_run
cut_rebuild                -> v4_cut_rebuilt_run
combined_recomposition     -> v4_combined_run
```

`derive_effective_run_mapping_context()` 先验证 exact run payload/artifact：task fingerprint、algorithm version、stage/role、artifact self-signature、formal output size/SHA。Legacy fallback 被拒绝。

### 6.2 Continuous mapping derivation

对 `mapping_source=coarse/fine`：

1. 从 occurrence 保存的 formal provenance path 读取 exact stage payload/artifact；
2. 验证 stage/output/hash/self-signature/task/algorithm；
3. 要求 mapping artifact ID 是 effective run artifact 的 upstream；
4. coarse/fine payload 的 occurrence identity 必须与 effective occurrence 一致；
5. Fine 路径额外要求 `fine_applied=true`、payload `applied=true`、Fine artifact upstream 包含 exact coarse artifact；
6. Fine `track_id` 与 `canonical_selection_sha256` 必须与 coarse 完全相同；
7. 直接读取 `TimeWarp.mapping.mode`。

合法 continuous mode：

```text
AFFINE
PIECEWISE_RATE
```

结构 guard：

- AFFINE 的 breakpoints/slope_deltas 必须为空；
- PIECEWISE_RATE 必须有 breakpoint 且 hinge arrays 等长；
- BPM 不参与 mapping kind 判定；
- `mapping_blocked=true` 返回 unavailable，不提供 repair candidate。

### 6.3 CUT_AWARE derivation

P3 不读取“用户说这是 cut”之类标签。CUT_AWARE 必须来自正式 materialization：

```text
occurrence.mapping_source = cut_aware_rebuild
occurrence.cut_rebuilt = true
cut mapping artifact stage = cut_timewarp_rebuild
output role = cut_aware_timewarp
payload.result.kind = CUT_AWARE
```

并验证：

- cut mapping artifact 在 effective-run upstream；
- cut payload 有非空 track/canonical selection identity；
- result 有 retained segments 与 cuts；
- occurrence cut_count == payload cuts == artifact evidence.cut_count；
- artifact evidence occurrence_id 一致；
- normalized config 的 confirmed_candidate_ids 完整；
- `source_review_artifact_id` 与 effective run `cut_rebuild` metadata 一致；
- source review artifact ID 同时是 cut mapping 与 effective run 的 upstream。

因此 review 阶段的 `decision_action=confirmed_cut` 只是“允许 rebuild”的决定，不是 CUT_AWARE mapping 本身。Dedicated `v4_rebuild_cut.py` 真正 materialize 后才获得该身份。

### 6.4 Overlap / combined behavior

`v4_recompose_overlap.py` 只替换 timeline materialization，occurrence 仍继承原 coarse/fine `mapping_source`；因此 P3 对 overlap-only run 继续派生原 continuous mode。

`v4_compose_materializations.py` 在 cut+overlap 同时存在时复制 cut occurrence，并保留 `cut_rebuilt=true`、cut mapping provenance；因此 P3 对 combined run 继续从 formal cut mapping 派生 CUT_AWARE，而不是从 combined timeline 名称猜测。

## 7. P3b `timeline/partial_repair_production.py`

正式生产入口：

```python
bridge_effective_artifacts_to_partial_repair(
    cues=...,
    run_path=...,
    run_artifact_path=...,
    fusion_path=...,
    fusion_artifact_path=...,
    explicit_trust=...,
)
```

### 7.1 Fusion formal artifact validation

Production bridge 先通过 P3a 得到 effective-run mapping context，再验证 exact P9 fusion pair：

```text
artifact stage = evidence_fusion_shadow
artifact output role = evidence_fusion
artifact task fingerprint = effective run task
artifact algorithm version = effective run algorithm
artifact self-signature valid
fusion payload size/SHA == formal artifact output record
```

随后要求三重 source-run binding 一致：

```text
fusion.source_run_stage == effective run stage
fusion.source_run_artifact_id == effective run artifact ID
fusion artifact normalized_config.source_run_artifact_id == effective run artifact ID
effective run artifact ID in fusion artifact upstream_artifact_ids
```

Fusion artifact evidence 还必须继续声明：

```text
mode = shadow_only
policy_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

因此即使有人手工修改 fusion JSON 中的 `source_timeline_boundary_ms`，旧 fusion artifact 的 output SHA/size 也会立即失配，不能进入 Partial Timeline Repair。

### 7.2 Report / privacy

`EffectiveRunMappingContext.to_report()` 只输出：

```text
task / algorithm / run stage / run artifact ID
occurrence ID
status
mapping kind/source
source stage/artifact ID
confirmed_cut
cut_count
reason
```

不输出 coarse/fine/cut 本地路径。Production report 只额外加入 formal fusion artifact ID 与 `production_inputs_artifact_verified=true`。

## 8. Validation boundary

P3 tests 证明 lineage contract 与 fail-closed 行为，包括 AFFINE、Fine PIEWISE_RATE、Fine/coarse track/canonical identity、review-only cut、materialized CUT_AWARE、combined cut+overlap、overlap-only、missing/upstream-tampered mapping artifact、tampered fusion output、fusion effective-run upstream/config binding。

这些测试不证明真实歌曲 timing accuracy。Partial Timeline Repair 当前继续固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
```

下一阶段必须使用 private calibration + independent blind-test 决定 calibrated cue trust policy；任何自动 timing write-back 都应作为后续独立 promotion 变更。
