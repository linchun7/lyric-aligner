# Lyric Aligner v4 深度架构复盘与后续原则

日期：2026-08-17  
评估基线：`codex/v4-v3.9-integration` / `a584abaca1233d3d5aebe02d125a23b3a518ffd3`

## 1. 决策结论

### v3.9：不删除，冻结

v3.9 相比 v3.8 的真实可见准确率提升有限，但它已经覆盖 middle-cut review/apply、连续分段变速、Enhanced LRC/QRC、overlap review 和成熟 QA gate。因此：

- **保留为 compatibility kernel / regression oracle / emergency fallback**；
- 不再把新算法直接堆进 `scripts/redo_karaoke_pipeline.py`；
- v4 模块允许依赖“v3.9 的已验证行为”，但 v4 package 不反向依赖 monolith；
- 当 v4 的 build/finalize/qa 全部迁出后，legacy 脚本只保留薄 CLI wrapper 或进入 `lyric_aligner/legacy/`；
- v3.9 不再作为未来 v4.1/v4.2 的架构基础。

### v4：目标是彻底重构，但采用渐进替换

“彻底重构”定义为：最终生产真源位于 `lyric_aligner/` 的类型化模块、版本化配置和 artifact chain，而不是 5000+ 行 legacy 脚本。

不采用一次性 rewrite。一次性重写会同时失去 v3.9 已验证的剪切/变速/QA 行为，无法做可信 A/B。采用 strangler pattern：每完成一个 v4 stage，就用契约把旧 stage 替换掉，直到 monolith 失去生产算法职责。

## 2. Codex 恢复/接线工作评价

值得保留：

- 正确恢复了 v3.9 baseline，而不是继续在远端 v3.8 上开发；
- 保留了全部 v3.9 测试与行为；
- v4 Asset Resolver 已进入 prepare/audio-align 的 fail-closed 路径；
- QA ready 后自动生成 release manifest；
- 没有提前把 TimeWarp/transition/LanguageSpan 当成生产真源。

深审发现的关键缺口：

1. **source-audio identity 没有真正闭环**：legacy `audio-align` 在拿到 TrackAsset 后仍会再次调用旧 `find_source_audio()`。
2. **canonical lyric identity 没有真正闭环**：legacy `parse_lrc()` 仍固定取同时间戳 `alternatives[0]`，可能与 `lyric-role-map` 的选择冲突。
3. **build/finalize/qa 仍可能重新解析旧 song-list/LRC，而不是统一消费 TrackAsset binding。**
4. **版本重复定义**：legacy 中存在独立 `V4_ALGORITHM_VERSION`，容易与 package `__version__` 漂移。
5. **release lineage 尚未完整串上 asset/coarse/fine/transition artifact IDs。**

因此 Codex 分支应作为 v4 整合基线，不应废弃；但不能直接宣称“v4 已完成生产接管”。

## 3. 当前目录结构评价

当前结构方向正确：

```text
lyric_aligner/
  assets/
  audio/
  contracts/
  io/
  qa/
  text/
  domain.py
```

优点：

- asset identity、audio mapping、QA contract 已从 monolith 分离；
- 模块可单独单元测试；
- v4 artifact 已具有 task/version/upstream/output hash 基础；
- coarse/fine/TimeWarp/transition 已按职责拆开，而不是重新形成第二个大文件。

但**还不能称为最优/完成态**。缺少：

```text
lyric_aligner/
  config.py                 # 已在 a2 hardening 建立
  pipeline/                 # 已在 a2 hardening 建立 context
  evidence/                 # 统一 ASR/editor/audio/forced-align evidence
  timeline/                 # TrackOccurrence activity/overlap/composer
  calibration/              # profile、校准结果、阈值选择
  cache/                    # feature/source alignment cache
  legacy/                   # 冻结 v3.9 adapter
```

`scripts/` 最终只能保留薄 CLI，不能继续承载领域算法。

## 4. a2 hardening 已开始修复的结构问题

`4.0.0a2` / TrackAsset schema `1.1`：

- canonical same-timestamp selection 加入 TrackAsset identity；
- 新增 `ResolvedAssetBinding`，下游只读取一个 source/LRC/original 真源；
- 新增 canonical lyric parser，统一普通 LRC、Enhanced LRC、QRC；
- 新增 `V4CalibrationProfile`，bootstrap 阈值开始从算法代码迁出；
- 新增 `PipelineContext`，绑定 task fingerprint + algorithm version + calibration profile + asset artifact + occurrence bindings；
- 新增 `legacy.bridge`，明确依赖方向只能 legacy -> v4 contract，不能 v4 -> legacy monolith。

## 5. 下一阶段优先级

### P0 — Integration truth

必须先完成：

1. legacy 全阶段使用 `ResolvedAssetBinding`；
2. source audio 禁止再次 fuzzy resolve；
3. canonical lyrics 禁止再次 `alternatives[0]`；
4. strict SRT parser / `max(end_ms)` 接入 legacy 输入边界；
5. asset artifact ID 与 calibration profile ID 从 prepare/audio-align 一直传到 final QA/release；
6. 删除 legacy 中独立的 v4 version 常量。

### P1 — v4 audio production shadow A/B

- 对每个 occurrence 同时产出 v3.9 mapping 与 v4 TimeWarp；
- build 先继续用 v3.9，v4 只记录 residual/boundary candidate；
- calibration/blind-test 达标后按 occurrence 切换；
- common fixed-speed case 必须保持 AFFINE 快路径。

### P2 — Timeline / overlap

- `nominal_start` 只作 prior；
- transition window 推断 A/B/A+B/silence；
- overlap 内部真源必须是两个 TrackOccurrence，不是把两首歌词拼一行。

### P3 — Evidence / forced alignment

只有 P0-P2 和 evaluator 稳定后再进入：

- source-side forced alignment；
- mix-vocal local alignment；
- ASR/editor/audio evidence fusion；
- 未知语言 fallback。

## 6. v4 生产完成的退出条件

只有全部满足，才可说“v4 已彻底重构并替代 v3.9”：

- 生产 CLI 只调用 package orchestrator；
- legacy monolith 不再决定 asset、canonical lyric、TimeWarp、timeline、release gate；
- 所有 stage artifact 都包含 profile/version/upstream lineage；
- real calibration + blind-test 显示 false-ready 不增加；
- 固定倍速普通歌曲不因复杂模型退化；
- cut / rate-change / overlap 指标有独立评估；
- zh/en/ko/ja/yue 与 mixed-language 都有分层结果；
- v3.9 只作为 regression/fallback，不再是生产主实现。

## 7. 长期维护原则

1. 新能力优先新增/替换 package stage，禁止继续扩大 monolith。
2. 阈值属于 calibration profile，不属于业务函数常量。
3. 同一个事实只能有一个真源：asset、canonical lyric、timewarp、timeline、decision 均如此。
4. 任何人工 override 都必须改变 artifact/semantic identity，不能只改变运行时行为。
5. shadow -> calibration -> gated production，禁止“新模型写完即默认上线”。
6. 文档必须区分 implemented / shadow / production，禁止把设计稿写成已上线能力。
