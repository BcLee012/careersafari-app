"""LLM 接入层：Stepfun（阶跃星辰），OpenAI 兼容协议。

三条纪律（对应 9/22 方案「三条硬约束」）：
  1. 模型只能基于给定的 JD 原文输出，不许引入外部知识；
  2. 每条拆出的需求必须带 evidence_span（原文片段），供程序事后校验；
  3. 无 API key 时走 mock，不许假装调用了模型 —— 降级路径必须诚实。

校验不在这里做，在 extractor.validate_extraction()：把 evidence_span 回到原文里做子串匹配。
这是「程序在展示前自动检查，出现资料库外的说法就拒绝显示」的最小可运行实现。
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

API_KEY_ENV = "STEPFUN_API_KEY"
BASE_URL_ENV = "STEPFUN_BASE_URL"
MODEL_ENV = "STEPFUN_MODEL"

# Stepfun 有两个端点：
#   /v1        平台按量计费端点（需要余额）
#   /step_plan/v1  coding plan 订阅端点（用订阅额度，单独一把 key）
# 本项目的 LLM 走 step_plan，因为账号持有的是 coding plan。
DEFAULT_BASE_URL = "https://api.stepfun.com/step_plan/v1"
DEFAULT_MODEL = "step-3.7-flash"


def _load_env_file() -> None:
    """极简 .env 加载（不引 python-dotenv 依赖，避免环境差异）。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            # 空值不覆盖已有配置：否则 .env 里留空的 ADMIN_TOKEN= 会屏蔽后面自动生成的值
            if v:
                os.environ.setdefault(k, v)


_load_env_file()


def is_configured() -> bool:
    return bool((os.environ.get(API_KEY_ENV) or "").strip())


# ---------------- 熔断：已知失败后短时间内不再重试 ----------------
# 没有它，每次生成建议都要白等一次必然 402 的请求（约 1–2 秒），
# 而且界面上会反复出现同一个错误。
_CIRCUIT = {"failed_at": 0.0, "reason": "", "tripped": False}
CIRCUIT_COOLDOWN = 300.0  # 秒


def circuit_open() -> bool:
    import time
    if not _CIRCUIT["tripped"]:
        return False
    if time.monotonic() - _CIRCUIT["failed_at"] > CIRCUIT_COOLDOWN:
        _CIRCUIT["tripped"] = False
        _CIRCUIT["reason"] = ""
        return False
    return True


def circuit_reason() -> str:
    return _CIRCUIT["reason"]


def _trip(reason: str) -> None:
    import time
    _CIRCUIT["tripped"] = True
    _CIRCUIT["failed_at"] = time.monotonic()
    _CIRCUIT["reason"] = reason


def reset_circuit() -> None:
    _CIRCUIT.update({"failed_at": 0.0, "reason": "", "tripped": False})


# ---------------- 错误分类：把 HTTP 状态码翻译成人话 ----------------

def classify_error(status_code: int, body: str) -> str:
    """返回给终端用户看的一句话。不要暴露原始 JSON。"""
    low = (body or "").lower()
    if status_code == 402 or "quota" in low or "billing" in low or "insufficient" in low:
        return "LLM 服务额度已用尽（需充值或更换 key）"
    if status_code == 401 or "unauthor" in low or "invalid api key" in low:
        return "LLM key 无效或未授权"
    if status_code == 429 or "rate limit" in low:
        return "LLM 请求频率超限"
    if status_code >= 500:
        return "LLM 服务端异常"
    return f"LLM 服务返回 HTTP {status_code}"


def _endpoint() -> str:
    base = (os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL).rstrip("/")
    return f"{base}/chat/completions"


