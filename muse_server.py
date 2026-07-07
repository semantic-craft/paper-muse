"""
论文构思者本地 API 服务：把 Co-STORM 圆桌包成 HTTP，给 PaperMuse.app（SwiftUI 壳）用。

    .venv/bin/python muse_server.py [--port 8765]

单会话设计（个人工具，一次一个圆桌）。所有 runner 调用用一把锁串行化。

接口：
    GET  /health   → {"ok": true}
    POST /session  {topic, model?, retrieve_top_k?, warmstart_experts?, warmstart_turns?, output_dir?}
                   立即返回；热身在后台线程跑，进度看 /status
    GET  /status   → {phase: idle|warming|ready|stepping|error, progress: [...], turns: [...], topic, output_dir, error?}
    POST /step     {utterance?: ""} 空=让圆桌自行推进一轮；非空=先插话再让圆桌回应。阻塞到本轮完成，返回新增 turns
    POST /report   生成报告并落盘 report.md / conversation.md / instance_dump.json / log.json
    GET  /profile       → {field, stance, familiar} 机器级研究者画像（缺文件回全空）
    POST /profile       {field?, stance?, familiar?} 写机器级 researcher.md（不含困惑）
    POST /scan          {topic, puzzle?, output_dir?} 起盲区扫描（画像取自 researcher.md，困惑本次传），轮询 /scan/status
    GET  /scan/status   → {phase: idle|scanning|done|error, cards: [...], output_dir, error?}
    POST /scan/feedback {name, verdict: 已知|新但不适用|新且值得深挖} 三键反馈（喂抑制表）
"""

import json
import os
import re
import threading
import traceback
from argparse import ArgumentParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from knowledge_storm.collaborative_storm.engine import (
    CollaborativeStormLMConfigs,
    RunnerArgument,
    CoStormRunner,
)
from knowledge_storm.collaborative_storm.modules.callback import BaseCallbackHandler
from knowledge_storm.lm import DeepSeekModel
from knowledge_storm.logging_wrapper import LoggingWrapper
from knowledge_storm.rm import (
    TavilySearchRM,
    PerplexitySearchRM,
    JinaFullTextRM,
    MixedRM,
)
from knowledge_storm.utils import load_api_key

import blindspot


def sanitize_topic(topic):
    topic = re.sub(r"[^\w-]", "_", topic.strip()).strip("_")
    return topic or "unnamed_topic"


class ProgressCallback(BaseCallbackHandler):
    """把引擎回调收进内存，供 /status 轮询。"""

    def __init__(self, session):
        self.session = session

    def _push(self, msg):
        self.session["progress"].append(msg)

    def on_warmstart_update(self, message, **kwargs):
        self._push(message)

    def on_turn_policy_planning_start(self, **kwargs):
        self._push("正在决定下一位发言者…")

    def on_expert_information_collection_start(self, **kwargs):
        self._push("专家正在检索资料…")

    def on_expert_utterance_polishing_start(self, **kwargs):
        self._push("正在润色发言…")


# ---- 单会话状态（ponytail: 个人工具一次一个圆桌，全局 dict + 一把锁足够）----
SESSION = {
    "phase": "idle",  # idle | warming | ready | stepping | error
    "topic": None,
    "runner": None,
    "progress": [],
    "output_dir": None,
    "error": None,
}
RUNNER_LOCK = threading.Lock()

SCAN = {"phase": "idle", "topic": None, "cards": [], "output_dir": None, "error": None}
SCAN_LOCK = threading.Lock()

app = FastAPI()

# web 画布：muse_server 同源静态托管 webui/（WKWebView 加载 /ui/，fetch /scan 无跨域）
# 挂在 /ui 子路径，不遮挡 /scan、/session 等 API 路由。
app.mount("/ui", StaticFiles(directory=str(ROOT / "webui"), html=True), name="ui")


class SessionReq(BaseModel):
    topic: str
    model: str = "deepseek-v4-flash"
    retrieve_top_k: int = 5
    warmstart_experts: int = 2
    warmstart_turns: int = 1
    retriever: str = "tavily"       # tavily | perplexity | mixed
    fulltext: bool = False          # True = Jina Reader 全文增强 top3
    output_dir: str | None = None


class StepReq(BaseModel):
    utterance: str = ""


class ScanReq(BaseModel):
    topic: str
    puzzle: str = ""                # 本次困惑：与主题并列的一次性输入，不进画像（ADR-0001/#3）
    output_dir: str | None = None


