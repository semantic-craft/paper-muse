# paper-muse 产品需求文档（PRD）

日期：2026-07-07　状态：**草案（待用户审定）**
文档关系：本 PRD 是产品层总纲——吸收《两幕剧设计规格 v2》（2026-07-05，审定）全部结论，叠加截至今日 main 的实现现状、领域模型修订（CONTEXT.md ＋ ADR-0001）、以及 GitHub 成熟项目调研的选型结论（[调研报告](../research/2026-07-07-github-mature-projects-for-paper-muse.md)，约 60 仓实查）。冲突处以本 PRD 为准；两幕交互细节仍以 spec v2 为权威。

## 1. 产品定义

**一句话**：给研究者（首发场景：中文法学）的论文构思助手——盲区扫描发掘「相对于你」的未知的未知，圆桌深挖，对抗审查，产物直接喂给写作流水线。

**核心不变式**（全产品最高约束，CONTEXT.md）：
> 发现力/新颖性相对于「这个研究者」判定——研究者画像＋本次困惑合成参照系，非显而易见/离群/新颖都以参照系为基准，不是相对模型语料。

**定位依据（调研坐实的双重空白）**：
1. **researcher-relative novelty 空白**：全网 ideation 项目（AI-Scientist 系、CoI-Agent、Nova、TrustResearcher、Google co-scientist 及其复刻等）的新颖性判定清一色 literature-relative / corpus-relative，无一以「研究者画像＋困惑」为参照系（调研 §3，含最强干扰项 freephdlabor 的排除验证）；
2. **CNKI/中文学界面空白**：OpenAlex 仅覆盖 37% 中文核心期刊/24% 文章、CNKI 采集 2016 年后近零（arXiv:2512.16339、2507.19302）；paper-qa/ScholarQA/OpenScholar/PaSa 文档均无 CNKI 路径。中文法学检索面在开源生态没有现成件——这是护城河，也是 zh_hits 必须自研的原因（调研 §5.3）。

**期望管理**：本工具是「提高意外发现命中率的机器」，不是命中保证器（spec §1）。

**目标用户**：单机单用户。首发用户=作者本人（中文法学研究者，自有 Zotero 库，CNKI 可用）。不做云端/多用户/移动端。

**核心场景**（spec §1/§3）：启动 App → 输入主题＋困惑（画像已存，自动带出）→ ≤20s 首批卡片上墙 → 三键反应（已知/新但不适用/新且值得深挖）→ 值得深挖的卡开圆桌 或 送对抗幕 → 产物落盘被 grill-with-docs / to-prove / paper-annotator 消费。

## 2. 目标与成功指标

三个并列产品目标（spec §1）：**发现力、好看好用（首批 ≤20s）、联动（文件契约）**。

验收全盘继承 spec §12（8 条），两条提级为北极星：
- **质量地板**：前三次真实使用中，≥1 张卡被标「新且值得深挖」；
- **速度**：首批卡 ≤20s；**卡片全上墙（含英文命中/自有库徽标）≤90s** 为硬线。**中文面（CNKI）zh_hits 徽标异步尾随、不计入 90s**——CNKI 经浏览器会话检索、每查约 35s，串行 N 卡的物理延迟压不进 90s；扫描在卡片全上墙即视为可用（可翻卡、可深挖），zh 徽标陆续补挂（口径拍板 2026-07-07，实测依据见 issue #15）。

新增一条验收（来自调研）：**红队抗注入**——草稿内藏「操纵审稿放水」指令时，对抗幕不得被收买（Breaking-the-Reviewer 式攻击作为固定冒烟用例）。

## 3. 术语

以 CONTEXT.md 为准：**研究者画像**（稳定身份：领域/立场/熟悉的理论，**不含困惑**）、**困惑**（本次一次性输入）、**参照系**（画像＋困惑）、**主题**。注意：spec v2 §4「画像含本篇困惑」已被领域模型修订推翻，以 CONTEXT.md 为准。

## 4. 现状盘点（截至 2026-07-07 main）

