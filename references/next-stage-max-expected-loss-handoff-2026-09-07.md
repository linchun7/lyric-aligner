# 字幕项目下一阶段唯一交接：Max Expected-Loss Production Upgrade

日期：2026-09-07
状态：设计确认，尚未实施
适用范围：后续字幕算法升级、真实生产回归、Max 自动裁决与最终 SRT 写回

> 后续会话接手本项目时，先读 `AGENTS.md`、`SKILL.md`、`references/v4-status.md`，然后以本文作为下一阶段唯一实施任务书。不要重新发散成“再加模型 / 再加 gate / 再做一次全仓 review”。

---

## 1. 项目核心目标

Max 的目标不是“尽量不犯错”，也不是“证明很多地方不敢改”。

**Max 的产品目标是：在当前可获得证据条件下，自动产出期望误差最低、结构语义自洽、人工需求尽可能低的最终字幕。**

具体含义：

1. **高确定性结果直接精准修改。**
2. **中等确定性结果，只要经过预先冻结的 calibration + untouched blind 证明其条件期望误差低于现有 editor，就自动修改。**
3. **即使 candidate 只能做到数百毫秒，只要 editor 已被独立证据证明更差，也应自动 rescue。**
4. **普通 timing 不确定性不应自动变成人工任务。** 如果没有候选被证明优于 editor，就自动保留 editor。
5. **只有歌词身份、歌曲 occurrence、cut/repeat/reorder/overlap/crossfade/cue ownership 等结构语义存在多个仍无法自动消歧的解释时，才保留人工。**
6. 人工应集中在真正需要“理解结构”的少量难点，而不是承担大规模逐句 timing 微调。

最终评价对象始终是：

> **exact final mix 上最终用户看到的 SRT，是否比旧 final / editor 更准。**

代码量、模型数量、artifact lineage、单测数量、`publish_ready`、gate 数量都只是手段，不能代替这个目标。

---

## 2. 当前 a19 为什么“工程进步很大，最终字幕提升很小”

2026-09-07 对 fresh a19 与历史 final 做逐 cue / 字节级比较：

- 7 个项目合计 6041 cues；
- 5994 cues 完全未变，约 99.22%；
- 30 条只做了 display end 长尾截短；
- 17 条为文本清理；
- **start timestamp 实际变化 0 条。**

根因不是最近工作没有价值，而是大量新能力被固定成只诊断、不写回：

- Pro 当前固定 `automatic_timing_change_allowed=false`；
- Max Next 当前固定 `selection_recommendation_only_no_srt_mutation`；
- outer Expected-Loss closeout 明确 `production_materializer_added=false`；
- outer start/end observer 未取得 production authority 时统一 `keep_editor`。

这些机制提高了安全性、provenance 和 false-ready 检测，但天然很难转化成最终时间轴提升。

**下一阶段必须把研发重心从“能否发现风险”切换到“在严格 blind 约束下，哪些改动可以安全写回并真实降低误差”。**

---

## 3. 关键修正：Editor 不是固定强先验，任何语言也不预设好坏

剪映 / editor 的可靠性会随语言、唱法、曲风、音色、混响、伴唱、局部切分和当前素材而变化。

**禁止写死以下规则：**

- 中文一定可靠；
- 韩文一定不可靠；
- 日文处于某个固定等级；
- 古风一定较差；
- 某艺人的歌固定降低权重。

这些都只能是历史观察或候选特征，不能直接成为 production authority。

### 3.1 Editor 可靠度必须动态、局部估计

至少拆成两条互不替代的轴：

- `editor_text_identity_reliability`
- `editor_timing_reliability`

例如某些韩文 cue 可能被识别成英文谐音：文本身份明显错误，但 cue 的大致 vocal onset 仍可能有参考价值。反过来，也可能文字碰巧对而 timing 很差。

因此 text identity 与 timing 必须独立评估。

### 3.2 语言、曲风、唱法只能作为弱特征

可以进入 reliability/risk model 的特征包括但不限于：

- language / script / code-switch；
- phoneme/token coverage；
- editor 文本与 canonical 的 lexical similarity；
- ASR language posterior / confusion；
- rap / fast syllabic density；
- 长拖腔 / ad-lib / melisma；
- 和声、群唱、双人；
- 混响、伴奏遮蔽、低 vocal SNR；
- 重复副歌 / 重复短句；
- cut / splice / crossfade / overlap 邻近；
- manual tempo change / local timewarp；
- 当前 track 上 editor 与独立音频锚点的已观测 residual；
- 当前 cue 周边多个候选的一致性与 ambiguity。

**这些只是 feature，不是 hard rule。**

### 3.3 Reliability 使用分层校准，而不是固定语言表

建议形式：

