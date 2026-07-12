# paper-muse 开发清账与跨机交接

> 状态快照：2026-07-13；核对基线：`origin/main` = `2c69b05`。
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

- 2026-07-13 核对时，GitHub 远端只保留 `main`，没有待处理 PR。
- PR [#95](https://github.com/semantic-craft/paper-muse/pull/95) 已 rebase-merge；原分支
  `docs/interestingness-verified` 已删除。远端对应提交是 `1a6f168`、`2c69b05`。
- helios 记录的 `710f5e9` **不是分叉**：它是当前 `main` 的祖先，落后 111 个提交。
  不要为它建恢复分支或做合并；在 helios 上按上面的命令快进即可。
- 多机器/多会话同时工作时，每次提交、删分支或合并前都重新执行前两行；功能改动走独立分支和 PR，禁止 force-push `main`。

## 一句话现状

PaperMuse 1.0 已发布；构思幕、圆桌、对抗幕、画像、证据契约和本地运行/发布链路均已有实现。当前主要欠账不是“把两幕从零做出来”，而是**用当前 HEAD 做带版本记录的付费真机复验与校准**，以及实现新规格 [#88](https://github.com/semantic-craft/paper-muse/issues/88) 的 `tension` / MCII 切片。

## 已完成并进入 main

| 范围 | 可核对证据 | 状态边界 |
|---|---|---|
| 构思幕 web 画布与圆桌 | `09b6670`、`f21df3e` | UI/接口已接线；不是当前付费真机验收证据 |
| 真实扫描修复与速度 | `19d9c8e`、`2784ca0`、`240aa29` | 历史实测首批从 84s 降至 2s、稳定拆解链 11s；代码变化后仍须按当前 HEAD 复验 |
| 画像与参照系 | `5ddad17`、`d51121e` | 机器级画像、困惑剥离、结构化 `vs_profile` 已实现 |
| 对抗幕 | `12d9fa8`、`2d63e86`、`401c168` | 回合机、证伪检索、UI/API 已实现；两模式/多 provider 的当前真机复验仍欠 |
| 统一证据契约 | PR #54–#70；父票 [#40](https://github.com/semantic-craft/paper-muse/issues/40) | #41–#53 共 13/13 子票已合并关闭；父票保留，等待付费/真机验收与 Elo 校准 |
| 稳定性与性能清扫 | PR #72、#76、#81 | 已合并；未把开放的真机与质量校准债自动视为完成 |
| 有趣性证据基础与 D9 | PR [#95](https://github.com/semantic-craft/paper-muse/pull/95) | 核验版研究与“按共同体改变张力形态”已定稿；仅文档/规格完成，功能未实现 |
| 发布 | release `papermuse-v1.0.0` | GitHub 已有 PaperMuse 1.0 与 main Python runtime 预发布件 |

## 当前不能报完成的门

以下项目需要用户在场、真实 provider/预算或活 Chrome，会继续保留为未核验：

1. 用**当前代码版本**跑扫描、圆桌、对抗幕完整付费冒烟，并把 run manifest、provider、耗时和产物路径一起留档。
2. CNKI 中文面 live 验证：活 Chrome 会话下确认 `zh_hits` 不是降级 `None`。
3. 三个 provider 横跨两幕的当前复验；对抗幕有稿/无稿两模式都产出 `failure-points.md`。
4. #51 真 Elo tournament 的付费校准数据，以及 #73/#77–#80 的质量与规格债。
5. App 自起 server 路径（`MuseServer.launch()`，不是复用已运行 server）的当前实机确认。

不要用 2026-07-07 的历史速度记录替代当前 HEAD 的验收，也不要在没有付费实测时关闭 [#40](https://github.com/semantic-craft/paper-muse/issues/40)。

## `tension` / MCII 工作线清账

- 地图：[#82](https://github.com/semantic-craft/paper-muse/issues/82)
- 总规格：[#88](https://github.com/semantic-craft/paper-muse/issues/88)
- PR #95 只完成证据基础与 D9；没有完成 #83、#84 或 #89–#94。
- [#83](https://github.com/semantic-craft/paper-muse/issues/83) 仍欠 prompt-ready 的教义学/社科法学两套完整判据、证据层级与中文示例。
- [#84](https://github.com/semantic-craft/paper-muse/issues/84) 仍欠“现行 vs 问题化”真机样张、出卡率与耗时数据。
- 真机门通过后，可并行前沿是：
  - [#89](https://github.com/semantic-craft/paper-muse/issues/89)：预置键契约单点声明；
  - [#90](https://github.com/semantic-craft/paper-muse/issues/90)：MCII 三元产物。
- 后续依赖：`#89 → #91 → #92/#93`；其中 #92 还依赖 #84，#93/#94 还依赖 #83。

## 其他开放债

2026-07-13 远端共有 19 个 open issue：

- [#40](https://github.com/semantic-craft/paper-muse/issues/40)：证据 PRD 代码完成后的真机/付费收尾；
- #73–#80：质量、性能与既有 spec 缺口；
- [#82](https://github.com/semantic-craft/paper-muse/issues/82)–[#94](https://github.com/semantic-craft/paper-muse/issues/94)：张力/MCII 地图、输入资产、总规格与实现票。

`ready-for-agent` 只表示票面规格足够清楚，不覆盖票内的 `Blocked by` 和真机门。领票前必须同时检查依赖、assignee、最新评论与 `origin/main`。

## 下一步建议

1. metis/helios 先快进到 GitHub `main`，确认工作树干净。
2. 用户确认预算并准备活 Chrome 后，按 spec §12 对当前 HEAD 跑一次完整真机验收；炸出的每个问题单独建 GitHub issue。
3. 验收通过后，两个会话可分别认领 #89、#90；不要提前并入被 #83/#84 阻塞的 #92–#94。
4. 每个实现票坚持独立分支、TDD、全量离线回归、PR；合并后更新对应 Issue，不再另造会话私有 handoff。

## 关键入口

- 产品与里程碑：[`docs/prd/2026-07-07-paper-muse-prd.md`](../prd/2026-07-07-paper-muse-prd.md)
- 两幕规格与 §12：[`docs/superpowers/specs/2026-07-05-muse-two-act-design.md`](specs/2026-07-05-muse-two-act-design.md)
- 领域模型：[`CONTEXT.md`](../../CONTEXT.md)
- Issue 规则：[`docs/agents/issue-tracker.md`](../agents/issue-tracker.md)
- 张力/MCII 规格：[`docs/superpowers/specs/2026-07-12-mechanisms-into-muse.md`](specs/2026-07-12-mechanisms-into-muse.md)
