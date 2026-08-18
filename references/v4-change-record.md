# Lyric Aligner v4 关键变更记录

> 所有实质性生产更新必须按 `documentation-contract.md` 在同一 PR 同步本文件。这里只记录已经进入代码的行为、兼容/迁移与验证边界。

## 已合入 main

- a3 production-first reconstruction：`cfa43f4c854b699819cd3acb0cfea575cd1a04c8`；
- a4 package-native render/release：`236d9d717229147ee1d1a8755d712e54db47a751`；
- a5 replayable review：`a80a531d6933946484c54d3a589bc55b0cb9e94b`；
- a6 confirmed overlap：`dfd840b3a6f893531cce8019aae53e803243f95c`；
- a7 confirmed cut/CUT_AWARE：`096210fbdbb8a55ee908b592bba20b1244c2821f`；
- a8 cut+overlap composition：`5c458d8327d2641ba053423fff3066d7fdd8ba3b`；
- P1 strict calibration/blind：`1c6babe37067c217d14a7404aa0ed6a1c4779a00`；
- P1.1 private dataset readiness：`ad6c403a56209e945a9a61a1eeab1a4bc3c204b4`；
- P2 editor shadow evidence：`2e96569189ac6eb16d987fb2f304403696bc809b`；
- P3 local acoustic evidence：`cd3420750c06a55fa1af7d6314ec56971e728928`；
- P4 evidence fusion shadow：`bc4e10760ffee2e5990ca580d5edbadd7d561eaf`；
- P5 ASR second-pass routing：`1abef200c3dbfe711dacf5432bb51ee7ac1bbe5d`；
- P6 ASR second-pass execution/composite：`6eacacc50e885684b0265e3abea729b19b1b7725`；
- P7 external source forced alignment：`9ad6df4f04b396871f757422bcb35f1fa7676678`；
- P8 forced alignment source-to-mix projection：PR #17，merge `00585a07b658ffea93509c4ed1a4b129deafd0a3`；
- P9 forced alignment multi-family shadow fusion：PR #19，merge `efbdbb926b03efdf1d91622d5c23cabef1f9850c`；
- Production Readiness Tooling：PR #21，merge `04e0802156f62006c6b6af5b4ef59b1acc81ce86`；
- Windows Local Validation Hardening：PR #22，merge `2b4a13132e95a551392811407f48573b36edab95`；
- Production Bounded Mix Decode：PR #23，merge `4d7e086aedd2b56210368302d9a17df29fef6a0c`；
- Source Harmonic Feature Cache：PR #24，merge `0b1f38c98542eed9ec80034677cef4bf8e7f9791`。

P7 head `2ee9e1d2ced75c3d24b5a00353e9f275fc9dc9f9` 的 validate #560 全绿后合入。P8 latest result tree 在 fast-core #1 完成 compile、documentation contract、完整 unit/E2E、Skill、privacy、diff-check 全绿后合入。P9 result tree 在 fast-core #2 完成同级验证并跑完 **324 tests** 全绿后，与 P8 main 同步 ancestry，再合入 main。PR22 exact-head fast-core 与 Python 3.10/3.12/3.14 + ASR validate 全绿后合入。PR23 exact-head fast-core #43 与 validate #675 全绿后合入。PR24 在 source-feature cache roundtrip、corruption/key isolation 与 direct/precomputed coarse equivalence 覆盖后合入。

---

## 2026-08-18 — P8 Forced Alignment Source-to-Mix Projection

P8 将 P7 absolute source-ms forced evidence 通过当前 occurrence 的 exact Source-to-Mix 映射投影到 edited-mix time，避免跨时基比较。

新增：

```text
lyric_aligner/alignment/forced_projection.py
scripts/v4_project_forced_alignment.py
scripts/test_v4_forced_mix_projection.py
scripts/test_v4_forced_mix_projection_end_to_end.py
```

Authority 不变：canonical lyrics 是 final text/order truth，Source-to-Mix 是 primary timing truth，forced alignment 只是 auxiliary evidence。

`AFFINE` / `PIECEWISE_RATE` 复用现有 `mix_time_for_source()`；`CUT_AWARE` 对 confirmed source gap 或 cross-cut line 标记 `unprojectable`，绝不 bridge；spans 独立投影。只解析 forced-evidence 实际引用 occurrences，relevant mapping/provenance 缺失仍 fail closed。