```text
global prior
  + language/style weak features
  + track-level observed reliability
  + local cue evidence
  -> candidate-specific conditional risk
```

重要规则：

1. 数据不足的 language/style bucket 自动回退到更宽的 global calibration，不自行猜测。
2. 当前 track 有足够独立 anchors 时，track-local residual 应覆盖宽泛的语言经验。
3. 当前 cue 的 direct final-mix evidence 应优先于语言标签。
4. 不允许为某语言单独放宽 catastrophic gate 来换 coverage。

---

## 4. 下一代 Max 架构：先结构，后 timing

### 4.1 Layer A — Structural Resolver

先解决：

- canonical lyric identity；
- track / song occurrence；
- lyric occurrence / repeated chorus；
- cut / repeat / reorder；
- overlap / crossfade；
- cue ownership；
- source segment identity。

输出只允许：

- `STRUCTURE_RESOLVED`
- `STRUCTURE_AMBIGUOUS_MANUAL`

只有第二类进入人工。

**结构不确定时，禁止通过时间取平均、最高 score 或强行 monotonic 来掩盖语义问题。**

### 4.2 Layer B — Boundary Candidate Set

在结构已解决的前提下，对每个 outer start / outer end 建立候选集合。

至少包括：

- editor 原时间（candidate 0）；
- Independent Fine；
- SOFA；
- HuBERTFA；
- local source→final-mix waveform / spectral matching；
- 必要时 bounded ASR word-span；
- 必要时 vocal onset / vocal offset evidence。

原则：

- start 与 end **必须分开建模、分开校准、分开验收**；
- 单曲 WAV 只提供局部模板，**final mix 才是真正时间轴**；
- 默认做 local / piecewise mapping，不允许用全曲单一 affine/BPM 覆盖手工分段调速、裁切、splice、crossfade；
- candidate generator 只产候选与特征，不自行宣布全局 authority。

### 4.3 Layer C — Joint Expected-Loss Selector

每个 boundary 同时比较 editor 与所有 candidate，不再问“哪个 backend 是真理”。

输出仅允许：

- `KEEP_EDITOR`
- `USE_CANDIDATE:<id>`
- `RESCUE_WITH_CANDIDATE:<id>`
- `STRUCTURE_AMBIGUOUS_MANUAL`

普通 timing 证据不足时应 `KEEP_EDITOR`，不是人工。

---

## 5. Expected-Loss 必须优化“相对 editor 的真实收益”

单看模型自身 MAE 不够。真正要估计的是：

> `E[loss(candidate) | 当前局部特征]` 与 `E[loss(editor) | 当前局部特征]`

只有 candidate 的条件期望损失低于 editor，并达到冻结的 promotion margin，才允许自动修改。

Loss 至少包含：

- absolute timing error；
- 比 editor 恶化 >100ms 的 harmful change；
- >250ms 明显误差；
- >500ms catastrophic；
- >1000ms severe catastrophic；
- paired improvement / regression；
- start/end 分开；
- track-grouped，而不是把同曲大量重复 cue 当独立样本。

结构语义错误不进入普通 timing loss，直接回 Structural Resolver。

所有 loss 权重、promotion margin、catastrophic cap、feature set 必须在 calibration 阶段冻结；**blind 揭晓后不得再调。**

---

## 6. 三档自动修改能力

### Tier A — Precise Auto

候选经 blind 证明高精度、低 catastrophic 风险。

动作：直接写回。

### Tier B — Calibrated-Better Auto

候选未必达到几帧级，但 calibration + blind 证明其 conditional expected loss 显著优于 editor。

动作：直接写回。

这是当前系统最缺的能力。

### Tier C — Rescue Auto

独立证据强烈表明 editor 明显错误，例如 editor 可能偏 1–2 秒，而最佳 candidate 虽只有 200–400ms 级，但期望损失显著更低。

动作：自动 rescue。

原则：

> **不能因为 candidate 不够完美，就保留已经被证明更差的 editor。**

### 无候选占优

结构已解决，但没有 candidate 被证明优于 editor：

- 自动 `KEEP_EDITOR`；
- 不创建人工任务。

---

## 7. 当前最值得解决的技术问题：Independent Fine 的灾难误配识别

Independent Fine 当前最有潜力：

- calibration selected 19/24；median/P90/max = 10.49/28.38/38.40ms；
- blind selected 16/24；median/P90 = 12.04/17.60ms；
- 但出现 1 个 1040.14ms catastrophic，因此 authority 被正确拒绝。

下一阶段首要问题不是让 median 再低几毫秒，而是：

> **如何在不知道 blind truth 的前提下，把这类约 1 秒 catastrophe 自动识别并拒绝。**

优先研究的通用特征：

