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
- P3 local acoustic evidence：`cd3420750c06a55fa1af7d6314ec56971e728928`。

P3 PR #12 latest head `c6c66f6a1fb1e2ee9d43e26064e7efc30fce3cbe` 的 validate #493 在 ASR + Python 3.10/3.12/3.14 compile/docs/full unit-E2E/Skill/privacy/environment/diff-check 全绿后 squash merge。

---

## 2026-08-18 — P4 Evidence Fusion Shadow

### 1. 目标

把已存在的独立 evidence family 汇总到 exact canonical line identity：

```text
Source-to-Mix canonical timeline
+ P2 editor_evidence_shadow
+ P3 asr_evidence_local
        ↓
evidence_fusion_shadow
```

P4 只做诊断/校准数据准备，不改变 canonical lyric 或 Source-to-Mix authority。

### 2. 新增模块 / CLI

```text
lyric_aligner/evidence/fusion.py
scripts/v4_fuse_evidence.py
references/evidence-fusion-shadow.md
```

Package export 同步到：

```text
lyric_aligner/evidence/__init__.py
```

Artifact：

```text
stage = evidence_fusion_shadow
role  = evidence_fusion
```

### 3. 固定安全语义

```text
mode = shadow_only
policy_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

这些字段在 root/line/artifact evidence 中都必须保持不可发布语义。

### 4. Bootstrap shadow states

```text
LOW      source timeline only
MEDIUM   source + 1 auxiliary boundary family
HIGH     source + editor + ASR，且二者 boundary disagreement <= conflict threshold
CONFLICT editor + ASR boundary disagreement > conflict threshold
```

默认：

```text
conflict_boundary_ms = 500
```

该阈值没有 real calibration 支持，只用于 shadow diagnostic。`HIGH` 不是 release confidence。

### 5. Family admission / selection

- `source_timeline` 是 primary timing authority；
- editor 只接受 P2 shadow line boundary proposal；
- ASR 只接受有 canonical line identity 且存在有效 local segment interval 的 jobs；
- occurrence-level ASR job 没有 line identity，不能冒充 line boundary evidence；
- 同一 line 多 ASR jobs 时，bootstrap 选 `canonical_text_support_score` 较高者，tie 由 job ID deterministic；这不是 calibrated model arbitration。

### 6. Lineage / fail-closed

`v4_fuse_evidence.py` 验证：

- task fingerprint；
- source run stage/role/output hash；
- exact effective canonical timeline artifact；
- editor/asr evidence output hash；
- auxiliary `source_run_artifact_id` 必须与当前 run artifact ID 相同；
- auxiliary artifact 必须把当前 source run 作为 upstream。

Canonical/editor text SHA mismatch、unknown line、cross-run evidence 都必须失败。

### 7. Privacy

Fusion output 不复制 raw canonical/editor/ASR text。保存 identity/hash/boundary/score/disagreement/shadow level。

即使输入 ASR artifact 是 `include_private_text=true`，fusion output 仍不复制原文。

### 8. Tests

新增：

- LOW/MEDIUM/HIGH/CONFLICT 直接 unit regressions；
- HIGH 仍 `release_gate_eligible=false`；
- canonical/editor SHA mismatch fail；
- auxiliary unknown line fail；
- artifact-level editor+ASR same-run fusion；
- cross-run auxiliary evidence fail；
- fusion output privacy。

### 9. 真实边界

P4 不能在没有 private reference truth 的情况下把 shadow level 升级成发布阈值。正确路径仍是：

```text
private calibration
-> 分语言/场景检查 family coverage 与 boundary error
-> 冻结 policy/thresholds
-> blind_test once
-> 才允许设计 calibrated release gate
```

P4 PR 必须以 latest-head GitHub Actions ASR + Python 3.10/3.12/3.14 全门禁为合并依据。