Artifact：

```text
stage = forced_alignment_mix_projection
role = forced_alignment_mix_evidence
mode = forced_alignment_mix_projection
```

公共 CI 只能证明 projection/cut/lineage/privacy，不能证明真实 forced-aligner checkpoint 对歌声准确。

---

## 2026-08-18 — P9 Forced Alignment Multi-Family Shadow Fusion

### 1. 目标

P8 让 forced evidence 与 editor/ASR 都进入 mix time 后，P9 才允许它作为独立 auxiliary family 进入 evidence fusion。仍然是 shadow-only：不修改 canonical text、Source-to-Mix、render timeline 或 release eligibility。

更新：

```text
lyric_aligner/evidence/fusion.py
scripts/v4_fuse_evidence.py
scripts/test_v4_evidence_fusion_end_to_end.py
```

新增：

```text
scripts/test_v4_forced_evidence_fusion.py
```

### 2. Forced family contract

只接受：

```text
mode = forced_alignment_mix_projection
source_evidence_backend = external_forced_aligner
primary_timing_authority = source_to_mix_only
forced_alignment_authority = auxiliary_acoustic_evidence_only
```

每个 forced job 必须绑定已知 occurrence/canonical line/track/canonical_text_sha256；job IDs 与 canonical line identity 必须唯一。`projected` job 才提供 mix boundary；`unprojectable` 会显式进入 diagnostics，但不计为可用 auxiliary boundary family。`unprojectable` payload 若携带 mix boundary 会 fail closed。

### 3. Conflict policy

P4 旧版只计算 editor↔ASR disagreement。P9 改为三 family pairwise fail-closed：

```text
editor ↔ asr
editor ↔ forced_alignment
asr ↔ forced_alignment
```

只要任意可用 auxiliary pair 的 onset/offset 最大分歧超过 `conflict_boundary_ms`，整行 shadow state 为 `CONFLICT`。不会用多数票掩盖 outlier。

无冲突时仍按未校准 shadow 规则：0 family=`LOW`，1=`MEDIUM`，>=2=`HIGH`。这里的 `HIGH` 仍不是 release confidence。

新增 diagnostics：

```text
editor_forced_boundary_disagreement_ms
asr_forced_boundary_disagreement_ms
max_auxiliary_boundary_disagreement_ms
summary.forced_alignment_line_counts
```

### 4. CLI / lineage

`v4_fuse_evidence.py` 新增：

```text
--forced-mix-evidence
--forced-mix-evidence-artifact
```

P8 artifact 必须与当前 source run 同 task、同 algorithm version、同 source_run_artifact_id，并把 source run 放在 upstream。fusion artifact 保存所有实际输入 artifacts 的 lineage，并记录 forced artifact ID 与 `any_auxiliary_pair_over_threshold_blocks` conflict policy。

### 5. Safety boundary

P9 固定：

```text
mode = shadow_only
policy_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

这就是代码阶段的刻意收口点。下一阶段必须使用 private real-song calibration/blind 来决定 family 的实际独立性、不同语言/风险类型阈值，以及是否允许任何自动 timing refinement。公共 synthetic CI 不得用于宣称真实 accuracy。

---

## 2026-08-18 — Production Readiness Tooling

本轮不改变 Source-to-Mix、P9 shadow policy 或 release authority，只补齐真实数据生产前的可操作性、可测量性和可复现性。

新增：

```text
lyric_aligner/doctor.py
scripts/v4_doctor.py
lyric_aligner/evaluation/family_calibration.py
scripts/v4_evaluate_evidence_families.py
lyric_aligner/runtime_snapshot.py
scripts/v4_runtime_snapshot.py
```

### Production Doctor

`v4_doctor.py` 使用当前真实 schema-2.0 task 与 effective-run shape 做只读检查，并可接收 run/editor/plan/ASR/forced source/forced mix/fusion 的 payload + artifact manifest 配对。除 stage readiness 外，它验证 artifact 自签名、task fingerprint、algorithm identity、exact payload output SHA/size、current run upstream，以及 payload 的 `source_run_artifact_id`（存在时）。`--require lineage` / `artifact:*` 可将这些条件变成机器可判定非零退出。

报告只保留必要的 basename、artifact ID prefix 与无敏感 diagnostics；不输出歌词、absolute local/artifact output paths、backend resolved paths 或完整 external command。Backend `available/execution_ready` 仍只表示运行前提可发现，不代表 singing accuracy。

### Evidence Family Calibration Evaluator

`v4_evaluate_evidence_families.py` 直接消费 P9 fusion 的统一 mix-time family boundaries 与 private hash-bound line truth；按 overall / language / risk bucket 输出 Source-to-Mix、editor、ASR、forced 的 coverage、onset/offset/boundary MAE、P50/P90/P95、250/500ms 命中率，以及 `CONFLICT` / forced `unprojectable` rate。输出不包含 raw lyric 或本地路径。

每份 family dataset 必须绑定一个 self-validating runtime snapshot；runtime 内容被修改但继续使用旧 `runtime_identity_sha256` 时 fail closed。跨 case 的 fusion policy identity 包含 algorithm、policy ID 与 semantic config，但排除每首歌必然不同的 `*_artifact_id` lineage 字段；因此同一 policy 的不同歌曲可以聚合，而不同 `conflict_boundary_ms` / conflict policy 等语义设置不能混算。

该 evaluator 要求 `canonical_text_sha256` 与 truth 精确匹配，并固定：

```text
policy_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

