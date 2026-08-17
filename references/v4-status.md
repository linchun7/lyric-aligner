# Lyric Aligner v4 当前实施状态

更新日期：2026-08-18  
当前开发分支：`agent/v4-evidence-fusion-shadow`  
当前 main：`cd3420750c06a55fa1af7d6314ec56971e728928`  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

## 1. 已进入 main

生产重建主链已完成：TrackAsset/canonical single truth、HPSS+Chroma/MFCC Source-to-Mix、AFFINE/PIECEWISE_RATE、Selective Fine、candidate review、confirmed overlap、confirmed cut/CUT_AWARE、partial-line fail-closed、cut+overlap safe composition、strict render/release。

已合入的 v4 增量：

```text
P1    strict calibration/blind framework
      1c6babe37067c217d14a7404aa0ed6a1c4779a00

P1.1  private dataset scaffold/readiness
      ad6c403a56209e945a9a61a1eeab1a4bc3c204b4

P2    editor/Jianying multilingual shadow evidence
      2e96569189ac6eb16d987fb2f304403696bc809b

P3    local acoustic evidence planner/backend/faster-whisper executor
      cd3420750c06a55fa1af7d6314ec56971e728928
```

P3 PR #12 latest head `c6c66f6a1fb1e2ee9d43e26064e7efc30fce3cbe` 的 validate #493：ASR environment + Python 3.10/3.12/3.14 compile/docs/full unit-E2E/Skill/privacy/environment/diff-check 全部 success 后 squash merge。

## 2. 当前 P4：Evidence Fusion Shadow

P4 只把已经存在的独立 evidence family 汇总到同一个 canonical line 身份上：

```text
Source-to-Mix canonical timeline
+ P2 editor shadow evidence
+ P3 local ASR evidence
        ↓
uncalibrated shadow support state
```

新增：

```text
lyric_aligner/evidence/fusion.py
scripts/v4_fuse_evidence.py
references/evidence-fusion-shadow.md
```

Artifact：

```text
stage = evidence_fusion_shadow
role  = evidence_fusion
```

固定安全语义：

```text
mode = shadow_only
policy_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

因此 P4 不修改 canonical text、Source-to-Mix、canonical timeline、renderer 或 FINAL.srt。

## 3. P4 shadow levels

输出：

```text
LOW
MEDIUM
HIGH
CONFLICT
```

Bootstrap 解释：

- `LOW`：只有 source timeline；
- `MEDIUM`：source + 1 个 auxiliary boundary family；
- `HIGH`：editor + ASR 都有 boundary proposal，且二者最大 onset/offset 分歧未超过 bootstrap conflict threshold；
- `CONFLICT`：editor + ASR 都有 proposal，但分歧超过 threshold。

**这些不是 calibrated accuracy/confidence。即使 `HIGH` 仍然 `release_gate_eligible=false`。**

默认 `conflict_boundary_ms=500` 只用于 shadow diagnostic，不能直接成为生产发布阈值。

## 4. Evidence family authority

```text
source_timeline
  authoritative_for_primary_timing = true

editor
  auxiliary shadow only

asr
  auxiliary shadow only
```

ASR 同一 line 有多个 local jobs 时，P4 只为 shadow fusion 选择一个 proposal，bootstrap 以 `canonical_text_support_score` 排序；这不是 calibrated model arbitration。

Occurrence-level ASR job 没有 canonical line identity，不会被强行映射成某条歌词的边界。

## 5. Privacy / lineage

P4 output 不复制：

- canonical raw lyric text；
- editor raw text；
- ASR raw observed text。

只保留 canonical text SHA、occurrence/track/line identity、source/editor/ASR boundary、必要分数、family count、disagreement 与 shadow level。

`v4_fuse_evidence.py` 必须验证：

- exact task fingerprint；
- exact source run artifact；
- effective canonical timeline artifacts；
- editor/asr evidence artifact output hash；
- auxiliary evidence `source_run_artifact_id` 与当前 source run 完全一致；
- auxiliary artifact lineage upstream 到当前 source run。

另一个 task/run 的 evidence 必须 fail-closed。

## 6. 当前 P3 真实运行边界

P3 已进入 main，但仍需区分：

```text
backend available
!= execution_ready
!= validated_on_singing
```

GitHub Actions #493 确认 `requirements-asr.txt` 的 faster-whisper environment 可以安装/检查；CI 仍**没有下载/运行真实 Whisper model**，也没有用户 private song/reference truth。

因此不能声称 GitHub Actions 已证明 large-v3/turbo 的真实歌声 word timing 或准确率。

Forced Alignment 当前仍只有 `source_forced_alignment` capability + local source-window planning + external command readiness；没有伪装 WhisperX/SOFA/MMS production-ready。

## 7. P4 tests

新增：

- source only -> LOW；
- exactly one auxiliary family -> MEDIUM；
- editor+ASR agreement -> HIGH；
- editor+ASR disagreement -> CONFLICT；
- HIGH 仍不可 release/auto-apply；
- canonical/editor text SHA mismatch fail-closed；
- auxiliary unknown line fail-closed；
- artifact-level same-run lineage；
- auxiliary evidence from another run fail-closed；
- fusion output 不泄露 private lyric/editor/ASR raw text。

P4 尚未通过本分支 latest-head GitHub Actions，所以当前不能宣称已可合并。

## 8. 仍未完成 / 真实阻塞

尚未完成：

- 用户授权 private calibration/blind dataset 的真实填充与指标；
- calibrated editor boundary application；
- production forced-aligner adapter + model/language/cache lineage；
- turbo -> large-v3 uncertain-window second-pass routing；
- vocal separation/local singing alignment；
- calibrated multi-family release gate；
- same-region cut+overlap joint acoustic model。

GitHub Actions 明确做不到/当前不做：

- 没有 private real-song/reference truth 时证明真实准确率提升；
- 在现有 workflow 中下载并跑大 Whisper/WhisperX/SOFA 模型来冒充真实验收；
- 未安装/未配置 forced aligner 时伪造 forced-alignment 结果。

> **当前正确表述：P0/P1/P1.1/P2/P3 已进入 main；P4 正在把 source/editor/ASR 合成可审计但未校准的 shadow fusion。任何 HIGH 都不等于可发布。**
