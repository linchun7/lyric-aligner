# Lyric Aligner v4 生产运行手册

更新：2026-08-17  
适用版本：`4.0.0a4`

> 新真实任务采用 **v4 production-first**。不确定 mapping/cut/transition 必须 review/BLOCK，不静默回退 v3.9。a4 新增 package-native final composer/renderer，正式路径变为 `v4_run → v4_render → v4_validate_release`。

## 1. Task Manifest

```powershell
python scripts/init_task.py `
  --task "任务名" `
  --source-srt "private/任务名/input/source.srt" `
  --audio "private/任务名/input/mix.wav" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --source-audio-dir "private/任务名/input/source-audio"
```

BPM 文件可选；BPM 不是 Source-to-Mix 正确映射的必要输入。

## 2. Reconstruction：`v4_run`

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --out-dir "output/<任务>/v4" `
  --git-commit "<commit>"
```

可选：`--profile`、`--language-map`、`--middle-cut-map`、`--lyric-role-map`。

执行链：

```text
Asset Resolution
 → Primary Coarse
 → AFFINE first / PIECEWISE_RATE fallback
 → Selective Fine
 → Canonical Timeline Projection
 → Shared-boundary LEFT/RIGHT evidence
 → Transition Probe
 → ready_for_render | review_required
```

`legacy_fallback_used` 必须为 `false`。

### Primary 与 transition window

Primary interval 使用 nominal start 分割单曲主 timeline；但 nominal start 不是真实声学硬边界。每个相邻边界另外建立 profile 控制的共享窗口（当前 bootstrap 为 ±10s），左右原曲都在同一 mix 区间取证。

共享窗口重叠 ≠ 已确认叠唱。强双侧 evidence 或 repeated-occurrence ambiguity 都进入 review。

## 3. Final Render：仅允许 `ready_for_render`

```powershell
python scripts/v4_render.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/v4_run.json" `
  --run-artifact "output/<任务>/v4/v4_run.artifact.json" `
  --track-assets "output/<任务>/v4/assets/track_assets.json" `
  --asset-artifact "output/<任务>/v4/assets/track_assets.artifact.json" `
  --final-srt "output/<任务>/v4/final/FINAL.srt" `
  --report "output/<任务>/v4/final/FINAL.csv" `
  --qa-json "output/<任务>/v4/final/FINAL.qa.json" `
  --artifact-out "output/<任务>/v4/final/FINAL.render.artifact.json" `
  --git-commit "<commit>"
```

Renderer 会重新验证：

- task fingerprint；
- v4 algorithm version；
- calibration profile version/id；
- run artifact materialized hash；
- supplied TrackAsset artifact **必须就是该 production run 的 upstream**；
- 每个 canonical timeline artifact 必须属于 production-run upstream；
- 每个 timeline artifact 必须由同一 TrackAsset artifact 派生；
- timeline 的 occurrence/track/ordinal/canonical-selection 必须与 `ResolvedAssetBinding` 一致；
- run occurrence set 必须精确覆盖全部 resolved TrackOccurrences；
- timeline/entity/materialized hash 不得漂移。

只要 run 是 `review_required`、含 issue、发生 legacy fallback、资产链被替换、timeline 被修改或 profile/version 不一致，就拒绝 render。

## 4. a4 Final Timeline Composer 规则

最终文字只来自 canonical projected timeline，不从 Jianying/ASR 重新生成歌词。

当前 bootstrap `render` profile：

```text
minimum_cue_duration_ms = 250
maximum_line_duration_ms = 12000
open_line_duration_ms = 5000
word_timing_tail_ms = 120
```

语义：

- cue 必须裁剪到当前 occurrence 有效窗口；
- line-LRC 的超长 next-line gap 不让上一句歌词穿过整段长间奏常驻；
- 最后一行没有明确 end 时使用有限 open-line duration，并受 occurrence end 限制；
- Enhanced LRC/QRC 有词级结束时只增加短 tail；
- cue 过短时 BLOCK，不擅自延长覆盖下一句；
- 未确认跨曲 cue overlap 直接 BLOCK，不自动拼两路歌词。

这些值属于 calibration profile，不是永久真理。

## 5. Final outputs

`v4_render` 同时生成：

```text
FINAL.srt
FINAL.csv
FINAL.qa.json
FINAL.render.artifact.json
```

