# 字幕项目批次封板记录（2026-09-06）

本记录封板当前明确要求验收的 7 个字幕成品：KPOP110 新混音、120 健走、KPOP130、140 健走、华语男声190、KPOP200、华语青春180 a19。机器可复验结果见 `references/subtitle-release-set-audit-2026-09-06.json`；该报告重新校验 task manifest 当前输入 SHA、QA/release task fingerprint、release→最终 SRT/QA 哈希绑定、review gate、未打码 f-word，以及当前 final 目录 INVALIDATED 标记，而不是只引用历史“已通过”结论。

## 当前最终成品

| 项目 | 最终 SRT | SHA-256 | 生产状态 |
|---|---|---|---|
| KPOP110 新混音 | `output/kpop110/final_20260902_newmix_splice_a8_r3/KPOP110_FINAL.srt` | `c51867f35dfc50264b30d352e6f3bc07aafb9a0ace1feee3113a54d238adc573` | `publish_ready=true`，0 review；task=`kpop110_newmix`，绑定 `kpop-110-后移16帧.wav` |
| 走路带风120 | `output/walk120_a13_20260903/WALK120_MAX_FINAL.srt` | `f5c81f3f8eeff70f080e1b3fb0cac3cceaabcdcc37811820febae2a584287261` | `publish_ready=true`，0 review；最终歌单 14 首，manifest 锁定 14 个调速源 WAV |
| KPOP130 | `output/kpop130_max_20260902_a8_contentend_r2/KPOP130_MAX_FINAL.srt` | `500d7aea2d25399d275cec6709e854719b600c8caebbd6e324d841bbc67a66c7` | `publish_ready=true`，0 review |
| 快乐健走140 | `output/快乐健走140_max_20260902_r2/KUAILE140_MAX_FINAL.srt` | `83e5c652040bc4283dd56db1484bcd5a4c90fea2265bebf7776f4615b8f36880` | `publish_ready=true`，0 review |
| 华语男声190 | `output/华语男声190_max_20260902_a9/H190_MAX_FINAL.srt` | `c497c9a5e5ed623c497504921975f99ce77c0747916fc261cea56020ea8141fc` | `publish_ready=true`，0 review |
| KPOP200 | `output/kpop200_a13_20260903/KPOP200_A13_FINAL.srt` | `c7792d729a9262b05e663cdc78aeae0cd0d0a9177c3c0c3f306cc96b2e412d6f` | `publish_ready=true`，0 review |
| 华语青春180 a19 | `output/华语青春180_v4_authority_a19_20260906/corrected/internal_direct_v2_textfix_v1/corrected.srt` | `02b89d49db3f8cbc5886e9495804e3d0ee1b409ef0ed91e7333223e4c84f9f7a` | `publish_ready=true`；8/8 regression；0 review；0 unverified timing mutation；internal Human-Gold joint authority ready |

## 华语青春180 a19 的 authority 边界

华语青春180 使用 `recovery baseline + Human-Gold-calibrated internal boundary overlay`。Replacement Human Anchor V2 已 24/24 真人确认；joint selector 对 internal 获得生产 authority，fresh plan 152 个 internal boundary 中 137 个获准自动切分，最终 781 cues 中 264 行为 audio-verified internal split。outer start/end 的单 backend Human-Gold calibration 仍未通过，因此继续确定性 `keep_editor`，不通过放宽 edge-clamp 换 coverage。

完整 hash-bound seal：`output/华语青春180_v4_authority_a19_20260906/qa/boundary_authority_overlay_release_seal.json`，seal SHA=`8732cb718b47b0c5b3910236ec3dd4d2ee41d8ce0021c45a3d9af4c93cb94d53`。

该 seal 明确不声称 full Max semantic-sync release：当前 recovery-overlay 链没有与之精确匹配的 current Max `run+fusion`，因此不冒充 `v4_audit_semantic_sync/v4_validate_release` 的完整 semantic authority。`whenever you come whatever we talk` 保留 editor `16:06.833–16:15.033`；用户此前提供的是约 16:07.000 的人工参考，在 outer authority 未通过且参考本身为近似值时不强写成伪精确毫秒。

## 晚到输入检查

KPOP110 当前新版音频目录只有 `kpop-110-初版.wav` 与 `kpop-110-后移16帧.wav`，最终 manifest 绑定后者，没有发现另一个更新命名的替代成品音频。120 当前最终歌单是 14 首，a13 manifest 的 `source_audio_dir` 同样锁定 14 个对应调速 WAV；候选目录中的其它调速歌曲未进入本期最终 14 首，不属于漏跑。

## 封板原则

已有 110/120/130/140/190/200 release 保持其各自 hash-bound algorithm/task identity；本轮没有因为 H180 专用 a19 boundary-authority overlay 而无条件重跑它们。原因不是“版本旧也算了”，而是 release-set audit 已重新证明它们的输入未漂移、当前 final 未被 INVALIDATED、QA 仍 publish-ready、release 哈希仍闭合，且没有已知的新生产失败要求迁移。后续若替换成品音频、源字幕、歌单、规范歌词、调速单曲或出现新的 human QA failure，应创建新 task fingerprint 并重新生产，不能复用本封板结论。

## Max outer observer 补充封板

本日后续研发没有修改上述 7 个已封板 SRT。3A speech CTC 与 3B singing CTC 均在 outer calibration 阶段被拒绝且未运行 holdout；Independent Fine 在预测前冻结 selector/protocol 后完成首次 blind 24-case holdout，但因 selected coverage=`66.67%`、selected max=`1040.14ms`、catastrophic=`1/16` 未通过冻结门槛，正式保持 diagnostic-only。Expected-Loss / Max Next 1.1 因此继续要求 candidate-specific production-authoritative local support；当前没有新的 outer observer 满足该条件，也没有新增 production SRT materializer。outer start/end 继续保留 editor strong prior，a19 internal joint authority 与本页 7 个成品身份均不变。机器 closeout：`references/v4-max-outer-observer-closeout-2026-09-06.json`，artifact SHA=`4439c76c2bd88a3bd244ba496be0fb63e351feb9bb837ad68c3c5901993425dd`。

## 机器验收入口

- 批次 release-set audit：`references/subtitle-release-set-audit-2026-09-06.json`，当前 `all_passed=true`，7/7。
- Max engineering seal：`references/v4-max-engineering-seal-2026-09-06.json`，file SHA=`27f2c54613d2922b4c1c618fc881f066b66a33f85a93c82407d06a53a34bef45`。
- H180 a19 seal reference：`references/v4-boundary-authority-a19-seal-20260906.json`。
- H180 当前状态：`references/v4-status.md`。
- H180 关键变更：`references/v4-change-record.md`。
