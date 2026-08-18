# Lyric Aligner v4 实施记录与关键代码说明

> 当前主线算法仍为 `4.0.0a8`。P2-P9 都属于 evidence/diagnostic 层；canonical lyric 仍是 final text/order truth，Source-to-Mix 仍是 primary timing truth。

## 1. 当前分层

```text
lyric_aligner/
  alignment/
    backends.py          # backend discovery/readiness
    planner.py           # P3 bounded local jobs
    asr_executor.py      # P3 bounded faster-whisper
    asr_routing.py       # P5 weak -> second-pass plan
    asr_second_pass.py   # P6 second-pass composite
    forced_executor.py   # P7 external source forced alignment
    forced_projection.py # P8 source forced evidence -> mix time
  evidence/
    editor.py
    fusion.py            # P4 + P9 multi-family shadow fusion
  evaluation/
    family_calibration.py # real-data per-family boundary metrics
  doctor.py              # read-only resume/readiness diagnostics
  runtime_snapshot.py    # reproducible runtime identity
  assets/ audio/ contracts/ pipeline/ review/ text/ timeline/ qa/
```

关键 evidence / readiness CLI：

```text
v4_editor_evidence.py
v4_alignment_backends.py
v4_plan_alignment.py
v4_execute_asr_evidence.py
v4_plan_asr_second_pass.py
v4_execute_asr_second_pass.py
v4_execute_forced_alignment.py
v4_project_forced_alignment.py
v4_fuse_evidence.py
v4_doctor.py
v4_runtime_snapshot.py
v4_evaluate_evidence_families.py
```

## 2. Authority graph

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
TrackAsset     -> source/canonical identity truth
Editor SRT     -> auxiliary shadow evidence in mix time
ASR            -> auxiliary acoustic evidence in mix time
Forced align P7-> auxiliary acoustic evidence in source time
Forced P8      -> same forced evidence projected to mix time
Fusion P9      -> editor/asr/forced pairwise diagnostic shadow state
```

禁止：

```text
ASR/forced text -> final canonical lyric
P7 source forced ms -> directly compare with mix-ms evidence
cross-cut forced line -> fake bridged mix interval
missing/foreign artifact -> fake result
2-of-3 majority -> silently hide third-family conflict
HIGH shadow state -> automatic timing mutation/release
fake protocol E2E -> claim real ML model accuracy
```

## 3. P7/P8 baseline

P7 external protocol 已合入 main `9ad6df4f04b396871f757422bcb35f1fa7676678`；P7 validate #560 全绿。

P8 输出：

```text
stage = forced_alignment_mix_projection
role  = forced_alignment_mix_evidence
mode  = forced_alignment_mix_projection
```

`AFFINE` / `PIECEWISE_RATE` 复用 `mix_time_for_source()`；`CUT_AWARE` 对 gap/cross-cut line `unprojectable`，spans 独立投影。P8 artifact 绑定 source run、P7 forced artifact 与实际使用的 exact mapping artifacts。

## 4. P9 `evidence/fusion.py`

### 4.1 Schema / policy

```text
FUSION_SCHEMA_VERSION = 1.1
FUSION_POLICY_ID = evidence-fusion-shadow-2026-08-18-v2-forced
```

P9 在旧 editor+ASR shadow fusion 之上增加 `forced_mix_evidence` 输入；旧调用不提供 forced evidence 时保持原行为。

### 4.2 Forced evidence index

只接受：

```text
mode = forced_alignment_mix_projection
source_evidence_backend = external_forced_aligner
primary_timing_authority = source_to_mix_only
forced_alignment_authority = auxiliary_acoustic_evidence_only
```

每个 job 必须满足：

```text
job_id unique/non-empty
occurrence_id non-empty
canonical_line_index valid
canonical line identity unique
projection_status in {projected, unprojectable}
```

Fusion 再与 canonical timeline 校验：

```text
occurrence/line exists
track_id matches
canonical_text_sha256 matches
```

因此即使 P8 payload 被错误拼到另一首歌/另一条 canonical line，也会 fail closed。

### 4.3 Projected / unprojectable

`projection_status=projected` 才允许读取 `mix_start_ms/mix_end_ms`，并要求 finite、`end > start`。如有 `line_confidence`，必须在 `[0,1]`。

`projection_status=unprojectable` 必须没有 mix boundary；它会作为 `forced_alignment` family 的 unavailable diagnostic 出现在行级结果中，保存 `projection_reason`，但不增加 `auxiliary_boundary_family_count`。

### 4.4 Pairwise conflict

P9 的 proposals 最多来自：

```text
editor
asr
forced_alignment
```

每对都计算：

```text
max(abs(onset_left-onset_right), abs(offset_left-offset_right))
```

输出：

```text
editor_asr_boundary_disagreement_ms
editor_forced_boundary_disagreement_ms
asr_forced_boundary_disagreement_ms
max_auxiliary_boundary_disagreement_ms
```

只要任意可用 pair 超过 `conflict_boundary_ms`，该 line 就是 `CONFLICT`。这里故意不做 2-of-3 majority，避免两个相关 family 掩盖第三个真实警报。

### 4.5 Shadow levels

```text
CONFLICT : any pair over threshold
HIGH     : >=2 available auxiliary families and no conflict
MEDIUM   : exactly 1 available auxiliary family
LOW      : no available auxiliary family
```

全部固定：

```text
shadow_level_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

