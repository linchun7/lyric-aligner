---
name: lyric-aligner
description: Reconstruct multilingual canonical lyric timelines for edited music mixes using fingerprinted TrackAssets, source-to-mix audio TimeWarp, selective fine alignment, transition/overlap evidence, editor/ASR evidence, review-blocking QA, and immutable artifact lineage. Use for music mixes, karaoke subtitles, lyric timing correction, repeated choruses, local speed changes, middle cuts, and overlapping song transitions.
---

# Lyric Aligner

使用本 Skill 将中、英、韩、日、粤及其他语言的规范歌词对齐到经过调速、裁切、混剪和加节拍器的音乐 mix。

当前默认架构为 **v4.0.0a3 production-first**：新真实任务优先进入 v4；无法可靠解释的片段进入 review/BLOCK，**不得静默回退 v3.9**。

## 不可违反的原则

1. **Canonical lyric 是最终文字与顺序真源。** ASR、剪映/编辑器文字只能提供证据，不能替代规范歌词。
2. **Source-to-Mix audio mapping 是主要时间真源。** 编辑器 SRT 时间可以作为 evidence，但不再拥有默认时间权威。
3. 大多数歌曲先走 AFFINE；只有真实声学 evidence 证明固定倍率解释不足时才升级 PIECEWISE_RATE。
4. BPM 只允许作为 soft prior。不得用一个全曲 BPM ratio 强制锁死 Source-to-Mix mapping。
5. 局部 rate change 可以突然发生；`rate change != cut`。只有 source-position discontinuity 才能产生 cut candidate。
6. `middle_cut=false|true|unknown` 只改变检测/review策略。任何 middle cut 都不得自动 confirmed 或自动删除歌词。
7. 曲首/曲尾 trim 不属于 middle cut，可由 Source-to-Mix entry/exit 自然解释。
8. `TrackAsset / TrackOccurrence / ResolvedAssetBinding` 一旦确定，下游不得重新 fuzzy resolve source audio、LRC 或 same-timestamp original。
9. 重复副歌按实际 occurrence 区分；不得仅因文本相同去重，也不得在 repeated source occurrence 歧义下强行选一个高分候选。
10. 相邻歌曲 nominal start 是 prior，不是硬边界。左右 source 必须可以在同一个 transition search window 取证。
11. overlap candidate 不等于 overlap truth。未确认的 cut/overlap/mapping conflict 必须 BLOCK/review。
12. 两首歌曲同时演唱时不得把两路歌词拼成一行；最终应保留两个独立 canonical lyric stream。
13. LRC 未收录的 `Uh/Oh/Yeah/Ah/Ooh/Huh/Na-na` 等发声不得自动加入最终字幕。
14. 通用算法不得包含某首歌、某个 cue、具体时间点、具体错词或当前任务名称的硬编码修补。
15. 所有任务与 stage artifacts 必须绑定 task fingerprint、algorithm version、calibration profile、upstream artifact IDs 和 materialized output SHA-256。
16. 所有实质性/关键性生产变更必须按 `references/documentation-contract.md` 同步 owning docs；CI 不通过不得合入主线。
17. `ready_for_render` 不等于 `publish_ready`。最终发布仍必须经过 final SRT/QA/release integrity gate。

不能保证物理不可辨片段绝对 100% 自动判断正确。系统应该暴露不确定性并阻止错误发布，而不是猜测。

## 源码与私有数据边界

可提交：

- `lyric_aligner/` package；
- `scripts/` 通用 CLI、算法、测试、环境/任务契约；
- `references/` 架构、运行、变更、数据协议；
- CI / dependency / Skill metadata。

默认不得提交：

- `private/` 中的真实音频、SRT、LRC、歌单、BPM、任务 QA/人工结论；
- `output/` 中的任务产物、缓存和回滚包；
- 本机绝对路径、账号、token、凭据和个人配置。

已获用户授权的真实素材可在私有环境用于 calibration、blind-test 和 regression；未经明确要求不得上传真实任务数据。

## 权威资源导航

新 v4 工作首先读取：

- **真实运行：** `references/v4-runtime-guide.md`
- **当前状态：** `references/v4-status.md`
- **架构与算法：** `references/v4-implementation.md`
- **架构复盘：** `references/v4-architecture-review-2026-08-17.md`
- **关键变更：** `references/v4-change-record.md`
- **文档同步契约：** `references/documentation-contract.md`
- **真实数据/盲测协议：** `references/dataset-protocol.md`
- **歌词 role override：** `references/v4-lyric-role-overrides.md`