因此 calibration 报告本身不能提升 auxiliary timing authority；P1 strict workflow 仍负责 source-group split isolation 与 calibration→blind lock，冻结候选 backend/model/profile/threshold/runtime identity 后必须在独立 blind set 验证。

### Runtime Snapshot

`v4_runtime_snapshot.py` 记录 Git commit/dirty state、Python、OS/arch、ffmpeg/ffprobe、关键 package versions、logical model IDs、device request 与 external forced-aligner command hash/basename，并生成稳定 `runtime_identity_sha256`。读取时会重新计算 canonical identity hash；clean worktree 明确记录 `dirty=false`。Hostname/username/absolute repo path/local model path/full external command 不进入 formal snapshot。

### Compatibility / rollback

这些工具都是 additive diagnostics/evaluation metadata，不改变现有 artifacts、renderer 或 release contracts；如需回滚可删除上述新增模块/CLI，而不会改变 P9 之前的 authoritative production output。真实数据出现前仍禁止凭 synthetic CI 调整 family threshold 或自动写回 timeline。

---

## 2026-08-18 — Windows Local Validation Hardening

本轮由真实 Windows 本地等价 CI 暴露三个问题：quoted executable path 在 external forced command 中解析不一致、`env={}` bootstrap tests 在 Windows/Python 3.10 下可能触发 CreateProcess 错误、以及测试 fixture 为了验证隐私而把本地用户路径 literal 写进 tracked source。

修复：

```text
lyric_aligner/command_line.py
lyric_aligner/alignment/backends.py
lyric_aligner/alignment/forced_executor.py
lyric_aligner/runtime_snapshot.py
scripts/test_v4_command_line.py
scripts/test_v4_cli_bootstrap.py
scripts/test_v4_calibration_cli_bootstrap.py
scripts/test_v4_doctor.py
scripts/test_v4_runtime_snapshot.py
scripts/privacy_scan.py
```

- backend readiness、P7 executor 与 runtime snapshot 共享同一 `split_external_command()`，Windows 双引号 executable/argument 可在 `shell=False` 下解析成一致 argv；malformed quote fail closed；
- Windows 仅规范化双引号，不把单引号错误当成 native Windows shell grouping；
- bootstrap tests 保留 OS 创建进程所需环境，同时移除 `PYTHONPATH/PYTHONHOME` 并设置 `PYTHONNOUSERSITE=1`，继续验证 CLI 不依赖外部 Python path；
- privacy scanner 恢复严格的本地用户目录根路径扫描（覆盖常见 Unix/macOS/Windows 形式），敏感示例由测试在 runtime 拼接，不引入 allowlist/排除规则。

Authority 与 release boundary 完全不变：canonical lyric、Source-to-Mix、P7/P8/P9 shadow semantics、threshold、release gate、automatic timing behavior 均未调整。该变更属于跨平台执行/验证可靠性修复，不是 accuracy promotion。

---

## 2026-08-18 — Production Bounded Mix Decode Fast Path

