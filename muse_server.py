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
    GET  /topic/suggest → {topic?, path?} 从 PAPER_MUSE_OUTPUT_DIR 最近 md 标题预填主题
    POST /scan          {topic, puzzle?, output_dir?} 起盲区扫描（画像取自 researcher.md，困惑本次传），轮询 /scan/status
    GET  /scan/status   → {phase: idle|scanning|done|error, cards: [...], output_dir, error?, has_profile}
                          has_profile=false（无画像参照系）→ webui 明示「发现力打折」
    POST /scan/feedback {name, verdict: 已知|新但不适用|新且值得深挖} 三键反馈（喂抑制表）
    GET  /evidence/status?pdf_dir= → PaperQA2 自有 PDF 库证据层可用性（可选能力，缺失不阻断启动）
    POST /evidence/ask {question, pdf_dir?, output_dir?, timeout?} 调 PaperQA2 深挖自有库并追加 sources.md
    GET  /adversary/drafts?output_dir= → {dir, drafts:[{name,path}]} 有稿模式草稿选择器（扫 *.md 含 01_成品稿/）
    POST /adversary     {mode: draft|line, draft?, line?, from_card?, output_dir?} 起对抗审查，轮询 /adversary/status
    GET  /adversary/status → {phase: idle|reviewing|done|error, mode, claims:[{text,span,failures:[...]}], output_dir, error?}
                             失败点带 verdict(已证伪|有佐证|未决)＋evidence；未决=无据不放行。落 failure-points.md
    GET  /perf/status   → 本进程检索缓存、sidecar 调用等性能计数（供 tools/perf_smoke.py 读数）
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback
from argparse import ArgumentParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _early_cli_value(flag):
    for i, arg in enumerate(sys.argv[1:], start=1):
        if arg == flag and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return None


def _abs_path(value):
    return Path(value).expanduser().resolve()


_EXPLICIT_SERVER_ROOT = os.environ.get("PAPER_MUSE_SERVER_ROOT") or _early_cli_value("--server-root")
SERVER_ROOT = _abs_path(_EXPLICIT_SERVER_ROOT) if _EXPLICIT_SERVER_ROOT else ROOT
RELEASE_MODE = "--release-mode" in sys.argv
os.chdir(SERVER_ROOT)

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from knowledge_storm.collaborative_storm.engine import (
    CollaborativeStormLMConfigs,
    RunnerArgument,
    CoStormRunner,
)
from knowledge_storm.collaborative_storm.modules.callback import BaseCallbackHandler
from knowledge_storm.lm import DeepSeekModel, GoogleModel, OpenAIModel
from knowledge_storm.logging_wrapper import LoggingWrapper
from knowledge_storm.rm import (
    TavilySearchRM,
    PerplexitySearchRM,
    JinaFullTextRM,
    MixedRM,
)
from knowledge_storm.utils import load_api_key

import blindspot
import adversary
import paperqa_bridge


def sanitize_topic(topic):
    topic = re.sub(r"[^\w-]", "_", topic.strip()).strip("_")
    return topic or "unnamed_topic"


def _first_markdown_title(path: Path):
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                title = line.strip().lstrip("\ufeff")
                if title.startswith("#"):
                    return title.lstrip("#").strip()
    except OSError:
        return None
    return None


def _suggest_topic_from_output_dir(output_dir=None):
    raw = output_dir or os.environ.get("PAPER_MUSE_OUTPUT_DIR")
    if not raw:
        return {"topic": "", "path": None}
    base = Path(raw).expanduser()
    if not base.is_dir():
        return {"topic": "", "path": None}
    for path in sorted(base.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
        title = _first_markdown_title(path)
        if title:
            return {"topic": title, "path": str(path)}
    return {"topic": "", "path": None}


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
    "model": None,
    "runner": None,
    "progress": [],
    "output_dir": None,
    "error": None,
}
RUNNER_LOCK = threading.Lock()

SCAN = {"phase": "idle", "topic": None, "cards": [], "output_dir": None, "error": None,
        "version": 0,
        "has_profile": False}  # 本次扫描是否有画像参照系；无 → webui 出「发现力打折」警示（#4）
SCAN_LOCK = threading.Lock()

# 对抗幕单会话（同 SCAN：个人工具一次一场审查，全局 dict + 一把锁）。mode = draft|line。
# source = 受审文本（有稿=草稿全文，供②稿面渲染 + 主张跨度定位；无稿=主线句）。
ADV = {"phase": "idle", "mode": None, "model": None, "topic": None, "claims": [], "source": None,
       "source_version": 0, "version": 0, "output_dir": None, "error": None,
       "sidecar": None}
