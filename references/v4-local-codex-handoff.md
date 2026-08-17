# v4 本地 Codex 接手任务：恢复 v3.9 基线并接线 v4

更新：2026-08-17  
目标仓库：`linchun7/lyric-aligner`  
当前 v4 分支：`agent/v4-accuracy-foundation`  
当前 PR：#1（Draft）

> 本任务只用于**本地 Git 工作树**。不要在 GitHub 网页直接手改约 5000 行的 `redo_karaoke_pipeline.py`。关键目标是先恢复/冻结真实 v3.9，再把已经完成的 v4 package 安全接入 legacy CLI。

## 1. 背景

远端 `main` 当前提交的 legacy 主脚本仍是 v3.8，但之前实际使用和验收的 v3.9 存在于未提交工作树。v3.9 已知包含：

- `review-audio-edits`；
- 多个 confirmed middle cut；
- 连续分段变速；
- Enhanced LRC / QRC token timing；
- 跨曲叠唱 review/confirmed interval；
- preserve/hybrid/rebuild；
- v3.9 QA/publish gate；
- 相应单元与 end-to-end 测试。

v4 新 package 已在 PR #1 实现并独立测试：

```text
lyric_aligner/contracts/*
lyric_aligner/qa/*
lyric_aligner/assets/*
lyric_aligner/audio/*
lyric_aligner/io/*
lyric_aligner/text/*
```

以及 `scripts/v4_*.py` stage CLI。

本地恢复版 v3.9 + 当前 v4 package 的 `unittest discover` 已验证 128/128 通过；但远端尚未拥有真实 v3.9 legacy tree。

## 2. 禁止事项

1. **不要直接把 PR #1 merge 到 main。**
2. 不要用远端 v3.8 `redo_karaoke_pipeline.py` 覆盖你本机仍存在的真实 v3.9 工作树。
3. 不要在一个 commit 同时完成“恢复 v3.9”和“接入 v4”。必须分开，方便 bisect/rollback。
4. 不要为了减少冲突而删除 v3.9 的 middle-cut、variable-speed、QRC、overlap、QA 行为。
5. 不要把 `middle_cut=true` 改成自动确认 cut。
6. 不要把 BPM 恢复成硬 slope / 全曲固定 ratio 的权威来源。
7. 不要把剪映韩/日/粤/unknown 文本恢复成 canonical 高权重来源。
8. 不要提交 `private/`、`output/`、真实音视频、真实歌词、任务 QA。

## 3. 第一步：保护当前本地工作树

```bash
git status --short
git branch --show-current
git remote -v
```

如果当前本机仍有 v3.9 未提交修改：

```bash
git diff --stat
git diff > ../lyric-aligner-v3.9-recovery.patch
```

额外备份关键文件：

```bash
cp scripts/redo_karaoke_pipeline.py ../redo_karaoke_pipeline.v3.9.recovery.py
cp scripts/test_redo_karaoke_pipeline.py ../test_redo_karaoke_pipeline.v3.9.recovery.py
```

不要先 `git reset --hard`。

## 4. 第二步：建立独立 v3.9 baseline 分支/提交

从远端 `main` 的当前提交建立恢复分支：

```bash
git fetch origin
git switch -c agent/v3.9-baseline origin/main
```

把**真实 v3.9 工作树文件**恢复进这个分支。优先使用你本机原工作树/备份，而不是根据聊天重新生成。

恢复后先检查：

```bash
grep -n 'ALGORITHM_VERSION' scripts/redo_karaoke_pipeline.py
python -m compileall -q scripts
python -m unittest discover -s scripts -p 'test_*.py'
python scripts/validate_skill.py .
python scripts/privacy_scan.py
```

必须确认：

- 算法版本确实是 3.9；
- middle-cut review/apply 仍在；
- Enhanced LRC/QRC 仍在；
- variable-speed runs 仍在；
- overlap review/confirmed interval 仍在；
- 旧真实任务不因恢复过程出现功能回退。

然后**只提交 v3.9 恢复**：

```bash
git add <v3.9恢复涉及的文件>
git commit -m 'restore lyric aligner v3.9 production baseline'
```

建议打本地/远端 tag（确认测试后）：

```bash
git tag v3.9-baseline
```

不要在这个 commit 引入任何 v4 package 接线。

## 5. 第三步：把 v4 分支建立在 v3.9 baseline 之上

当前远端已有 `agent/v4-accuracy-foundation`。推荐做法：

```bash
git fetch origin
git switch agent/v4-accuracy-foundation
git merge --no-ff agent/v3.9-baseline
```

会有冲突的高概率文件：

```text
scripts/evaluate_dataset.py
scripts/test_evaluate_dataset.py
scripts/language_profiles.py
scripts/validate_multilingual_asr.py
scripts/test_redo_karaoke_pipeline.py
references/*
```

冲突策略：

- `scripts/redo_karaoke_pipeline.py`：以真实 v3.9 为 legacy 基线，然后单独接 v4；
- evaluator：保留 PR #1 的 v4 sequence/split/merge/onset/offset/cut/overlap 指标；
- language profile：保留 v4 `yue/auto/generic`；
- ASR：保留 v3.9 production 行为，同时保留 v4 auto/generic detect 改动；
- tests：两边测试取并集，禁止为了 merge 方便删除回归；
- docs：保留 v4 文档，并把 v3.9 恢复 commit/tag 写入状态文档。

冲突解决后：

```bash
python -m compileall -q lyric_aligner scripts
PYTHONPATH=.:scripts python -m unittest discover -s scripts -p 'test_*.py'
python scripts/validate_skill.py .
python scripts/privacy_scan.py
```

任何失败先修，不要跳过。

## 6. 第四步：接入 v4 Asset Resolver