- 多窗口尺度预测一致性；
- source→mix 与 mix→source 双向 cycle consistency；
- top1/top2 peak ambiguity、margin 与位置间距；
- candidate 是否命中/接近搜索边界；
- 相邻可靠 anchors 的 local timewarp 连续性；
- 重复歌词/重复副歌的多峰风险；
- candidate 与 editor 差值；
- 多 backend 局部共识/冲突；
- vocal activity 在 candidate 附近是否真的发生 onset/offset；
- cut/crossfade/manual tempo change proximity。

旧 1040ms holdout case只用于 failure taxonomy，**不得用来事后调旧 selector 阈值。** 新规则必须在新的 calibration population 上形成，再用新的 untouched holdout 验证。

---

## 8. 分阶段实施：每一步都必须可做、可测、可验收

### P0 — 建立真实产品质量基线

目标：先测清 editor / 旧 final / a19 在不同场景到底多准。

任务：

1. 建立新的 outer start/end human-gold benchmark。
2. selection 在任何新 selector/backend 调参前冻结。
3. 以 **track 为 split unit**，禁止同曲泄漏 calibration→holdout。
4. 起步目标：
   - start >= 60 gold points；
   - end >= 60 gold points；
   - >= 12 首不同 track；
   - 覆盖中文、韩文、日文、英文/混合中当前真实可得项目，不要求人为凑语言比例；
   - 覆盖普通、重复、含糊咬字、rap/快句、拖腔、手工调速、结构邻界等实际 failure mode。
5. 每个点记录 editor、旧 final、a19 与可用独立候选误差。

验收：

- selection lock + final-audio SHA + human gold；
- editor baseline：median/P90/P95/max、>100/>250/>500/>1000ms；
- 按 track、language feature、style/risk feature 分层报告，但不把分层结果硬编码成 production rule；
- 不允许根据 holdout 调参。

如果没有足够 gold，不继续声称“准确率升级”。

### P1 — 统一 Candidate Generator

目标：所有 observer 统一只负责产候选和局部特征。

任务：

1. 定义 candidate schema：boundary kind、candidate_ms、backend、correlation group、local features、provenance。
2. editor 永远作为 candidate 0。
3. Independent Fine、SOFA、HuBERTFA、local waveform 等进入同一接口。
4. start/end 独立。
5. 单曲→final mix 使用 bounded local/piecewise mapping。

测试：

- deterministic replay；
- hash/provenance binding；
- 中文路径；
- cut/crossfade 不跨结构边界搜索；
- repeated lyric 多峰不静默选错 occurrence；
- backend unavailable 时 editor candidate 仍可完成生产。

验收：

- 同一输入重复运行 candidate set blob-identical；
- 本阶段 **不得修改 SRT**。

### P2 — Dynamic Editor Reliability + Candidate Risk

目标：不按语言写死 prior，而是估计当前 boundary 的 editor 与 candidate 条件风险。

任务：

1. 建立 global + track + local cue 的分层 reliability features。
2. text identity 与 timing reliability 分开。
3. language/style 只作为 weak feature，不作为 hard rule。
4. 数据稀疏 bucket 回退到 global calibration。
5. 冻结 feature set、loss、threshold、promotion margin。

测试：

- 去掉 language feature 时模型仍能依赖直接证据工作；
- language label 改变但所有 direct evidence 不变时，不允许出现无依据的大幅 authority 翻转；
- track-local evidence 足够时能覆盖宽泛 global prior；
- sparse bucket fail-safe 回退。

验收：

- calibration-only report；
- frozen policy artifact；
- 运行 untouched holdout 前 policy SHA 固定。

### P3 — Catastrophic Rejection

目标：解决“多数很准、偶发 1 秒错”。

任务：

- 对 candidate ambiguity/cycle consistency/local continuity 等建立 rejection/risk 规则；
- track-grouped calibration/cross-validation；
- 冻结后再跑 untouched holdout。

验收门槛：

- auto-selected subset expected loss 明确低于 editor；
- paired track-grouped bootstrap improvement 95% CI 不支持明显劣于 editor；
- **不得制造新的 >500ms catastrophic auto-change**；
- changed-boundary harmful-change rate（比 editor 恶化 >100ms）目标 <=5%；
- 不能靠把自动修改 coverage 压到接近 0 来“通过”。

### P4 — Joint Selector：KEEP / AUTO / RESCUE

目标：把 candidate-specific selection authority 接起来。

任务：

1. start selector 与 end selector 分开。
2. 对每个 boundary 估计 editor 与 candidates 的 conditional expected loss。
3. 输出 KEEP / USE / RESCUE / STRUCTURE_MANUAL。
4. Tier A/B/C 的 promotion 规则在 blind 前冻结。

验收：

- selector decision artifact 可独立 replay；
- 没有结构歧义的 timing case不得落人工；
- rescue case 可以在 candidate 绝对误差不漂亮、但相对 editor 明显更优时自动选择 candidate。

