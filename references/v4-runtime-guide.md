# Lyric Aligner v4 生产运行手册

更新：2026-08-17  
适用版本：`4.0.0a3`

> v4 已采用 **production-first** 策略。新真实任务优先运行 v4；遇到不确定 Source-to-Mix、cut、transition/overlap 等情况时进入 `review_required`，**不静默回退 v3.9**。v3.9 只保留历史 commit/tag 作为比较与仓库级回滚点，不再维护第二套生产运行路径。

## 1. 默认入口

新任务默认使用：

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --out-dir "output/<任务>/v4" `
  --git-commit "<当前commit>"
```

可选任务级配置：

```powershell
  --profile "private/<任务>/qa/v4_profile.json" `
  --language-map "private/<任务>/qa/language_map.json" `
  --middle-cut-map "private/<任务>/qa/middle_cut_map.json" `
  --lyric-role-map "private/<任务>/qa/lyric_role_map.json"
```

`v4_run.py` 会从 task manifest 读取 mix audio、song list、lyrics directory、source-audio directory，并按同一 task fingerprint 执行完整 v4 evidence/timeline 链。

## 2. `v4_run` 当前实际执行链

```text
schema 2.0 Task Manifest
        ↓
Asset Resolution
TrackAsset + TrackOccurrence
        ↓
每首 Primary Coarse Alignment
        ↓
AFFINE first
  ├─ clean/high-confidence → 保持 AFFINE
  └─ ambiguous/complex → Selective Fine Alignment
        ↓
Effective TimeWarp
        ↓
Canonical Timeline Projection
        ↓
每个相邻歌曲边界：
LEFT source  ┐
             ├─ 同一个 boundary ± margin 搜索窗口
RIGHT source ┘
        ↓
Transition / overlap evidence
        ↓
v4_run.json + production_orchestration artifact
```

当前默认 transition search margin 为 calibration profile 中的 `10s`，不是硬编码业务真理；以后通过 real calibration 修改 profile，而不是直接改算法代码。

## 3. Primary interval 与 Transition window 的区别

主歌曲区间仍使用：

```text
当前 nominal_start → 下一首 nominal_start
```

用于生成当前单曲主 TimeWarp / canonical timeline。

但 **nominal_start 不是歌曲真实硬边界**。对于 A→B 交界，v4 另外建立：

```text
boundary - search_margin → boundary + search_margin
```

并让 A、B 两个 source 都在同一个 mix 区间独立取证。

因此：

- 搜索窗口重叠 ≠ 已确认叠唱；
- A/B 同时有强、非歧义声学证据 → `cross_track_overlap_candidate`；
- repeated chorus 等造成低 margin → `uncertain_intervals`；
- 两种情况都会进入 review，不能自动发布。

## 4. TimeWarp 生产语义

### 普通歌曲

优先拟合：

```text
source_time = intercept + slope * mix_time
```

即 `AFFINE`。

### 自动升级分段倍率

用户不需要声明歌曲是不是动态 BPM。只有 AFFINE 在 residual / drift / coverage 上明显不足，并且多个独立音频特征共同支持更复杂模型时，才升级：

```text
PIECEWISE_RATE
```

例如可表达：

```text
1.08 → 1.17 → 1.43
```

局部倍率突然改变本身 **不是 cut**。

### Middle cut

默认：

```text
middle_cut = false
```

任务明确知道中间剪过时可声明 `true`；不确定可声明 `unknown`。

无论哪个值，系统发现 source-position jump 后都不能自动确认删除歌词：

- `false` + discontinuity → BLOCK；
- `true` / `unknown` → review candidate；
- trim-start / trim-end 不属于 middle cut。

## 5. Canonical lyric 真源

生产文本来自 `TrackAsset` 已确认的 canonical lyric interpretation：

```text
raw LRC / Enhanced LRC / QRC
        +
same-timestamp original selection
        ↓
