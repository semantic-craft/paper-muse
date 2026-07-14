# paper-muse 开发清账与跨机交接

> 状态快照：2026-07-14；核对基线：`origin/main` = `d764ea0`。
> 读者：在 helios、metis 或其他机器继续开发的人与代理。
> GitHub `main` 是共享事实源；本文件只记录可由提交、Issue、PR 或测试证明的状态。

## 先同步，不要从旧 handoff 继续猜

```bash
git status --short --branch
git log --oneline --decorate -8
git fetch --prune origin
git switch main
git pull --ff-only origin main
git status --short --branch
```

- 2026-07-14 收官后，远端 open PR 共两个：[#96](https://github.com/semantic-craft/paper-muse/pull/96)（本台账）、
  [#104](https://github.com/semantic-craft/paper-muse/pull/104)（draft，#84 样张 prompt 草案，等真机小跑）。
- 多机器/多会话同时工作时，每次提交、删分支或合并前都重新执行前两行；功能改动走独立分支和 PR，禁止 force-push `main`。

## 验收方针变更（2026-07-14）

项目所有者决定：**真机/付费验收整体挂起，改为实际使用中暴露问题再按独立 issue 修复**。

- [#40](https://github.com/semantic-craft/paper-muse/issues/40) 已按此方针于 2026-07-13 收口关闭；
  票内收口评论明确其真机项是「用户豁免并延期」，**不是已验证通过**。
- 旧版台账「当前不能报完成的门」清单不再作为动工门。各票内「待真机校准」「验收后定」
  「引擎动工门：真机 §12 签收」等字样按豁免处理，但相应 PR/issue 必须注明
  「真机验证按所有者 2026-07-14 决定延期，仅含离线验证」。
- 被豁免的真机欠账仍然存在，实践中出问题时单独采证：付费端到端冒烟（扫描/圆桌/对抗幕）、
  CNKI 中文面 live 验证（`zh_hits` 非降级 `None`）、三 provider 两幕复验、
  `MuseServer.launch()` 自起实测、真 Elo 付费校准（#79）及 #73 gold 判据的真机数据。
  注：#77 兜底逻辑已离线实现合并，但其阈值/离群选择性仍待真机观察校准。

## 一句话现状

PaperMuse 1.1.0 已在 main 备好并打 tag，**GitHub Release 已于 2026-07-14 发布**（内嵌版，见下节）；张力/MCII 线的
证据基础、本土判据、快照键收口、MCII 三元产物、`tension` 字段贯通、对抗幕 rubric
均已合并进 main。Codex 离线批次全部完成——5 张票（#77/#78/#74/#80/#93）已合并关闭，
张力质量闸（#93）已接入 `finalize_card_quality` 收尾链。剩余全是**非离线可结**的债：
被真机门挡住的 #84→#92，以及等实践数据的 #73/#75/#79（父票 #82/#88 待子票齐后收）。

## 已完成并进入 main（自上一版台账基线 `2c69b05` 起）

| 范围 | 可核对证据 | 状态边界 |
|---|---|---|
| #83 中文法学「有趣性」判据（教义学/社科法学两套） | PR [#97](https://github.com/semantic-craft/paper-muse/pull/97)（`42d6a6f`） | 判据/文档定稿，供 #93 消费 |
| #89 卡片快照键单点声明 | PR [#98](https://github.com/semantic-craft/paper-muse/pull/98)（`b6b8a65`） | prefactor，解锁 #91 |
| #90 MCII 三元产物 | PR [#99](https://github.com/semantic-craft/paper-muse/pull/99)（`e015a49`、`92b1a86`） | 目标—障碍—if–then 进产物与圆桌 |
| v1.1.0 发布准备 | PR [#100](https://github.com/semantic-craft/paper-muse/pull/100)、[#101](https://github.com/semantic-craft/paper-muse/pull/101)；tag `papermuse-v1.1.0` → `aabda65` | **tag 已推，GitHub Release 未发布**（Latest 仍为 1.0.0）；发布是所有者动作 |
| #91 `tension` 字段最小贯通（schema→上墙→产物→卡面） | PR [#102](https://github.com/semantic-craft/paper-muse/pull/102)（`4e840da`），2026-07-14 合并 | 解锁 #93 |
| #94 对抗幕 rubric 增「有趣性/贡献」一行 | PR [#103](https://github.com/semantic-craft/paper-muse/pull/103)（`1161b3a`），2026-07-14 合并 | — |
| #77 离群「≥1」保证兜底 | PR [#105](https://github.com/semantic-craft/paper-muse/pull/105)（`46ebaf4`），2026-07-14 合并 | isolated∧high_quality 为空时回退最高分孤立卡 |
| #78 Proximity 嵌入聚类去重接线 | PR [#107](https://github.com/semantic-craft/paper-muse/pull/107)（`32658a5`），2026-07-14 合并 | 有 encoder key 时 `proximity_basis=embedding`，无 key 明示降级 |
| #74 对抗幕 sidecar 并发化 | PR [#108](https://github.com/semantic-craft/paper-muse/pull/108)（`f6b9a27`），2026-07-14 合并 | TALLY/cache 下沉每主张，`asyncio.gather` |
| #80 三类卡配额代码强制（明示降级） | PR [#106](https://github.com/semantic-craft/paper-muse/pull/106)（merge `ea6874d`），2026-07-14 合并 | 缺类只标注、不补枚举一轮 |
| #93 张力质量闸（纯函数、零网络） | PR [#109](https://github.com/semantic-craft/paper-muse/pull/109)（merge `d764ea0`），2026-07-14 合并 | 5 维闸（本土判据/原创/效用/非同义/steelman 存活），弱张力只降展示序、不动 quality_score/outlier/徽标 |

Codex 批次五票均仅含离线验证，真机按 2026-07-14 决定延期（票内收口评论已注明）。
#106 与 #78 在扫描收尾同层：两类降级统一收进一份 `degradation` 列表（`/scan/status`＋
manifest 共用），`card_type_status` 另存结构化细节供 webui。#93 接在 `apply_tension_quality_gate
→ mark_outliers → apply_card_display_ranks` 收尾链，webui 与 perspectives.md 均按
`display_rank` 排序。合并后离线全量回归 **314 passed**（基线 303）。

更早的完成项（构思幕/圆桌/对抗幕/画像/证据契约 13 子票/1.0 发布）见
`git log` 与已关 issue，不再重复罗列。

## 开放债（2026-07-14 共 7 个 open issue，均非离线可结）

### 张力/MCII 线（地图 [#82](https://github.com/semantic-craft/paper-muse/issues/82)，总规格 [#88](https://github.com/semantic-craft/paper-muse/issues/88)）

- [#84](https://github.com/semantic-craft/paper-muse/issues/84) 现行 vs 问题化样张：
  PR #104 草稿在飞，定稿需所有者真机小跑的样张反应。
- [#92](https://github.com/semantic-craft/paper-muse/issues/92) 问题化枚举定稿：
  仍被 #84 挡，等样张结论。
- 父票 [#82](https://github.com/semantic-craft/paper-muse/issues/82) 地图、
  [#88](https://github.com/semantic-craft/paper-muse/issues/88) 总规格：子票 #84/#92 齐后收。

### 质量/性能/spec 债（均等实践数据）

- [#75](https://github.com/semantic-craft/paper-muse/issues/75) 批量分类：verdict 安全相关的
  精度权衡，票面明示不宜擅自改，**缓**，等实践数据。
- [#79](https://github.com/semantic-craft/paper-muse/issues/79) 真 Elo tournament、
  [#73](https://github.com/semantic-craft/paper-muse/issues/73) gold 判据校准：
  本质依赖付费运行时数据，**缓**，等实际使用积累。

## Codex 离线批次（2026-07-14）——已收官

- 五票全部离线做完并合并关闭：#77、#78、#74、#80、#93（PR #105/#107/#108/#106/#109）。
  #106（#80）切自合并前旧基线、与已进 main 的 #78 扫描收尾同层，由所有者会话手工整合
  （两类降级统一 `degradation` 列表 + `card_type_status` 结构化）后合并。
- **离线可分包的债已清空**。剩余 7 个 open issue 全部需要所有者真机/实践数据，不可再离线代跑。
- 批次纪律留档备复用：零付费；一票一分支一 PR、draft 交所有者评审合并；TDD＋离线全量回归；
  PR/issue 注明「真机验证按 2026-07-14 决定延期」；`gh` 全部带 `--repo semantic-craft/paper-muse`。

## v1.1.0 GitHub Release（2026-07-14 已发布）

- 发布地址：`semantic-craft/paper-muse-releases` → tag `papermuse-v1.1.0`
  （<https://github.com/semantic-craft/paper-muse-releases/releases/tag/papermuse-v1.1.0>）。
- **发布前审计发现原 draft 资产是坏包**：其 `PaperMuse-macos-arm64.zip` 建于 `7357227`(#100)，
  早于 `aabda65`(#101「fix(release): include server import dependencies」)。#101 才把
  `annotation.py`/`run_manifest.py`/`feedback_events.py`/`evidence_graph.py` 加进打包清单
  `PUBLIC_FILES`＋校验清单 `REQUIRED_PATHS`；原包缺这 4 个证据契约服务端模块，真机首用即崩。
- **处置**：从 tag `aabda65` 内嵌模型重建（`MAIN_RUNTIME_FILE`＋`PAPER_MUSE_APP_VERSION=1.1.0`），
  `release_assets.py scan` 按 `REQUIRED_PATHS` 自校验 4 模块通过 → 重签名(397 Mach-O)＋重公证
  (submission `0b1b1209-7347-47af-ae53-bb267ce307ff`, Accepted)＋staple＋Gatekeeper 全过 →
  clobber 替换 draft 资产、body 修 commit `7357227`→`aabda65`、更新 zip SHA → 所有者确认后 publish。
- 最终资产：`PaperMuse-macos-arm64.zip` 301,502,283 B，SHA-256
  `3cda59011c3ce742f12f81e6c2b7ce9bca627811a4658a8f2a8165cb018afc55`；
  内嵌 main runtime 源 SHA `1c9afd28…`（blessed 公开资产）。
- 公证凭据：钥匙串 profile `papermuse-notary`（App Store Connect API key，零明文，Apple 校验通过）。

## 下一步建议

1. ~~发布 v1.1.0 GitHub Release~~ **已于 2026-07-14 发布**（内嵌版，含 #101 修复）。
2. 所有者在实际使用中跑真机：#84 样张定稿 → 解锁 #92；实践暴露的问题单独立 issue 采证。
3. 真机跑起来后再回收 #73/#75/#79 与父票 #82/#88；不再有离线可提前做的实现票。

## 关键入口

- 产品与里程碑：[`docs/prd/2026-07-07-paper-muse-prd.md`](../prd/2026-07-07-paper-muse-prd.md)
- 两幕规格与 §12：[`docs/superpowers/specs/2026-07-05-muse-two-act-design.md`](specs/2026-07-05-muse-two-act-design.md)
- 领域模型：[`CONTEXT.md`](../../CONTEXT.md)
- Issue 规则：[`docs/agents/issue-tracker.md`](../agents/issue-tracker.md)
- 张力/MCII 规格：[`docs/superpowers/specs/2026-07-12-mechanisms-into-muse.md`](specs/2026-07-12-mechanisms-into-muse.md)