第一轮真实私有生产暴露了吞吐问题：一个长 mix 含多首歌时，`v4_run.py` 会逐 occurrence 调用 coarse/fine CLI，而旧 CLI 每次都重新完整解码长 mix。核心 coarse/fine feature 实际只使用当前 occurrence 的局部窗口，因此完整解码属于重复计算。

本轮先做不改变 authoritative 算法决策的执行优化：

```text
lyric_aligner/audio/coarse_mapper.py
lyric_aligner/audio/fine_alignment.py
scripts/v4_coarse_align.py
scripts/v4_fine_align.py
scripts/test_v4_coarse_mapper.py
scripts/test_v4_fine_alignment.py
```

### Bounded decode

- coarse 先通过音频 metadata 取得完整 mix duration，再只解码 `mix_start..mix_end` 外加两侧最多 2 秒 padding；
- fine 从 coarse retrieval windows 推导实际需要的 global mix interval，只解码该区间外加最多 2 秒 padding；
- core mapper/refiner 新增 `mix_audio_start` 与 `full_mix_duration`，允许输入一个局部 waveform buffer，同时继续使用 absolute mix coordinates；
- `feature_scope.full_mix_duration` 仍表示原始完整 mix 时长，不因 cropped buffer 改义；
- 正式 payload 继续计算并保存完整 mix file SHA，因此 asset/task identity 没有被局部 decode 弱化。

### Equivalence / safety

新增内存等价测试：同一 waveform 使用完整 buffer 与 cropped buffer + absolute origin 运行时，coarse windows/path/timewarp 与 fine path/timewarp 必须相同。默认 core API 仍以 `mix_audio_start=0` 工作，旧调用方保持兼容。

该变更不调整 coarse/fine threshold、slope grid、cut detection、review policy、canonical/Source-to-Mix authority、P9 shadow、release gate。它也不是尚未校准的“低精度 fast mode”；任何复杂 cut/overlap 仍按原规则 fail closed/review。

### 后续性能路线

真实 benchmark 后再依次考虑：

1. 对有效 artifact 做 resume/cache，避免中断或下游变化时重算 unchanged occurrence；
2. 对独立 occurrence 加 bounded worker 并发，保持输出顺序与 artifact lineage；
3. 对普通 global-rate 歌曲研究 sparse fast probe，但必须在不确定时自动 fallback 当前 full mapping，并在 real calibration 前保持非默认/非 authority promotion。

回滚本轮只需恢复 full-mix decode 调用方式；artifact contract 和 release contract 无需迁移。

---

## 2026-08-18 — Source Harmonic Feature Cache

第一轮多歌 production 还暴露第二个重复计算：同一 source track 会在 primary coarse 及相邻 transition coarse 中多次使用，旧实现每次都重新 decode 原曲并运行 HPSS、Chroma CENS 与 MFCC。

本轮增加 disposable source feature cache：

```text
lyric_aligner/audio/feature_cache.py
lyric_aligner/audio/coarse_mapper.py
scripts/v4_coarse_align.py
scripts/test_v4_feature_cache.py
scripts/test_v4_coarse_mapper.py
```

### Cache identity / reuse

- `FeatureCacheSpec` key 绑定 source audio SHA-256、`sr`、`hop_length`、MFCC dimensionality、feature implementation ID 与 librosa version；
- `v4_run.py` 生成在 `primary/` 与 `transitions/` 下的 coarse outputs 会自动解析到同一 V4-local `cache/features` 目录，因此同一 source 的后续 transition coarse 可以直接复用 numeric feature bundle；
- cache hit 后不再 decode source audio，也不再运行该 source 的 HPSS/chroma/MFCC；
- standalone coarse CLI 可用 `--feature-cache-dir` 显式指定缓存目录。

### Safety / reproducibility

- cache 不保存歌词、source absolute path、Source-to-Mix mapping、review decision 或完整 command；
- cache miss/corrupt/incompatible entry 都回到 SHA-bound source audio 正常重算；cache write 失败不会 BLOCK production；
- formal payload/artifact 不引用 cache path 或 cache file，仍绑定完整 source/mix SHA、task fingerprint、profile、algorithm version 和 upstream asset artifact；
- `build_coarse_timewarp()` 只有在 cached `FeatureBundle` 的 `sr/hop_length` 与当前 coarse config 完全一致时才接受；
- 新测试验证 cache roundtrip、key isolation、corruption-as-miss，以及 direct source extraction 与 precomputed source features 产生完全相同的 coarse result。

