"""CareerSafari API 服务层（FastAPI）。

两个采集面，一个库、一套 ID 规则：
  /api/profile   —— 用户画像端（大学初期人群：硬约束 → 能力两栏 → 兴趣 → 价值观 → 未知登记）
  /api/jd/*      —— 职场端（JD 原文 → 自动检查 → 入库或待确认）

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
from . import db, extractor, fetcher, ingestion, llm, match as match_mod
from . import profile as profile_mod
from .schema import SCHEMA_VERSION
from .security import (allowlist_mode, configured_admin_token, parse_limiter,
                       read_limiter, require_admin)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("careersafari")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(title="CareerSafari", version=SCHEMA_VERSION)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    _ensure_admin_token()
    if not llm.is_configured():
        logger.warning("模型未配置：预览可使用规则基线，自动入库进入待确认。")


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
    positions = db.list_positions(limit=None)
    with db.connect() as con:
        counts = dict(con.execute("SELECT status,count(*) FROM jd_records GROUP BY status").fetchall())
    inventory = {"submitted": sum(counts.values()), "review": counts.get("review", 0),
                 "checking": counts.get("checking", 0), "excluded": counts.get("excluded", 0),
                 "fetch_failed": len(db.source_failures()), "admitted": counts.get("ready", 0)}
    if not positions:
        headline = (f"已保存 {inventory['submitted']} 份岗位原文，其中 {inventory['review']} 份待确认、"
                    f"{inventory['checking']} 份正在检查、{inventory['excluded']} 份范围外。"
                    "当前还没有通过检查的岗位，暂不形成匹配结果。") if inventory["submitted"] else (
                    "尚未上传岗位资料，当前没有可比对的岗位。")
        if inventory["fetch_failed"]:
            headline += f"另有 {inventory['fetch_failed']} 个来源采集失败，需重新采集。"
        return {"headline": headline,
                "positions": [], "learning_plan": [], "two_week_actions": [],
                "evidence_strength": {"level": "无法评估", "note": "无比对对象"},
                "excluded": [], "generated_by": "deterministic", "library_status": inventory}
    report = match_mod.match_all(positions, prof, answers)
    adv = advice_mod.build_advice(report, prof, answers)
    adv["library_status"] = inventory
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
    """只预览结构/引用检查，不落库；语义审查在统一入库流程完成。"""
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
    extraction: Optional[Dict[str, Any]] = None


class JdIngestIn(JdParseIn):
    retry: bool = False


@app.post("/api/jd/ingest")
def jd_ingest(payload: JdIngestIn, x_admin_token: Optional[str] = Header(default=None)):
    err = _admin(x_admin_token)
    if err:
        return err
    try:
        parse_limiter.hit("ingest:admin")
    except PermissionError as exc:
        return JSONResponse(status_code=429, content={"ok": False, "error": str(exc)})
    return ingestion.ingest(payload.raw_text, payload.source_url, payload.source_type,
                            payload.user_id, retry=payload.retry)


@app.post("/api/jd/confirm")
def jd_confirm(payload: JdConfirmIn, x_admin_token: Optional[str] = Header(default=None)):
    """兼容旧接口；人工点击不能跳过自动检查，也不信任客户端 confidence。"""
    err = _admin(x_admin_token)
    if err:
        return err
    try:
        parse_limiter.hit("ingest:admin")
    except PermissionError as exc:
        return JSONResponse(status_code=429, content={"ok": False, "error": str(exc)})
    candidate = dict(payload.extraction or {})
    candidate.update(requirements=payload.requirements, canonical_name=payload.canonical_name)
    return ingestion.ingest(payload.raw_text, payload.source_url, payload.source_type,
                            payload.user_id, supplied=candidate)


@app.get("/api/jd/reviews")
def jd_reviews(x_admin_token: Optional[str] = Header(default=None)):
    err = _admin(x_admin_token)
    if err:
        return err
    reviews = db.list_assessments()
    for entry in reviews:
        attempts = entry["details"].get("attempts", [])
        extracted = next((a.get("extraction") for a in reversed(attempts) if a.get("extraction")), {})
        entry["title"] = extracted.get("title") or "岗位名称待识别"
        entry["company"] = extracted.get("company") or "公司待核对"
        entry["source_url"] = entry["details"].get("source_url", "")
    return {"ok": True, "reviews": reviews, "source_failures": db.source_failures()}


@app.post("/api/jd/{jd_id}/retry")
def jd_retry(jd_id: str, x_admin_token: Optional[str] = Header(default=None)):
    err = _admin(x_admin_token)
    if err:
        return err
    try:
        parse_limiter.hit("ingest:admin")
    except PermissionError as exc:
        return JSONResponse(status_code=429, content={"ok": False, "error": str(exc)})
    row = db.get_jd(jd_id)
    if not row:
        return {"ok": False, "error": "JD 不存在"}
    latest = db.get_assessment(jd_id)
    details = latest["details"] if latest else {}
    return ingestion.ingest(row["raw_text"], details.get("source_url") or row["source_url"],
                            details.get("source_type") or row["source_type"], jd_id=jd_id, retry=True)


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
    document, err, warns = fetcher.fetch_document(payload.url)
    capture_id = db.save_capture(payload.url, document, err)
    if document is None:
        return {"ok": False, "error": err, "warnings": warns, "capture_id": capture_id}

    text = document["raw_text"]
    return {"ok": True, "raw_text": text, "source_url": payload.url,
            "source_type": "url_fetch", "chars": len(text), "warnings": warns,
            "capture_id": capture_id, "source_facts": document.get("metadata", {}).get("facts", {})}


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


@app.get("/api/library/coverage")
def library_coverage(x_admin_token: Optional[str] = Header(default=None)):
    denied = _admin(x_admin_token)
    if denied is not None:
        return denied
    from .coverage import coverage_report
    return {"ok": True, **coverage_report(db.list_positions(None))}


@app.get("/api/knowledge/onet")
def onet_status():
    from . import onet
    return {"ok": True, **onet.status()}


@app.get("/api/knowledge/search")
def knowledge_search(family: str, q: str = "", limit: int = 4):
    from . import onet
    return {"ok": True, "references": onet.retrieve(family, q, limit),
            "limitation": onet.LIMITATION, "attribution": onet.ATTRIBUTION}


@app.post("/api/knowledge/explain")
def knowledge_explain(payload: Dict[str, Any], x_admin_token: Optional[str] = Header(default=None)):
    denied = _admin(x_admin_token)
    if denied is not None:
        return denied
    from . import rag
    return {"ok": True, **rag.answer(payload.get("family", ""), payload.get("question", ""))}


# ---------------------------------------------------------------- 前端


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
