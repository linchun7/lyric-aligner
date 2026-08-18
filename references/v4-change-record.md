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
- P6 ASR second-pass execution/composite：`6eacacc50e885684b0265e3abea729b19b1b7725`。

P3 validate #493、P4 #517、P5 #530、P6 #545 均在 ASR + Python 3.10/3.12/3.14 compile/docs/full unit-E2E/Skill/privacy/environment/diff-check 全绿后合入。

---

## 2026-08-18 — P7 External Source Forced Alignment

### 1. 目标

建立一个 backend-neutral、真实 external subprocess 可运行的 source-side forced-alignment 协议；不把 ASR 冒充 forced alignment，不假装 WhisperX/SOFA/MFA 已安装。

新增：

```text
lyric_aligner/alignment/forced_executor.py
scripts/v4_execute_forced_alignment.py
references/forced-alignment-protocol.md
```

更新：

```text
lyric_aligner/alignment/backends.py
lyric_aligner/alignment/__init__.py
```

### 2. Authority

固定：

```text
canonical_text_authority = canonical_lyrics_only
timing_authority = auxiliary_source_forced_alignment_evidence
```

Forced-alignment evidence 不直接改 canonical/final timing。

### 3. Source binding / lineage

CLI 必须验证：

- exact task fingerprint/input hashes；
- source run artifact；
- P3 alignment plan artifact；
- `asset_resolution / track_assets` artifact；
- asset artifact 是 source run upstream；
- canonical timeline artifacts 是 source run upstream；
- source audio live SHA；
- occurrence/track identity；
- planner canonical text SHA 与 current timeline 一致。

源音频只从已解析 `track_assets` 取得，不再 filename guess。

### 4. External JSON protocol

Config：

```text
command
backend_id/backend_version
model_id/model_revision
timeout_seconds
```

Command 使用 `shlex` 拆分且不通过 shell；backend registry/runner 只用第一个 token 判断 executable 是否存在，后续 args 保留。

调用：

```text
<command> --request <temp-request> --response <temp-response>
```

Request 可临时含 canonical raw text + local source path；response 必须回显 exact protocol/job/backend/model identity。

### 5. Response contract

时间统一为 absolute source milliseconds。

Required line boundary：

```text
line_source_start_ms
line_source_end_ms
```

Optional spans：

```text
char_start/char_end
source_start_ms/source_end_ms
confidence
```

Char/time spans 必须单调、非重叠、在 canonical/source window 内；confidence 若存在必须 `[0,1]`。

### 6. Privacy

正式 evidence 不复制 canonical raw text、source path、完整 command、backend stdout/stderr。

输出 canonical line/fragment SHA、char offsets、source timing/confidence、source audio SHA、backend/model identity。

Artifact 只记录 `command_sha256` 和 executable basename。

### 7. Artifact

```text
stage = source_forced_alignment_evidence
role = forced_alignment_evidence
```

Upstream：alignment plan + asset_resolution + source run + exact canonical timelines。

### 8. Tests

Package：

- exact source-window request；
- output 无 raw canonical text；
- explicit empty selection 不解析/执行 command；
- source SHA tamper fail；
- canonical SHA mismatch fail；
- response model revision mismatch fail；
- out-of-window span fail；
- command-with-arguments registry check。

CLI E2E：使用临时 Python fake aligner **真实 subprocess** 完成 request/response；并测试 executable-not-found 有工作时非零失败。

### 9. GitHub Actions 边界

P7 Actions 可验证 external protocol 本身，但不能宣称 WhisperX/SOFA/MFA 或任何真实 checkpoint/G2P 已安装、已运行或已证明歌声准确率。

真实 forced-aligner production promotion 仍要求 model/checkpoint/language resources/license/runtime identity + private calibration/blind-test。