ADV_LOCK = threading.Lock()
SIDECAR_BOOTSTRAP_LOCK = threading.Lock()

app = FastAPI()

# web 画布：muse_server 同源静态托管 webui/（WKWebView 加载 /ui/，fetch /scan 无跨域）
# 挂在 /ui 子路径，不遮挡 /scan、/session 等 API 路由。
app.mount("/ui", StaticFiles(directory=str(SERVER_ROOT / "webui"), html=True), name="ui")


def _env_path(name):
    value = os.environ.get(name)
    return _abs_path(value) if value else None


def _set_path_env(name, value):
    if value is not None:
        os.environ[name] = str(_abs_path(value))


def _secrets_path():
    explicit = os.environ.get("PAPER_MUSE_SECRETS_FILE")
    if explicit:
        return _abs_path(explicit)
    config_dir = _env_path("PAPER_MUSE_CONFIG_DIR")
    return (config_dir / "secrets.toml") if config_dir else (SERVER_ROOT / "secrets.toml")


def _results_base():
    data_dir = _env_path("PAPER_MUSE_APP_DATA_DIR")
    return (data_dir / "results") if data_dir else (SERVER_ROOT / "results")


def _logs_dir():
    logs_dir = _env_path("PAPER_MUSE_LOGS_DIR")
    return logs_dir if logs_dir else (_results_base().parent / "logs")


def _runtime_dir():
    return _env_path("PAPER_MUSE_RUNTIME_DIR") or (SERVER_ROOT / ".venv")


def _sidecar_status_runtime_dir():
    return _env_path("PAPER_MUSE_RUNTIME_DIR")


def _copy_config_template():
    config_dir = _env_path("PAPER_MUSE_CONFIG_DIR")
    if config_dir is None:
        return None
    config_dir.mkdir(parents=True, exist_ok=True)
    src = SERVER_ROOT / "secrets.toml.example"
    dst = config_dir / "secrets.toml.example"
    if src.exists() and not dst.exists():
        shutil.copy2(src, dst)
    return dst if dst.exists() else None


def _write_sidecar_failure(runtime_dir: Path, message: str):
    path = adversary.sidecar_failed_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"error": message}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sidecar_bootstrap_bg(runtime_dir: Path):
    marker = adversary.sidecar_installing_path(runtime_dir)
    failed = adversary.sidecar_failed_path(runtime_dir)
    try:
        script = SERVER_ROOT / "tools" / "runtime_bootstrap.py"
        manifest = SERVER_ROOT / "runtime-manifest.json"
        if not script.exists() or not manifest.exists():
            raise RuntimeError("缺少 sidecar runtime bootstrap 工具或 manifest")
        if failed.exists():
            failed.unlink()
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "bootstrap",
                "--manifest", str(manifest),
                "--runtime-dir", str(runtime_dir),
                "--component", "sidecar_runtime",
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "sidecar bootstrap failed")[-2000:])
    except Exception as e:
        _write_sidecar_failure(runtime_dir, str(e))
    finally:
        marker.unlink(missing_ok=True)


def _start_sidecar_bootstrap():
    runtime_dir = _runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    marker = adversary.sidecar_installing_path(runtime_dir)
    with SIDECAR_BOOTSTRAP_LOCK:
        if marker.exists():
            return adversary.sidecar_status(runtime_dir=runtime_dir)
        marker.write_text("installing\n", encoding="utf-8")
        threading.Thread(target=_sidecar_bootstrap_bg, args=(runtime_dir,), daemon=True).start()
    return adversary.sidecar_status(runtime_dir=runtime_dir)