CanonicalLine / CanonicalToken
```

v4 下游不得重新猜 LRC 文件，也不得重新选择同时间戳第一行。

如果系统无法唯一判断原文，可用：

```json
{
  "Artist - Song": {
    "1000": 1,
    "45200": 0
  }
}
```

通过 `--lyric-role-map` 明确选择。该选择参与 `track_id/version_id/canonical_selection_sha256`，改变选择后旧 artifact 不可静默复用。

## 6. Calibration profile

导出默认 a3 profile：

```powershell
python scripts/v4_profile.py --write-default "private/<任务>/qa/v4_profile.json"
```

验证：

```powershell
python scripts/v4_profile.py --validate "private/<任务>/qa/v4_profile.json"
```

生产链只承认同一个 profile identity：

```text
profile_version
profile_id = SHA-256(profile complete content)
```

临时 CLI override 只用于实验；出现未固化 override 的 artifact 时 release guard 必须 BLOCK。

## 7. 输出目录

`v4_run --out-dir output/<任务>/v4` 当前生成：

```text
assets/
  track_assets.json
  track_assets.artifact.json

primary/
  <occ>.coarse.json
  <occ>.coarse.artifact.json
  [<occ>.fine.json]
  [<occ>.fine.artifact.json]

transitions/
  <A>__<B>/
    left.coarse.json
    right.coarse.json
    transition.json
    *.artifact.json

timelines/
  <occ>.timeline.json
  <occ>.timeline.artifact.json

v4_run.json
v4_run.artifact.json
```

所有主要 stage 都绑定：

- task fingerprint；
- v4 algorithm version；
- calibration profile version/id；
- TrackAsset/canonical identity；
- upstream artifact IDs；
- output SHA-256。

## 8. `v4_run` 状态

### `ready_for_render`

表示当前 v4 Source-to-Mix、Fine、Transition 和 canonical timeline 阶段没有未解决 review issue。

**它不等于 `publish_ready`。**

当前 a3 尚缺 package-native 的最终 SRT composer/renderer + 完整 release path，因此不能把 `ready_for_render` 当成最终发布批准。

### `review_required`

表示至少存在：

- blocked TimeWarp；
- source discontinuity / middle-cut candidate；
- repeated occurrence ambiguity；
- transition overlap candidate；
- transition uncertain interval；
- fine unresolved window。

必须处理证据或更新任务级明确声明后重跑。不得自动切回 v3.9 获取一个“看起来可发布”的结果。

## 9. 单 Stage CLI 的定位

以下脚本继续保留：

- `v4_resolve_assets.py`
- `v4_coarse_align.py`
- `v4_fine_align.py`
- `v4_probe_transition.py`
- `v4_profile.py`
- `v4_validate_release.py`

它们主要用于：

- 调试具体 stage；
- calibration / A-B；
- 重现某个 artifact；
- 难例诊断。

普通新任务应优先使用 `v4_run.py`，避免人工漏跑 transition/fine 或混用不同 profile/artifact。

## 10. 当前仍未完成

a3 已让 v4 真正拥有 Source-to-Mix 与 canonical timeline 生产链，但还没有完成最终字幕全链：

1. package-native timeline composer / final SRT renderer；
2. review decision artifact（cut/overlap 等确认后的可重放决策）；
3. Editor Evidence / LanguageSpan 全面进入最终 cue scoring；
4. final render 与 `v4_validate_release` 的原生一键接线；
5. real private calibration / blind-test；
6. 之后再按数据决定 Forced Alignment / ASR v2 的投入顺序。

因此当前正确表述是：

> **v4.0.0a3 已进入真实任务 production-first 使用，用于实际 Source-to-Mix、transition evidence 和 canonical timeline reconstruction；最终字幕 renderer/release 仍在迁移中。**

不能宣称：

- a3 已是稳定版；
- bootstrap profile 已经最优；
- overlap candidate 等于已确认叠唱；
- middle-cut candidate 可自动删除歌词；
- 真实歌词/时间准确率已经提高某个固定百分比。
