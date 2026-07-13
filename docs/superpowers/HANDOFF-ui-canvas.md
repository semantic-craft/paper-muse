# paper-muse 开发清账与跨机交接

> 状态快照：2026-07-14；核对基线：`origin/main` = `51e1ebd`。
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

- 2026-07-14 核对时，远端 open PR 共两个：[#96](https://github.com/semantic-craft/paper-muse/pull/96)（本台账）、
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
  `MuseServer.launch()` 自起实测、真 Elo 付费校准及 #73/#77 的真机数据。

## 一句话现状

PaperMuse 1.1.0 已在 main 备好并打 tag（GitHub Release 尚未发布）；张力/MCII 线的
证据基础、本土判据、快照键收口、MCII 三元产物、`tension` 字段贯通、对抗幕 rubric
均已合并进 main。剩余主线是 #92/#93 收尾、#73–#80 质量/性能债，以及已排定的
Codex 离线欠账批次。

## 已完成并进入 main（自上一版台账基线 `2c69b05` 起）

| 范围 | 可核对证据 | 状态边界 |
|---|---|---|
| #83 中文法学「有趣性」判据（教义学/社科法学两套） | PR [#97](https://github.com/semantic-craft/paper-muse/pull/97)（`42d6a6f`） | 判据/文档定稿，供 #93 消费 |
| #89 卡片快照键单点声明 | PR [#98](https://github.com/semantic-craft/paper-muse/pull/98)（`b6b8a65`） | prefactor，解锁 #91 |
| #90 MCII 三元产物 | PR [#99](https://github.com/semantic-craft/paper-muse/pull/99)（`e015a49`、`92b1a86`） | 目标—障碍—if–then 进产物与圆桌 |
| v1.1.0 发布准备 | PR [#100](https://github.com/semantic-craft/paper-muse/pull/100)、[#101](https://github.com/semantic-craft/paper-muse/pull/101)；tag `papermuse-v1.1.0` → `aabda65` | **tag 已推，GitHub Release 未发布**（Latest 仍为 1.0.0）；发布是所有者动作 |
| #91 `tension` 字段最小贯通（schema→上墙→产物→卡面） | PR [#102](https://github.com/semantic-craft/paper-muse/pull/102)（`4e840da`），2026-07-14 合并 | 解锁 #93 |
| #94 对抗幕 rubric 增「有趣性/贡献」一行 | PR [#103](https://github.com/semantic-craft/paper-muse/pull/103)（`1161b3a`），2026-07-14 合并 | — |

更早的完成项（构思幕/圆桌/对抗幕/画像/证据契约 13 子票/1.0 发布）见
`git log` 与已关 issue，不再重复罗列。

## 开放债（2026-07-14 共 12 个 open issue）

### 张力/MCII 线（地图 [#82](https://github.com/semantic-craft/paper-muse/issues/82)，总规格 [#88](https://github.com/semantic-craft/paper-muse/issues/88)）

- [#93](https://github.com/semantic-craft/paper-muse/issues/93) 张力质量闸：
  阻塞已全部解除（#91、#83 均关），纯函数零网络，可动工——已排入 Codex 批次。
- [#84](https://github.com/semantic-craft/paper-muse/issues/84) 现行 vs 问题化样张：
  PR #104 草稿在飞，定稿需所有者真机小跑的样张反应。
- [#92](https://github.com/semantic-craft/paper-muse/issues/92) 问题化枚举定稿：
  仍被 #84 挡，等样张结论。

### 质量/性能/spec 债

- [#77](https://github.com/semantic-craft/paper-muse/issues/77) 离群「≥1」兜底、
  [#80](https://github.com/semantic-craft/paper-muse/issues/80) 三类卡配额代码强制、
  [#78](https://github.com/semantic-craft/paper-muse/issues/78) Proximity 嵌入接线、
  [#74](https://github.com/semantic-craft/paper-muse/issues/74) 对抗幕 sidecar 并发化
  ——离线可做，已排入 Codex 批次（见下）。
- [#75](https://github.com/semantic-craft/paper-muse/issues/75) 批量分类：verdict 安全相关的
  精度权衡，票面明示不宜擅自改，**缓**，等实践数据。
- [#79](https://github.com/semantic-craft/paper-muse/issues/79) 真 Elo tournament、
  [#73](https://github.com/semantic-craft/paper-muse/issues/73) gold 判据校准：
  本质依赖付费运行时数据，**缓**，等实际使用积累。

## Codex 离线批次（2026-07-14 排定）

- 分包票与顺序：#77 → #80 → #78 → #74 →（条件）#93；#102 已合并，条件已满足。
- #80 策略已由所有者定为**明示降级**（缺类只标注、不补枚举一轮）。
- 约束：零付费调用；一票一分支一 PR；TDD＋离线全量回归；不合并 PR（所有者评审合并）；
  `gh` 全部带 `--repo semantic-craft/paper-muse`（防 upstream fork footgun）。
- #77/#78/#80/#93 同涉扫描链路（`blindspot.py`/`muse_server.py`），须同会话串行；
  #74 只动 `gptr_sidecar.py`（隔离 `.venv-gptr`），可并行另开会话。
- 完整提示词由所有者持有并投放 Codex 会话；状态一律落 GitHub issue/PR，不另造私有 handoff。

## 下一步建议

1. 所有者发布 v1.1.0 GitHub Release（tag 已在远端）。
2. 投放 Codex 离线批次，逐 PR 评审合并。
3. 所有者在实际使用中跑真机：#84 样张定稿 → 解锁 #92；实践暴露的问题单独立 issue 采证。
4. 每个实现票坚持独立分支、TDD、全量离线回归、PR；合并后更新对应 Issue。

## 关键入口

- 产品与里程碑：[`docs/prd/2026-07-07-paper-muse-prd.md`](../prd/2026-07-07-paper-muse-prd.md)
- 两幕规格与 §12：[`docs/superpowers/specs/2026-07-05-muse-two-act-design.md`](specs/2026-07-05-muse-two-act-design.md)
- 领域模型：[`CONTEXT.md`](../../CONTEXT.md)
- Issue 规则：[`docs/agents/issue-tracker.md`](../agents/issue-tracker.md)
- 张力/MCII 规格：[`docs/superpowers/specs/2026-07-12-mechanisms-into-muse.md`](specs/2026-07-12-mechanisms-into-muse.md)