**已建**：
- 扫描引擎 `blindspot.py`：三家合议（DeepSeek/OpenAI/Gemini 并行）、三类卡配额、离群标亮、新颖性三面（Perplexity/Tavily 英文面、opencli cnki 中文面、zsearch 自有面）、抑制表（angle-feedback.json）、七件产物落盘。
- `muse_server.py`（FastAPI :8765）：/scan 系（后台扫描/增量轮询/三键反馈）、圆桌 /session /status /step /report、/scan/products 产物清单、/ui 静态托管。
- web 画布 `webui/index.html`：卡片墙（文稿台视觉，四方向反应法定稿）＋圆桌视图＋产物抽屉＋画像 A+B（首扫「开笔卡」采集、左栏就地编辑、「因你」词面启发式标签）。
- SwiftUI 壳：WKWebView 画布、museBridge（reveal/open）、MuseServer 进程管理。
- 圆桌引擎：Co-STORM（stanford-oval/storm 上游）＋多源检索层（Perplexity/Jina/Mixed RM）。

**未建**：对抗幕（引擎＋UI 全无，web 里是占位 tab）、圆桌钉死席位（引擎侧未强制两 persona）、主题预填。

**核心欠账（门槛）**：真实端到端冒烟从未跑过——全部验证止于付费边界（demo 渲染/模拟流式/xcodebuild）。CNKI 活会话未验。app 自启 server 路径未验。

**待决冲突**：ADR-0001 判画像存机器级 `~/.config/paper-muse/researcher.md`（跨论文复用、不进 git），但已上线代码（5be05e9）走每论文 `profile.md`（UI 采集→/scan→blindspot 落盘）。两者方向相反并存于 main。解法见 R2。

**persona 原文槽位**：「第一性原理」「对抗式审查」两组工作方法论原文已落位于 `prompt_assets.py`，并被盲区扫描、圆桌固定席位和对抗幕红队复用。

## 5. 外部选型（build-vs-buy，调研结论）

完整依据见[调研报告](../research/2026-07-07-github-mature-projects-for-paper-muse.md)。总表：

| 模块 | 结论 | 借力对象 |
|---|---|---|
| 盲区扫描引擎 | **自研正确** | 借流程设计：AI-Scientist-v2 ideation、CoI-Agent、Nova、TrustResearcher |
| 多模型合议/离群排序 | **抽组件集成** | Kaimen-Inc/Co-Scientist（Apache-2.0）：Elo 锦标赛＋Proximity 嵌入去重 |
| 圆桌深挖 | **维持现状** | stanford-oval/storm（MIT；上游 2025-09 停更→按自养 fork 对待） |
| 对抗幕·审稿引擎 | **自研 orchestration＋抽设计** | MARG 专化分工、AgentReview 阶段剧场、AI-Scientist rubric（只借设计） |
| 对抗幕·证据检索 | **抽组件集成** | gpt-researcher（pip，Apache-2.0，21 retriever 含 custom/MCP） |
| 卡片锚点/own_hits | **采用为底座** | paper-qa / PaperQA2（pip，Apache-2.0，LiteLLM 任意模型） |
| en_hits 客户端 | 采用为客户端 | semanticscholar ＋ pyalex（均 MIT）；Owl「Has anyone…?」问式只借问式 |
| zh_hits/CNKI 面 | **自研正确（护城河）** | —（生态无现成件，见 §1 空白证据） |
| 引擎宿主形态 | **自研正确（不换 harness）** | deep research 赛道已 harness 化（deer-flow 2.0 等），与「FastAPI 内嵌领域引擎」方向相反 |

**License 红线**（决定「抄代码」还是「只看设计」，全文见调研 §7.8）：
- 可复制代码（Apache/MIT）：gpt-researcher、paper-qa、ai2-scholarqa-lib、Kaimen Co-Scientist、MARG、AgentReview、storm、semanticscholar、pyalex、K-Dense skills、DeepReviewer-v2；
- 只看不抄：AI-Scientist v1/v2（2025-12 起换自定义 RAIL 系许可）、CycleResearcher 主仓（研究许可＋注册）、HKUDS/AI-Researcher、IRIS、ResearchAgent、Nova（无 LICENSE＝保留所有权利）。

**明确不做**：不采任何 deep research harness 为底座；不自训/自部署审稿模型进 MVP（GPU＋License 成本）；不依赖 FutureHouse/Edison 云 API 做新颖性判据（公司分拆、GitHub 仓已 404，连续性风险）。

## 6. 需求详述

### R1 · 门槛：真实端到端冒烟＋§12 验收基线（P0，先于一切功能）
起 server＋app，真跑一个中文法学主题，逐条对 spec §12：首批 ≤20s、三类卡齐、≥1 离群、每卡最强反驳＋真实锚点、中英密度非空（或明示未检）、抑制表生效、深挖圆桌全流程七件产物、「在访达打开」定位、三 provider 两幕跑通。炸出的 bug 全部建 GitHub issue 走 /triage（tracker 已就绪）。CNKI 项需用户 Chrome 会话在场配合。**通过前不上任何新功能切片。**