def configure_runtime_paths(
    *,
    server_root=None,
    app_data_dir=None,
    config_dir=None,
    cache_dir=None,
    runtime_dir=None,
    logs_dir=None,
    release_mode=False,
):
    global SERVER_ROOT, RELEASE_MODE
    RELEASE_MODE = bool(release_mode)
    explicit_server_root = bool(server_root or os.environ.get("PAPER_MUSE_SERVER_ROOT") or _EXPLICIT_SERVER_ROOT)
    if server_root is not None:
        SERVER_ROOT = _abs_path(server_root)
        os.environ["PAPER_MUSE_SERVER_ROOT"] = str(SERVER_ROOT)
        explicit_server_root = True
    if release_mode and not explicit_server_root:
        raise RuntimeError("release mode requires explicit --server-root or PAPER_MUSE_SERVER_ROOT")

    required = {
        "PAPER_MUSE_APP_DATA_DIR": app_data_dir,
        "PAPER_MUSE_CONFIG_DIR": config_dir,
        "PAPER_MUSE_CACHE_DIR": cache_dir,
        "PAPER_MUSE_RUNTIME_DIR": runtime_dir,
        "PAPER_MUSE_LOGS_DIR": logs_dir,
    }
    if release_mode:
        missing = [name for name, value in required.items() if value is None and not os.environ.get(name)]
        if missing:
            raise RuntimeError("release mode requires explicit paths: " + ", ".join(missing))

    for name, value in required.items():
        _set_path_env(name, value)
        path = _env_path(name)
        if path is not None:
            path.mkdir(parents=True, exist_ok=True)

    _copy_config_template()
    os.chdir(SERVER_ROOT)


def _main_runtime_status():
    runtime_dir = _env_path("PAPER_MUSE_RUNTIME_DIR")
    if runtime_dir is None:
        return {"state": "dev", "message": "developer runtime", "runtime_dir": str(SERVER_ROOT / ".venv")}
    temp_installs = sorted(p.name for p in runtime_dir.glob(".paper-muse-runtime-*"))
    if temp_installs:
        return {
            "state": "bootstrap_in_progress",
            "runtime_dir": str(runtime_dir),
            "message": "main runtime bootstrap in progress",
            "temp_installs": temp_installs,
        }
    python = runtime_dir / "main" / "bin" / "python"
    if not python.exists():
        return {"state": "runtime_missing", "runtime_dir": str(runtime_dir), "python": str(python)}
    if not os.access(python, os.X_OK):
        return {"state": "bootstrap_failed", "runtime_dir": str(runtime_dir), "python": str(python),
                "message": "main runtime python is not executable"}
    try:
        result = subprocess.run([str(python), "--version"], capture_output=True, text=True, timeout=10)
    except Exception as e:
        return {"state": "bootstrap_failed", "runtime_dir": str(runtime_dir), "python": str(python),
                "message": str(e)}
    if result.returncode != 0:
        return {"state": "bootstrap_failed", "runtime_dir": str(runtime_dir), "python": str(python),
                "message": (result.stderr or result.stdout or "main runtime health check failed")[-500:]}
    return {"state": "ready", "runtime_dir": str(runtime_dir), "python": str(python),
            "version": (result.stdout or result.stderr or "").strip()}


def _optional_tool_status(name: str, command: str):
    path = shutil.which(command)
    if path:
        return {"state": "available", "optional": True, "command": command, "path": path}
    return {"state": "unavailable", "optional": True, "command": command,
            "message": f"{command} not found; related evidence surface will degrade"}


def _optional_capabilities():
    return {
        "cnki": _optional_tool_status("cnki", "opencli"),
        "zsearch": _optional_tool_status("zsearch", "zsearch"),
        "paperqa": paperqa_bridge.paperqa_status(),
    }


def _optional_degraded(status):
    return status.get("state") not in {"available", "ready"}


def _developer_path_warnings():
    if not RELEASE_MODE:
        return []
    warnings = []
    dev_root = ROOT.resolve()
    paths = {
        "server_root": SERVER_ROOT,
        "app_data_dir": _env_path("PAPER_MUSE_APP_DATA_DIR"),
        "config_dir": _env_path("PAPER_MUSE_CONFIG_DIR"),
        "cache_dir": _env_path("PAPER_MUSE_CACHE_DIR"),
        "runtime_dir": _env_path("PAPER_MUSE_RUNTIME_DIR"),
        "logs_dir": _env_path("PAPER_MUSE_LOGS_DIR"),
    }
    for name, path in paths.items():
        if path is None:
            continue
        resolved = path.resolve()
        if resolved == dev_root or dev_root in resolved.parents:
            warnings.append({"path": name, "value": str(resolved), "message": "release mode is using a developer checkout path"})
    return warnings


