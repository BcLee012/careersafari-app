"""SQLite 存储层。

设计要点：
  - responses 是 append-only 且带 schema_version —— 任何时候都要能回答
    「这条结论是基于哪版问卷、哪条原始回答得出的」。
  - profiles 与 responses 分离：前者是解析后的结论，后者是用户原话。
  - jd_records 永不修改，只追加；position/requirement 是它的派生视图。
  - 数据库是构建产物，可由种子数据 + 采集记录重建。
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional

from .schema import SCHEMA_VERSION, JdRecord, Profile

DB_PATH = os.environ.get(
    "CAREERSAFARI_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "careersafari.db"),
)

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);

-- 每次采集的原始回答。append-only，不许 UPDATE / DELETE。
CREATE TABLE IF NOT EXISTS responses (
    response_id     TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    schema_version  TEXT NOT NULL,
    raw_answers     TEXT NOT NULL,          -- JSON：用户原话，逐题
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resp_user ON responses(user_id, created_at);

-- 画像快照（解析后的结论）
CREATE TABLE IF NOT EXISTS profiles (
    profile_id      TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    persona         TEXT,
    constraints_json TEXT NOT NULL,
    skills_json     TEXT NOT NULL,
    interest_json   TEXT NOT NULL,
    values_json     TEXT NOT NULL,
    unknowns_json   TEXT NOT NULL,
    schema_version  TEXT NOT NULL,
    response_id     TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prof_user ON profiles(user_id, created_at);

-- JD 原文留存。append-only。
CREATE TABLE IF NOT EXISTS jd_records (
    jd_id           TEXT PRIMARY KEY,
    raw_text        TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    source_url      TEXT,
    captured_date   TEXT NOT NULL,
    ingested_by     TEXT,
    status          TEXT NOT NULL,
    credibility     TEXT,
    parse_error     TEXT,
    created_at      TEXT NOT NULL
);

-- 标准化岗位
CREATE TABLE IF NOT EXISTS positions (
    position_id     TEXT PRIMARY KEY,
    canonical_name  TEXT NOT NULL,
    industry        TEXT,
    variant_names   TEXT,
    work_scenarios  TEXT,
    stressors       TEXT,
    source_jd_ids   TEXT,
    schema_version  TEXT,
    created_at      TEXT NOT NULL
);

-- 原子化需求。evidence_span 指回 JD 原文哪一句。
CREATE TABLE IF NOT EXISTS requirements (
    req_id          TEXT PRIMARY KEY,
    position_id     TEXT NOT NULL,
    kind            TEXT NOT NULL,          -- hard / soft
    category        TEXT NOT NULL,
    text            TEXT NOT NULL,
    evidence_span   TEXT,
    confidence      TEXT NOT NULL,
    team_confidence REAL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_req_pos ON requirements(position_id);

-- 交互日志：{输入, 检索到的证据 ID, 模型输出, 校验结果, 用户反馈}
-- 9/22 方案要求「从第一天起就记」，它既是评估数据集也是未来训练原料。
CREATE TABLE IF NOT EXISTS interactions (
    interaction_id  TEXT PRIMARY KEY,
    user_id         TEXT,
    kind            TEXT NOT NULL,          -- jd_parse / profile_save / ...
    input_digest    TEXT,
    evidence_ids    TEXT,
    model_output    TEXT,
    validation      TEXT,                   -- JSON：校验结果
    user_feedback   TEXT,
    created_at      TEXT NOT NULL
);
"""


def _now() -> str:
    import datetime as _dt
    return _dt.datetime.now().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def init_db(path: Optional[str] = None) -> None:
    p = path or DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    con = sqlite3.connect(p)
    try:
        con.executescript(SCHEMA_SQL)
        con.commit()
    finally:
        con.close()


@contextmanager
def connect(path: Optional[str] = None):
    p = path or DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


# ------------------------------------------------------------------ 用户与画像


def ensure_user(user_id: str) -> str:
    with connect() as con:
        row = con.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            con.execute("INSERT INTO users(user_id, created_at) VALUES (?,?)", (user_id, _now()))
    return user_id


def save_response(user_id: str, raw_answers: Dict[str, Any]) -> str:
    """原始回答落库。append-only。"""
    rid = new_id("RESP")
    with connect() as con:
        con.execute(
            "INSERT INTO responses(response_id,user_id,schema_version,raw_answers,created_at)"
            " VALUES (?,?,?,?,?)",
            (rid, user_id, SCHEMA_VERSION, json.dumps(raw_answers, ensure_ascii=False), _now()),
        )
    return rid