### R2 · 研究者画像与参照系对齐（P0）
按 ADR-0001＋CONTEXT.md 修正现有实现：
- **存储迁移**：画像源头改为机器级 `${XDG_CONFIG_HOME:-~/.config}/paper-muse/researcher.md`；扫描时将快照物化为该论文 `profile.md`（只读副本）——文件契约七件不变，下游消费者无感。
- **结构调整**：画像＝领域/已有立场/熟悉的理论三要素；**困惑从画像剥离**，UI 上与主题并列为本次输入（画像 A+B 的开笔卡采集与就地编辑保留，字段照此调整）。
- **「因你」升级**：从词面启发式升级为引擎结构化输出——卡 schema 增加 `vs_profile` 字段（这张卡相对画像哪一条构成非显而易见）。
- 验收：无画像时 UI 明示「发现力打折」；同一画像跨两篇论文复用免重填；困惑变化不污染画像。

### R3 · 盲区扫描·新颖性定位强化（P1）
- **en_hits 换学术源**：`semanticscholar`＋`pyalex` 双路并查（S2 限流时 OpenAlex 免 key 兜底）替换/增补 Perplexity/Tavily 做英文学术命中计数；查询语句统一规范为 Owl 式「Has anyone …?」问式；沿用「novelty check＝工具调用＋无 key 降级明示」模式（AI-Scientist-v2 同款）。
- **合议升级**（借 Kaimen Co-Scientist，Apache-2.0）：a) Proximity 嵌入聚类去重（防三模型出同质角度）；b) 离群标亮从「仅一家提出」启发式升级为成对辩论 Elo 锦标赛——离群＝Elo 高且簇内孤立。其 Nature 论文逐字 prompt 可作合议 prompt 底稿。
- **出卡过滤层**（借 TrustResearcher 两段式）：先对外部文献查新颖、再内部去重＋多样性选择，然后才上墙。
- **prompt 资产**：从 K-Dense scientific-agent-skills（MIT）摘 Hypothesis Generation / Scientific Brainstorming / Peer Review 三个 skill 方法论文本，中文法学语境改写后入 prompt 库。
- 既定不变：CNKI 中文面（降级明示）、zsearch 自有面（📚已藏未用徽标）、中英密度差金标、抑制表。

### R4 · 对抗幕（P1，构思幕验收通过后最大的新功能）
spec §6 全量：有稿模式（扫 `$PAPER_MUSE_OUTPUT_DIR` 选草稿→抽中心主张→每条 3-5 失败点→检索证伪/佐证→无证据标「未决」→failure-points.md）＋无稿模式（攻击一句主线，卡片一键送入）。persona 用「对抗式审查」原文。选型落地：
- **证据检索**：嵌 `gpt-researcher`（pip）为证伪检索编排器；为 opencli cnki 与 zsearch 各写 custom retriever 适配器（或包成 MCP server 走其 mcp retriever），多源混跑；自定义 report_type 为「证伪备忘录」。
- **审稿 orchestration 自研回合机**，结构映射三件（均只借设计）：MARG 的 leader-specialist 分工（每中心主张→一个专化红队 agent）＋ AgentReview 的阶段剧场（红队评审→作者代理答辩→仲裁 meta-review）＋ AI-Scientist 的 rubric＋自反思（rubric 重写为法学论文评审维度）。
- **按角色配模型**（open_deep_research 思路）：证伪检索广度用快/便宜模型，主张审查与仲裁用强模型。
- **鲁棒性验收**：注入攻击用例固定进冒烟（见 §2）。

### R5 · own_hits 证据层升级（P2）
引入 `paper-qa>=5`（pin 版本，Apache-2.0）对 Zotero 导出 PDF 目录建索引；LLM 走 LiteLLM 配 DeepSeek/Gemini；`ask()` 带引注回答映射进 sources.md 契约。zsearch 保留做秒级命中计数（own_hits 徽标），paper-qa 负责「深挖时引用自有库原文」的证据问答（圆桌/对抗幕共用）。

### R6 · 圆桌钉死席位（P2）
引擎侧强制 ①第一性原理专家 ②跨学科猎人 两 persona 进真 CoStormRunner（现仅 web demo 演出）。上游按自养 fork 对待：不等上游修 bug，安全补丁自己打。