def release_health_status():
    setup = _setup_status(REQUIRED_ROUNDTABLE_KEYS)
    runtime = _main_runtime_status()
    optional = _optional_capabilities()
    sidecar = adversary.sidecar_status(runtime_dir=_sidecar_status_runtime_dir())
    developer_warnings = _developer_path_warnings()
    components = {
        "runtime": runtime,
        "server_import": {"state": "ready", "message": "muse_server imported successfully"},
        "setup": setup,
        "optional_capabilities": optional,
        "sidecar": sidecar,
        "developer_paths": {"state": "warning" if developer_warnings else "ok", "warnings": developer_warnings},
    }
    if runtime["state"] in {"runtime_missing", "bootstrap_in_progress", "bootstrap_failed"}:
        state = runtime["state"]
        blocking = True
    elif developer_warnings:
        state = "developer_path"
        blocking = True
    elif setup["missing_required_keys"]:
        state = "missing_required_key"
        blocking = True
    elif any(_optional_degraded(v) for v in optional.values()) or sidecar["state"] != "ready":
        state = "ready_degraded"
        blocking = False
    else:
        state = "ready"
        blocking = False
    return {
        "ok": not blocking,
        "state": state,
        "blocking": blocking,
        "release_mode": RELEASE_MODE,
        "components": components,
        "message": _release_health_message(state, components),
    }


def _release_health_message(state, components):
    if state == "missing_required_key":
        return components["setup"]["message"]
    if state == "developer_path":
        return "Release mode is using developer checkout paths"
    if state in {"runtime_missing", "bootstrap_in_progress", "bootstrap_failed"}:
        return components["runtime"].get("message") or state
    if state == "ready_degraded":
        return "Ready with optional capability degradation"
    return "Ready"


ROUNDTABLE_BASE_KEYS = ("TAVILY_API_KEY", "ENCODER_API_TYPE")
REQUIRED_ROUNDTABLE_KEYS = ("DEEPSEEK_API_KEY", *ROUNDTABLE_BASE_KEYS)
LLM_PROVIDER_KEYS = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY")
ROUNDTABLE_PROVIDER_KEYS = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",
}
ROUNDTABLE_DEFAULT_MODELS = {
    "deepseek": "deepseek-v4-flash",
    "openai": "chat-latest",
    "gemini": "gemini-3.1-flash-lite",
}
KNOWN_PROVIDER_KEYS = (
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "TAVILY_API_KEY",
    "PERPLEXITY_API_KEY",
    "JINA_API_KEY",
    "ENCODER_API_TYPE",
    "ENCODER_API_KEY",
)
PLACEHOLDER_MARKERS = ("YOUR_", "sk-YOUR", "tvly-dev-YOUR", "AIzaSy-YOUR", "pplx-YOUR", "jina_YOUR")


class SetupRequiredError(RuntimeError):
    pass


def _load_provider_config():
    load_api_key(toml_file_path=str(_secrets_path()))


def _configured_key(name):
    value = (os.getenv(name) or "").strip()
    return bool(value) and not any(marker in value for marker in PLACEHOLDER_MARKERS)


def _setup_status(required_keys=REQUIRED_ROUNDTABLE_KEYS):
    _load_provider_config()
    template = _copy_config_template()
    provider_keys = {name: _configured_key(name) for name in KNOWN_PROVIDER_KEYS}
    missing = [name for name in required_keys if not provider_keys.get(name)]
    paths = {
        "config_dir": str(_env_path("PAPER_MUSE_CONFIG_DIR") or SERVER_ROOT),
        "secrets_file": str(_secrets_path()),
        "secrets_template": str(template or (SERVER_ROOT / "secrets.toml.example")),
        "researcher_profile": str(blindspot.researcher_md_path()),
        "results_dir": str(_results_base()),
        "cache_dir": str(_env_path("PAPER_MUSE_CACHE_DIR") or Path.home() / ".cache" / "paper-muse"),
        "runtime_dir": str(_env_path("PAPER_MUSE_RUNTIME_DIR") or SERVER_ROOT / ".venv"),
        "logs_dir": str(_logs_dir()),
    }
    message = "设置完成"
    if missing:
        message = (
            "首次设置未完成：缺少 "
            + ", ".join(missing)
            + f"。请在 {paths['secrets_file']} 填入 provider key；模板见 {paths['secrets_template']}。"
        )
    return {
        "ok": not missing,
        "setup_required": bool(missing),
        "missing_required_keys": missing,
        "provider_keys": provider_keys,
        "has_llm_provider": any(provider_keys.get(name) for name in LLM_PROVIDER_KEYS),
        "paths": paths,
        "message": message,
    }