### P5 — Production Materializer

目标：让已经通过 blind 的 selector 结果真正进入最终 SRT。

任务：

1. 新增/扩展 production materializer 消费 exact selector decision bundle。
2. 只允许修改 selector 授权的 boundary。
3. 保留 old/editor timing、候选、risk、decision 的完整 audit lineage。
4. start/end 可分别来自不同 decision，但必须满足 cue geometry 和结构约束。
5. 不允许 materializer 自己重新选 candidate。

测试：

- unauthorized boundary mutation hard fail；
- stale selector/bundle/audio SHA hard fail；
- monotonic/order/overlap/negative duration 全覆盖；
- text ownership 不因 timing materialization 改变。

验收：

- synthetic + frozen real regression 通过；
- exact SRT diff 只包含授权变化。

### P6 — 真实生产 A/B

优先重跑具有代表性的现有项目，而不是一次全量乱跑。

建议顺序：

1. KPOP130：已有文本/display 改善，适合验证韩文/混合场景的动态 editor reliability；
2. KPOP110：Gee 手工多段调速，适合验证 local/piecewise mapping；
3. H180：已有 human-gold/internal authority，可验证 outer selector 与结构层协作；
4. Walk140：验证普通中文/清晰场景下“多数 KEEP_EDITOR、少数改得更好”；
5. 再扩 H190/KPOP200/Walk120。

每个项目必须输出：

- old vs new cue count；
- start changed count；
- end changed count；
- KEEP/AUTO/RESCUE/MANUAL counts；
- human-gold 覆盖区 old/new paired errors；
- changed-boundary improvement rate；
- harmful-change rate；
- >100/>250/>500/>1000ms；
- 最终 SRT A/B；
- 哪些变化是 text/display-only，不能混入 timing improvement。

**如果新版最终 SRT 没有真实改善，就不能因为内部 gate 更完善而宣布本阶段成功。**

---

## 9. Human review 的收敛原则

后续人工队列只允许主要包含：

- identity ambiguity；
- repeated occurrence ambiguity；
- cut/repeat/reorder ambiguity；
- overlap/crossfade ownership ambiguity；
- 多个结构解释都与当前证据相容。

以下情况默认不应人工：

- timing candidate 不够强 → KEEP_EDITOR；
- editor 明显差且 candidate 经校准更优 → RESCUE；
- 多个 timing candidate 中一个期望损失最低且通过 promotion → AUTO；
- backend unavailable → 使用剩余候选/KEEP_EDITOR。

人工率是产品指标，但不能单独优化；降低人工不能以增加 catastrophic timing 为代价。

---

## 10. 每次升级必须报告的产品 KPI

以后每次 Max 算法升级，必须同时报告：

1. 最终 SRT 实际修改了多少 start；
2. 修改了多少 end；
3. KEEP / AUTO / RESCUE / MANUAL 比例；
4. editor baseline MAE/median/P90/P95/max；
5. 新版相同指标；
6. paired improvement；
7. changed-boundary improvement rate；
8. harmful-change rate；
9. >100ms / >250ms / >500ms / >1000ms；
10. track-grouped 结果；
11. 按 language/style/risk feature 的诊断分层；
12. 真实 SRT old/new diff。

没有这些数据，不得把“代码升级”描述成“字幕准确率升级”。

---

## 11. 明确禁止的下一步

后续会话不要：

- 继续为了“更严谨”增加一堆永远 no-mutation 的 gate；
- 直接给 SOFA/HuBERTFA/Independent Fine 某一个 backend 全局 authority；
- 把语言标签硬编码为 editor 高/低可信；
- 用旧 blind catastrophic case 调阈值再重新宣布通过；
- 为提高 coverage 放宽 edge guard / catastrophic gate；
- 用单曲 WAV 假定等于 final mix；
- 把 semantic gate 的秒级阈值当成帧级 accuracy evaluator；
- 用单测、QA `publish_ready`、artifact seal 代替最终 SRT A/B；
- 一开始就全量重跑 7 个项目，先完成 P0–P5 的严格小范围闭环。

---

## 12. 后续会话的执行顺序

接手后严格按：

```text
P0 真实 outer human-gold / editor baseline
  -> P1 candidate schema/generator
  -> P2 dynamic editor reliability
  -> P3 catastrophic rejection
  -> P4 joint expected-loss selector
  -> P5 production materializer
  -> P6 representative real A/B
  -> 达标后再扩全量 production
```

任何一步未达到验收条件，不要靠后续步骤“掩盖”。

最重要的判断标准只有一个：

> **最终 SRT 是否在真实 final mix 上，以可复验的人耳 gold / 独立证据证明，比现有 editor/旧 final 更接近期望最优。**
