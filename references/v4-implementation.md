# Lyric Aligner v4 实施记录与关键代码说明

> 当前主线算法仍为 `4.0.0a8`。P2-P8 都属于 evidence/diagnostic 层；canonical lyric 仍是 final text/order truth，Source-to-Mix 仍是 primary timing truth。

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
    fusion.py
  assets/ audio/ contracts/ evaluation/ pipeline/ review/ text/ timeline/ qa/
```

关键 evidence CLI：

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
Fusion         -> diagnostic/shadow until calibrated
```

禁止：

```text
ASR text -> final canonical lyric
forced-aligner output -> final canonical text
P7 source forced ms -> directly compare with mix-ms evidence
cross-cut forced line -> fake bridged mix interval
missing mapping/backend -> fake result
fake protocol E2E -> claim real ML model accuracy
```

## 3. P7 baseline

P7 external protocol 已合入 main `9ad6df4f04b396871f757422bcb35f1fa7676678`；head `2ee9e1d2ced75c3d24b5a00353e9f275fc9dc9f9` validate #560 全绿。

P7 输出：

```text
stage = source_forced_alignment_evidence
role  = forced_alignment_evidence
```

它绑定 exact source asset/canonical line/backend/model/source window，并保持 raw lyric/path/command/stdout/stderr privacy。

## 4. P8 `alignment/forced_projection.py`

### 4.1 输入 contract

P8 只接受 P7 formal evidence：

```text
backend = external_forced_aligner
source_run_artifact_id = exact current source run artifact
jobs[] with occurrence/track/line/source boundaries
```

job identity 必须唯一且 occurrence mapping 必须可解析。projection 不重新识别歌词或音频，只做 exact timebase conversion。

### 4.2 Continuous mapping

`AFFINE` / `PIECEWISE_RATE` 不复制 mapping math，而调用 timeline projector 的：

```python
mix_time_for_source(...)
```

因此 Source-to-Mix 的 analytical inverse 仍只有一个实现来源。

### 4.3 CUT_AWARE

P8 从 confirmed cut materialization 读取 retained source segments。

line interval：

```text
start/end same retained source segment -> projected
boundary in confirmed gap             -> unprojectable
start/end on different segments       -> unprojectable
```

禁止跨 confirmed cut 合成假连续 line interval。

character spans 独立调用 interval projection；因此一个 line 即使整体 cross-cut，cut 两侧合法 spans 仍可以分别产生局部 mix evidence。

### 4.4 Projection result

每个 interval 输出明确状态：

```text
status = projected | unprojectable
reason = <deterministic reason when unprojectable>
source_start_ms/source_end_ms
mix_start_ms/mix_end_ms when projected
```

所有输出 ms 规范化为整数边界。

## 5. Mapping resolution scope

CLI 只解析 forced evidence 实际引用的 occurrences：

```text
mapping_scope = forced_evidence_occurrences_only
```

这是重要的 fail-closed 范围收窄：

- unrelated occurrence 即使 blocked，也不应阻断当前 job；
- relevant occurrence mapping 缺失/blocked/unbound 必须失败；
- relevant coarse/fine/cut payload 与 artifact/output hash/provenance 不一致必须失败。

这避免把全局 run 中无关坏轨道误当作当前 forced projection 的依赖，同时不降低真正依赖的 lineage 要求。

## 6. P8 CLI `v4_project_forced_alignment.py`

输入：

```text
task manifest
P7 forced evidence + artifact
source run + artifact
mapping payload/artifacts referenced by that source run
```

CLI 验证 exact task fingerprint、forced/source artifact identity、upstream relation，以及每个 referenced occurrence 的 exact mapping artifact。

输出：

```text
stage = forced_alignment_mix_projection
role  = forced_alignment_mix_evidence
mode  = forced_alignment_mix_projection
```

Artifact upstream 至少包括：

```text
source run artifact
P7 forced evidence artifact
exact coarse/fine/cut mapping artifacts actually used
```

normalized config 记录 projection schema/mapping scope，不依赖本地绝对路径。

## 7. P8 privacy

P8 不需要 canonical raw text，因此 projected payload 继续只保留 identity/hash/timing/confidence/backend-model lineage。E2E 测试显式验证 private lyric 不进入 projected artifact/evidence。

## 8. P8 tests

Package tests覆盖：

- AFFINE；
- PIECEWISE_RATE；
- CUT_AWARE same-segment；
- gap boundary / cross-cut unprojectable；
- independent spans；
- missing/blocked relevant mapping；
- invalid/foreign mapping lineage。

CLI E2E 覆盖：

- synthetic authoritative source run + artifact；
- exact forced artifact upstream；
- continuous projection 输出；
- unrelated blocked occurrence isolation；
- mapping artifact tamper fail；
- artifact upstream completeness；
- raw lyric privacy。

## 9. CI 边界

前一版 P8 head `94aa6df29f8505f703c37c5ce59c292f149806e3` validate #577 在 Documentation Contract 步骤失败；compile 已通过，unit/E2E 因 docs contract fail 被后续跳过。当前 PR 补齐 change-record/status/runtime/implementation 后必须重新以 latest head 跑完整 Python 3.10/3.12/3.14 validation。

公共 Actions 只能证明 protocol/projection/lineage/privacy，不证明真实 forced-aligner 在歌声上的 accuracy。

## 10. P8 之后

下一阶段只能消费 `forced_alignment_mix_evidence`，将它作为与 editor/ASR 分离的 acoustic family 接入 fusion。family agreement/disagreement、missing/unprojectable 与 release-gate 仍必须保持 shadow/fail-closed，直到 private real-song calibration/blind 给出可审计阈值。

真实生产还需要：

```text
real forced-aligner adapter/runtime preflight
package/model/checkpoint/language resource identity
private real-song calibration + blind metrics
local high-risk vocal refinement（如数据证明需要）
calibrated boundary application/release gate
```