def _require_setup(required_keys=REQUIRED_ROUNDTABLE_KEYS):
    status = _setup_status(required_keys)
    if status["missing_required_keys"]:
        raise SetupRequiredError(status["message"])
    return status


def _raise_setup_http(error: SetupRequiredError):
    raise HTTPException(status_code=428, detail=str(error))


def _roundtable_model_spec(model: str):
    raw = (model or "deepseek").strip()
    key = raw.lower()
    for provider in ROUNDTABLE_DEFAULT_MODELS:
        prefix = provider + "/"
        if key == provider:
            return provider, ROUNDTABLE_DEFAULT_MODELS[provider]
        if key.startswith(prefix):
            return provider, raw.split("/", 1)[1]
    if "gemini" in key:
        return "gemini", raw
    if key.startswith(("gpt-", "chat")):
        return "openai", raw
    return "deepseek", raw


def _roundtable_required_keys(req: "SessionReq"):
    provider, _ = _roundtable_model_spec(req.model)
    return (ROUNDTABLE_PROVIDER_KEYS[provider], *ROUNDTABLE_BASE_KEYS)


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


class EvidenceAskReq(BaseModel):
    question: str
    pdf_dir: str | None = None
    output_dir: str | None = None
    timeout: int = paperqa_bridge.DEFAULT_TIMEOUT


class AdversaryReq(BaseModel):
    mode: str = "line"              # draft=有稿（读 draft 草稿）| line=无稿（攻击 line 主线句）
    model: str | None = None        # 可选：openai/deepseek/gemini；空则按 adversary.REVIEW_PREFERENCE
    draft: str | None = None        # 有稿：草稿 .md（相对 output_dir 或绝对路径）
    line: str | None = None         # 无稿：主线句
    from_card: bool = False         # 无稿来源=构思幕卡片一键送入（仅标注 from）
    output_dir: str | None = None


def _scan_bump_locked():
    SCAN["version"] += 1


def _scan_update(**fields):
    with SCAN_LOCK:
        SCAN.update(fields)
        _scan_bump_locked()


def _scan_append_card(card):
    with SCAN_LOCK:
        SCAN["cards"].append(card)
        _scan_bump_locked()


def _scan_replace_cards(cards):
    with SCAN_LOCK:
        SCAN["cards"] = list(cards)
        _scan_bump_locked()


def _scan_touch(_card=None):
    with SCAN_LOCK:
        _scan_bump_locked()


def _adv_bump_locked():
    ADV["version"] += 1


def _adv_update(**fields):
    with ADV_LOCK:
        ADV.update(fields)
        _adv_bump_locked()


def _adv_append_claim(claim):
    with ADV_LOCK:
        ADV["claims"].append(claim)
        _adv_bump_locked()


def _adv_touch(_claim=None):
    with ADV_LOCK:
        _adv_bump_locked()


def _turn_has_utterance(turn):
    return bool(str(getattr(turn, "utterance", "") or "").strip())


def _visible_roundtable_turns(turns):
    return [t for t in turns if _turn_has_utterance(t)]


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
    _require_setup(_roundtable_required_keys(req))
    provider, model = _roundtable_model_spec(req.model)

    def lm(max_tokens):
        kwargs = {"temperature": 1.0, "top_p": 0.9, "max_tokens": max_tokens}
        if provider == "openai":
            return OpenAIModel(
                model=model,
                api_key=os.getenv("OPENAI_API_KEY"),
                api_base=os.getenv("OPENAI_API_BASE", "https://api.openai.com"),
                **kwargs,
            )
        if provider == "gemini":
            return GoogleModel(model=model, api_key=os.getenv("GOOGLE_API_KEY"), **kwargs)
        return DeepSeekModel(
            model=model,
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            api_base=os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
            **kwargs,
        )

    lm_config = CollaborativeStormLMConfigs()
    lm_config.set_question_answering_lm(lm(1000))
    lm_config.set_discourse_manage_lm(lm(500))
    lm_config.set_utterance_polishing_lm(lm(2000))
    lm_config.set_warmstart_outline_gen_lm(lm(500))
    lm_config.set_question_asking_lm(lm(300))
    lm_config.set_knowledge_base_lm(lm(1000))

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
    return {"ok": True, "setup": _setup_status()}


@app.get("/setup/status")
def setup_status():
    return _setup_status()


@app.get("/release/health")
def release_health():
    return release_health_status()


@app.get("/sidecar/status")
def sidecar_status():
    return adversary.sidecar_status(runtime_dir=_sidecar_status_runtime_dir())


