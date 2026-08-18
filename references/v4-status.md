# Lyric Aligner v4 当前实施状态

更新日期：2026-08-18  
当前 main：P9 已合入；production readiness tooling 正在本轮收口  
P8 merge：`00585a07b658ffea93509c4ed1a4b129deafd0a3`  
P9 merge：`efbdbb926b03efdf1d91622d5c23cabef1f9850c`  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

## 1. 已进入 main

生产主链与 evidence 链当前包含：

```text
a3-a8 production reconstruction / render / review / overlap / cut / composition
P1    strict calibration/blind framework
P1.1  private dataset scaffold/readiness
P2    editor/Jianying multilingual shadow evidence
P3    local acoustic planner/backend/faster-whisper executor
P4    shadow multi-family evidence fusion
P5    bounded ASR second-pass routing
P6    ASR second-pass execution + composite evidence
P7    external source forced alignment
P8    forced alignment source-to-mix projection
P9    editor/ASR/forced multi-family shadow fusion
```

P3 validate #493、P4 #517、P5 #530、P6 #545、P7 #560 均在各自 merge 前全绿。P8 latest result tree 的 fast-core #1 完成 compile、documentation contract、完整 unit/E2E、Skill、privacy、diff-check 全绿后合入。P9 result tree 的 fast-core #2 同样全绿，日志显示 **Ran 324 tests / OK**；随后 P9 branch 与已经合入的 P8 main 同步 ancestry，再以 PR #19 合入。

CI 同时增加：

```text
pull_request validation
push only on main
concurrency + cancel-in-progress
timeout-minutes
bounded ffprobe/apt setup
fast-core ubuntu-slim lane
```

这样可避免 feature-branch push + PR 双重排队，以及失控的 ffmpeg apt job 长时间占用 runner。

## 2. Authority graph（生产时不要改写）

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
Editor SRT     -> auxiliary mix-time shadow evidence
ASR            -> auxiliary mix-time acoustic evidence
P7 forced      -> auxiliary source-time acoustic evidence
P8 forced      -> same forced evidence projected to mix time
P9 fusion      -> diagnostic/shadow support state only
```

当前固定：

```text
policy_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

因此 `HIGH` 不是“可以自动改字幕”的生产置信度；它只表示当前未校准规则下多个 auxiliary families 相互支持。

## 3. P8：Forced Alignment Source-to-Mix Projection

正式输出：

```text
stage = forced_alignment_mix_projection
role = forced_alignment_mix_evidence
mode = forced_alignment_mix_projection
```

规则：

- `AFFINE` / `PIECEWISE_RATE` 复用现有 `mix_time_for_source()`；
- `CUT_AWARE` cross-gap/cross-cut line -> `unprojectable`；
- 不跨 confirmed cut bridge 假连续 interval；
- spans 独立投影；
- relevant mapping/artifact provenance 缺失或不一致 fail closed；
- P7 source-ms evidence 禁止直接与 editor/ASR mix-ms 比较。

## 4. P9：Multi-Family Shadow Fusion

可用 auxiliary boundary families：

```text
editor
asr
forced_alignment
```

Forced family 只接受 P8 mix-time evidence，并再次检查 occurrence/canonical line/track/canonical_text_sha256 identity。

所有可用 auxiliary pair 都做 boundary disagreement：

```text
editor ↔ asr
editor ↔ forced_alignment
asr ↔ forced_alignment
```

任意 pair 超过 `conflict_boundary_ms` -> `CONFLICT`，不会以 2-of-3 多数票隐藏 outlier。

无冲突时：

```text
0 auxiliary family -> LOW
1 auxiliary family -> MEDIUM
>=2 auxiliary families -> HIGH
```

新增 diagnostics：

```text
editor_forced_boundary_disagreement_ms
asr_forced_boundary_disagreement_ms
max_auxiliary_boundary_disagreement_ms
forced_alignment_line_counts = projected / unprojectable / absent
```

