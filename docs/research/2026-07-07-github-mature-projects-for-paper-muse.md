# GitHub 成熟开源项目调研：paper-muse 复用/借力/自研选型（2026-07-07）

> 调研方法：所有 stars/最近 push/License 均由 `gh api` 于 2026-07-07 实查（非训练记忆）；项目能力读各 repo README/docs 一手材料；发现性搜索用 `gh search repos` + web 检索。本报告是「完整 PRD」的选型依据。
>
> 评估基准（paper-muse 核心不变式）：发现力/新颖性相对「这个研究者」（画像+困惑=参照系）与其领域文献（中文法学，CNKI）判定，不是相对模型语料。paper-muse 要引擎不要产品壳（SwiftUI 壳 + FastAPI :8765 + Python 引擎已定）。

---

## 1. TL;DR：build-vs-buy 总表

| paper-muse 模块 | 结论 | 借力对象 | 一句理由 |
|---|---|---|---|
| 盲区扫描引擎（构思幕核心） | **自研正确** | 借鉴 AI-Scientist-v2 ideation、CoI-Agent、Nova、TrustResearcher 的流程设计 | 全部现有 ideation 项目的新颖性都以「文献/语料」为参照系，无一以「研究者画像+困惑」为参照系（见 §3 空白验证） |
| 多模型合议 + angle-feedback（离群标亮） | **抽组件集成 / 借鉴设计** | Kaimen-Inc/Co-Scientist（Google co-scientist 忠实复刻，Apache-2.0） | Elo 锦标赛排序 + Reflection 审查 + Proximity 嵌入去重三件套是纯 Python、provider 无关，正好补「离群角度怎么排序/去重」的短板 |
| 圆桌深挖 | **维持现状**（已采用为底座） | stanford-oval/storm（Co-STORM，MIT） | 已建成；上游 2025-09-30 后停更，按「自养 fork」对待 |
| 对抗幕：审稿引擎 | **自研 orchestration + 抽设计** | MARG（多 agent 专化审稿，Apache-2.0）、AgentReview（角色/阶段设计）、AI-Scientist reviewer（rubric 设计，License 警告）、DeepReviewer-v2（MIT） | 现成审稿项目全是「模拟 ML 会议评审」，无中文法学、无 failure-points.md 这种产物契约；但多 agent 专化 + 多轮 rebuttal 的结构可直接搬 |
| 对抗幕：证据检索（证伪引擎） | **抽组件集成** | assafelovic/gpt-researcher（pip 库，Apache-2.0，21 个 retriever 含 custom/MCP） | 唯一「检索器全插拔 + 可库嵌入」的成熟深研引擎，CNKI/zsearch 可挂 custom retriever；不必自写检索编排循环 |
| 卡片文献锚点 / own_hits 层 | **采用为底座** | Future-House/paper-qa（PaperQA2，Apache-2.0，pip） | LiteLLM 任意模型（DeepSeek/Gemini 均可）、本地 PDF 目录 + S2/Crossref 元数据，直接盖住 Zotero 自有语料层的证据问答 |
| 新颖性三角定位（en/zh/own_hits） | **自研正确**（外围客户端可借） | semanticscholar、pyalex（均 MIT）做 en_hits 客户端；Owl 的「Has anyone…」问式做查询规范（设计借鉴） | zh_hits 无人能替：OpenAlex 对中文核心期刊覆盖仅 37%/文章 24%，CNKI 采集 2016 年后近零（硬证据，见 §4.3） |
| 检索面（CNKI/中文法学） | **自研正确（护城河）** | — | 国际学术索引对 CNKI 覆盖近零 = paper-muse 的差异化被数据证实 |
| 引擎宿主形态 | **自研正确**（不换 harness） | 观察 deer-flow 2.0 / EvoScientist 的 harness 化趋势即可 | deep research 赛道 2026 已收敛为「通用 agent harness」，paper-muse 要的是嵌入 FastAPI 的领域引擎，方向相反 |

---

## 2. 赛道一：研究构思 / 假设生成 / idea novelty

### 2.1 候选对比表（gh api 实查 2026-07-07）