def save_profile(profile: Profile, response_id: str = "") -> str:
    with connect() as con:
        con.execute(
            "INSERT INTO profiles(profile_id,user_id,persona,constraints_json,skills_json,"
            "interest_json,values_json,unknowns_json,schema_version,response_id,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                profile.profile_id, profile.user_id, profile.persona,
                json.dumps([c.model_dump() for c in profile.constraints], ensure_ascii=False),
                json.dumps([s.model_dump() for s in profile.skills], ensure_ascii=False),
                profile.interest.model_dump_json(),
                profile.values.model_dump_json(),
                json.dumps([u.model_dump() for u in profile.unknowns], ensure_ascii=False),
                profile.schema_version, response_id, profile.created_at,
            ),
        )
    return profile.profile_id


def list_profiles(user_id: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM profiles"
    args: List[Any] = []
    if user_id:
        sql += " WHERE user_id=?"
        args.append(user_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with connect() as con:
        rows = con.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("constraints_json", "skills_json", "unknowns_json"):
            d[k.replace("_json", "")] = json.loads(d[k])
        d["interest"] = json.loads(d["interest_json"])
        d["values"] = json.loads(d["values_json"])
        out.append(d)
    return out


# ------------------------------------------------------------------ JD


def save_jd(rec: JdRecord) -> str:
    with connect() as con:
        con.execute(
            "INSERT INTO jd_records(jd_id,raw_text,source_type,source_url,captured_date,"
            "ingested_by,status,credibility,parse_error,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rec.jd_id, rec.raw_text, rec.source_type, rec.source_url, rec.captured_date,
             rec.ingested_by, rec.status, rec.credibility, rec.parse_error, _now()),
        )
    return rec.jd_id


def update_jd_status(jd_id: str, status: str, parse_error: str = "") -> None:
    with connect() as con:
        con.execute("UPDATE jd_records SET status=?, parse_error=? WHERE jd_id=?",
                    (status, parse_error, jd_id))


def list_jds(limit: int = 100) -> List[Dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            "SELECT jd_id, source_type, source_url, captured_date, status, credibility,"
            " length(raw_text) AS raw_len, created_at FROM jd_records"
            " ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_jd(jd_id: str) -> Optional[Dict[str, Any]]:
    with connect() as con:
        r = con.execute("SELECT * FROM jd_records WHERE jd_id=?", (jd_id,)).fetchone()
    return dict(r) if r else None


def jd_exists_by_url(url: str) -> bool:
    """幂等：同一 URL 不重复入库。"""
    if not url:
        return False
    with connect() as con:
        r = con.execute("SELECT 1 FROM jd_records WHERE source_url=? LIMIT 1", (url,)).fetchone()
    return r is not None


def save_position(position) -> str:
    with connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO positions(position_id,canonical_name,industry,variant_names,"
            "work_scenarios,stressors,source_jd_ids,schema_version,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (position.position_id, position.canonical_name, position.industry,
             json.dumps(position.variant_names, ensure_ascii=False),
             json.dumps(position.work_scenarios, ensure_ascii=False),
             json.dumps(position.stressors, ensure_ascii=False),
             json.dumps(position.source_jd_ids, ensure_ascii=False),
             position.schema_version, _now()),
        )
        con.execute("DELETE FROM requirements WHERE position_id=?", (position.position_id,))
        for req in position.requirements:
            con.execute(
                "INSERT INTO requirements(req_id,position_id,kind,category,text,evidence_span,"
                "confidence,team_confidence,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (req.req_id, position.position_id, req.kind, req.category, req.text,
                 req.evidence_span, req.confidence.value, req.team_confidence, _now()),
            )
    return position.position_id


def list_positions(limit: int = 100) -> List[Dict[str, Any]]:
    with connect() as con:
        prows = con.execute(
            "SELECT * FROM positions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for p in prows:
            d = dict(p)
            rrows = con.execute(
                "SELECT * FROM requirements WHERE position_id=? ORDER BY req_id",
                (d["position_id"],)).fetchall()
            d["requirements"] = [dict(r) for r in rrows]
            d["variant_names"] = json.loads(d["variant_names"] or "[]")
            d["work_scenarios"] = json.loads(d["work_scenarios"] or "[]")
            d["stressors"] = json.loads(d["stressors"] or "[]")
            d["source_jd_ids"] = json.loads(d["source_jd_ids"] or "[]")
            out.append(d)
    return out


def log_interaction(kind: str, user_id: str = "", input_digest: str = "",
                    evidence_ids: Optional[Iterable[str]] = None,
                    model_output: str = "", validation: Optional[Dict[str, Any]] = None,
                    user_feedback: str = "") -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO interactions(interaction_id,user_id,kind,input_digest,evidence_ids,"
            "model_output,validation,user_feedback,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (new_id("LOG"), user_id, kind, input_digest,
             json.dumps(list(evidence_ids or []), ensure_ascii=False),
             model_output, json.dumps(validation or {}, ensure_ascii=False),
             user_feedback, _now()),
        )
