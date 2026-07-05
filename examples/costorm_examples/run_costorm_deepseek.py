"""
论文构思者 · 互动圆桌（Co-STORM × DeepSeek）

多位 LLM 专家 + 主持人围绕你的主题做圆桌讨论；主持人专门提出
「检索到但还没人讨论过」的问题，你随时插话转向。适合论文构思期
勘探视角，或写作卡壳时找角度。

需要的 key（secrets.toml 或环境变量，与批量版完全相同）：
    DEEPSEEK_API_KEY / DEEPSEEK_API_BASE
    TAVILY_API_KEY
    ENCODER_API_TYPE / ENCODER_MODEL / ENCODER_API_KEY / ENCODER_API_BASE
    PERPLEXITY_API_KEY / JINA_API_KEY（--retriever perplexity/mixed 或 --fulltext 时需要，可选）

输出（默认落在 $PAPER_MUSE_OUTPUT_DIR 或 ./results 下的 costorm_<主题>/）：
    report.md            # 圆桌成果报告（带 [1][2] 引用）
    conversation.md      # 完整对话记录（供其他 agent / 写作技能读取）
    instance_dump.json   # 会话快照（含思维导图，可恢复）
    log.json             # 调用日志
"""

import json
import os
import re
import sys
from argparse import ArgumentParser

# 本仓库未 pip 安装 knowledge_storm，从脚本位置定位仓库根目录
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from knowledge_storm.collaborative_storm.engine import (
    CollaborativeStormLMConfigs,
    RunnerArgument,
    CoStormRunner,
)
from knowledge_storm.collaborative_storm.modules.callback import (
    LocalConsolePrintCallBackHandler,
)
from knowledge_storm.lm import DeepSeekModel
from knowledge_storm.logging_wrapper import LoggingWrapper
from knowledge_storm.rm import (
    TavilySearchRM,
    PerplexitySearchRM,
    JinaFullTextRM,
    MixedRM,
)
from knowledge_storm.utils import load_api_key


def sanitize_topic(topic):
    topic = re.sub(r"[^\w-]", "_", topic.strip()).strip("_")
    return topic or "unnamed_topic"


