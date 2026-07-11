# 提示词：证据流水线收尾（#52 私有形状收口 · #53 evidence graph）— 下一会话开工

「统一证据契约」(PRD #40) 已完成 **11/13**：#41–#51 全部合并进 main。本文件是最后两个 issue
（#52、#53）的开工提示词。次序：**先 #52（机械收口，解锁 #53）→ 再 #53（graph + UI）**。

## 一句话现状

`evidence.py` 的 `EvidenceRef`/`EvidenceBundle`/`EvidenceGateway` 已贯穿构思幕/PaperQA/对抗幕/
圆桌/批注交接/manifest 六处消费端。`main` 干净（无 open PR、无死分支）。全仓 **253 测试通过、纯离线**。
剩：把跨 seam 的旧 provider 私有形状机械删净（#52），再从权威产物 + 事件构建可重建的
evidence graph 投影（#53，撞 UI 可教墙）。

## #52　移除 provider 私有证据形状（叶子，先做；纯机械收口，不加新功能）

**验收标准（issue #52）**
- [ ] 所有产品调用方只消费 EvidenceRef/EvidenceBundle 或公开 gateway 接口。
- [ ] 旧 anchors/source 私有字典与 GPT/PaperQA/Co-STORM 特有字段不再跨越 provider seam。
- [ ] 迁移期兼容路径删除前有调用方审计，删除后无静默 fallback。
- [ ] 七件文件名称/消费者含义/人类可读内容保持兼容。
- [ ] contract/API/release 测试全绿，测试不再 patch provider 私有注册或依赖内部字典布局。
- [ ] 只做机械收口，不顺带加新功能。

**已做的只读审计（2026-07-11，HEAD e1a7c44）——要处理的确切残留**
1. **`anchors`（卡级 `{title,url}` 私有壳，与 `evidence` EvidenceRef 并存）**：
   - 产出：[`blindspot.py:633`](../../blindspot.py) `card["anchors"] = [{title,url} for r in en_payload["results"][:3]]`；合并 [`blindspot.py:152`](../../blindspot.py)；重置 `blindspot.py:801`。
   - 消费：**webui 直接渲染 `card.anchors`**（[`webui/index.html`](../../webui/index.html) `.anchors` 样式 + DEMO_CARDS 里 `anchors:[...]`）；seven-file 写盘 `blindspot.py:748` `for a in c.get("anchors", [])`（perspectives/sources.md）。
   - **收口方向**：`evidence`(EvidenceRef) 已含 `source.title/url`。要么在呈现层从 `evidence` 派生 anchors（删跨 seam 的私有字段、保 webui/七件文件显示不变），要么迁 webui/写盘消费 `evidence`。**保持七件文件人类可读内容兼容**是硬约束。
2. **检索结果旧壳 `results`/`anchors`/`source`**：[`blindspot.py:580`](../../blindspot.py) `result.get("results") or result.get("anchors")`、`blindspot.py:586` `result.get("source")`——检索层返回的私有 dict 形状。审计 `_retrieval_payload`/`_novelty_for` 的调用方，收敛到 `EvidenceGateway`/`EvidenceRef`。
3. **PaperQA 迁移兼容路径 `legacy_context`**：[`paperqa_bridge.py:295`](../../paperqa_bridge.py)——旧 PaperQA 响应形状的过渡分支。确认锁定版 `paper-qa==2026.3.18` 后删（删前审计确无调用方依赖旧形状，删后无静默 fallback）。

**纪律**：这是 expand–contract 的 contract 阶段。**删前先 grep 审计每处调用方**、列清单；删后跑全套 + `node --check` webui。测试不得再 patch provider 私有注册或依赖内部字典布局（若有，改成走公开契约）。

## #53　构建可重建 evidence graph 投影（← #48,#50,#52；撞 UI 可教墙）

**验收标准（issue #53）**：从七件产物 + EvidenceRef + run manifest + 反馈事件构建可随时重建的
evidence graph，按卡片/主张查支持/证伪/上下文/来源/批注/后续判断，不引入第二事实源。
- [ ] 节点 topic/card/claim/failure/evidence/annotation/feedback + 关系 supports/refutes/context/derived-from/annotates/deepens。
- [ ] 删投影后能从权威文件 + 事件完整重建；重建幂等、不改源数据。
- [ ] UI 至少一条按卡片、一条按主张查看证据关系的完整路径。**（← 可教墙：UI 呈现按「4 方向反应法」由用户签收）**
- [ ] unresolved locator / degraded provider / 被修正反馈在图中可见，不当 verified。
- [ ] 投影不替代七件文件、不要求通用图编辑器、不引入多用户/云同步。
- [ ] 测试覆盖重建、重复输入、部分损坏、关系查询、源数据更新后刷新。

**接缝**：新投影器（宜 `evidence_graph.py`）——纯读 `evidence.json`（`paperqa_bridge.persist/read`）、
`failure-points.md`/`annotation-handoff.json`（#48）、`run-manifest.jsonl`（#49）、`feedback-events.jsonl`（#50），
折叠成 `{nodes, edges}`，幂等重建。关系来源：EvidenceRef.relation（supports/refutes/context）、
批注 annotates、反馈 deepens、卡片 derived-from。**引擎侧（投影 + 查询 + 测试）可离线自主做**；
**UI 的按卡/按主张查看路径要先出 4 方向 mock 让用户挑、再接线**（可教墙）。

## 可做（离线自主，不花 API） / 可教（要用户出手）

- **可做**：#52 全量（审计 + 删除 + 测试收口）；#53 的投影器 + 查询 + 幂等重建测试。
- **可教**：#53 的 UI 查看路径（4 方向 mock → 签收 → 接线）；贯穿欠账——CNKI 中文面 live 验证、
  persona 原文替换、#51 真 Elo 付费 tournament 校准数据、任何真机付费冒烟。

## 怎么跑 / 纪律

- 测试 `.venv/bin/python -m pytest -q`（现 253 全绿基线）；webui 改动跑内联 JS `node --check`。
- 起 server `.venv/bin/python muse_server.py --port 8765`，UI `/ui/`，不接后端 `/ui/?demo=1`。
- 多会话并行同 repo：提交/删改前 `git status` + 看近期 log；每 issue 一分支一 PR、合后删分支；
  改动追溯到验收标准；`.agents/` 机器本地软链**永不提交**（`git add` 用显式文件名，别 `-A`）。
- 每 PR 合并后 `git fetch --prune` + 删远程分支，保持 main 唯一。