def chat(system: str, user: str, *, temperature: float = 0.2,
         max_tokens: Optional[int] = None, json_mode: bool = True,
         effort: Optional[str] = None, _retry: int = 0) -> Dict[str, Any]:
    """
    max_tokens=None 时用环境变量 LLM_MAX_TOKENS，再不行用 4096。
    传入具体值时以参数为准 —— 环境变量只作默认值，不能覆盖调用方的明确要求。
    （曾经的 bug：环境变量无条件覆盖参数，导致 extractor 传的 16384 被 .env 的 4096 吃掉，
      长 JD 的 reasoning 占满上限，content 恒为空。）
    """
    """
    调用 Stepfun。返回 {"ok": bool, "content": str, "error": str, "model": str, "mock": bool}
    json_mode=True 时要求模型输出 JSON。
    """
    if not is_configured():
        return {"ok": False, "content": "", "error": "未配置 LLM key",
                "model": "", "mock": True}

    if circuit_open():
        return {"ok": False, "content": "", "error": circuit_reason(),
                "model": "", "circuit": True}

    payload: Dict[str, Any] = {
        "model": (os.environ.get(MODEL_ENV) or DEFAULT_MODEL),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        # 推理模型会把 token 花在 reasoning 上；给足余量，否则 content 会被截断为空
        "max_tokens": int(max_tokens if max_tokens is not None
                          else (os.environ.get("LLM_MAX_TOKENS") or 4096)),
    }
    eff = (effort if effort is not None
           else (os.environ.get("LLM_REASONING_EFFORT") or "").strip())
    if eff:
        payload["reasoning_effort"] = eff
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        resp = requests.post(
            _endpoint(),
            headers={
                "Authorization": f"Bearer {os.environ[API_KEY_ENV].strip()}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=90,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Stepfun 请求失败：%s", exc)
        msg = "LLM 服务连接失败（网络或超时）"
        _trip(msg)
        return {"ok": False, "content": "", "error": msg, "mock": False}

    if resp.status_code != 200:
        logger.error("Stepfun HTTP %s：%s", resp.status_code, resp.text[:400])
        msg = classify_error(resp.status_code, resp.text)
        _trip(msg)
        return {"ok": False, "content": "", "error": msg, "mock": False,
                "status_code": resp.status_code}

    try:
        data = resp.json()
        msg = data["choices"][0]["message"]
        content = msg.get("content") or ""
        # 推理模型在极端截断下 content 可能为空而 reasoning 非空。
        # 此时不能算成功——返回空内容会让上层以为解析失败。明确标记。
        if not content.strip():
            # 推理模型会把 token 花在 reasoning 上，偶尔吃满上限导致 content 为空。
            # 这是非确定性的，重试一次并放宽 token 上限即可，不必当成故障上报。
            if _retry < 1:
                base_mt = max_tokens if max_tokens is not None else \
                    int(os.environ.get("LLM_MAX_TOKENS") or 4096)
                return chat(system, user, temperature=temperature,
                            max_tokens=base_mt * 2, json_mode=json_mode,
                            effort=effort, _retry=_retry + 1)
            return {"ok": False, "content": "",
                    "error": "模型两次返回空内容（reasoning 占满 token 上限）",
                    "mock": False, "empty_content": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "content": "", "error": f"响应解析失败：{exc}", "mock": False}

    return {"ok": True, "content": content, "error": "", "mock": False,
            "model": data.get("model", "")}


def parse_json_loose(text: str) -> Optional[Any]:
    """容错解析：模型可能把 JSON 包在 markdown 代码块里，或掺了前后废话。"""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"(\{.*\}|\[.*\])", t, re.DOTALL)
    if m:
        frag = m.group(1)
        try:
            return json.loads(frag)
        except json.JSONDecodeError:
            pass
        # 截断救援：模型输出被 max_tokens 切断时，JSON 尾部不完整。
        # 逐个回退到最后一个完整的数组元素 / 键值对，尽量保住已生成的部分。
        for closer in ("}]}", "]}", "}"):
            idx = frag.rfind(closer)
            while idx != -1:
                try:
                    return json.loads(frag[: idx + len(closer)])
                except json.JSONDecodeError:
                    idx = frag.rfind(closer, 0, idx)
    return None


def mock_extract(raw_text: str) -> Dict[str, Any]:
    """
    无 key 时的降级实现。返回一个明确标记为 mock 的空骨架，
    让界面显示「未接入 LLM，以下为占位」而不是假装分析成功。
    """
    return {
        "company": "", "title": "", "location": "", "job_type": "",
        "canonical_name": "", "industry": "",
        "variant_names": [], "work_scenarios": [], "stressers": [],
        "requirements": [],
        "extraction_notes": "MOCK：未配置 STEPFUN_API_KEY，未做真实抽取。请配置后重试。",
        "_mock": True,
    }