Audit CSV 与 SRT 每个 cue 严格绑定：时间、正文、cue id、text hash、TrackOccurrence、canonical line index。

QA 只有在 run 已完全 review-free 且 composer 无异常时才写：

```text
passed=true
structurally_valid=true
fully_reviewed=true
publish_ready=true
review_candidate_count=0
```

QA 同时记录 a4 calibration profile id/version。这里的 `publish_ready` 仍必须经过下一步 release integrity 验证后才成为实际 release artifact。

## 6. Release Integrity

```powershell
python scripts/v4_validate_release.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --final-srt "output/<任务>/v4/final/FINAL.srt" `
  --report "output/<任务>/v4/final/FINAL.csv" `
  --qa-json "output/<任务>/v4/final/FINAL.qa.json" `
  --algorithm-version "4.0.0a4" `
  --upstream-artifact "output/<任务>/v4/final/FINAL.render.artifact.json" `
  --out-manifest "output/<任务>/v4/final/release.artifact.json"
```

对于 v4，Release Guard 现在要求：

1. 至少一个 upstream artifact；
2. **恰好一个 `final_render` upstream**；
3. `--algorithm-version` 必须与 upstream algorithm version 完全一致；
4. upstream calibration profile id/version 必须存在，并与 `FINAL.qa.json` 完全一致；
5. `final_render` artifact 中的 `final_srt` / `audit_csv` / `qa_json` size 与 SHA-256 必须逐一匹配当前三个实体文件；
6. SRT 与 audit 仍要逐 cue 核对时间、文本、cue id、text hash；
7. QA 必须 `passed/structurally_valid/fully_reviewed/publish_ready=true` 且 `review_candidate_count=0`。

因此即使有人把 SRT、CSV、QA 三份文件一起协调修改，使三者彼此仍然一致，只要没有重新生成与之对应的 `FINAL.render.artifact.json`，release 仍必须失败。

成功生成 `release.artifact.json` 后，才视为该 v4 产物通过当前发布完整性门禁。

## 7. a3 → a4 迁移

a4 的 `V4CalibrationProfile` 新增 `render` 区段，profile version 更新为：

```text
production-bootstrap-2026-08-17-a4
```

因此：

> **a3 的 TrackAsset/profile artifact 不允许直接拿给 a4 renderer。**

升级 a4 后应从 `v4_run` / Asset Resolution 重跑，使整条 artifact chain 使用同一 a4 algorithm/profile identity。不要手工往旧 JSON 补字段。

## 8. `review_required`

当前 renderer 不处理未解决 issue：

- blocked TimeWarp；
- source discontinuity / middle-cut candidate；
- repeated source occurrence ambiguity；
- transition overlap candidate；
- transition uncertain interval。

下一阶段提供 fingerprinted Review Decision artifact。对于人工确认“没有 overlap”的 transition，可安全解除该 issue；人工确认“确有 overlap”时必须生成 transition-aware 双路 canonical timeline，不能只把 BLOCK 布尔值改成 false。

## 9. 单 Stage CLI

这些主要用于诊断/calibration/artifact 重现：

- `v4_resolve_assets.py`
- `v4_coarse_align.py`
- `v4_fine_align.py`
- `v4_probe_transition.py`
- `v4_profile.py`

普通生产任务优先完整执行：

```text
v4_run → v4_render → v4_validate_release
```

## 10. 当前验证状态

PR #3 的 GitHub Actions 当前被账户付款/Spending Limit 阻断，runner 没有启动；这不是代码测试失败。Actions 恢复后必须对**最新 a4 head**重新执行 Python 3.10/3.12/3.14、run→render→release synthetic E2E、Documentation Contract、Skill/privacy/environment/diff-check 与 ASR environment。未全绿前不合 main。

## 11. 当前下一步

1. Replayable Review Decision artifact；
2. confirmed-overlap transition timeline composition；
3. 真实私有任务 calibration / blind-test；
4. Editor Evidence + LanguageSpan 进入最终 cue scoring；
5. 根据真实误差决定 Forced Alignment / ASR v2。

不能宣称当前 bootstrap profile 已最优，也不能宣称真实准确率已提高固定百分比。真实任务数据必须通过 evaluator/calibration 得出结论。
