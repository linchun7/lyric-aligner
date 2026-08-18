# Lyric Aligner v4 当前实施状态

更新日期：2026-08-18  
当前 main：P9 + Production Readiness Tooling + Windows validation hardening + bounded mix decode + source feature cache 已合入；真实私有任务可从 V4 production path 开始  
P8 merge：`00585a07b658ffea93509c4ed1a4b129deafd0a3`  
P9 merge：`efbdbb926b03efdf1d91622d5c23cabef1f9850c`  
PR21 merge：`04e0802156f62006c6b6af5b4ef59b1acc81ce86`  
PR22 merge：`2b4a13132e95a551392811407f48573b36edab95`  
PR23 merge：`4d7e086aedd2b56210368302d9a17df29fef6a0c`  
PR24 merge：`0b1f38c98542eed9ec80034677cef4bf8e7f9791`  
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
PR21  production doctor + family evaluator + runtime snapshot
PR22  Windows command/bootstrap/privacy validation hardening
PR23  bounded mix decode for coarse/fine production alignment
PR24  source harmonic feature cache for repeated coarse jobs
```

P3 validate #493、P4 #517、P5 #530、P6 #545、P7 #560 均在各自 merge 前全绿。P8 latest result tree 的 fast-core #1 完成 compile、documentation contract、完整 unit/E2E、Skill、privacy、diff-check 全绿后合入。P9 result tree 的 fast-core #2 同样全绿，日志显示 **Ran 324 tests / OK**；随后 P9 branch 与已经合入的 P8 main 同步 ancestry，再以 PR #19 合入。PR21 以 merge `04e0802156f62006c6b6af5b4ef59b1acc81ce86` 进入 main，补齐 Doctor、runtime snapshot 与 family calibration tooling。PR22 exact-head fast-core 与 Python 3.10/3.12/3.14 + ASR validate 全绿后，以 merge `2b4a13132e95a551392811407f48573b36edab95` 进入 main。PR23 exact-head fast-core #43 与 validate #675 全绿后，以 merge `4d7e086aedd2b56210368302d9a17df29fef6a0c` 进入 main。PR24 将同一 source track 的 harmonic features 做 SHA/config/runtime-bound disposable cache，合入 merge `0b1f38c98542eed9ec80034677cef4bf8e7f9791`。

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

如果任务明确要求“仅按规范歌词修正字词，剪映 SRT 的 cue 数量、编号和全部起止时间保持不变”，优先使用 text-only repair fast path；它不读取音频，也不运行 Source-to-Mix / coarse / fine / transition / ASR / forced alignment：

```text
scripts/v4_text_repair.py
```

只有需要重新判断时间轴、cut、overlap、缺失 cue 或声学边界时才进入下面完整 V4：

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

## 7. Production Readiness Tooling

已进入 main 的三个只读/评估型能力，目的是让本地 Codex 能可靠续跑真实任务并积累 calibration 数据，而不是继续凭 synthetic 数据改算法：

```text
scripts/v4_doctor.py
scripts/v4_evaluate_evidence_families.py
scripts/v4_runtime_snapshot.py
```

`v4_doctor.py`：检查 task/run/evidence/dataset/backend readiness，支持 `--require` 返回非零，并给出 recommended next action。报告不泄露歌词、绝对路径或完整 external command。

`v4_evaluate_evidence_families.py`：对 P9 fusion 中统一到 mix-time 的 Source-to-Mix/editor/ASR/forced boundaries 与 private hash-bound truth 做逐 family 误差统计；输出 overall、language、risk bucket 的 coverage、MAE/P95、250/500ms 命中率、CONFLICT 与 unprojectable rate。它仍是 calibration evidence，不改变 authority。

`v4_runtime_snapshot.py`：生成稳定 `runtime_identity_sha256`，记录 Git/Python/OS/ffmpeg/package/model/device/forced-command hash 等可复现信息，并对本地路径/完整命令做 redaction。

推荐本地真实任务在开始时先运行 runtime snapshot；已有 artifact 时运行 doctor；完成 P9 fusion 并有人工作 truth 后运行 family evaluator。这样下一轮决定 backend/checkpoint/threshold 时有机器可比的数据，而不是凭肉眼印象。

## 8. Windows validation hardening（已合入 PR22）

PR22 只处理真实 Windows 本地验收暴露的跨平台问题，不扩大 timing scope：

- external forced-aligner command parsing 由 backend readiness、P7 executor、runtime snapshot 共用同一 helper；
- Windows 双引号 executable path / quoted arguments 在 `shell=False` 下保持一致 argv 语义，malformed quoting fail closed；
- CLI bootstrap tests 保留 Windows CreateProcess 必需环境，同时删除 `PYTHONPATH/PYTHONHOME` 并禁用 user-site，继续验证 repository-root bootstrap；
- privacy scanner 恢复严格本地路径规则，测试 fixture 改为运行时拼接敏感示例，不再通过 allowlist 削弱 scanner。

该修复不改变 canonical lyric、Source-to-Mix、P7/P8/P9 authority、threshold、release gate 或 automatic timing behavior。

## 9. Bounded mix decode（已合入 PR23）

第一轮真实 12 首私有生产任务证明旧串行 V4 存在明显重复 I/O：每个 occurrence/transition coarse 或 fine CLI 都可能重新解码最终长 mix，虽然核心 feature 实际只需要局部时间窗。

PR23 已把这部分变成同算法语义的 bounded decode：

- coarse CLI 只解码当前 occurrence / transition 区间及 2 秒保护 padding；
- fine CLI 只解码 coarse retrieval windows 覆盖区间及保护 padding；
- core mapper/refiner 接受带 absolute start 的 bounded mix buffer，仍输出全局 mix 坐标；
- 正式 payload 继续绑定完整 `mix_audio_sha256`；
- full-buffer ↔ bounded-buffer 等价测试要求 path/timewarp 保持一致。

这不是低精度模式，不改变 threshold、cut/review、Source-to-Mix authority 或 release semantics。

## 10. Source harmonic feature cache（已合入 PR24）

`v4_run.py` 对 12 首 primary occurrence 以及 11 个 transition boundary 会多次调用同一原曲的 coarse alignment。即使 mix 已 bounded decode，旧实现仍会对同一 source audio 重复执行 HPSS、Chroma CENS、MFCC feature extraction；一首歌通常会在 primary 及相邻 transition 中重复使用。

PR24 增加可删除的本地 source feature cache：

- cache 仅保存数值 harmonic `FeatureBundle`，不保存歌词、source path 或 timing decision；
- key 绑定 source audio SHA-256、sample rate、hop length、feature implementation ID 与 librosa version；
- 默认 V4 输出树的 primary/transition coarse 共享同一 `cache/features`；
- cache hit 时不再重新 decode/source HPSS/chroma/MFCC；
- cache miss 正常从 SHA-bound source audio 计算并原子写入；
- corrupt / incompatible / missing cache 一律当 miss，不 BLOCK 生产；
- formal coarse payload/artifact 仍绑定完整 source/mix SHA、task fingerprint、profile、algorithm version 和原有 lineage，cache 本身不是 upstream artifact。

该性能层不修改 slope grid、score/margin threshold、cut/review 或 Source-to-Mix 选择逻辑。

## 11. Text-only subtitle repair fast path（当前分支）

针对“规范歌词已经可靠、剪映时间轴已经可用、只需要修正字词”的真实生产场景，当前分支新增独立 text-only path：

```text
lyric_aligner/text_repair.py
scripts/v4_text_repair.py
scripts/test_v4_text_repair.py
```

核心边界：

- 不读取 audio，不调用 librosa，不运行 coarse/fine/transition/ASR/forced alignment；
- 支持按参数顺序串联多个 canonical LRC/TXT，LRC 时间标签只用于展开重复 occurrence，不用于改 SRT timing；
- 文本匹配使用 Unicode NFKC + punctuation/whitespace-insensitive normalization，再进行有序 fuzzy sequence alignment；
- 只有高置信且长度结构安全的 1:1 cue/lyric pair 自动替换；低置信、分段不一致、无法匹配的 cue 保持原文并标记 `review_required`；
- 输出前后强制比较每个 cue 的原始编号和 timing line，任何变化都立即失败；
- CLI 拒绝 `--out` 覆盖 `--source-srt`，因此原字幕不会被原地修改；
- report 记录 source/canonical/output SHA-256、替换/未变/review 数和逐 cue decision，但不读取或生成音频 artifact。

这个入口只适用于 **preserve timeline** 的文字纠错，不是 V4 Source-to-Mix 的低精度替代。当 cue 缺失、分段改变、cut/overlap 或时间边界本身可疑时，必须回到完整 V4。

后续完整 V4 性能优先级调整为：去除重复整文件验证/hash I/O → 安全 artifact resume → bounded workers → 真实数据验证后的 sparse ordinary-song probe + automatic fallback。语言本身仍不作为降低 Source-to-Mix 检查强度的理由；text-only path 的依据是任务明确冻结 timeline，而不是“中文所以可以少检查”。
