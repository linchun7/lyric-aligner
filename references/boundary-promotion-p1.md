# P1 — Boundary-level Timing Promotion Shadow Gate

更新：2026-09-12

## 目标

P1 只回答一个问题：**在不破坏 Smart / Best-Safe timing floor 的前提下，机器能否在具体 boundary 上事先判断“candidate 比 Smart 更接近真实边界”，并在新的独立 truth 上证明稳定净收益。**

P1 不是新的 ASR/forced-aligner 项目，不重新开启已经停止的 Qwen/SOFA/HuBERTFA/STARS 调参，也不授予 production timing authority。

当前链路复用已有组件：

```text
Smart boundary + candidate boundary
        ↓
timing_decision_pack.py
(pre-gold population + selection lock)
        ↓
P1 selector decisions frozen BEFORE truth
(selection_payload_sha256)
        ↓
timing_decision_review.py
(review manifest 绑定 exact P1 selection hash；candidate positions hidden)
        ↓
candidate-blind human response
        ↓
response -> Gold ingestion
(Gold 继承 review manifest hash + P1 selection hash)
        ↓
boundary_promotion_shadow.py
(selection + manifest + raw response + Gold 四方一致性校验)
        ↓
blind / holdout shadow gate PASS / BLOCK
        ↓
PASS 也仅表示：值得单独评审 production authority
BLOCK：继续 Smart / Best-Safe floor
```

## 1. Pre-gold 冻结

候选位置、selector revision、selector code SHA、每个 boundary 的 `KEEP Smart / PROMOTE candidate` 决策、独立 evidence lineage、gate policy，以及该次人工 review 的 intended partition 都必须在读取人工 truth **之前**冻结。

入口：

```powershell
python scripts/v4_boundary_promotion_shadow.py freeze-selection \
  --pack <timing-decision-pack.json> \
  --decisions <machine-decisions.json> \
  --out <boundary-promotion-selection.json>
```

`machine-decisions.json` 必须：

- schema=`boundary-promotion-machine-decisions-1.0`；
- 绑定 exact timing decision `selection_lock_sha256`；
- `gold_read=false`；
- 每个 frozen case 恰好一个 decision；
- `expected_smart_ms` / `expected_candidate_ms` 与 frozen pack 完全一致；
- promotion 必须有 `independent_timing_evidence=true`；
- candidate 与 independent evidence 的 correlation group 必须不同；
- evidence 必须有 SHA-256 identity；
- `promote_candidate` 的 candidate 必须与 Smart boundary 真正不同；deterministic unchanged control 不能伪装成 promotion 补数量/track 门槛；
- gate policy 与 selector identity 一起冻结，不能看完 truth 再改阈值。

缺失、重复、stale、同证据家族自证、rehash 后的 identity/count 漂移或 unchanged-control promotion 均 fail closed。冻结结果的 `selection_payload_sha256` 必须随后与 intended partition 一起写入 review manifest；只冻结一份可自行重算 hash 的 selection、却不把它和 `blind|holdout|development|calibration|regression` 用途绑定进人工盲标材料，不足以证明 pre-gold selector/partition freeze。

## 2. 新 blind truth campaign

下一轮真正有资格影响 production authority 的数据必须是**新的、未被 development 调参消费过的 final-mix truth**。

建议首轮冻结 **32 个 boundary**，目标至少得到 **24 个有效可评分 boundary**；至少覆盖 **4 个独立 source group / track**，并让实际 promotion 至少覆盖 4 个独立 track。优先抽取：

- Smart 与高模式 candidate 明显分歧；
- 重复副歌；
- 长停顿 / 拖长音；
- crossfade / cut 邻域；
- 快速英文；
- vocalization / ad-lib；
- CJK / 韩日混合 ownership 难点；
- 少量 deterministic unchanged controls。

不得把以下内容包装成新的 blind truth：

- 已经人工查看过候选位置的欧美140点位；
- development-visible Gold；
- 为当前 selector/threshold 调过参数的样本；
- 同一 source group 同时进入 calibration 与 blind/holdout。

人工页面继续使用 `timing_decision_review.py` 的 candidate-blind UI：不显示 Smart/candidate 位置，只按音频标 boundary；不可判必须标 invalid，不能猜。P1 必须用已经冻结的 selection 构建 review manifest，并在页面生成前同时指定 intended partition，使人工 response 从产生时就绑定 exact `selection_payload_sha256` + frozen partition：

```powershell
python scripts/v4_build_timing_decision_review.py \
  --pack <timing-decision-pack.json> \
  --final-mix <exact-final-mix.wav> \
  --boundary-promotion-selection <boundary-promotion-selection.json> \
  --boundary-promotion-partition blind \
  --out-dir <blind-review-dir>
```

