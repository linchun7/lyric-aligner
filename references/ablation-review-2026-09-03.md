# V4 消融与项目清理 Review — 2026-09-03

状态：completed calibration-only review
范围：Max 计算链、历史入口、文档入口、本地 artifact 生命周期
约束：未读取或使用未观察 blind truth 调参；未改变 production authority / release gate。

## 1. 结论

当前项目适合做**受控 calibration-only 消融**，但不适合通过大规模删核心模块来“降复杂度”。本轮证据支持：

- 保留 Chroma + MFCC 融合；
- 保留 Fine alignment；
- 保留 Piecewise TimeWarp 能力，当前只是缺少独立正例 calibration truth；
- 不动 Smart immutable version chain、safe-wrapper `_v4_*_impl.txt`、review/release/provenance/path-safety；
- 清理应优先减少重复入口与可再生缓存，而不是删除 production evidence。

## 2. Chroma / MFCC 离线消融

在 6 个 calibration occurrence、325 个 coarse windows 上，仅改变 feature family 的 top-1 选择：

| variant | 与 fused top-1 不同 | 比例 |
|---|---:|---:|
| Chroma-only | 47 / 325 | 14.46% |
| MFCC-only | 28 / 325 | 8.62% |

Chroma-only 存在多次超过 2 秒的候选跳转，因此不能因为 MFCC 当前融合权重较小就删除该 feature family。双特征融合继续保留。

## 3. Fine 全链消融

对 K110 同一 task fingerprint 运行完整 no-Fine Max 链：17 个 primary occurrence 与 16 个 transition 均正常完成，raw status 与 full-Fine 均为 `review_required`，issue 数量和语义完全一致。

### 3.1 Full timeline / composer 差异

最终 composer 可见 cue identity 在 17 / 17 occurrence 上完全一致。2316 个可见边界的 full-Fine vs no-Fine 差异：

- MAE：`16.692 ms`
- P95：`43 ms`
- Max：`71 ms`
- `>100 ms`：`0`

这说明 Fine 在该真实任务上主要负责小幅边界精修，而不是改变最终歌词身份。

### 3.2 独立 calibration reference 评分

随后只使用 K110 的两个 calibration case，通过 strict `v4_calibration_workflow.py evaluate` 比较同一 composer 输出；未读取任何 blind case/reference/metric。

| metric | full-Fine | no-Fine |
|---|---:|---:|
| unit F1 | 0.993895 | 0.993895 |
| line exact F1 | 0.996491 | 0.996491 |
| cue text exact | 0.996491 | 0.996491 |
| boundary MAE | **16.265 ms** | 22.939 ms |
| boundary P95 | **7 ms** | 25 ms |
| onset MAE | **7.735 ms** | 14.541 ms |
| offset MAE | **24.796 ms** | 31.337 ms |

Fine 对文本身份没有贡献，但对 timing tail quality 有明确贡献，尤其 boundary P95 从 25 ms 降至 7 ms。因此本轮否决“删除 Fine”。

当前 calibration 也不足以安全证明某类 occurrence 可以长期跳过 Fine，所以不新增 production `--skip-fine` 或新的 selective gate；避免用有限 corpus 过拟合运行策略。

## 4. Piecewise TimeWarp

已审 calibration truth 和真实调速候选：当前没有独立、可冻结的 `piecewise_rate` 正例。现有 calibration occurrence 的有效 TimeWarp 均为 AFFINE，但这只能证明 coverage gap，不能证明 Piecewise 代码无用。

结论：保留 Piecewise capability，不进入本轮删除清单；未来只有获得独立制作/编辑 truth 后再评估。

## 5. 已实施的代码/入口清理

`scripts/karaoke_subtitle_pipeline.py` 原 1569 行 pre-v4 diagnostic/draft 实现没有当前代码、测试或权威 workflow 依赖，且包含第二套 SRT/LRC/ASR/alignment authority。它已缩为 fail-closed migration wrapper：

- `--help`：展示当前 Standard / Smart / Pro / Max 替代路径，exit 0；
- 旧实际命令：不自动转译语义，明确退役并 exit 2；
- 历史实现仍可从 Git history 恢复。

该改动直接删除约 1500 行 active duplicate implementation，而不影响 V4 主链。

`references/dataset-protocol.md` 同步统一评估入口：正式 calibration / blind-test 只推荐 strict `v4_calibration_workflow.py`；低层 evaluator 仅作为 internal/compatibility surface。

## 6. 本地 artifact 清理原则

本地体积主要来自 `private/` 和 `output/`，不是 tracked 源码。新增 `references/local-artifact-retention.md`，统一为：

- KEEP：production final、truth/blind lock、review/release、不可再生人工证据；
- ARCHIVE：已被明确 supersede 且完成等价/非回归验证的历史 run；
- REGENERABLE：`__pycache__`、feature cache、resume sidecar、普通临时 decode/cache。

本轮只清理确定可再生缓存，不按版本号批量删除 production output。

## 7. 当前上限与下一步触发条件

现阶段普通歌词 timing 已接近“工程误差而非算法大错”区间；继续增加模型/结构层的边际收益较低。主要上限来自：

1. 缺少 `piecewise_rate / hard_cut / true_overlap` 的独立真实 truth；
2. transition / cut / overlap 的物理不可辨边界；
3. editor segmentation authority 与 canonical timing truth 的最后 reconcile；
4. 新生产任务中尚未见过的真实失败分布。

因此后续优化应 event-driven：出现新的独立 truth 或新的真实 production failure 再开算法研究；不要为了“继续优化”消费 blind 或堆新模块。