def save_results(runner, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    print("\n正在整理思维导图并生成报告…")
    runner.knowledge_base.reorganize()
    article = runner.generate_report()
    with open(os.path.join(output_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(article)

    with open(os.path.join(output_dir, "conversation.md"), "w", encoding="utf-8") as f:
        f.write(f"# 圆桌讨论记录：{runner.runner_argument.topic}\n\n")
        for turn in runner.conversation_history:
            f.write(f"**{turn.role}**: {turn.utterance}\n\n")

    with open(os.path.join(output_dir, "instance_dump.json"), "w", encoding="utf-8") as f:
        json.dump(runner.to_dict(), f, indent=2, ensure_ascii=False)

    with open(os.path.join(output_dir, "log.json"), "w", encoding="utf-8") as f:
        json.dump(runner.dump_logging_and_reset(), f, indent=2, ensure_ascii=False)

    print(f"已保存：{output_dir}/report.md（报告）、conversation.md（对话记录）")


def main(args):
    load_api_key(toml_file_path="secrets.toml")
    for var in ("DEEPSEEK_API_KEY", "TAVILY_API_KEY", "ENCODER_API_TYPE"):
        if not os.getenv(var):
            sys.exit(f"缺少 {var}（请填在 secrets.toml 或环境变量里）")

    deepseek_kwargs = {
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
        "api_base": os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
        "temperature": 1.0,
        "top_p": 0.9,
    }

    def ds(max_tokens):
        return DeepSeekModel(model=args.model, max_tokens=max_tokens, **deepseek_kwargs)

    lm_config = CollaborativeStormLMConfigs()
    lm_config.set_question_answering_lm(ds(1000))
    lm_config.set_discourse_manage_lm(ds(500))
    lm_config.set_utterance_polishing_lm(ds(2000))
    lm_config.set_warmstart_outline_gen_lm(ds(500))
    lm_config.set_question_asking_lm(ds(300))
    lm_config.set_knowledge_base_lm(ds(1000))

    topic = args.topic or input("讨论主题: ").strip()
    if not topic:
        sys.exit("主题不能为空")

    base_dir = args.output_dir or os.environ.get("PAPER_MUSE_OUTPUT_DIR") or "./results"
    output_dir = os.path.join(base_dir, f"costorm_{sanitize_topic(topic)}")

    runner_argument = RunnerArgument(
        topic=topic,
        retrieve_top_k=args.retrieve_top_k,
        warmstart_max_num_experts=args.warmstart_experts,
        warmstart_max_turn_per_experts=args.warmstart_turns,
        # ponytail: 线程压到保守值防 DeepSeek 限流，批量版同款取舍
        max_search_thread=3,
        warmstart_max_thread=3,
        max_thread_num=5,
    )
    def _tavily():
        return TavilySearchRM(
            tavily_search_api_key=os.getenv("TAVILY_API_KEY"),
            k=runner_argument.retrieve_top_k,
            include_raw_content=True,
        )

    if args.retriever == "perplexity":
        rm = PerplexitySearchRM(k=runner_argument.retrieve_top_k)
    elif args.retriever == "mixed":
        rm = MixedRM([_tavily(), PerplexitySearchRM(k=runner_argument.retrieve_top_k)])
    else:
        rm = _tavily()
    if args.fulltext:
        rm = JinaFullTextRM(base_rm=rm, top_n=3)
    runner = CoStormRunner(
        lm_config=lm_config,
        runner_argument=runner_argument,
        logging_wrapper=LoggingWrapper(lm_config),
        rm=rm,
        callback_handler=LocalConsolePrintCallBackHandler(),
    )

    print(f"\n=== 热身中：检索「{topic}」并组建专家圆桌（约 1-3 分钟）… ===\n")
    runner.warm_start()
    # 引擎会吞掉热身阶段异常（只打印不重抛），空对话说明热身实际失败了
    if not runner.conversation_history:
        sys.exit("热身失败（对话为空），请检查上方报错后重试。")

    print("\n=== 热身完成，以下是圆桌开场 ===\n")
    for turn in runner.conversation_history:
        print(f"【{turn.role}】{turn.utterance}\n")

    print("=== 互动开始：回车＝听下一位发言｜输入文字＝插话转向｜q＝结束并出报告 ===\n")
    try:
        while True:
            try:
                user_in = input("你 > ").strip()
            except EOFError:
                break
            if user_in.lower() in {"q", "quit", "exit"}:
                break
            if user_in:
                runner.step(user_utterance=user_in)
            turn = runner.step()
            print(f"\n【{turn.role}】{turn.utterance}\n")
    except KeyboardInterrupt:
        print("\n（已中断，正在保存…）")

    save_results(runner, output_dir)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--topic", type=str, default=None, help="讨论主题（不传则交互式询问）")
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="输出目录（默认 $PAPER_MUSE_OUTPUT_DIR 或 ./results）",
    )
    parser.add_argument(
        "--model",
        type=str,
        choices=["deepseek-v4-flash", "deepseek-v4-pro"],
        default="deepseek-v4-flash",
        help="互动场景默认用快模型 v4-flash；要更深的问题可换 v4-pro（慢）",
    )
    parser.add_argument("--retrieve-top-k", type=int, default=5, help="每次检索取前 k 条")
    parser.add_argument(
        "--retriever",
        type=str,
        choices=["tavily", "perplexity", "mixed"],
        default="tavily",
        help="检索源：tavily 快 / perplexity 深 / mixed 双源混合",
    )
    parser.add_argument(
        "--fulltext",
        action="store_true",
        help="用 Jina Reader 把 top3 结果增强为全文（需 JINA_API_KEY）",
    )
    parser.add_argument("--warmstart-experts", type=int, default=2, help="热身阶段专家数")
    parser.add_argument("--warmstart-turns", type=int, default=1, help="热身阶段每专家轮数")
    main(parser.parse_args())