`references/workflow.md` 与 `references/change-record.md` 仍保存 schema 2.0 / legacy QA 历史和迁移信息，但 **v4 新任务运行方式以 `v4-runtime-guide.md` 为准**。

## 标准执行顺序

### 1. 环境与任务 manifest

```powershell
python scripts/check_environment.py
python scripts/check_environment.py --asr

python scripts/init_task.py `
  --task "任务名" `
  --source-srt "private/任务名/input/source.srt" `
  --audio "private/任务名/input/mix.wav" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --source-audio-dir "private/任务名/input/source-audio"
```

`--bpm-changes` 是可选输入，不存在就不要传。BPM 不是 v4 正确结果的必要条件。

任务初始化后以：

```text
private/<任务>/qa/task_manifest.json
```

作为所有 stage 的身份根。

### 2. 新任务默认运行 v4

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --out-dir "output/<任务>/v4" `
  --git-commit "<当前commit>"
```

可选：

- `--profile`
- `--language-map`
- `--middle-cut-map`
- `--lyric-role-map`

`v4_run` 当前负责：

```text
Asset Resolution
 → Primary Coarse
 → AFFINE / evidence-driven PIECEWISE_RATE
 → Selective Fine
 → Canonical Timeline Projection
 → shared-boundary Transition Evidence
 → v4_run summary/artifact
```

输出：

- `ready_for_render`：mapping/timeline 前置链没有 unresolved review；
- `review_required`：至少一个 mapping/cut/transition/ambiguity 需要处理。

明确记录：

```json
"legacy_fallback_used": false
```

### 3. 单 Stage CLI

以下命令用于诊断、calibration、重现 artifact，不应成为普通任务的手工必跑链：

- `v4_resolve_assets.py`
- `v4_coarse_align.py`
- `v4_fine_align.py`
- `v4_probe_transition.py`
- `v4_profile.py`
- `v4_validate_release.py`

不要因为单 stage 看起来成功就跳过 `v4_run` 的其他 evidence/review 阶段。

### 4. 当前 a3 最终字幕边界

`v4.0.0a3` 已实际接管：

- asset/canonical identity；
- Source-to-Mix mapping；
- selective fine alignment；
- transition evidence；
- canonical timeline reconstruction。

但 package-native 的 final timeline composer / SRT renderer 仍在下一阶段建设。

因此当前不得把：

```text
ready_for_render
```

解释成：

```text
publish_ready
```

最终 SRT 一旦由当前/后续 renderer 生成，必须仍通过严格 release integrity 校验，且 unresolved review count 必须为 0。

## Calibration 原则

所有生产阈值进入完整 `V4CalibrationProfile`，不能散落在 AI 提示、CLI 临时参数或隐藏函数默认值里。

```powershell
python scripts/v4_profile.py --write-default profile.json
python scripts/v4_profile.py --validate profile.json
```

真实任务发现问题后：

1. 先记录匿名 failure/regression case；
2. 判断是通用算法、profile、语言策略还是任务级明确事实；
3. 通用改动增加/更新测试；
4. profile 改动生成新的 named profile/version；
5. blind-test 不得参与调参；
6. 更新相关文档并通过 Documentation Contract。

## 当前版本与回归

- 当前 v4 package：`4.0.0a3`。
- v3.9 不再是新任务默认生产算法；只保留历史比较/仓库级 rollback 价值。
- 当前公开 Git 历史从脱敏根提交 `4ce42eb` 开始；更早已清理历史不能用普通 Git 恢复。
- 每次实质性改动至少运行：

```powershell
python -m compileall -q lyric_aligner scripts
python scripts/validate_docs_contract.py
python -m unittest discover -s scripts -p "test_*.py"
python scripts/validate_skill.py .
python scripts/privacy_scan.py
python scripts/check_environment.py
git diff --check
```

CI 当前覆盖 Python 3.10 / 3.12 / 3.14，并单独检查 ASR 环境。

## 后续优先级

1. package-native final timeline composer / SRT renderer；
2. review decision artifact（cut / overlap 等人工确认可重放）；
3. real-task calibration / blind-test；
4. Editor Evidence + LanguageSpan 进入最终 cue decision；
5. 根据真实误差再决定 Forced Alignment / ASR v2 / vocal local alignment。

不要在前四项尚未形成真实数据闭环时，为“模型更高级”而无条件增加重型依赖。