该优化只减少重复 source feature extraction；不改变 slope grid、candidate search、score/margin threshold、cut detection、fine routing、review policy、canonical/Source-to-Mix authority 或 release semantics。

下一步性能优化优先做 safe artifact resume，然后再做 bounded worker 并发；普通歌曲 sparse probe 必须先有真实 benchmark/calibration，并在不确定时自动 fallback 当前 authoritative path。

---

## 2026-08-18 — Text-Only Subtitle Repair Fast Path

真实生产反馈表明：当规范歌词已经可靠、剪映 SRT 时间轴明确要求冻结，只需要修正字词时，让任务进入完整 Source-to-Mix/coarse/fine/transition 链会产生与目标无关的音频计算。本轮因此增加独立的 text-only fast path，而不是降低完整 V4 的检查强度。

新增：

```text
lyric_aligner/text_repair.py
scripts/v4_text_repair.py
scripts/test_v4_text_repair.py
```

### Text-only contract

- CLI 只读取 source SRT 与按歌曲顺序重复传入的 canonical LRC/TXT/QRC；不读取 audio，不导入/调用 librosa，不运行 coarse、fine、transition、ASR 或 forced alignment；
- LRC 多时间戳会展开为重复 canonical occurrence；Enhanced LRC `<mm:ss.xx>` 与 QRC `(start,duration)` 词级时间标记只被剥离，不用于修改 SRT 时间；
- 匹配前使用 Unicode NFKC + casefold，并忽略 punctuation/whitespace/control 字符；随后执行单调 fuzzy sequence alignment；
- 只有达到自动阈值且长度结构安全的 1:1 pair 才替换文字；短行采用更严格门槛；低置信、分段不一致、未匹配 cue 保持原字幕并报告 `review_required`；
- 输出重新解析后逐 cue 比较原始 index line 与 timing line，任何变化都会 fail closed；CLI 还拒绝 `--out` 与 `--source-srt` 指向同一文件，因此不会原地覆盖原件；
- report 记录 cue/canonical/replacement/review 数、逐 cue decision，以及 source/canonical/output SHA-256；不记录音频 lineage，因为该路径明确不消费音频。

CLI 在 `status=ready` 时退出 0；只要存在未解决文本候选就输出 `review_required` 并退出 2。这个结果只代表“冻结时间轴前提下的文字修复是否还有待复核项”，不能被解释成完整 V4 的 timing/release confidence。

### Safety / fallback

Text-only path 的启用条件是任务语义明确为 **preserve timeline**，不是“中文就少检查”。如果存在缺失 cue、需要新增/拆分/合并字幕、cut/overlap、时间边界可疑或任何需要声学判断的情况，必须使用完整 V4。Canonical lyric 的文字真源地位不变；完整 V4 的 Source-to-Mix timing authority、threshold、review/fail-closed 与 release gate 均未改动。

回滚该 fast path 只需删除新增 module/CLI/test，不需要迁移现有 V4 artifact。

---

## 2026-08-18 — Verified Mix Hash Reuse

在 PR23 将 waveform decode 改为 bounded 后，coarse/fine CLI 仍存在一项重复整文件 I/O：`assert_manifest_paths()` 已经对 mix 重新计算 SHA-256 并与 task manifest 比对成功，随后构建 payload 时又调用 `sha256_file(args.mix_audio)` 再顺序读取整份 mix 一次。

本轮只删除 **同一子进程内的第二次重复 hash**：

```text
scripts/v4_coarse_align.py
scripts/v4_fine_align.py
```

两者仍先执行 `assert_manifest_paths()`；只有该校验确认当前 `--mix-audio` 路径与内容都和 task manifest 完全一致后，payload 才复用刚刚被验证过的 `task.inputs.audio.sha256`。因此正式 `mix_audio_sha256` 值与旧实现相同，没有跳过 manifest 内容校验，也没有改变 task fingerprint、artifact lineage 或任何对齐算法。

这一步仍未消除不同 coarse/fine 子进程之间各自的 manifest 验证读取。下一轮完整 V4 性能工作应继续评估 parent-verified execution context / safe artifact resume，在不削弱 standalone CLI fail-closed 语义的前提下减少跨子进程重复 I/O。