人工导出的 response 仍只包含 manifest hash 与盲标结果，不暴露候选位置。随后必须从该 exact manifest + response 生成 Gold：

```powershell
python scripts/v4_ingest_timing_decision_review.py \
  --manifest <blind-review-dir/manifest.json> \
  --response <review-response.json> \
  --partition blind \
  --out <timing-decision-human-gold.json>
```

P1 Gold 会继承 `review_manifest_sha256`、`boundary_promotion_selection_sha256` 与 frozen `boundary_promotion_partition`。ingest 的 `--partition` 必须与 manifest 预先冻结值完全一致，因此 development/calibration response 不能事后重新标记成 blind/holdout。评估时还会重新用 raw response 计算期望 Gold，并逐字段核对实际 Gold；因此不能在 response 之后手工改 Gold，也不能把一份已产生的 Gold 换绑到另一个 post-gold selection。

## 3. Shadow gate

默认 gate 在 selection freeze 时一起锁定：

```text
valid truth                    >= 24
independent tracks             >= 4
promoted boundaries            >= 8
promoted independent tracks    >= 4
new >500 ms catastrophic harm  = 0
harmful >100 ms rate           <= 5%
selected P90 regression        <= 0 ms
mean gain vs Smart             >= +1 ms
track-equal mean gain          >= +1 ms
track-bootstrap 95% CI lower   >= 0 ms
```

这组阈值是**第一轮 shadow preregistration**，不是已证明最优阈值。不能在看到同一 blind truth 后放宽；若要换 policy，必须冻结新的 selection/runtime identity，并使用新的 untouched truth。

评估入口：

```powershell
python scripts/v4_boundary_promotion_shadow.py evaluate \
  --pack <timing-decision-pack.json> \
  --selection <boundary-promotion-selection.json> \
  --review-manifest <blind-review-dir/manifest.json> \
  --review-response <review-response.json> \
  --gold <timing-decision-human-gold.json> \
  --out <boundary-promotion-shadow-evaluation.json>
```

只有预先冻结为 `blind` / `holdout` 的 review partition 有资格让 `shadow_gate_passed=true`；预先冻结为 development/calibration 的人工结果即使指标很好也只作诊断，不能事后改标签。P1 evaluate 必须同时读 frozen selection、其绑定的 review manifest、原始 candidate-blind response 与由该 response ingest 出来的 Gold；manifest hash、selection hash、frozen partition、Gold 内容或 response lineage 任一不一致都 fail closed。

这里的 hash 链解决的是仓库内 artifact 的一致性与“不能拿旧 Gold 重新选择 selector”的问题；它不是外部可信时间戳或数字签名。真正 blind campaign 仍必须保存原始 review response、避免事后重写整套 artifact，并按预注册流程执行。

## 4. Authority 边界

无论 shadow gate 是否 PASS，当前实现都固定输出：

```text
production_authority_granted = false
production_writeback_permitted = false
```

P1 selection/evaluation artifact 还遵守仓库统一 writer 安全：输出先经过 `validate_separate_artifact_paths`，不得覆盖或落入 pack/decision/selection/review-manifest/review-response/gold 等直接输入；目标文件必须为新路径，并通过 `atomic_write_json` 原子写出。重跑不得静默覆盖上一份冻结 selection/evaluation。

因此 P1 工具**不能**生成 Best-Safe `timing_promotions`，也不能改 Smart/Best-Safe SRT。

若未来 shadow gate 在新的独立 truth 上通过，下一步仍必须单独 review：

1. evidence independence 是否真实成立；
2. 是否存在 language/genre/structure 子群 catastrophic harm；
3. selector/runtime 是否与 blind freeze 完全一致；
4. 是否需要第二批 untouched holdout；
5. 才能设计新的 production authority schema，并新增 Best-Safe verifier/test/docs。

没有单独的 authority 变更，Best-Safe 1.1 继续只接受 human-truth-bound timing promotion。

## 5. 停止条件

首轮新 blind campaign 如果出现以下任一情况，默认停止继续增加 timing heuristic/model：

- 产生新的 >500ms catastrophic error；
- P90 比 Smart 退化；
- track-equal 净收益不为正；
- promotion 只集中在少数同质曲目；
- bootstrap 不能排除总体负收益；
- 有效 truth 不足，且补样只能依赖已看过的 development 数据。

此时结论不是“再换一个更大的模型”，而是继续使用 Smart / Best-Safe timing floor，等待新的真实 production failure 和新的独立 truth。