class ProfileReq(BaseModel):
    # 研究者画像三要素（机器级 researcher.md，不含困惑）
    field: str = ""
    stance: str = ""
    familiar: str = ""


class FeedbackReq(BaseModel):
    name: str
    verdict: str  # 已知 | 新但不适用 | 新且值得深挖


def turn_to_dict(turn):
    return {"role": turn.role, "utterance": turn.utterance}


def build_rm(req: "SessionReq", k: int):
    def tavily():
        return TavilySearchRM(
            tavily_search_api_key=os.getenv("TAVILY_API_KEY"),
            k=k,
            include_raw_content=True,
        )

    if req.retriever == "tavily":
        base = tavily()
    elif req.retriever == "perplexity":
        base = PerplexitySearchRM(k=k)
    elif req.retriever == "mixed":
        base = MixedRM([tavily(), PerplexitySearchRM(k=k)])
    else:
        raise RuntimeError(f"未知检索源 {req.retriever}（可选 tavily / perplexity / mixed）")
    if req.fulltext:
        base = JinaFullTextRM(base_rm=base, top_n=3)
    return base


def build_runner(req: SessionReq):
    load_api_key(toml_file_path=str(ROOT / "secrets.toml"))
    for var in ("DEEPSEEK_API_KEY", "TAVILY_API_KEY", "ENCODER_API_TYPE"):
        if not os.getenv(var):
            raise RuntimeError(f"缺少 {var}（请填在 secrets.toml 或环境变量里）")

    kwargs = {
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
        "api_base": os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
        "temperature": 1.0,
        "top_p": 0.9,
    }

    def ds(max_tokens):
        return DeepSeekModel(model=req.model, max_tokens=max_tokens, **kwargs)

    lm_config = CollaborativeStormLMConfigs()
    lm_config.set_question_answering_lm(ds(1000))
    lm_config.set_discourse_manage_lm(ds(500))
    lm_config.set_utterance_polishing_lm(ds(2000))
    lm_config.set_warmstart_outline_gen_lm(ds(500))
    lm_config.set_question_asking_lm(ds(300))
    lm_config.set_knowledge_base_lm(ds(1000))

    runner_argument = RunnerArgument(
        topic=req.topic,
        retrieve_top_k=req.retrieve_top_k,
        warmstart_max_num_experts=req.warmstart_experts,
        warmstart_max_turn_per_experts=req.warmstart_turns,
        max_search_thread=3,
        warmstart_max_thread=3,
        max_thread_num=5,
    )
    rm = build_rm(req, runner_argument.retrieve_top_k)
    return CoStormRunner(
        lm_config=lm_config,
        runner_argument=runner_argument,
        logging_wrapper=LoggingWrapper(lm_config),
        rm=rm,
        callback_handler=ProgressCallback(SESSION),
    )


def warm_start_bg(req: SessionReq):
    try:
        with RUNNER_LOCK:
            runner = build_runner(req)
            SESSION["runner"] = runner
            runner.warm_start()
            # 引擎会吞热身异常（只打印不重抛），空对话即失败
            if not runner.conversation_history:
                raise RuntimeError("热身失败（对话为空），检查服务端日志")
        SESSION["phase"] = "ready"
    except Exception:
        SESSION["error"] = traceback.format_exc()
        SESSION["phase"] = "error"


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/session")
def create_session(req: SessionReq):
    if SESSION["phase"] in ("warming", "stepping"):
        raise HTTPException(409, "圆桌正忙，等当前操作结束再开新主题")
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(400, "主题不能为空")
    req.topic = topic
    base = req.output_dir or os.environ.get("PAPER_MUSE_OUTPUT_DIR") or str(ROOT / "results")
    SESSION.update(
        phase="warming",
        topic=topic,
        runner=None,
        progress=[],
        error=None,
        output_dir=os.path.join(base, f"costorm_{sanitize_topic(topic)}"),
    )
    threading.Thread(target=warm_start_bg, args=(req,), daemon=True).start()
    return {"ok": True, "topic": topic, "output_dir": SESSION["output_dir"]}


@app.get("/status")
def status():
    runner = SESSION["runner"]
    turns = []
    if runner is not None and SESSION["phase"] in ("ready", "stepping"):
        turns = [turn_to_dict(t) for t in runner.conversation_history]
    return {
        "phase": SESSION["phase"],
        "topic": SESSION["topic"],
        "progress": SESSION["progress"][-5:],
        "turns": turns,
        "output_dir": SESSION["output_dir"],
        "error": SESSION["error"],
    }