`HIGH` 只能解释为“多个当前辅助 family 在未校准阈值下相互支持”，不能解释为 production confidence。

### 4.6 Summary

除旧 `shadow_level_counts` 外新增：

```text
forced_alignment_line_counts:
  projected
  unprojectable
  absent
```

这能在真实数据 calibration 时区分 forced backend coverage 不足、cut 导致不可投影、以及根本未运行 forced family。

## 5. P9 CLI `v4_fuse_evidence.py`

新增参数：

```text
--forced-mix-evidence
--forced-mix-evidence-artifact
```

两者必须成对提供。CLI 使用与 editor/ASR 相同的 artifact contract，并额外要求：

```text
stage = forced_alignment_mix_projection
role = forced_alignment_mix_evidence
payload.source_run_artifact_id == current run artifact_id
current run artifact_id in forced artifact upstreams
```

Fusion output 增加：

```text
source_forced_mix_evidence_artifact_id
```

Fusion artifact upstreams 包含所有实际输入的 run/timeline/editor/asr/forced artifacts。normalized config 记录：

```text
forced_mix_evidence_artifact_id
conflict_policy = any_auxiliary_pair_over_threshold_blocks
```

## 6. Tests

Package tests覆盖：

- forced-only -> MEDIUM；
- editor+forced agreement -> HIGH；
- ASR+forced disagreement -> CONFLICT；
- 三 family 任一 outlier -> CONFLICT；
- unprojectable forced 不计 family；
- forced canonical hash mismatch / unknown line fail closed；
- unprojectable payload 夹带 mix boundary fail closed。

CLI E2E 扩展为 editor+ASR+forced 三 family，同时验证：

- fusion artifact upstream completeness；
- forced artifact ID 写入 formal output；
- private canonical/editor text 不进入 fusion evidence；
- mutated auxiliary payload 不通过 artifact validation。

## 7. Compatibility

`build_evidence_fusion(..., forced_mix_evidence=None)` 保留旧 editor/ASR 使用方式。旧字段 `editor_asr_boundary_disagreement_ms` 保留；新增字段都是 additive。Schema 从 `1.0` 升到 `1.1`，policy ID 升为 v2，明确区分是否支持 forced family。

## 8. CI / real-data boundary

公共 CI 应验证 Python 3.10/3.12/3.14 compile、unit/E2E、documentation contract、Skill/privacy/environment/diff-check。

