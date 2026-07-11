"""#49：版本化、无秘密的 run manifest。

为扫描 / 卡片证据问答 / 圆桌 / 对抗幕生成统一运行记录，把任何结果关联到代码、模型、
prompt/方法论版本、provider 能力、预算、缓存、耗时、降级与产物版本，并指向产物与
evidence ids——但**不复制、不取代**七件文件契约（manifest 是可重建的观察投影，
落 run-manifest.jsonl，不可变追加）。

**秘密红线**：密钥 / token / 密码 / 完整私密画像 / 未授权原文绝不进入 manifest。
双保险：(1) `build()` 白名单构造，只收安全字段（画像只记 has_profile 布尔，产物只记路径名）；
(2) `scrub()` 兜底递归 redact 键名像密钥的字段与值里像密钥的串。

纯 stdlib（+ blindspot 的 muse 目录）；时间戳与 run id 由调用方传入（可注入 → 离线测试确定性）。
"""

import hashlib
import json
import re
import subprocess
import threading
from pathlib import Path

import blindspot

SCHEMA_VERSION = 1
MANIFEST_FILE = "run-manifest.jsonl"          # 不可变追加，一行一次运行；与七件文件并列的观察投影
REDACTED = "***redacted***"
_LOCK = threading.Lock()
_HERE = Path(__file__).resolve().parent

# 键名像密钥/凭证/私密内容 → redact 其值（兜底：白名单构造本就不该带这些键）。
_SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|auth|bearer|cookie|credential|private_profile)")
# 值里像密钥的串 → redact（provider 能力等自由子字典误带 key 时兜底）。
_SECRET_VAL_RE = re.compile(
    r"(sk-[A-Za-z0-9]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|ghp_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{20,})")

# manifest 顶层字段白名单——build() 只发这些键，杜绝调用方塞进画像/原文/密钥。
_ALLOWED_FIELDS = (
    "schema_version", "kind", "run_id", "parent_run_id", "code_version",
    "started_at", "ended_at", "prompt_version", "model", "provider_capability",
    "retrieval_version", "index_version", "budget", "cache", "perf",
    "artifacts", "evidence_ids", "degradation", "has_profile",
)


def code_version() -> str:
    """当前代码版本（git HEAD）。无 git / 出错 → 空串（不阻断运行）。"""
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                           text=True, timeout=5, cwd=str(_HERE))
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def new_run_id(kind: str, seed: str) -> str:
    """稳定 run id：由 kind + seed（如 topic + 起始时间）确定性派生，可注入 → 测试确定。"""
    h = hashlib.sha256(f"{kind}\n{seed}".encode("utf-8")).hexdigest()[:12]
    return f"run_{kind}_{h}"


def scrub(value):
    """递归清洗：键名像密钥 → 值 redact；字符串里像密钥的串 → redact。纯净数据原样返回。"""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[k] = REDACTED if _SECRET_KEY_RE.search(str(k)) else scrub(v)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return _SECRET_VAL_RE.sub(REDACTED, value)
    return value


def build(kind: str, *, run_id: str, started_at: str, ended_at: str = "",
          parent_run_id: str = "", code_version_=None, prompt_version: str = "",
          model: str = "", provider_capability=None, retrieval_version: str = "",
          index_version: str = "", budget=None, cache=None, perf=None,
          artifacts=None, evidence_ids=None, degradation=None, has_profile=None) -> dict:
    """白名单构造 manifest（只收安全字段），再 scrub 兜底。
    kind ∈ scan|evidence|roundtable|adversary|perf-smoke。has_profile 只记布尔，不记画像内容；
    artifacts 只记产物路径名，不记内容；evidence_ids 关联证据身份。"""
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "run_id": run_id,
        "parent_run_id": parent_run_id,
        "code_version": code_version() if code_version_ is None else code_version_,
        "started_at": started_at,
        "ended_at": ended_at,
        "prompt_version": prompt_version,
        "model": model,
        "provider_capability": provider_capability or {},
        "retrieval_version": retrieval_version,
        "index_version": index_version,
        "budget": budget or {},
        "cache": cache or {},
        "perf": perf or {},
        "artifacts": list(artifacts or []),
        "evidence_ids": list(evidence_ids or []),
        "degradation": list(degradation or []),
        "has_profile": has_profile,
    }
    return scrub(manifest)


def append(output_dir, manifest: dict) -> Path:
    """把一次运行追加进 run-manifest.jsonl（不可变追加，并发安全）。返回文件路径。"""
    d = blindspot._muse_dir(str(output_dir))
    path = d / MANIFEST_FILE
    line = json.dumps(scrub(manifest), ensure_ascii=False)
    with _LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    return path


def read(output_dir) -> list:
    """读回该 output_dir 的全部运行记录（供 #50 离线 replay / 跨流程关联）。缺文件 → []。"""
    path = blindspot._muse_dir(str(output_dir)) / MANIFEST_FILE
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def emit(kind: str, output_dir, *, seed: str, started_at: str, ended_at: str = "",
         **fields) -> dict:
    """便捷：build + append 一步。seed 派生 run_id；best-effort（调用方用 try 包，
    manifest 失败绝不拖垮研究运行）。返回落盘的 manifest。"""
    run_id = fields.pop("run_id", None) or new_run_id(kind, seed)
    manifest = build(kind, run_id=run_id, started_at=started_at, ended_at=ended_at, **fields)
    append(output_dir, manifest)
    return manifest
