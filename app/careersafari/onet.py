"""Versioned O*NET 31.0 snapshot and bounded, cited local retrieval.

All original tables remain in the snapshot. The index contains candidate-linked
occupations only. US ratings are never used as Chinese recruitment thresholds.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .coverage import ONET_CROSSWALK

VERSION = "31.0"
DOWNLOAD_URL = "https://www.onetcenter.org/dl_files/database/db_31_0_csv.zip"
LICENSE_URL = "https://www.onetcenter.org/license_db.html"
ATTRIBUTION = ("包含 O*NET 31.0 Database 信息，来源：U.S. Department of Labor, Employment and Training "
               "Administration (USDOL/ETA)，CC BY 4.0。CareerSafari 对数据作了筛选、索引与候选映射；"
               "USDOL/ETA 未批准、认可或测试这些改动。")
LIMITATION = "美国职业参考；岗位族对应为本地候选映射，尚未验证适用于国内实习/校招，不参与录用条件或能力得分。"
DEFAULT_DIR = Path(__file__).resolve().parents[1] / "data" / "onet"
INDEX_TABLES = {"occupation_data", "task_statements", "essential_skills", "transferable_skills",
                "knowledge", "abilities", "work_styles", "work_context", "career_interest_types", "software_skills"}
TRANSLATIONS = {
    "沟通": "communication speaking listening", "协作": "coordination cooperation teamwork",
    "数据": "data analysis database", "统计": "statistics mathematics", "预测": "forecast demand",
    "库存": "inventory supply", "采购": "purchasing suppliers", "物流": "logistics transportation",
    "项目": "project planning", "产品": "product requirements", "金融": "financial investment",
    "风险": "risk compliance", "系统": "systems design", "编程": "programming software",
    "分析": "analyze analysis", "任务": "tasks", "兴趣": "interests",
}


def index_path():
    return Path(os.environ.get("CAREERSAFARI_ONET_DB", str(DEFAULT_DIR / "knowledge.db")))


def _row_text(row):
    return " | ".join(f"{key}: {row[key]}" for key in
        ("Title", "Description", "Task", "Element Name", "Workplace Example", "Scale Name", "Data Value") if row.get(key))


def import_snapshot(archive, target=None):
    """Atomic rebuild from a local official archive; no effect on JD/user database."""
    target = Path(target or index_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    blob = Path(archive).read_bytes()
    archive_hash = hashlib.sha256(blob).hexdigest()
    selected = {c for codes in ONET_CROSSWALK.values() for c in codes}
    tables, indexed, scales, known = [], 0, {}, set()
    temp = target.with_suffix(".building.db")
    temp.unlink(missing_ok=True)
    con = sqlite3.connect(temp)
    try:
        con.executescript("""
          CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE documents(id INTEGER PRIMARY KEY, citation_id TEXT UNIQUE, table_name TEXT,
            row_number INTEGER, soc TEXT, title TEXT, body TEXT, raw_json TEXT, source_url TEXT);
          CREATE INDEX documents_soc ON documents(soc, table_name);
          CREATE VIRTUAL TABLE search USING fts5(body, content='documents', content_rowid='id');
        """)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            members = [m for m in z.infolist() if m.filename.endswith(".csv")]
            if not members or sum(m.file_size for m in members) > 400_000_000:
                raise ValueError("O*NET archive missing tables or exceeds size limit")
            # Preserve every original table with hashes; no path traversal from archive names.
            for m in members:
                basename = Path(m.filename).name
                if not re.fullmatch(r"[a-z_]+\.csv", basename):
                    raise ValueError("Unexpected O*NET table name")
                data = z.read(m)
                dest = target.parent / VERSION / basename
                dest.parent.mkdir(exist_ok=True)
                dest.write_bytes(data)
                name = basename[:-4]
                reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
                total = 0
                for rownum, row in enumerate(reader, 2):
                    total += 1
                    if name == "scales_reference":
                        scales[row["Scale ID"]] = row
                    if name == "occupation_data":
                        known.add(row["O*NET-SOC Code"])
                    soc = row.get("O*NET-SOC Code", "")
                    if name not in INDEX_TABLES or soc not in selected:
                        continue
                    citation = f"ONET-{VERSION}:{name}:{rownum}"
                    source = f"https://www.onetcenter.org/dl_files/database/db_31_0_csv/{basename}"
                    con.execute("INSERT INTO documents(citation_id,table_name,row_number,soc,title,body,raw_json,source_url) VALUES(?,?,?,?,?,?,?,?)",
                                (citation, name, rownum, soc, row.get("Title", ""), _row_text(row), json.dumps(row, ensure_ascii=False), source))
                    indexed += 1
                tables.append({"table": name, "rows": total, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        if selected - known:
            raise ValueError(f"Unknown candidate O*NET codes: {sorted(selected-known)}")
        required = INDEX_TABLES | {"scales_reference", "content_model_reference"}
        if not required.issubset({t["table"] for t in tables}):
            raise ValueError("Required O*NET tables missing")
        manifest = {"version": VERSION, "source_url": DOWNLOAD_URL, "license_url": LICENSE_URL,
                    "license": "CC BY 4.0", "attribution": ATTRIBUTION, "archive_sha256": archive_hash,
                    "imported_at": datetime.now(timezone.utc).isoformat(), "tables": tables,
                    "occupation_count": len(known), "indexed_occupation_count": len(selected),
                    "indexed_rows": indexed, "limitation": LIMITATION}
        for k, v in {"manifest": manifest, "scales": scales}.items():
            con.execute("INSERT INTO metadata VALUES(?,?)", (k, json.dumps(v, ensure_ascii=False)))
        con.execute("INSERT INTO search(search) VALUES('rebuild')")
        con.commit()
    except Exception:
        con.close(); temp.unlink(missing_ok=True)
        raise
    con.close()
    os.replace(temp, target)
    (target.parent / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def _connect():
    p = index_path()
    if not p.is_file():
        return None
    con = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def status():
    con = _connect()
    if con is None:
        return {"available": False, "reason": "O*NET 快照尚未导入"}
    try:
        manifest = json.loads(con.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
        return {"available": True, **manifest}
    finally:
        con.close()


def _eligible_reference(row):
    # Suppressed / not-relevant data stays in snapshot but is not explained as positive evidence.
    raw = json.loads(row["raw_json"])
    return raw.get("Recommend Suppress") != "Y" and raw.get("Not Relevant") != "Y"


def _reference(row):
    raw = json.loads(row["raw_json"])
    return {"citation_id": row["citation_id"], "version": VERSION, "table": row["table_name"],
            "row_number": row["row_number"], "soc": row["soc"], "title": row["title"],
            "excerpt": row["body"], "raw": raw, "source_url": row["source_url"],
            "occupation_url": f"https://www.onetonline.org/link/summary/{row['soc']}"}


def retrieve(family, query="", limit=4):
    codes = ONET_CROSSWALK.get(family, [])
    if not codes:
        return []
    con = _connect()
    if con is None:
        return []
    limit = max(1, min(int(limit), 12))
    placeholders = ",".join("?" for _ in codes)
    try:
        if query.strip():
            # Whitelisted bilingual expansion, no arbitrary SQL/FTS syntax from a user.
            translated = " ".join(v for k, v in TRANSLATIONS.items() if k in query)
            tokens = list(dict.fromkeys(re.findall(r"[a-zA-Z][a-zA-Z0-9_]{1,30}", (query[:300] + " " + translated).lower())))[:16]
            if not tokens:
                return []
            expression = " OR ".join('"'+t+'"' for t in tokens)
            rows = con.execute(f"SELECT d.* FROM search JOIN documents d ON d.id=search.rowid WHERE search MATCH ? AND d.soc IN ({placeholders}) ORDER BY bm25(search),d.id LIMIT 100", (expression, *codes)).fetchall()
        else:
            rows = con.execute(f"SELECT * FROM documents WHERE soc IN ({placeholders}) AND table_name IN ('occupation_data','task_statements') ORDER BY CASE table_name WHEN 'occupation_data' THEN 0 ELSE 1 END,row_number LIMIT 100", codes).fetchall()
        valid = [r for r in rows if _eligible_reference(r)]
        return [_reference(r) for r in valid[:limit]]
    finally:
        con.close()


def family_context(family):
    refs = retrieve(family, limit=4)
    return {"available": bool(refs), "version": VERSION, "references": refs,
            "mapping_status": "candidate_local_mapping", "ranking_use": False,
            "summary": f"检索到与「{family}」候选对应的美国职业定义和任务，可用于了解工作内容。" if refs else "暂无可引用的职业参考。",
            "limitation": LIMITATION, "attribution": ATTRIBUTION, "license_url": LICENSE_URL}