公共 CI 不能证明：

```text
real forced-aligner singing accuracy
editor/asr/forced statistical independence
language-specific thresholds
calibrated release confidence
automatic timing refinement safety
```

这些只能在用户本地 private real-song calibration/blind 中完成。

## 9. 本地生产阶段下一步

代码层收口后，Codex 应先用真实数据跑完整 shadow chain，记录每条 line 的 source/editor/asr/forced 边界误差、coverage、CONFLICT、cut-unprojectable 与语言/风险桶，再通过 calibration/blind 选择阈值。数据证明收益前，不得将 P9 输出直接写回 authoritative timeline。

## 10. Production readiness / evaluation architecture

### 10.1 `lyric_aligner.doctor`

Doctor 是 orchestration 之外的只读诊断层，不生成或改写任何 production artifact。它只做三类事：

1. 对显式传入的 task/run/editor/plan/ASR/forced source/forced mix/fusion/runtime snapshot 做最小 stage-contract 检查；
2. 复用 `evaluation.readiness.inspect_dataset_readiness()` 与 `alignment.backends.inspect_backends()` 给出 dataset/backend readiness；
3. 根据现有 stage 推荐下一条生产动作，并允许 `--require` 把 readiness 条件变成机器可判定 exit code。

Doctor 故意不把 backend `available/execution_ready` 解释为模型准确率，也不输出 backend `discovery/detail`，因为这些字段可能包含 resolved local path/full command。

### 10.2 `evaluation.family_calibration`

Family evaluator 不重新解析 editor/ASR/P7 forced 的各自原始 schema，而是消费 P9 fusion。原因是 P9 已经完成三件关键归一化：

```text
all comparable families are in mix time
canonical occurrence/line/hash identity already bound
forced cross-cut evidence is explicitly unavailable/unprojectable
```

Private truth schema 只保存：

```text
occurrence_id
canonical_line_index
canonical_text_sha256
truth_start_ms
truth_end_ms
```

因此不需要复制 raw lyric。每条 truth 必须在 fusion 中找到同 identity 行且 SHA 精确匹配，否则 fail closed。

Family metrics 以 truth line 为 denominator，分别计算 Source-to-Mix/editor/ASR/forced coverage 与 onset/offset/boundary errors；`within_250ms_rate` / `within_500ms_rate` 使用该行 onset/offset 的 max error，避免“一头准一头偏”被平均掩盖。另行输出 fusion `CONFLICT` rate 与 forced `unprojectable` rate。

Dataset manifest 单次只允许 `calibration` 或 `blind_test` 一个 split，并按 language / risk bucket 分组。Formal report 固定 `policy_calibrated=false`、`release_gate_eligible=false`、`automatic_timing_change_allowed=false`，所以 evaluator 只提供 promotion 决策证据，不执行 promotion。

### 10.3 `runtime_snapshot`

Runtime snapshot 的 stable hash 覆盖：Git commit/dirty/branch、Python implementation/version、OS/release/machine、ffmpeg/ffprobe first version line、关键 package versions、model identities、forced command identity、requested device。

隐私规则：

```text
no hostname / username
no absolute repo path
absolute local model path -> basename + SHA only
external forced command -> executable basename + command SHA + arg count
no raw command / stdout / local source path
```

Snapshot 不加载 ML model，因此适合任务起始、候选切换和 blind-test 前快速固化环境 identity。它与现有 artifact backend/model lineage 是互补关系：artifact 证明某一步用的输入/模型身份，runtime snapshot 证明当时整个执行环境候选。

### 10.4 Authority remains unchanged

Readiness tooling 是 additive metadata/evaluation layer，不进入 renderer、不改变 canonical timeline，也不更新 P9 fusion policy。任何 future calibrated boundary refinement/release-gate integration 必须是独立变更，并以真实 calibration + 独立 blind-test 结果为前提。