| 项目 | stars | 最近 push | License | 语言 | 可嵌入性 | provider 可换 | 检索可插拔 |
|---|---|---|---|---|---|---|---|
| [SakanaAI/AI-Scientist](https://github.com/SakanaAI/AI-Scientist) | 14,165 | 2025-12-19 | ⚠️ 自定义（AI Scientist Source Code License v1.0） | Python/Jupyter | 差（研究代码） | OpenAI/Claude/Gemini（无 DeepSeek） | 固定 S2 |
| [SakanaAI/AI-Scientist-v2](https://github.com/SakanaAI/AI-Scientist-v2) | 6,772 | 2025-12-19 | ⚠️ 同上 | Python | 中（ideation 是独立脚本） | 同上 | 固定 S2 |
| [SamuelSchmidgall/AgentLaboratory](https://github.com/SamuelSchmidgall/AgentLaboratory) | 5,726 | 2025-08-20 | MIT | Python | 差（整套流水线，无 pip 包） | OpenAI + **DeepSeek** | arXiv/HF 等，写死 |
| [HKUDS/AI-Researcher](https://github.com/HKUDS/AI-Researcher) | 5,546 | 2025-10-16 | ⚠️ 无 LICENSE 文件 | Python | 差（docker 全套） | Claude/OpenAI/DeepSeek/openrouter | arXiv/IEEE/ACM/GScholar 等 |
| [EvoScientist/EvoScientist](https://github.com/EvoScientist/EvoScientist) | 4,078 | 2026-07-06 | Apache-2.0 | Python | 中（PyPI + SDK，但基于 LangChain DeepAgents harness） | Claude/OpenAI/Gemini/MiniMax/NVIDIA | Tavily + MCP |
| [K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills) | 30,320 | 2026-07-06 | MIT | Python（skills 库） | 好（Agent Skills 标准，非引擎） | 与 agent 无关 | 每技能自带 |
| [Kaimen-Inc/Co-Scientist](https://github.com/Kaimen-Inc/Co-Scientist) | 187 | 2026-06-16 | Apache-2.0 | Python | **好**（模块化：Supervisor/SQLite 队列/各 agent 可拆） | anthropic/openai/openrouter/gemini/groq/together/mistral/ollama/openai_compatible | 文献检索在 Generation 内，可改 |
| [DAMO-NLP-SG/CoI-Agent](https://github.com/DAMO-NLP-SG/CoI-Agent) | 509 | 2025-01-15 | Apache-2.0 | Python | 差（main.py 脚本 + grobid/java 依赖） | Azure/OpenAI 兼容端点 | 固定 Semantic Scholar |
| [ltjed/freephdlabor](https://github.com/ltjed/freephdlabor) | 566 | 2026-05-14 | MIT | Python | 中 | smolagents 系 | 可配 |
| [cheerss/SciPIP](https://github.com/cheerss/SciPIP) | 71 | 2025-02-27 | MIT | Python | 差 | 部分 | 固定 |
| [RenqiChen/Virtual-Scientists](https://github.com/RenqiChen/Virtual-Scientists)（VIRSCI，ACL 2025） | 68 | 2025-07-26 | Apache-2.0 | Python | 差（科学社会学模拟） | 部分 | 固定 |
| [valleysprings/TrustResearcher](https://github.com/valleysprings/TrustResearcher)（WWW 2026 demo） | 46 | 2026-02-26 | MIT | Python | 中（CLI + Web UI，模块化 skill 系统） | 可配 | Semantic Scholar |
| [JinheonBaek/ResearchAgent](https://github.com/JinheonBaek/ResearchAgent)（NAACL 2025） | 43 | 2025-08-24 | ⚠️ 无 LICENSE | Python | 差 | OpenAI | S2 学术图谱 |
| [Anikethh/IRIS](https://github.com/Anikethh/IRIS-Interactive-Research-Ideation-System)（ACL 2025 demo） | 35 | 2026-05-10 | ⚠️ 无 LICENSE | Python | 差（Flask app） | **LiteLLM 任意** | Semantic Scholar |
| [hflyzju/Nova](https://github.com/hflyzju/Nova)（ACL 2025 findings） | 7 | 2025-09-23 | ⚠️ 无 LICENSE | Python | 差 | — | — |

一笔带过：mims-harvard/ToolUniverse（1,533★，Apache-2.0，2026-07-06 活跃）是「600+ 科学工具的 MCP 化工具生态」，属工具层不属构思层，若日后要挂更多科学 API 可回头看。conradry/open-coscientist-agents（68★）已 archived。The-Swarm-Corporation/AI-CoScientist（115★）是极简复刻。jimmc414/Kosmos（548★，无 License）是 Edison Kosmos 的 Claude Code 驱动式社区仿制。renee-jia/scholar-loop（409★）自闭环 AI scientist。AgentRxiv 只找到 github.io 页面，独立代码仓未证实。SciPIP/Nova/VIRSCI 学术原型价值在论文不在代码。

### 2.2 深评

**SakanaAI/AI-Scientist-v2**（[repo](https://github.com/SakanaAI/AI-Scientist-v2)）。ideation 是独立脚本 `ai_scientist/perform_ideation_temp_free.py`，可脱离实验流水线单独跑，产 JSON——这正是 paper-muse 关心的唯一部分；其新颖性判定走 Semantic Scholar 工具调用（`S2_API_KEY`，README 明言没有 key 时「reduced novelty checking during ideation」）。参照系是「英文文献库里有没有」，与 paper-muse 的研究者参照系不同构。**重大警告**：2025-12 起 v1/v2 同步换成自定义「The AI Scientist Source Code License」（基于 Responsible AI Source Code License v1.1），含使用限制与「AI Scientist 条款」（机器生成的稿件必须显著披露）；允许衍生与分发但非 OSI 许可证——**只借鉴设计，不复制代码**。实验部分要 NVIDIA GPU + 沙箱执行 LLM 生成代码，与 paper-muse 无关。

**SamuelSchmidgall/AgentLaboratory**（[repo](https://github.com/SamuelSchmidgall/AgentLaboratory)）。文献综述→实验→写作三阶段流水线，MIT，原生支持 DeepSeek（o1/gpt-4o/deepseek-chat），有 copilot mode（人在环）。但无 pip 包、阶段耦合成一个 `ai_lab_repo.py`，文献检索源写死 arXiv 系；2025-08 后未再 push，热度在 AgentRxiv 论文后回落。价值：copilot mode 的「每阶段人审」交互设计与 paper-muse 卡片墙人审同路。

**HKUDS/AI-Researcher**（[repo](https://github.com/HKUDS/AI-Researcher)，NeurIPS 2025）。Level-1（给详细想法）/Level-2（只给参考文献，自动生成新想法）双档输入设计值得借鉴——paper-muse 的「主题+画像+困惑」相当于第三种更富的输入档。检索面广（arXiv/IEEE/ACM/GScholar/GitHub/HF）。但 docker 全家桶部署、**仓库无 LICENSE 文件**（默认保留所有权利，代码不可复用），且团队重心已迁向商业版 novix.science。只读架构不碰代码。

**Kaimen-Inc/Co-Scientist**（[repo](https://github.com/Kaimen-Inc/Co-Scientist)）——本赛道最可抽组件的项目。对 Google AI co-scientist（Gottweis et al., Nature 2026）的忠实开源复刻：Generation（文献+模拟辩论）→ Reflection（新颖性/正确性/可测性审查+假设深验证）→ Ranking（假设间模拟辩论的 **Elo 锦标赛**）→ Evolution（组合/简化/出格重构）→ Proximity（FAISS 嵌入聚类去重）→ Meta-review，Supervisor 用 SQLite 持久队列调度。附论文补充材料的全套伪代码与逐字 prompt。Apache-2.0、纯 Python、provider 全家桶（含 ollama/openai_compatible，DeepSeek 可走 openai_compatible）。stars 少（187）但工程完成度与文档质量高。paper-muse 的多模型合议目前是「并行出角度+离群标亮」，缺「角度之间怎么较量、怎么去重」——Elo 锦标赛与 Proximity 聚类正是现成答案。

**TrustResearcher**（[repo](https://github.com/valleysprings/TrustResearcher)，WWW 2026 demo）。五阶段：S2 检索+知识图谱构建→双路 idea 生成（直接生成+变体生成）+cross-pollination→两段式筛选（**对外部文献查新颖 + 对内部去重**）→多维评审（novelty/feasibility/impact/clarity）→阈值选优。MIT、2026-02 刚大改架构（「modular skill system」）。体量小但流程设计与 paper-muse 构思幕几乎逐段对应，是流程蓝本级参考。

**CoI-Agent（DAMO）**（[repo](https://github.com/DAMO-NLP-SG/CoI-Agent)）。Chain-of-Ideas：把检索到的文献组织成「发展链」再顺链生成 idea，配 Idea Arena 评估。Apache-2.0 可复用，但工程侧重（grobid+java+SciPDF 解析）与 2025-01 后停更使它更适合当论文读：其「文献按演化脉络组织再找下一步」的思路可揉进盲区扫描的「跨学科理论」方向生成。

**K-Dense-AI/scientific-agent-skills**（[repo](https://github.com/K-Dense-AI/scientific-agent-skills)）。2026 年现象级：148 个科学技能（含 Scientific Brainstorming、Hypothesis Generation、Peer Review、Paper Lookup——后者聚合 PubMed/arXiv/OpenAlex/Crossref/S2）、MIT、宣称 160k+ 科学家使用、30k★ 且日更。它不是引擎而是 Agent Skills 标准的技能包（`npx skills add`，装到 `~/.agents/skills/`）。对 paper-muse 的意义：a) 其 prompt 资产（brainstorming/peer-review skills 的方法论文本）MIT 可采；b) 验证了「科研能力做成可移植 skills 而非产品」的趋势，与用户现有 `.agents/skills` 白名单体系同构。

**赛道 verdict**：**自研正确**。三点理由：① 没有任何项目以「研究者画像+困惑」为新颖性参照系（§3）；② 头部项目（AI-Scientist 系）License 已转向限制性/无 License，代码复用面本就窄；③ 可借的是流程件而非引擎——Kaimen 的锦标赛/去重、TrustResearcher 的两段式查新、AI-Scientist-v2 的「novelty check 走检索工具调用」模式、HKUDS 的输入分级。盲区扫描的「四方向反应法+参照系」结构在开源界没有对应物。

---

## 3. 专节：「researcher-relative novelty」是否空白

**检索过程**：
- `gh search repos`：`personalized research idea`、`researcher profile idea generation`、`research ideation`、`hypothesis generation LLM`、`scientific novelty`、`novelty check paper`（2026-07-07）——无一项目做「相对研究者的新颖性」。
- Web 检索：`"personalized novelty" / "researcher-relative" / "relative to the researcher" scientific idea generation LLM`；逐一核对最近邻论文/项目。

**最近邻及其参照系**（均不是 researcher-relative）：
- [IdeaBench](https://arxiv.org/abs/2411.02429)：「personalized quality ranking」指评估时可自定义质量指标权重，参照系仍是文献与评审偏好，不是某个研究者。
- [Scideator](https://arxiv.org/abs/2409.14634)（CHI 系，人机协同 ideation）：novelty = 与文献 facet 重叠度，参照系是论文库。
- [Nova](https://arxiv.org/abs/2410.14255)（ACL 2025，[代码](https://github.com/hflyzju/Nova) 7★）：迭代规划检索外部知识提升 idea 新颖/多样性，参照系是检索到的知识本身。
- [freephdlabor](https://github.com/ltjed/freephdlabor)（[arXiv:2510.15624](https://arxiv.org/abs/2510.15624)，标题带「Personalized」的最强干扰项）：读摘要证实「personalized」= 用户可增删改 agent 团队构成（「users can modify, add, or remove agents」），不是研究者画像参照系，更无相对研究者的新颖性判定。
- ResearchAgent（NAACL 2025）：从核心论文+实体图谱出发生成 idea，参照系是学术图谱。
- VIRSCI（ACL 2025）：模拟「虚拟科学家团队」的 persona 是生成器不是参照系，且 persona 是虚构的、非真实用户。
- Google AI co-scientist（Nature 2026）及其复刻：Reflection 的 novelty 审查对文献判定；研究者只提供 research goal。
- FutureHouse Owl（前名 HasAnyone，[平台文档](https://futurehouse.gitbook.io/futurehouse-cookbook/futurehouse-client)）：「Has anyone done X before?」是文献先例查询——与盲区扫描新颖性判据同构，但参照系是「全体文献」，没有画像概念；且为云 API 非开源引擎。
- 个性化 agent 综述（[arXiv:2602.22680](https://arxiv.org/abs/2602.22680)）把 personalization 框定为记忆/偏好/长期交互，未触及「以研究者为参照系的新颖性」。

**结论**：在本次检索范围内（GitHub + 2024-2026 主要论文），**「researcher-relative novelty / 以研究者画像+困惑为参照系的新颖性判定」未找到任何已有实现，空白成立**。所有现有系统的 novelty 都是 literature-relative（相对文献库）或 corpus-relative（相对模型语料）。叠加中文法学/CNKI 维度（§4.3 证实国际索引覆盖近零），paper-muse 是双重空白上的定位。PRD 可引用本节。（诚实标注：检索以英文关键词为主，中文学界如有未开源的同类工作不在本次覆盖内。）

---

## 4. 赛道二：Deep research / 检索证伪引擎

### 4.1 候选对比表

| 项目 | stars | 最近 push | License | 语言 | 可嵌入性 | provider 可换 | 检索可插拔 |
|---|---|---|---|---|---|---|---|
| [bytedance/deer-flow](https://github.com/bytedance/deer-flow) | 76,275 | 2026-07-06 | MIT | Python | 中（有 DeerFlowClient 库嵌入，但 2.0 是全栈 harness） | 好（OpenAI 兼容/vLLM/Claude Code OAuth 等） | 好（Tavily/InfoQuest/MCP） |
| [assafelovic/gpt-researcher](https://github.com/assafelovic/gpt-researcher) | 28,115 | 2026-07-05 | Apache-2.0 | Python | **好**（pip `gpt-researcher`，async 两行调用） | 好（多 provider + 自定义 base_url） | **极好**（21 个 retriever：tavily/semantic_scholar/openalex/arxiv/pubmed_central/exa/searx/**custom**/**mcp**…） |
| [Alibaba-NLP/DeepResearch](https://github.com/Alibaba-NLP/DeepResearch)（通义） | 19,612 | 2026-02-27 | Apache-2.0 | Python | 差（自训 30B-A3B 模型 + 推理框架，模型中心） | 绑自家模型 | 部分 |
| [dzhng/deep-research](https://github.com/dzhng/deep-research) | 19,295 | 2026-04-11 | MIT | TypeScript | 差（TS，独立进程） | 中 | 中（Firecrawl） |
| [langchain-ai/open_deep_research](https://github.com/langchain-ai/open_deep_research) | 11,945 | 2026-06-26 | MIT | Python | 中（LangGraph graph，可 pip 装但拖 LangGraph 栈） | **好**（summarization/research/compression/final report 四角色分配模型，`init_chat_model` 任意 provider） | 好（Tavily 默认 + 原生搜索 + **MCP**） |
| [langchain-ai/local-deep-researcher](https://github.com/langchain-ai/local-deep-researcher) | 9,244 | 2026-06-28 | MIT | Python | 中 | 好（本地 Ollama 侧重） | 中 |
| [LearningCircuit/local-deep-research](https://github.com/LearningCircuit/local-deep-research) | 8,677 | 2026-07-06 | MIT | Python | 中（pip + docker，web 产品化） | 好（任意本地/云） | 好（SearXNG 等多引擎） |
| [MiroMindAI/MiroThinker](https://github.com/MiroMindAI/MiroThinker) | 8,331 | 2026-07-06 | Apache-2.0 | Python | 差（开源模型+agent 框架，模型中心） | 绑自家模型为主 | 部分 |
| [zilliztech/deep-searcher](https://github.com/zilliztech/deep-searcher) | 7,914 | 2025-11-19 | Apache-2.0 | Python | 中（私有数据侧重，Milvus 系） | 好 | 中 |
| [jina-ai/node-DeepResearch](https://github.com/jina-ai/node-DeepResearch) | 5,194 | 2026-05-01 | Apache-2.0 | TypeScript | 差（TS 服务） | 中 | 中（Jina 系） |
| [huggingface/smolagents](https://github.com/huggingface/smolagents)（open_deep_research 示例） | 28,219 | 2026-06-23 | Apache-2.0 | Python | 库好用，但 ODR 只是 examples/ 下的 GAIA demo | 好 | 自己写工具 |

一笔带过：khoj（35.5k★）与 openhuman（34.3k★）是个人助理/第二大脑，DocsGPT（18k★）是企业检索平台，均与证伪引擎不同赛道。nickscamara/open-deep-research（6.3k★）2025-05 后停更。u14app/deep-research（4.6k★）偏 UI 产品。

### 4.2 深评

**gpt-researcher**——本赛道对 paper-muse 唯一「即插组件」。pip 包内 `GPTResearcher(query=...)` 两个 await 出报告；planner/execution agents 架构（planner 出研究问题，执行 agent 并行抓证据，publisher 聚合）；retriever 目录里 21 个实现且含 `custom` 与 `mcp` 两个逃生门（`RETRIEVER=tavily,mcp` 风格多检索器混用），Semantic Scholar/OpenAlex/arXiv/PubMed 学术源已内置——把 opencli cnki 与 zsearch 包成 custom retriever/MCP server 即可进它的编排循环。Apache-2.0、周更活跃、生态最厚。风险：报告体裁偏「网页综述」，对抗幕要的是「证伪导向检索」，需自定义 prompt/report type（其 report_type 机制支持）。

**deer-flow 2.0**——最大惊奇：从 deep research 框架彻底转型为「long-horizon SuperAgent harness」（README：「DeerFlow 2.0 is a ground-up rewrite. It shares no code with v1」），lead agent 派生并行 sub-agent + 沙箱 + 记忆 + skills。MIT、76k★、字节日更。提供 `DeerFlowClient` 可「不起 HTTP 服务嵌入 Python」。但它是「万能干活 harness」，拖 LangGraph/LangChain 全栈，与 paper-muse「FastAPI 内嵌领域引擎」的形态相反——**观察其 skills/sandbox 设计，不采底座**。v1 的 coordinator/planner/researcher/reporter 工作流设计仍值得读。

**langchain-ai/open_deep_research**——LangGraph 官方深研图：supervisor-researcher 并行、四角色分别配模型（研究用贵的、压缩用便宜的——与 paper-muse 多模型分工同思路）、MCP 兼容、Deep Research Bench RACE 0.4344。MIT。若 paper-muse 未来引入 LangGraph 栈它是首选蓝本，但当前引擎无 LangGraph 依赖，故列为设计参考。其「按角色分模型」值得直接抄进对抗幕（证伪检索用便宜模型跑广度，审稿主张用强模型）。

**Tongyi DeepResearch / MiroThinker**——2025 下半年起 deep research 出现「模型派」：自训 30B-A3B（Apache-2.0 开权重）+ 配套推理框架，跑分导向（Humanity's Last Exam 等）。对 paper-muse 意义：若日后想要本地化深研（法学数据敏感场景），开权重深研模型是选项；当前 API 三模型路线下不采。

**jina-ai/node-DeepResearch**——「search→read→reason 直到预算耗尽」的 token 预算驱动循环，是「证伪检索循环」的最干净参考设计（虽为 TS 不嵌入）。paper-muse 对抗幕若自写循环，预算终止条件设计可借它。

**赛道 verdict**：**抽组件集成**——对抗幕证据检索直接嵌 gpt-researcher（pip 库 + custom retriever 挂 CNKI/zsearch），不自写检索编排；deep research 底座/harness 一概不采（形态冲突）；open_deep_research 的按角色配模型 + node-DeepResearch 的预算循环作为设计输入。

---

## 5. 赛道三：学术文献检索 / QA / novelty 核查

### 5.1 候选对比表

| 项目 | stars | 最近 push | License | 语言 | 可嵌入性 | provider 可换 | 检索可插拔 |
|---|---|---|---|---|---|---|---|
| [Future-House/paper-qa](https://github.com/Future-House/paper-qa)（PaperQA2） | 8,822 | 2026-06-29 | Apache-2.0 | Python | **好**（pip `paper-qa>=5`，`Settings`+`ask` 即用） | **好**（LiteLLM 全兼容：DeepSeek/Gemini/Ollama 均可） | 中（本地 PDF 目录 + S2/Crossref/Unpaywall 元数据；CNKI 未提及） |
| FutureHouse 平台（Owl/Crow/Falcon；futurehouse-client） | GitHub 仓已 404；PyPI `futurehouse-client` 0.7.1 在维护 | — | 客户端闭/云服务 | Python 客户端 | 云 API（`job-futurehouse-hasanyone`） | 否（云端模型） | 否 |
| [allenai/ai2-scholarqa-lib](https://github.com/allenai/ai2-scholarqa-lib) | 279 | 2026-06-25 | Apache-2.0 | Python | 好（pip `ai2-scholar-qa` 或 docker 全栈） | 好（LiteLLM 任意） | 中（S2 snippet/search API 为主，`retrieval_service` 可换实现） |
| [AkariAsai/OpenScholar](https://github.com/AkariAsai/OpenScholar) | 1,557 | 2025-08-13 | Apache-2.0 | Python | 差（45M 论文 datastore + 自训 8B 模型，重基建） | 中 | 差（绑 peS2o/S2） |
| [bytedance/pasa](https://github.com/bytedance/pasa)（ACL 2025） | 1,617 | 2025-05-27 | Apache-2.0 | Python | 差（RL 训练的 7B crawler+selector 双模型） | 绑自家 7B（或 prompt 版 GPT-4o） | 差（英文 AI 会议域） |
| [danielnsilva/semanticscholar](https://github.com/danielnsilva/semanticscholar) | 471 | 2026-07-03 | MIT | Python | 好（纯 API 客户端） | n/a | n/a |
| [J535D165/pyalex](https://github.com/J535D165/pyalex) | 396 | 2026-07-06 | MIT | Python | 好（纯 API 客户端） | n/a | n/a |
| [scholarly-python-package/scholarly](https://github.com/scholarly-python-package/scholarly) | 1,873 | 2026-03-24 | Unlicense | Python | 好但脆（Google Scholar 爬虫，封禁风险） | n/a | n/a |

一笔带过：allenai/s2-folks（278★）是 S2 API 社区仓；ScholarXIV（1.1k★）是 arXiv 阅读 app；OpenScholarXIV 同类。Ai2 的 asta 系未搜到独立开源仓（未证实）。

### 5.2 深评

**paper-qa (PaperQA2)**——own_hits 层的现成底座。pip 库、Python 3.11+、代理式三阶段（Paper Search→Gather Evidence→Generate Answer，工具可乱序迭代调用）；LiteLLM 使 DeepSeek/OpenAI/Gemini 三模型即换；对 `paper_directory` 本地 PDF（=Zotero 库导出）建证据索引并出带引注回答，元数据自动挂 Semantic Scholar/Crossref/Unpaywall。**CNKI/中文支持文档未提及**——用它管英文+自有 PDF，中文仍走 opencli cnki。FutureHouse 系工程质量高（配套 aviary/ldp 框架、LitQA 基准），Apache-2.0。风险：v5 大版本 API 迭代快，pin 版本。

**FutureHouse 平台 / Owl（前名 HasAnyone）**。「Has anyone done X before?」先例查询与盲区扫描新颖性判据直接同构——但它是**云 API**（`futurehouse-client`，job 名 `job-futurehouse-hasanyone`），非开源引擎；且公司结构 2025-11 变动：FutureHouse（非营利）分拆出 Edison Scientific（商业化，[公告](https://www.futurehouse.org/research-announcements/announcing-edison-scientific)，融资 $70M，产品 Kosmos），PyPI 同时出现 `edison-client`，GitHub 上 `futurehouse-client` 仓已 404——**平台连续性有风险，不做依赖，只借问式**。第三方评测（[poltextlab](https://promptrevolution.poltextlab.com/assessing-the-futurehouse-owl-agents-ability-to-detect-defined-concepts-in-academic-research/)）指 Owl 在「概念是否已被提出」类任务上会漏检/错溯源——印证「先例查询」这件事本身仍是开放难题，paper-muse 三角定位（en/zh/own 三路交叉）是更稳的结构。

**ai2-scholarqa-lib**——检索→重排→**Quote Extraction→Planning and Clustering→Summary Generation** 的三步生成管线是「引证先行」设计的最好公开实现（先抽原文引文再组织成有出处的综述），pip 可嵌、LiteLLM 任意模型、`retrieval_service` 声明可换检索实现（默认 S2 snippet API）。对 paper-muse 卡片深挖/对抗幕证据呈现，「先抽 quote 再聚类成观点」的管线值得整段借鉴，检索层换成 CNKI/zsearch。

**OpenScholar / PaSa**——两条「重资产」路线：OpenScholar 是 45M 论文 datastore+自训检索器+8B 模型的自建索引路线；PaSa 是 RL 训练 7B crawler（自主扩引文、调搜索）+7B selector 的搜索代理路线，实测远超 Google Scholar 基线（RealScholarQuery recall@20 +37.78%）。都不适合嵌入（GPU/基建重、英文域），但 PaSa 的「crawler 扩引文队列 + selector 逐篇判据」双角色设计可以 prompt 版复刻进 en_hits 深检索。

**API 客户端层**：`semanticscholar`（MIT，活跃）与 `pyalex`（MIT，活跃）即装即用，直接做 en_hits 的两条腿；`scholarly` 是 Google Scholar 爬虫（Unlicense，封 IP 风险高），只做最后兜底、不进主路径。

### 5.3 中文文献（CNKI）覆盖专项核查——paper-muse 差异化的硬证据

- **OpenAlex**：[arXiv:2512.16339](https://arxiv.org/abs/2512.16339)（Beyond openness: Inclusiveness and usability of Chinese scholarly data in OpenAlex）：OpenAlex 仅索引 **37% 的中文核心期刊、24% 的文章**。[arXiv:2507.19302](https://arxiv.org/abs/2507.19302)（Understanding discrepancies in the coverage of OpenAlex: the case of China）：来自 CNKI 的采集份额从 2003-2011 年的 60-70% 跌到 2014 年 34.7%，**2016 年后近零**；抽样 100 篇被标为英文的 CNKI 论文，99% 实为中文（语言元数据大面积错标）。
- **Semantic Scholar**：未找到官方对 CNKI 收录的说明（未证实；其语料以英文/国际出版社为主，预期覆盖极低）。
- **各深研/文献 QA 项目**：paper-qa、ai2-scholarqa-lib、OpenScholar、PaSa 的文档均未提及 CNKI 或中文检索路径。
- **结论**：「中文法学 + CNKI」检索面在整个开源生态里没有可借用的现成件，paper-muse 经浏览器会话走 opencli cnki 的路线虽脆但**独占**；这是 PRD 里应明写的护城河，也是 zh_hits 必须自研的原因。

**赛道 verdict**：paper-qa **采用为底座**（own_hits/自有语料证据层）；semanticscholar+pyalex **采用为客户端**（en_hits）；ai2-scholarqa-lib 的 quote-first 管线与 Owl 的「Has anyone」问式**借鉴设计**；zh_hits 与三角定位逻辑**自研正确**。

---

## 6. 赛道四：自动审稿 / 对抗性审查

### 6.1 候选对比表

| 项目 | stars | 最近 push | License | 语言 | 可嵌入性 | provider 可换 | 定位 |
|---|---|---|---|---|---|---|---|
| [ResearAI/DeepReviewer-v2](https://github.com/ResearAI/DeepReviewer-v2) | 497 | 2026-04-28 | MIT | Python | 中 | 开权重模型系 | 多视角审稿模拟 v2（2026-03 开源，配免费平台 deepscientist.cc） |
| [zhu-minjun/Researcher](https://github.com/zhu-minjun/Researcher)（CycleResearcher/CycleReviewer，ICLR 2025） | 396 | 2026-03-05 | ⚠️ 自定义（基于 Mistral AI Research License，需注册） | Python | 中（pip `ai_researcher`） | 开权重 8B/14B/70B/123B，需本地推理 | 研究-审稿闭环 |
| [Ahren09/AgentReview](https://github.com/Ahren09/AgentReview)（EMNLP 2024 oral） | 118 | 2026-05-10 | Apache-2.0 | Python/Jupyter | 中 | OpenAI/AzureOpenAI | 评审过程**社会学模拟**（非实用审稿器） |
| [allenai/marg-reviewer](https://github.com/allenai/marg-reviewer)（MARG） | 63 | 2026-03-05 | Apache-2.0 | Python | 差（docker demo） | OpenAI | 多 agent 专化审稿生成 |
| AI-Scientist v1/v2 内置 reviewer | （随主仓） | 2025-12-19 | ⚠️ 自定义 License | Python | 中（模块相对独立） | OpenAI 系 | NeurIPS 表单式 rubric 审稿 |
| [AliManjotho/open-reviewer](https://github.com/AliManjotho/open-reviewer) | 29 | 2026-03-30 | （小项目） | Python | 未评 | 未评 | 证据驱动多 agent 审稿（2026 新） |
| [maxidl/openreviewer](https://github.com/maxidl/openreviewer) | 11 | 2025-06-21 | 无 | Python | 差 | 本地 Llama 系 | 审稿生成（停滞） |
| [makemebitter/ideaforge](https://github.com/makemebitter/ideaforge) | 2 | 2026-03-20 | MIT | Python | 未评 | 未评 | 对抗辩论生成 idea + 50K 真实评审训练的校准判官 |

一笔带过：Lin-TzuLing/Breaking-the-Reviewer（2025）证明 LLM 审稿系统可被论文内注入攻击操纵——对抗幕要把它当红线测试用例。PKU-ONELab/where-do-llms-go-wrong 提供审稿系统的扰动诊断 CLI。songxxzp/OpenReviewers（23★）是评审模拟。WestlakeNLP 名下无 CycleReviewer 仓（实际在 zhu-minjun/Researcher，已核实）。

### 6.2 深评

**MARG（allenai/marg-reviewer）**。多 agent 审稿生成的架构原型：把超上下文长度的论文分块派给多个 worker agent，leader 协调、专化 agent（实验/清晰度/影响力等分工）出具体可执行意见，实验显示专化版（MARG-S）把「泛泛而谈」意见率显著压低。Apache-2.0，代码是 docker demo 形态（研究复现包），**抄结构不抄代码**：paper-muse 对抗幕的「7 文件契约里 failure-points.md 按主张分块、每主张一个专化红队 agent」可直接映射 MARG 的 leader-worker-specialist 拓扑。

**AgentReview**。定位是评审过程模拟器（reviewer/author/AC 三角色五阶段：独立评审→作者答辩→reviewer-AC 讨论→meta-review→决议），产出是研究发现（如 37.1% 决议波动源于 reviewer 偏见、社会影响理论效应）而非实用审稿工具。Apache-2.0。对 paper-muse 的价值在**角色化+阶段化的对抗剧场设计**：对抗幕若做「多 agent 红队攻击草稿主张→作者代理答辩→仲裁人汇总」的回合制，AgentReview 的阶段机是现成脚本。

**CycleReviewer / DeepReviewer（zhu-minjun/Researcher + ResearAI/DeepReviewer-v2）**。唯一「开权重审稿专用模型」谱系：CycleReviewer 8B/70B/123B 输出结构化评分+决议，DeepReviewer 14B 做多审稿人模拟（Fast/Standard/Best 三档、可设 reviewer_num）+自校验，pip `ai_researcher` 一行加载。**警告**：主仓 License 是 Mistral AI Research License 变体（研究用途+需注册提交用户信息）；但 **DeepReviewer-v2 独立仓是 MIT**（2026-03 开源，497★）。需本地 GPU/vLLM 推理，与 paper-muse 当前纯 API 架构不合——列为「后期可选的第四个合议声部」（离线审稿人），不进 MVP。

**AI-Scientist 内置 reviewer**。用 GPT-4o 按 NeurIPS 评审表单打分+自反思多轮，是「rubric 驱动审稿」最被广泛复现的 prompt 设计；v1 论文声称其 reviewer 在 ICLR 拒稿判别上接近人类基线（自报数据）。License 已换自定义条款，**prompt 设计自己重写实现，不复制文件**。

**赛道 verdict**：**自研 orchestration + 抽设计**。理由：① 全部候选面向英文 ML 会议评审体裁，无中文法学、无「围绕草稿中心主张产 failure-points.md」的产物契约；② 最强工程件（CycleReviewer 系）要 GPU 且 License 受限；③ 但三块设计可整段搬：MARG 的专化分工、AgentReview 的回合制角色剧场、AI-Scientist 的 rubric+自反思循环；证据侧则接赛道二的 gpt-researcher 证伪检索。另把 Breaking-the-Reviewer 的注入攻击设为对抗幕的鲁棒性验收用例（防「草稿里藏 prompt 操纵红队放水」）。

---

## 7. 对 PRD 的具体建议清单（可执行粒度）

1. **对抗幕·证据检索**：嵌 `gpt-researcher`（pip，Apache-2.0）为证伪检索编排器；为 opencli cnki 与 zsearch 各写一个其 `custom` retriever 适配器（或包成 MCP server 走其 `mcp` retriever），配 `RETRIEVER=tavily,custom,mcp` 多源混跑；自定义 report_type 为「证伪备忘录」而非综述。
2. **own_hits/卡片锚点**：引入 `paper-qa>=5`（pin 版本）对 Zotero 导出的 PDF 目录建索引；`Settings` 里 LLM 走 LiteLLM 配 DeepSeek/Gemini；把 `ask()` 的带引注回答映射进 sources.md 契约字段。
3. **en_hits 客户端**：`semanticscholar`（MIT）+ `pyalex`（MIT）双路并查（S2 会限流，OpenAlex 免 key），`scholarly` 不进主路径。novelty 查询语句统一规范成 Owl 式「Has anyone …?」正则化问式（只借问式，不依赖 FutureHouse 云 API——其公司结构 2025-11 已变动）。
4. **多模型合议升级**：从 Kaimen-Inc/Co-Scientist（Apache-2.0）vendor 两个模块的实现思路：a) Proximity（嵌入聚类去重，防三模型出同质角度）；b) Ranking 的成对辩论 Elo（把「离群标亮」从启发式升级为锦标赛淘汰赛，离群=Elo 高且簇内孤立）。其 per-agent prompt（来自 Nature 论文补充材料，仓内逐字收录）可作合议 prompt 底稿。
5. **盲区扫描流程件**：借 TrustResearcher 的「两段式选优」（先对外部文献查新颖、再内部去重+多样性选择）作为卡片墙出卡前的过滤层；借 AI-Scientist-v2 的「novelty check 作为工具调用+无 key 降级」模式实现三角定位的 en_hits 探针；借 HKUDS 的 Level-1/Level-2 输入分级思想定义「只给主题」vs「主题+困惑」两档扫描深度。
6. **对抗幕·审稿 orchestration**：自研回合机，结构映射：MARG 的 leader-specialist 分工（每个中心主张→一个专化红队 agent）+ AgentReview 的五阶段角色剧场（红队评审→作者代理答辩→仲裁 meta-review）+ AI-Scientist 的 rubric+自反思（rubric 换成法学论文评审维度，prompt 重写不复制）。产物落 failure-points.md 契约。
7. **鲁棒性验收**：把 Breaking-the-Reviewer 式「草稿内注入操纵指令」做成对抗幕冒烟测试用例（红队不得被草稿文本收买）。
8. **License 红线表**（写进 PRD 附录）：可复制代码——gpt-researcher/paper-qa/ai2-scholarqa-lib/Kaimen/MARG/AgentReview/storm/semanticscholar/pyalex/K-Dense skills（Apache/MIT）；只可看不可抄——AI-Scientist v1/v2（自定义 RAIL 系）、CycleResearcher 主仓（研究许可+注册）、HKUDS/AI-Researcher、IRIS、ResearchAgent、Nova（无 LICENSE=保留所有权利）；标注例外——DeepReviewer-v2 独立仓为 MIT。
9. **圆桌（Co-STORM）**：上游 stanford-oval/storm 最近 push 2025-09-30、纯 STORM 时代 API 稳定，PRD 按「自养 fork」定预期：不等上游修 bug，安全补丁自己打。
10. **prompt 资产采购**：从 K-Dense scientific-agent-skills（MIT）摘 Hypothesis Generation / Peer Review / Scientific Brainstorming 三个 skill 的方法论文本，改写为中文法学语境后并入扫描与对抗 prompt 库（与用户现有 `.agents/skills` 白名单机制同构，复用成本低）。
11. **PRD 引用空白结论**：§3（researcher-relative novelty 空白）与 §5.3（CNKI 覆盖近零）两节可直接作为差异化论证引用，注明「截至 2026-07-07 检索」。
12. **不做清单**：不采任何 deep research harness 为底座（deer-flow/EvoScientist/smolagents 形态冲突）；不自训/自部署审稿模型进 MVP（CycleReviewer 系 GPU+License 成本）；不依赖 FutureHouse/Edison 云 API 做新颖性判据（连续性风险+第三方评测可靠性存疑）。

---

## 8. 来源清单

**GitHub 仓库**（元数据 = `gh api repos/<owner>/<repo>`，2026-07-07 实查）：
- https://github.com/SakanaAI/AI-Scientist ・ https://github.com/SakanaAI/AI-Scientist-v2 （License 全文经 `gh api …/license` 核读）
- https://github.com/SamuelSchmidgall/AgentLaboratory ・ https://github.com/HKUDS/AI-Researcher ・ https://github.com/DAMO-NLP-SG/CoI-Agent ・ https://github.com/JinheonBaek/ResearchAgent ・ https://github.com/cheerss/SciPIP ・ https://github.com/hflyzju/Nova ・ https://github.com/RenqiChen/Virtual-Scientists ・ https://github.com/valleysprings/TrustResearcher ・ https://github.com/Anikethh/IRIS-Interactive-Research-Ideation-System ・ https://github.com/Kaimen-Inc/Co-Scientist ・ https://github.com/EvoScientist/EvoScientist ・ https://github.com/K-Dense-AI/scientific-agent-skills ・ https://github.com/ltjed/freephdlabor ・ https://github.com/mims-harvard/ToolUniverse ・ https://github.com/jimmc414/Kosmos ・ https://github.com/conradry/open-coscientist-agents（archived）
- https://github.com/assafelovic/gpt-researcher（retriever 清单经 `gh api …/contents/gpt_researcher/retrievers` 核实）・ https://github.com/bytedance/deer-flow ・ https://github.com/langchain-ai/open_deep_research ・ https://github.com/langchain-ai/local-deep-researcher ・ https://github.com/LearningCircuit/local-deep-research ・ https://github.com/Alibaba-NLP/DeepResearch ・ https://github.com/MiroMindAI/MiroThinker ・ https://github.com/zilliztech/deep-searcher ・ https://github.com/jina-ai/node-DeepResearch ・ https://github.com/dzhng/deep-research ・ https://github.com/huggingface/smolagents ・ https://github.com/nickscamara/open-deep-research
- https://github.com/Future-House/paper-qa ・ https://github.com/allenai/ai2-scholarqa-lib ・ https://github.com/AkariAsai/OpenScholar ・ https://github.com/bytedance/pasa ・ https://github.com/danielnsilva/semanticscholar ・ https://github.com/J535D165/pyalex ・ https://github.com/scholarly-python-package/scholarly ・ https://github.com/allenai/s2-folks
- https://github.com/Ahren09/AgentReview ・ https://github.com/allenai/marg-reviewer ・ https://github.com/zhu-minjun/Researcher（LICENSE 全文核读）・ https://github.com/ResearAI/DeepReviewer-v2 ・ https://github.com/maxidl/openreviewer ・ https://github.com/AliManjotho/open-reviewer ・ https://github.com/makemebitter/ideaforge ・ https://github.com/Lin-TzuLing/Breaking-the-Reviewer ・ https://github.com/stanford-oval/storm

**论文/文档/平台**：
- Nova: https://arxiv.org/abs/2410.14255 ・ IdeaBench: https://arxiv.org/abs/2411.02429 ・ Scideator: https://arxiv.org/abs/2409.14634 ・ freephdlabor 论文: https://arxiv.org/abs/2510.15624 ・ 个性化 LLM agent 综述: https://arxiv.org/abs/2602.22680 ・ 创造力综述: https://arxiv.org/abs/2511.07448 ・ 新颖性评估算法: https://arxiv.org/abs/2503.01508
- OpenAlex 中文覆盖: https://arxiv.org/abs/2512.16339 ・ https://arxiv.org/abs/2507.19302
- FutureHouse 平台/Owl: https://futurehouse.gitbook.io/futurehouse-cookbook/futurehouse-client ・ Edison 分拆公告: https://www.futurehouse.org/research-announcements/announcing-edison-scientific ・ PyPI: https://pypi.org/project/futurehouse-client/ 、 https://pypi.org/project/edison-client/ ・ Owl 第三方评测: https://promptrevolution.poltextlab.com/assessing-the-futurehouse-owl-agents-ability-to-detect-defined-concepts-in-academic-research/
- Google AI co-scientist（Nature 2026，经 Kaimen 仓 README 引注）: https://www.nature.com/articles/s41586-026-10644-y

**未证实事项**（诚实清单）：Semantic Scholar 对 CNKI 的官方覆盖数据；Ai2 asta 系是否有独立开源仓；AgentRxiv 独立代码仓；AI-Scientist reviewer 接近人类基线为其自报数据；LearningCircuit/local-deep-research 的 ~95% SimpleQA 为自报基准。