@app.post("/step")
def step(req: StepReq):
    # 相位检查/置位必须与执行同锁，否则并发 /step 双双通过 ready 检查后排队多跑一轮（TOCTOU）
    if not RUNNER_LOCK.acquire(blocking=False):
        raise HTTPException(409, "圆桌正忙（另一操作进行中）")
    try:
        if SESSION["phase"] != "ready":
            raise HTTPException(409, f"圆桌未就绪（当前状态 {SESSION['phase']}）")
        SESSION["phase"] = "stepping"
        runner = SESSION["runner"]
        try:
            new_turns = []
            utterance = req.utterance.strip()
            if utterance:
                runner.step(user_utterance=utterance)
                new_turns.append({"role": "你", "utterance": utterance})
            turn = runner.step()
            new_turns.append(turn_to_dict(turn))
            return {"turns": new_turns}
        except Exception as e:
            raise HTTPException(500, f"本轮发言失败：{e}")
        finally:
            SESSION["phase"] = "ready"
    finally:
        RUNNER_LOCK.release()


def _merge_roundtable_into_muse(paper_dir, topic, article, conversation):
    """圆桌产物并入七件契约（#16，spec §5/§8）：mindmap.md 用报告标题层级作导图（覆盖），
    questions.md 追加圆桌对谈里的新拷问句（同话题幂等）。
    paper_dir = costorm_* 子目录的上一级 = 论文根（七件契约所在）。"""
    muse = os.path.join(paper_dir, "docs", "agents", "muse")
    os.makedirs(muse, exist_ok=True)
    heads = [ln.rstrip() for ln in article.splitlines() if ln.lstrip().startswith("#")]
    body = "\n".join(heads) if heads else "（本轮圆桌报告无标题层级）"
    with open(os.path.join(muse, "mindmap.md"), "w", encoding="utf-8") as f:
        f.write(f"# 思维导图（圆桌深挖）：{topic}\n\n{body}\n")
    text = " ".join(getattr(t, "utterance", "") or "" for t in conversation)
    qs, seen = [], set()
    for q in re.findall(r"[^。！？\n]{6,}？", text):
        q = q.strip()
        if q not in seen:
            seen.add(q)
            qs.append(q)
    qfile = os.path.join(muse, "questions.md")
    marker = f"## 圆桌深挖：{topic}"
    existing = open(qfile, encoding="utf-8").read() if os.path.exists(qfile) else ""
    if qs and marker not in existing:
        with open(qfile, "a", encoding="utf-8") as f:
            f.write(f"\n\n{marker}\n" + "\n".join(f"- {q}" for q in qs[:15]) + "\n")


@app.post("/report")
def report():
    # 同 /step：相位检查/置位与执行同锁，堵并发 TOCTOU
    if not RUNNER_LOCK.acquire(blocking=False):
        raise HTTPException(409, "圆桌正忙（另一操作进行中）")
    try:
        if SESSION["phase"] != "ready":
            raise HTTPException(409, f"圆桌未就绪（当前状态 {SESSION['phase']}）")
        SESSION["phase"] = "stepping"
        runner = SESSION["runner"]
        output_dir = SESSION["output_dir"]
        try:
            os.makedirs(output_dir, exist_ok=True)
            runner.knowledge_base.reorganize()
            article = runner.generate_report()
            with open(os.path.join(output_dir, "report.md"), "w", encoding="utf-8") as f:
                f.write(article)
            with open(os.path.join(output_dir, "conversation.md"), "w", encoding="utf-8") as f:
                f.write(f"# 圆桌讨论记录：{SESSION['topic']}\n\n")
                for t in runner.conversation_history:
                    f.write(f"**{t.role}**: {t.utterance}\n\n")
            with open(os.path.join(output_dir, "instance_dump.json"), "w", encoding="utf-8") as f:
                json.dump(runner.to_dict(), f, indent=2, ensure_ascii=False)
            with open(os.path.join(output_dir, "log.json"), "w", encoding="utf-8") as f:
                json.dump(runner.dump_logging_and_reset(), f, indent=2, ensure_ascii=False)
            files = ["report.md", "conversation.md", "instance_dump.json", "log.json"]
            try:
                _merge_roundtable_into_muse(os.path.dirname(output_dir), SESSION["topic"],
                                            article, runner.conversation_history)
                files += ["../docs/agents/muse/mindmap.md", "../docs/agents/muse/questions.md(+圆桌)"]
            except Exception:
                traceback.print_exc()  # 并入失败不该让已生成的报告 500
            return {"output_dir": output_dir, "files": files}
        except Exception as e:
            raise HTTPException(500, f"生成报告失败：{e}")
        finally:
            SESSION["phase"] = "ready"
    finally:
        RUNNER_LOCK.release()


