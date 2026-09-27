"""CareerSafari API 服务层（FastAPI）。

两个采集面，一个库、一套 ID 规则：
  /api/profile   —— 用户画像端（大学初期人群：硬约束 → 能力两栏 → 兴趣 → 价值观 → 未知登记）
  /api/jd/*      —— 职场端（JD 原文 → 原子化需求 → 人工确认 → 入库）

安全设计移植自上一个项目：
  - 写操作要求 X-Admin-Token；ADMIN_TOKEN 未配置时 fail-closed。
  - /api/jd/parse 限流 10 次/分钟（要发外部请求，最耗资源）。
  - URL 采集走 SSRF 校验 + 域名白名单，不采集招聘平台。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import advice as advice_mod
from . import db, extractor, fetcher, llm, match as match_mod
from . import profile as profile_mod
from .schema import SCHEMA_VERSION, JdRecord, Position, Requirement
from .security import (allowlist_mode, configured_admin_token, parse_limiter,
                       read_limiter, require_admin)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("careersafari")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(title="CareerSafari", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    _ensure_admin_token()
    if not llm.is_configured():
        logger.warning("STEPFUN_API_KEY 未配置 —— LLM 功能走 mock 降级路径，"
                       "JD 拆解将返回占位结果而非真实分析。")


def _ensure_admin_token() -> None:
    """本地开发便利：未配置 ADMIN_TOKEN 时自动生成并写入 .env。
    这不削弱安全性（token 依然存在且必须匹配），只是省去手动生成。"""
    if configured_admin_token():
        return
    env_path = os.path.join(BASE_DIR, ".env")
    token = secrets.token_hex(24)
    try:
        with open(env_path, "a", encoding="utf-8") as fh:
            fh.write(f"\n# 自动生成于首次启动。删掉这行并重启可重新生成。\nADMIN_TOKEN={token}\n")
        os.environ["ADMIN_TOKEN"] = token
        logger.info("已自动生成 ADMIN_TOKEN 并写入 .env")
    except OSError as exc:
        logger.warning("无法写入 .env：%s（写操作将保持关闭）", exc)


def _admin(x_admin_token: Optional[str]) -> Optional[JSONResponse]:
    try:
        require_admin(x_admin_token)
        return None
    except PermissionError as exc:
        return JSONResponse(status_code=401, content={"ok": False, "error": str(exc)})


# ---------------------------------------------------------------- 基础


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "llm_configured": llm.is_configured(),
        "llm_model": os.environ.get("STEPFUN_MODEL") or llm.DEFAULT_MODEL,
        "llm_base_url": os.environ.get("STEPFUN_BASE_URL") or llm.DEFAULT_BASE_URL,
        "admin_token_required": bool(configured_admin_token()),
        "allowlist_mode": allowlist_mode(),
        "schema_version": SCHEMA_VERSION,
    }


@app.get("/api/questionnaire")
def questionnaire():
    return profile_mod.questionnaire()


# ---------------------------------------------------------------- 用户画像端


class ProfileIn(BaseModel):
    user_id: str = "anonymous"
    answers: Dict[str, Any]


def build_advice_for(prof, answers: Dict[str, Any]) -> Dict[str, Any]:
    """对全部已沉淀岗位做确定性匹配，生成求职/学习建议。"""
    positions = db.list_positions(limit=200)
    if not positions:
        return {"headline": "岗位知识库还是空的——先去「岗位知识库」页沉淀几条 JD，"
                            "否则没有任何可比对的对象。",
                "positions": [], "learning_plan": [], "two_week_actions": [],
                "evidence_strength": {"level": "无法评估", "note": "无比对对象"},
                "excluded": [], "generated_by": "deterministic"}
    report = match_mod.match_all(positions, prof, answers)
    adv = advice_mod.build_advice(report, prof, answers)
    adv = advice_mod.polish_with_llm(adv)
    return adv


@app.post("/api/advice")
def advice_endpoint(payload: ProfileIn):
    """只算建议，不落库（用于前端反复调整问卷时实时预览）。"""
    prof = profile_mod.build_profile(payload.user_id or "anonymous", payload.answers)
    return {"ok": True, "advice": build_advice_for(prof, payload.answers)}


@app.post("/api/profile")
def save_profile(payload: ProfileIn):
    """保存画像。原始回答与解析结论分开落库。"""
    uid = db.ensure_user(payload.user_id or "anonymous")
    resp_id = db.save_response(uid, payload.answers)

    prof = profile_mod.build_profile(uid, payload.answers)
    prof.profile_id = db.new_id("PROF")
    db.save_profile(prof, resp_id)

    card = profile_mod.profile_card(prof, payload.answers)
    card["profile_id"] = prof.profile_id
    card["response_id"] = resp_id

    # 求职建议：确定性匹配 + 模板叙述（LLM 仅可选润色，且受引用校验约束）
    advice = build_advice_for(prof, payload.answers)

    db.log_interaction("profile_save", uid, input_digest=resp_id,
                       model_output=json.dumps(card, ensure_ascii=False)[:2000],
                       validation={"schema_version": prof.schema_version})
    return {"ok": True, "card": card, "advice": advice}


@app.get("/api/profiles")
def list_profiles(user_id: str = "", limit: int = 50):
    return {"ok": True, "profiles": db.list_profiles(user_id, limit)}


# ---------------------------------------------------------------- 职场端：JD


class JdParseIn(BaseModel):
    raw_text: str
    source_type: str = "manual_paste"
    source_url: str = ""
    user_id: str = "anonymous"


@app.post("/api/jd/parse")
def jd_parse(payload: JdParseIn):
    """解析 JD 原文 → 原子化需求 + 生成后校验。不落库，等人工确认。"""
    try:
        read_limiter.hit(f"parse:{payload.user_id}")
        parse_limiter.hit(f"parse:{payload.user_id}")
    except PermissionError as exc:
        return JSONResponse(status_code=429, content={"ok": False, "error": str(exc)})

    text = (payload.raw_text or "").strip()
    if len(text) < 50:
        return {"ok": False, "error": f"JD 原文过短（{len(text)} 字符），至少需要 50 字符。"}

    extraction, meta = extractor.extract_from_jd(text)
    degraded = False
    if extraction is None:
        # LLM 不可用（无 key / 配额耗尽 / 网络失败）→ 落到规则基线，保证链路可演示。
        # 界面会明确标注这是 rule_based 而非 LLM 分析，不假装成功。
        degraded = True
        extraction = extractor.rule_based_extract(text)
        meta = {"mock": False, "rule_based": True,
                "error": f"{meta.get('error', 'LLM 不可用')}。已使用规则基线解析"}

    validation = extractor.validate_extraction(extraction, text)
    preview_id = "PREVIEW"
    requirements = extractor.to_requirements(extraction, preview_id, text)

    db.log_interaction(
        "jd_parse", payload.user_id,
        input_digest=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        evidence_ids=[r["req_id"] for r in requirements if r.get("span_ok")],
        model_output=json.dumps(extraction, ensure_ascii=False)[:4000],
        validation=validation,
    )

    return {
        "ok": True,
        "mock": bool(meta.get("mock")),
        "rule_based": bool(meta.get("rule_based")),
        "degraded": degraded,
        "model": meta.get("model", ""),
        "error": meta.get("error", ""),
        "extraction": extraction,
        "validation": validation,
        "requirements_preview": requirements,
        "reject_all": validation["reject_all"],
    }


class JdConfirmIn(BaseModel):
    user_id: str = "anonymous"
    raw_text: str
    source_type: str = "manual_paste"
    source_url: str = ""
    credibility: str = "P1"
    canonical_name: str = ""
    industry: str = ""
    requirements: List[Dict[str, Any]]


@app.post("/api/jd/confirm")
def jd_confirm(payload: JdConfirmIn, x_admin_token: Optional[str] = Header(default=None)):
    """人工确认后入库。这一步是人工闸门——LLM 输出不直接进库。"""
    err = _admin(x_admin_token)
    if err:
        return err

    if not payload.requirements:
        return {"ok": False, "error": "没有可入库的需求条目。"}

    jd_id = db.new_id("JD")
    db.save_jd(JdRecord(
        jd_id=jd_id, raw_text=payload.raw_text, source_type=payload.source_type,
        source_url=payload.source_url, ingested_by=payload.user_id,
        status="confirmed", credibility=payload.credibility,
    ))

    pos_id = db.new_id("POS")
    reqs: List[Requirement] = []
    for i, r in enumerate(payload.requirements, start=1):
        text = str(r.get("text", "")).strip()
        if not text:
            continue
        reqs.append(Requirement(
            req_id=f"{pos_id}-REQ-{i:02d}",
            kind=str(r.get("kind", "hard")),
            category=str(r.get("category", "技能")),
            text=text,
            evidence_span=str(r.get("evidence_span", "")),
            confidence=str(r.get("confidence", "external")),
            team_confidence=r.get("team_confidence"),
        ))

    pos = Position(
        position_id=pos_id,
        canonical_name=payload.canonical_name or "未命名岗位",
        industry=payload.industry,
        variant_names=[str(x) for x in (payload.variant_names or [])] if hasattr(payload, "variant_names") else [],
        work_scenarios=[str(x) for x in (payload.work_scenarios or [])] if hasattr(payload, "work_scenarios") else [],
        stressors=[str(x) for x in (payload.stressors or [])] if hasattr(payload, "stressors") else [],
        requirements=reqs,
        source_jd_ids=[jd_id],
    )
    db.save_position(pos)

    db.log_interaction("jd_confirm", payload.user_id, evidence_ids=[r.req_id for r in reqs],
                       validation={"jd_id": jd_id, "position_id": pos_id, "n_req": len(reqs)})
    return {"ok": True, "jd_id": jd_id, "position_id": pos_id, "n_requirements": len(reqs)}


class JdFetchIn(BaseModel):
    url: str
    user_id: str = "anonymous"


@app.post("/api/jd/fetch")
def jd_fetch(payload: JdFetchIn):
    """受控 URL 采集。白名单 + SSRF 校验 + 逐跳重定向校验。"""
    try:
        parse_limiter.hit(f"fetch:{payload.user_id}")
    except PermissionError as exc:
        return JSONResponse(status_code=429, content={"ok": False, "error": str(exc)})

    if not payload.url.strip():
        return {"ok": False, "error": "URL 不能为空。"}
    if db.jd_exists_by_url(payload.url):
        return {"ok": False, "error": "该 URL 已采集过（幂等保护，不重复入库）。"}

    text, err, warns = fetcher.fetch_url(payload.url)
    if text is None:
        return {"ok": False, "error": err, "warnings": warns}

    return {"ok": True, "raw_text": text, "source_url": payload.url,
            "source_type": "url_fetch", "chars": len(text), "warnings": warns}


@app.post("/api/jd/upload")
async def jd_upload(file: UploadFile, user_id: str = "anonymous"):
    """文件批量导入。"""
    data = await file.read()
    text, err = fetcher.read_uploaded_file(file.filename or "", data)
    if text is None:
        return {"ok": False, "error": err}
    return {"ok": True, "raw_text": text, "source_type": "file_upload",
            "filename": file.filename, "chars": len(text)}


@app.get("/api/jds")
def list_jds(limit: int = 500):
    return {"ok": True, "jds": db.list_jds(limit)}


@app.get("/api/positions")
def list_positions(limit: int = 1000):
    return {"ok": True, "positions": db.list_positions(limit)}


# ---------------------------------------------------------------- 前端


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
