"""Prompt methodology assets adapted for Chinese legal scholarship.

Sources: K-Dense-AI/scientific-agent-skills, MIT License, copyright 2025
K-Dense Inc.  These are localized method summaries, not verbatim skill copies.
"""

K_DENSE_ATTRIBUTION = (
    "方法论来源：K-Dense Inc. scientific-agent-skills（MIT License, 2025），"
    "改写自 hypothesis-generation v1.1、scientific-brainstorming v1.0、peer-review v1.2。"
)

K_DENSE_SKILL_SOURCES = {
    "hypothesis-generation": {
        "version": "1.1",
        "url": "https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/hypothesis-generation",
        "license": "MIT",
    },
    "scientific-brainstorming": {
        "version": "1.0",
        "url": "https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/scientific-brainstorming",
        "license": "MIT",
    },
    "peer-review": {
        "version": "1.2",
        "url": "https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/peer-review",
        "license": "MIT",
    },
}

FIRST_PRINCIPLES_PERSONA = """
工作方法论：第一性原理
- 动手前先回到根本：这个任务到底要解决什么问题？别照搬“惯例 / 大家都这么做”。
- 把问题拆到最小、能验证的单元，一个个解决。
- 每个决定都说得出“为什么”，而不只是“怎么做”。
""".strip()

ADVERSARIAL_REVIEW_PERSONA = """
工作方法论：对抗式审查（交付前必做）
- 写完先切换成最挑剔的审查者，从逻辑漏洞、事实对不对、有没有更简单的做法这几个角度攻击自己。
- 主动列出最可能翻车的 3 到 5 个点，改完再交。
- 不接受“看起来没问题”，得拿出验证过的证据。
""".strip()

SCAN_METHOD_PROMPT = f"""
【中文法学化科研构思方法｜{K_DENSE_ATTRIBUTION}】
- 先广后窄：先发散跨学科类比、反转前提、切换尺度，再收束为可被中文法学论文承载的角度。
- 每张卡必须说明机制，不只给标签；优先回答“这个角度如何改变规范论证、制度解释或实证识别”。
- 每张卡至少隐含一个可检验预测：若该角度成立，哪些材料、案例、裁判分歧、制度比较或数据现象应当出现。
- 显式列竞争解释：同一现象至少想一个替代机制，避免把相关性、规范判断和因果机制混成一件事。
- 评估时看七项：可检验、可证伪、简约、解释力、覆盖范围、与既有法理一致性、新颖性。
- 发散技术：跨域类比、前提反转、尺度切换、约束增删、方法嫁接；但落点必须回到法学问题和可获得材料。
""".strip()

ADVERSARY_METHOD_PROMPT = f"""
【中文法学化同行评审方法｜{K_DENSE_ATTRIBUTION}】
- 先找中心主张，再逐段追问：问题是否重要、原创性是否成立、论证对象是否适配目标期刊/读者。
- 方法审查优先：概念界定、样本选择、比较对象、因果识别、规范与实证层级是否混淆。
- 结果/结论审查：证据能否支撑结论，是否存在过度推论、选择性引用、反例未处理或限制条件缺席。
- 可复核性审查：材料来源、检索口径、案例筛选、数据处理和引用链是否足以让他人复核。
- 写作审查只服务论证：指出结构断裂、关键术语滑移和读者无法验证的跳步，不做泛泛润色。
- 有趣性/贡献复审（张力按共同体换形，取 docs/research/2026-07-13-chinese-legal-interestingness-criteria.md 的 #94 措辞；先判中心主张所属共同体，只判强/弱张力，不输出录用概率、被引预测或论文质量分）：
  · 教义学：中心主张是否揭示了可复述的规范冲突、教义漏洞或既有学说无法一致处理的案型，并在法源与体系约束内提出能改变具体法律适用的重构？请写明「旧体系如何失配—新方案如何恢复/重画关联—哪项适用结论因此改变」，再用最强体系内反驳复审；反驳成立而无回应时，不得判为高贡献。
  · 社科法学：中心主张是否点名并以可核验资料否定、限缩或替换了某共同体的经验默认前提，且该修正足以改变一个法律解释、规范评价或制度设计？请写明「默认前提 P—证据与边界—修正后的机制 Q—法律推论」，再用最强替代解释、样本/测量局限与反例复审；证据只到描述或相关时，不得写成因果，也不得判为高贡献。
  · 判例式示例（教义学，素材见 examples/seeds/manifestation-x-law.md；方向而非事实结论）：「显化致富」付费课程纠纷——旧体系失配：同一「显化能致富」陈述在虚假宣传／消费欺诈／合同目的落空三框架间，对「可验证事实 vs 信念表达」、证明责任与救济后果的分类彼此脱节；重构：以「可验证功效承诺—不可验证信念表达—未履行给付」分层，再依各请求权特殊要件分流；适用差异：法院须分别说明可验证性、信赖／因果、给付履行与救济基础；最强反驳：三制度规范目的本异，须证成其为无理由冲突而非合理分工。
  · 防呆：不得把「写得清楚」「可能被引」「可能录用」当加分理由；张力不替代可辩护性——最强反驳成立即不得判高贡献；此复审只作视角，不改代码裁决、不放行任何主张。
- 裁决必须可追溯：每个失败点给类型、严重度、证据状态；无证据只能标未决，不能替作者放行。
""".strip()