Cross-run evidence、artifact hash 篡改、unknown canonical line、track/text identity 漂移、`unprojectable` forced payload 夹带 mix boundary 均 fail closed。

## 5. 本地 Codex 生产入口

本地 Codex 开始任务先读：

```text
SKILL.md
references/v4-runtime-guide.md
references/v4-status.md
references/v4-implementation.md
references/dataset-protocol.md
```

标准顺序：

```text
1. task + canonical LRC + source audio + edited mix/editor SRT
2. v4_run / review / cut-overlap materialization -> authoritative effective run
3. editor evidence
4. ASR first-pass + bounded second-pass
5. external source forced alignment（需要时）
6. P8 forced source->mix projection
7. P9 shadow fusion：先看 CONFLICT / unprojectable / missing family
8. 人工核查高风险行
9. render
10. validate_release
11. 把真实人工 ground truth 写入 private calibration/blind 数据集
12. calibration 选型/阈值冻结后，再用独立 blind set 验证
```

遇到 provenance/blocking error 时重新生成或修正上游输入，**不要手改 artifact JSON 绕过 lineage**。

## 6. 从现在开始必须由真实私有数据完成的工作

代码阶段不再继续凭 synthetic 测试放宽 timing authority。下一阶段应直接在本地真实歌曲上：

1. 选择/安装实际 forced-aligner adapter/runtime；
2. 锁定 backend/package version、model/checkpoint revision、language/G2P resources、runtime/device identity；
3. 每种主要语言先取 3–5 个 30–90 秒片段，并覆盖 normal global-rate、dynamic local stretch、cut、overlap、弱人声/强伴奏、editor 弱语言；
4. 人工 ground truth 记录 Source-to-Mix/editor/ASR/forced boundary error、family coverage、CONFLICT、unprojectable 与 language/risk bucket；
5. calibration set 选择真实 backend/threshold/profile；
6. 冻结后只在 blind set 验证；
7. **只有 blind 数据证明收益后**，才设计/启用 calibrated automatic boundary refinement 或 release-gate integration。

公共 Actions、fake external subprocess 和 synthetic fixtures 能证明 contract/math/lineage/privacy，但不能证明 WhisperX/SOFA/MFA 或任何真实 checkpoint 对歌声的准确率。

## 7. 本轮 Production Readiness Tooling

本轮新增三个只读/评估型能力，目的是让本地 Codex 能可靠续跑真实任务并积累 calibration 数据，而不是继续凭 synthetic 数据改算法：

```text
scripts/v4_doctor.py
scripts/v4_evaluate_evidence_families.py
scripts/v4_runtime_snapshot.py
```

`v4_doctor.py`：检查 task/run/evidence/dataset/backend readiness，支持 `--require` 返回非零，并给出 recommended next action。报告不泄露歌词、绝对路径或完整 external command。

`v4_evaluate_evidence_families.py`：对 P9 fusion 中统一到 mix-time 的 Source-to-Mix/editor/ASR/forced boundaries 与 private hash-bound truth 做逐 family 误差统计；输出 overall、language、risk bucket 的 coverage、MAE/P95、250/500ms 命中率、CONFLICT 与 unprojectable rate。它仍是 calibration evidence，不改变 authority。

`v4_runtime_snapshot.py`：生成稳定 `runtime_identity_sha256`，记录 Git/Python/OS/ffmpeg/package/model/device/forced-command hash 等可复现信息，并对本地路径/完整命令做 redaction。

推荐本地真实任务在开始时先运行 runtime snapshot；已有 artifact 时运行 doctor；完成 P9 fusion 并有人工作 truth 后运行 family evaluator。这样下一轮决定 backend/checkpoint/threshold 时有机器可比的数据，而不是凭肉眼印象。

> **当前结论：核心 timing authority 已在 P9 前收口；本轮再补齐 doctor + family evaluator + runtime snapshot 后，仓库就应转入本地真实私有数据 production/calibration/blind，而不是继续增加未校准自动 timing 行为。**