def scan_bg(req: ScanReq):
    try:
        load_api_key(toml_file_path=str(ROOT / "secrets.toml"))
        provs = blindspot.real_providers()
        if not provs:
            raise RuntimeError("没有任何可用的 LLM key（DEEPSEEK/OPENAI/GOOGLE）")

        def on_card(card):
            with SCAN_LOCK:
                SCAN["cards"].append(card)

        # 机器级 researcher.md 为画像源头；run_scan 物化只读快照为该论文 profile.md（ADR-0001）
        profile = blindspot.profile_text_from_dict(blindspot.load_researcher_profile())
        blindspot.run_scan(
            topic=req.topic, profile=profile, puzzle=req.puzzle, output_dir=SCAN["output_dir"],
            providers=provs, decompose_llm=blindspot.pick_decompose_llm(provs),
            en_search=blindspot.real_en_search(), zh_search=blindspot.real_cnki_search(),
            own_search=blindspot.real_own_search(), on_card=on_card)
        SCAN["phase"] = "done"
    except Exception:
        SCAN["error"] = traceback.format_exc()
        SCAN["phase"] = "error"


@app.get("/profile")
def get_profile():
    """机器级研究者画像（三要素）。缺文件回全空 → webui 起空画像/首填。跨两篇论文复用免重填。"""
    return blindspot.load_researcher_profile()


@app.post("/profile")
def put_profile(req: ProfileReq):
    """写机器级 researcher.md（左栏就地编辑 / 开笔卡保存触发）。困惑不在此列。"""
    blindspot.save_researcher_profile(req.model_dump())
    return {"ok": True, "path": str(blindspot.researcher_md_path())}


@app.post("/scan")
def start_scan(req: ScanReq):
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(400, "主题不能为空")
    base = req.output_dir or os.environ.get("PAPER_MUSE_OUTPUT_DIR") or str(ROOT / "results" / "muse" / sanitize_topic(topic))
    # 同 /step：相位检查与置位必须原子，堵并发 /scan 双双通过检查各起一个扫描线程
    with SCAN_LOCK:
        if SCAN["phase"] == "scanning":
            raise HTTPException(409, "扫描进行中")
        SCAN.update(phase="scanning", topic=topic, cards=[], error=None, output_dir=base)
    threading.Thread(target=scan_bg, args=(req,), daemon=True).start()
    return {"ok": True, "output_dir": base}


@app.get("/scan/status")
def scan_status():
    with SCAN_LOCK:
        cards = list(SCAN["cards"])
    return {"phase": SCAN["phase"], "topic": SCAN["topic"], "cards": cards,
            "output_dir": SCAN["output_dir"], "error": SCAN["error"]}


# 产物抽屉：docs/agents/muse/ 下 7 件的存在状态 + 绝对路径（打开/在访达用）。
# mindmap/failure-points 引擎暂不写 → exists 恒 false，UI 显示「待生成」。
MUSE_PRODUCTS = [
    ("perspectives.md", "全部切入点卡（含反馈状态）", "paper-annotator / 任何 agent"),
    ("questions.md", "每卡 1–2 拷问句（不开圆桌也有种子）", "grill-with-docs"),
    ("sources.md", "文献锚点", "引用核查"),
    ("profile.md", "研究者画像", "本论文复用参照系"),
    ("angle-feedback.json", "已知角度抑制表", "再扫抑制"),
    ("mindmap.md", "圆桌思维导图", "圆桌深挖后"),
    ("failure-points.md", "对抗幕失败点（带证据或未决）", "to-prove / diagnose"),
]


@app.get("/scan/products")
def scan_products():
    base = SCAN["output_dir"]
    if not base:
        return {"dir": None, "files": []}
    d = os.path.join(base, "docs", "agents", "muse")
    files = [
        {"name": n, "path": os.path.join(d, n), "exists": os.path.exists(os.path.join(d, n)),
         "desc": desc, "consumer": cons}
        for n, desc, cons in MUSE_PRODUCTS
    ]
    return {"dir": d, "files": files}


@app.post("/scan/feedback")
def scan_feedback(req: FeedbackReq):
    if not SCAN["output_dir"]:
        raise HTTPException(409, "尚无扫描会话")
    if req.verdict not in ("已知", "新但不适用", "新且值得深挖"):
        raise HTTPException(400, "verdict 必须是 已知/新但不适用/新且值得深挖")
    blindspot.record_feedback(SCAN["output_dir"], req.name, req.verdict)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    parser = ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