### R7 · 联动与文件契约（持续不变式）
`docs/agents/muse/` 七件：perspectives / questions / mindmap / failure-points / sources / profile / angle-feedback。消费者 grill-with-docs、to-prove/diagnose、paper-annotator。联动靠文件约定，不靠共享界面。anamra 注入 PAPER_MUSE_OUTPUT_DIR 照旧。

### R8 · UI/审美（已定稿方向，持续）
文稿台（单栏手稿＋朱批旁注）已定稿；新面（对抗幕 UI、画像字段调整）沿用同一设计语言与 tokens.css；新大面仍走「4 方向静态 mock 先反应」流程。多 provider 三选一横切两幕（既定验收项）；多模型合议独立于此开关（发现机制，非成本选项）。

### R9 · 小债清理（P2，随手）
app 自启 server 路径验证；`app/build/` 旧产物清理；圆桌错误态（409/热身失败）从 toast 升级为可恢复态；主题预填（读 PAPER_MUSE_OUTPUT_DIR 近期 md 标题）。

## 7. 非目标

- 继承 spec §11：不生成论文正稿（活人感归 humanize）、不做云端/多用户/移动端、本期不做 Zotero VectorRM 全量混检入圆桌、不做思维导图可视化。
- 调研后新增排除：不做通用 agent harness、不自训模型、不接 FutureHouse/Edison 云 API、不用 scholarly（Google Scholar 爬虫封禁风险）进主路径。

## 8. 里程碑

| 里程碑 | 内容 | 完成判据 |
|---|---|---|
| **M0 冒烟验收** | R1 | §12 逐条记录全绿，或 bug 清单归零路径明确（issue 化） |
| **M1 参照系对齐** | R2 | 画像机器级迁移＋困惑分离＋vs_profile 字段上卡 |
| **M2 新颖性强化** | R3 | en_hits 走 S2/OpenAlex；合议去重＋Elo 离群上线 |
| **M3 对抗幕** | R4 | 两模式产出 failure-points.md，§12 条 5＋抗注入用例达标 |
| **M4 证据层＋席位＋小债** | R5/R6/R9 | paper-qa 进深挖链路；钉死席位进真引擎 |

每里程碑完成即真实使用一轮，北极星（质量地板）持续观测。M1/M2 顺序可互换；M3 依赖 M0 通过。

## 9. 风险与开放问题

- **CNKI 依赖浏览器会话**：风控/会话失效→中文面判据缺失→密度分类降级。缓解：降级明示＋解锁指引；此面无开源替代（调研 §5.3），脆但独占。
- **20s 首批 vs 流程加层**：R3 的过滤层/锦标赛若同步执行必超时——一律异步补挂（先上墙后补徽标/排序），首批时延红线不动。
- **依赖健康**：paper-qa v5 API 迭代快（pin 版本）；storm 上游停更（自养 fork）；gpt-researcher 报告体裁偏综述（需自定义 report_type）；Kaimen Co-Scientist 星少但工程完成度高（vendor 思路而非依赖其发布节奏）。
- **画像迁移**：R2 涉及 UI/server/engine 三层，需一次对齐避免中间态。
- **persona 原文**：两组工作方法论原文已落位（`prompt_assets.py`），不再阻塞 R4 persona 与扫描第一性拆解的最终形态。
- **开放**：对抗幕单次证伪检索的成本上限（每主张检索次数/预算终止条件，可借 node-DeepResearch 的 token 预算循环设计）；failure-points 与 paper-annotator 锚回稿面的字段契约细化；R2 迁移后 profile.md 快照是否保留「就地编辑回写机器级」的双向同步（建议首版单向：机器级→快照）。

## 10. 附录

- 调研报告（选型依据，含 License 红线全表与来源清单）：[docs/research/2026-07-07-github-mature-projects-for-paper-muse.md](../research/2026-07-07-github-mature-projects-for-paper-muse.md)
- 设计规格：[docs/superpowers/specs/2026-07-05-muse-two-act-design.md](../superpowers/specs/2026-07-05-muse-two-act-design.md)（v2 审定）
- 领域模型：[CONTEXT.md](../../CONTEXT.md) ＋ [docs/adr/0001](../adr/0001-researcher-profile-machine-global.md)
- 交接现状：[docs/superpowers/HANDOFF-ui-canvas.md](../superpowers/HANDOFF-ui-canvas.md)