目标：legacy production 不再调用“模糊失败后直接取最相似 LRC/source”的逻辑。

推荐兼容策略：

1. `init/prepare` 前先生成 fingerprinted `track_assets.json`；
2. legacy `parse_song_list` / source lookup 改为消费 TrackAsset/TrackOccurrence；
3. 保留旧解析函数作为 legacy diagnostic fallback，但生产模式禁止无阈值 fallback；
4. production 必须校验 asset artifact/task fingerprint；
5. occurrence identity 从 `track.title` 升级为 `occurrence_id`，至少新 v4 路径不得再只以 title 做字典 key。

已有 CLI：

```text
scripts/v4_resolve_assets.py
```

已有文档：

```text
references/v4-runtime-guide.md
references/v4-lyric-role-overrides.md
```

## 7. 第五步：接入 Editor Evidence / LanguageSpan

目标：剪映对不同语言采用不同可信度。

规则不能改变：

```text
zh/en       -> direct_text
ko/ja       -> phonetic_hint
yue         -> timing_hint
auto/generic-> timing_hint
```

行内混合语言使用：

```text
lyric_aligner/text/language_spans.py
```

例如韩+英一行：英文 span 可以使用剪映英文 text evidence；韩文 span 不能因为同 cue 中有英文而整体获得 direct-text 权重。

生产 sequence/boundary score 必须记录 editor evidence mode/provenance，不能只得到一个不可解释总分。

## 8. 第六步：接入 v4 Source→Mix TimeWarp

不要一次删除 legacy waveform mapping。推荐 shadow/A-B 接入：

1. legacy audio-align 继续产生旧 mapping；
2. 同时调用 v4 coarse mapper 产生 v4 mapping；
3. 先写入独立 artifact/审计，不直接改变终稿；
4. 私有 calibration 确认后，再把 v4 设为主 mapping；
5. legacy mapping 保留一个版本作为 rollback，之后再移除。

v4 链：

```text
HPSS harmonic
 -> Chroma CENS + MFCC
 -> multi-candidate windows
 -> monotonic global path
 -> AFFINE first
 -> evidence-driven PIECEWISE_RATE
 -> selective fine alignment
```

已有实现：

```text
lyric_aligner/audio/features.py
lyric_aligner/audio/coarse_mapper.py
lyric_aligner/audio/timewarp.py
lyric_aligner/audio/fine_alignment.py
scripts/v4_coarse_align.py
scripts/v4_fine_align.py
```

### BPM 约束

BPM 只能作为 soft prior，不得再次：

- 在少锚点时硬锁 slope；
- 把整首 source 按一个 BPM ratio time-stretch 后视为唯一真值。

### Piecewise 约束

- clean AFFINE 不升级；
- local slope 可以突变；
- source position 连续的 slope change 不是 cut；
- source discontinuity 才是 cut candidate；
- `middle_cut=true` 仍须 review。

## 9. 第七步：接入歌曲交界

相邻歌曲的 nominal boundary 不再是硬 end。

在边界 ±有限 margin 分别对 A/B 做 source search，再调用：

```text
scripts/v4_probe_transition.py
```

结果只有三类语义：

- clear sequential/no overlap；
- overlap candidate -> review；
- ambiguous -> BLOCK。

禁止自动把两首歌词拼成一行。

## 10. 第八步：接入 Final Integrity / Artifact Lineage

legacy finalize/qa 输出后必须自动执行 v4 release integrity：

```text
FINAL.srt
FINAL audit.csv
FINAL QA.json
       ↓
strict one-to-one binding
       ↓
release ArtifactManifest
```

正式 production release 不应再依赖人工单独运行 `v4_validate_release.py`；接线完成后由主 CLI 自动调用。

任何 stage 必须记录：

- task fingerprint；
- algorithm version；
- normalized config；
- dependency/model revision（适用时）；
- upstream artifact IDs；
- output hashes。

## 11. 第九步：私有 A/B 与 Calibration

不要先上 Forced Alignment。

先用同一批真实私有任务比较：

```text
v3.9 baseline
v4 shadow mapping
```

必须测：

- sequence WER；
- line exact precision/recall/F1；
- missing/extra/wrong-order；
- split/merge；
- onset/offset MAE/P50/P90/P95；
- cut precision/recall；
- overlap precision/recall/IoU；
- track attribution；
- review candidates / 10min；
- runtime / audio minute；
- false-ready。

协议：`references/dataset-protocol.md`。

只有真实 calibration 以后才能调整当前 bootstrap threshold。

## 12. 验收门槛

接线完成后至少运行：

```bash
python -m compileall -q lyric_aligner scripts
PYTHONPATH=.:scripts python -m unittest discover -s scripts -p 'test_*.py'
python scripts/validate_skill.py .
python scripts/check_environment.py
python scripts/privacy_scan.py
git diff --check
```

当前恢复版 v3.9 + v4 package 的本地参考结果：

```text
128/128 tests passed
```

但本地接线后测试数量可能增加，不能以“至少 128”替代“全部测试必须通过”。

## 13. PR/提交建议

保持 PR #1 Draft，推荐新增清晰 commit：

```text
restore lyric aligner v3.9 production baseline
wire v4 asset identities into production flow
wire language-aware editor evidence
shadow v4 TimeWarp beside legacy mapping
wire transition evidence and release integrity
add private calibration hooks
```

每个 commit 后跑相关测试；最终全套通过后再考虑把 PR 标记 Ready for Review。

## 14. 完成后必须更新文档

至少更新：

- `references/v4-status.md`
- `references/v4-change-record.md`
- `references/v4-implementation.md`
- `references/v4-runtime-guide.md`
- 如有失败/拒绝的算法实验，更新 `references/v4-experiments.md`

不要只修改代码而不记录关键生产语义。