@app.post("/sidecar/bootstrap")
def sidecar_bootstrap():
    return {"ok": True, "sidecar": _start_sidecar_bootstrap()}


@app.get("/evidence/status")
def evidence_status(pdf_dir: str | None = None):
    return paperqa_bridge.paperqa_status(pdf_dir=pdf_dir)


def _current_evidence_output_dir(explicit=None):
    if explicit:
        return explicit
    with SCAN_LOCK:
        scan_dir = SCAN["output_dir"]
    if scan_dir:
        return scan_dir
    with ADV_LOCK:
        return ADV["output_dir"]


@app.post("/evidence/ask")
def evidence_ask(req: EvidenceAskReq):
    try:
        load_api_key(toml_file_path=str(_secrets_path()))
        payload = paperqa_bridge.ask_self_library(
            req.question,
            pdf_dir=req.pdf_dir,
            output_dir=_current_evidence_output_dir(req.output_dir),
            timeout=max(30, min(int(req.timeout), 3600)),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"PaperQA 证据问答失败：{e}")
    return payload


@app.post("/session")
def create_session(req: SessionReq):
    if SESSION["phase"] in ("warming", "stepping"):
        raise HTTPException(409, "圆桌正忙，等当前操作结束再开新主题")
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(400, "主题不能为空")
    try:
        _require_setup(_roundtable_required_keys(req))
    except SetupRequiredError as e:
        _raise_setup_http(e)
    req.topic = topic
    provider, model = _roundtable_model_spec(req.model)
    base = req.output_dir or os.environ.get("PAPER_MUSE_OUTPUT_DIR") or str(_results_base())
    SESSION.update(
        phase="warming",
        topic=topic,
        model=provider,
        runner=None,
        progress=[],
        error=None,
        output_dir=os.path.join(base, f"costorm_{sanitize_topic(topic)}"),
    )
    threading.Thread(target=warm_start_bg, args=(req,), daemon=True).start()
    return {"ok": True, "topic": topic, "model": provider, "llm": model, "output_dir": SESSION["output_dir"]}


@app.get("/status")
def status():
    runner = SESSION["runner"]
    turns = []
    if runner is not None and SESSION["phase"] in ("ready", "stepping"):
        turns = [turn_to_dict(t) for t in _visible_roundtable_turns(runner.conversation_history)]
    return {
        "phase": SESSION["phase"],
        "topic": SESSION["topic"],
        "model": SESSION["model"],
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
            if _turn_has_utterance(turn):
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
            visible_turns = _visible_roundtable_turns(runner.conversation_history)
            with open(os.path.join(output_dir, "conversation.md"), "w", encoding="utf-8") as f:
                f.write(f"# 圆桌讨论记录：{SESSION['topic']}\n\n")
                for t in visible_turns:
                    f.write(f"**{t.role}**: {t.utterance}\n\n")
            with open(os.path.join(output_dir, "instance_dump.json"), "w", encoding="utf-8") as f:
                json.dump(runner.to_dict(), f, indent=2, ensure_ascii=False)
            with open(os.path.join(output_dir, "log.json"), "w", encoding="utf-8") as f:
                json.dump(runner.dump_logging_and_reset(), f, indent=2, ensure_ascii=False)
            files = ["report.md", "conversation.md", "instance_dump.json", "log.json"]
            try:
                _merge_roundtable_into_muse(os.path.dirname(output_dir), SESSION["topic"],
                                            article, visible_turns)
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
        load_api_key(toml_file_path=str(_secrets_path()))
        provs = blindspot.real_providers()
        if not provs:
            raise RuntimeError("没有任何可用的 LLM key（DEEPSEEK/OPENAI/GOOGLE）")

        def on_card(card):
            _scan_append_card(card)

        # 机器级 researcher.md 为画像源头；run_scan 物化只读快照为该论文 profile.md（ADR-0001）
        profile = blindspot.profile_text_from_dict(blindspot.load_researcher_profile())
        # 记本次是否有画像参照系——供 /scan/status 透出、webui 无画像时明示「发现力打折」（#4）
        _scan_update(has_profile=bool(profile.strip()))
        cards = blindspot.run_scan(
            topic=req.topic, profile=profile, puzzle=req.puzzle, output_dir=SCAN["output_dir"],
            providers=provs, decompose_llm=blindspot.pick_decompose_llm(provs),
            en_search=blindspot.real_en_search(), zh_search=blindspot.real_cnki_search(),
            own_search=blindspot.real_own_search(), on_card=on_card, on_update=_scan_touch)
        _scan_replace_cards(cards)
        _scan_update(phase="done")
    except Exception:
        _scan_update(error=traceback.format_exc(), phase="error")


@app.get("/profile")
def get_profile():
    """机器级研究者画像（三要素）。缺文件回全空 → webui 起空画像/首填。跨两篇论文复用免重填。"""
    return blindspot.load_researcher_profile()


@app.get("/topic/suggest")
def topic_suggest():
    return _suggest_topic_from_output_dir()


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
    try:
        _require_setup(())
        if not _setup_status(())["has_llm_provider"]:
            raise SetupRequiredError("首次设置未完成：缺少 DEEPSEEK_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY 至少一个。")
    except SetupRequiredError as e:
        _raise_setup_http(e)
    base = req.output_dir or os.environ.get("PAPER_MUSE_OUTPUT_DIR") or str(_results_base() / "muse" / sanitize_topic(topic))
    # 同 /step：相位检查与置位必须原子，堵并发 /scan 双双通过检查各起一个扫描线程
    with SCAN_LOCK:
        if SCAN["phase"] == "scanning":
            raise HTTPException(409, "扫描进行中")
        SCAN.update(phase="scanning", topic=topic, cards=[], error=None, output_dir=base,
                    has_profile=False)  # scan_bg 载入画像后据实置位
        _scan_bump_locked()
    threading.Thread(target=scan_bg, args=(req,), daemon=True).start()
    return {"ok": True, "output_dir": base}


@app.get("/scan/status")
def scan_status(since: int | None = None):
    with SCAN_LOCK:
        version = SCAN["version"]
        phase, topic, output_dir, error = (
            SCAN["phase"], SCAN["topic"], SCAN["output_dir"], SCAN["error"])
        if since is not None and since == version:
            return {"version": version, "unchanged": True, "phase": phase,
                    "topic": topic, "output_dir": output_dir, "error": error}
        cards = list(SCAN["cards"])
        has_profile = SCAN["has_profile"]
    return {"phase": phase, "topic": topic, "cards": cards,
            "output_dir": output_dir, "error": error,
            "has_profile": has_profile, "version": version, "unchanged": False}


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


# ---- 对抗幕（spec §6）：/scan 系的孪生——单会话一把锁，POST 起 + status 轮询增量 ----

def _adv_base(output_dir=None):
    return output_dir or os.environ.get("PAPER_MUSE_OUTPUT_DIR") or str(_results_base())


def _list_drafts(base):
    """扫 base 下 *.md 草稿（含 01_成品稿/ 子目录），跳过 docs/agents/muse 产物与 costorm_* 报告。"""
    root = Path(base)
    if not root.exists():
        return []
    drafts = []
    for p in sorted(root.rglob("*.md")):
        parts = p.relative_to(root).parts
        if ("agents" in parts and "muse" in parts) or any(x.startswith("costorm_") for x in parts):
            continue
        drafts.append({"name": str(p.relative_to(root)), "path": str(p)})
    return drafts


def _resolve_draft(base, name):
    p = Path(name)
    if not p.is_absolute():
        p = Path(base) / name
    p = p.resolve()
    if p.suffix != ".md" or not p.exists():
        raise RuntimeError(f"草稿不存在或非 .md：{name}")
    return p.read_text(encoding="utf-8")


def adversary_bg(req: AdversaryReq):
    try:
        load_api_key(toml_file_path=str(_secrets_path()))
        review_llm = adversary.real_review_llm(req.model) if req.model else adversary.real_review_llm()
        with ADV_LOCK:
            ADV["sidecar"] = adversary.sidecar_status(runtime_dir=_sidecar_status_runtime_dir())
            _adv_bump_locked()

        def on_claim(claim):
            _adv_append_claim(claim)

        base = ADV["output_dir"]
        if req.mode == "draft":
            source, has_draft, from_ = _resolve_draft(base, req.draft), True, "draft"
        else:
            source, has_draft = (req.line or "").strip(), False
            from_ = "card" if req.from_card else "input"
        with ADV_LOCK:
            ADV["source"] = source   # ② 稿面渲染 + 跨度定位靠它（轮询期即可读到）
            ADV["source_version"] = ADV["version"] + 1
            _adv_bump_locked()
        adversary.run_review(
            source_text=source, has_draft=has_draft, output_dir=base, review_llm=review_llm,
            falsify_search=adversary.real_falsify_search(),   # #8 = gpt-researcher sidecar（隔离 venv）
            on_claim=on_claim, from_=from_, on_update=_adv_touch)
        _adv_update(phase="done")
    except Exception:
        _adv_update(error=traceback.format_exc(), phase="error")


@app.get("/adversary/drafts")
def adversary_drafts(output_dir: str | None = None):
    """有稿模式草稿选择器：扫 output_dir（缺省 PAPER_MUSE_OUTPUT_DIR）下的 .md 草稿。"""
    base = _adv_base(output_dir)
    return {"dir": base, "drafts": _list_drafts(base)}


@app.post("/adversary")
def start_adversary(req: AdversaryReq):
    if req.mode not in ("draft", "line"):
        raise HTTPException(400, "mode 必须是 draft（有稿）或 line（无稿）")
    if req.mode == "draft" and not (req.draft or "").strip():
        raise HTTPException(400, "有稿模式需指定 draft 草稿")
    if req.mode == "line" and not (req.line or "").strip():
        raise HTTPException(400, "无稿模式需输入主线句 line")
    try:
        _require_setup(())
        if not _setup_status(())["has_llm_provider"]:
            raise SetupRequiredError("首次设置未完成：缺少 DEEPSEEK_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY 至少一个。")
    except SetupRequiredError as e:
        _raise_setup_http(e)
    base = _adv_base(req.output_dir)
    # 同 /scan：相位检查与置位原子，堵并发 /adversary 双双通过检查各起一场审查
    with ADV_LOCK:
        if ADV["phase"] == "reviewing":
            raise HTTPException(409, "对抗审查进行中")
        ADV.update(phase="reviewing", mode=req.mode, claims=[], source=None, error=None,
                   model=req.model,
                   source_version=ADV["version"] + 1, output_dir=base,
                   topic=(req.line or req.draft or "").strip(),
                   sidecar=adversary.sidecar_status(runtime_dir=_sidecar_status_runtime_dir()))
        _adv_bump_locked()
    threading.Thread(target=adversary_bg, args=(req,), daemon=True).start()
    return {"ok": True, "output_dir": base, "mode": req.mode, "model": req.model}


@app.get("/adversary/status")
def adversary_status(since: int | None = None):
    # 流式快照：主张/失败点原地补挂（只换预置键的值），浅拷贝快照序列化安全（同 /scan/status）
    with ADV_LOCK:
        version = ADV["version"]
        phase, mode, model, topic, output_dir, error, sidecar = (
            ADV["phase"], ADV["mode"], ADV["model"], ADV["topic"],
            ADV["output_dir"], ADV["error"], ADV["sidecar"])
        if since is not None and since == version:
            return {"version": version, "unchanged": True, "phase": phase, "mode": mode,
                    "model": model, "topic": topic, "output_dir": output_dir, "error": error}
        claims = list(ADV["claims"])
        source = None if since is not None and since >= ADV["source_version"] else ADV["source"]
        source_version = ADV["source_version"]
    return {"phase": phase, "mode": mode, "model": model, "topic": topic, "source": source,
            "source_version": source_version, "claims": claims, "output_dir": output_dir,
            "error": error, "sidecar": sidecar, "version": version, "unchanged": False}


@app.get("/perf/status")
def perf_status():
    return {
        "retrieval_cache": blindspot.retrieval_cache_stats(),
        "sidecar": adversary.sidecar_stats(),
        "sidecar_runtime": adversary.sidecar_status(runtime_dir=_sidecar_status_runtime_dir()),
        "llm_cache": {"available": False, "note": "LiteLLM cache hits are not exposed here"},
    }


if __name__ == "__main__":
    import uvicorn

    parser = ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--server-root")
    parser.add_argument("--app-data-dir")
    parser.add_argument("--config-dir")
    parser.add_argument("--cache-dir")
    parser.add_argument("--runtime-dir")
    parser.add_argument("--logs-dir")
    parser.add_argument("--release-mode", action="store_true")
    args = parser.parse_args()
    configure_runtime_paths(
        server_root=args.server_root,
        app_data_dir=args.app_data_dir,
        config_dir=args.config_dir,
        cache_dir=args.cache_dir,
        runtime_dir=args.runtime_dir,
        logs_dir=args.logs_dir,
        release_mode=args.release_mode,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
