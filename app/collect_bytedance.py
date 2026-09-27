"""
真实 JD 采集器 · 字节跳动校招（企业官方，P1 可信度）

数据来源：jobs.bytedance.com 的前端职位搜索接口（其官网自己调用的同一个接口）。
每条 JD 都带原始 URL，可回溯到官方页面。

为什么用这个源而不是招聘平台：
  - 企业官网 = P1 最高可信度（智联/BOSS 是 P2 二手转载）
  - 接口返回的是完整原文（description + requirement），不是摘要
  - 抓取量小、带来源、限速，不构成批量抓取

用法：
    .venv/bin/python collect_bytedance.py                 # 只采集原文，落 jd_records
    .venv/bin/python collect_bytedance.py --extract 20    # 额外对前 20 条跑 LLM 拆解
"""
from __future__ import annotations

import argparse
import json
import time
from typing import Any, Dict, List

import requests

from careersafari import db, extractor
from careersafari.schema import JdRecord, Position, Requirement

API = "https://jobs.bytedance.com/api/v1/search/job/posts"
WEB = "https://jobs.bytedance.com/campus/position"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/127.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": WEB,
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 面向管理科学与工程（1201）对口方向的关键词。
# 五个研究方向：金融服务与金融工程 / 复杂工程与智能管理 /
#               大数据分析与决策 / 运营与供应链管理 / 智能系统管理
KEYWORDS = [
    "数据分析", "商业分析", "数据分析师", "经营分析",
    "供应链", "物流", "采购",
    "运营", "产品运营", "用户增长",
    "战略分析", "行业研究",
    "数据挖掘", "数据工程",
    "产品经理", "项目管理",
]


def search(keyword: str, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
    body = {"keyword": keyword, "limit": limit, "offset": offset,
            "job_category_id_list": [], "portal_type": 2,
            "job_function_id_list": [], "recruit_project_id_list": []}
    r = requests.post(API, headers=HEADERS, json=body, timeout=30)
    if r.status_code != 200:
        print(f"  ! HTTP {r.status_code} keyword={keyword}")
        return []
    data = r.json().get("data") or {}
    return data.get("job_post_list") or []


def to_raw_text(p: Dict[str, Any]) -> str:
    """把接口字段拼成 JD 原文，尽量保留原始换行与编号。"""
    city = (p.get("city_info") or {}).get("name", "")
    rtype = (p.get("recruit_type") or {}).get("name", "")
    cat = (p.get("job_category") or {}).get("name", "")
    parts = [
        f"岗位名称：{p.get('title','')}",
        f"工作地点：{city}",
        f"职位类型：{rtype}",
        f"职位类别：{cat}",
        "",
        "【职位描述】",
        (p.get("description") or "").strip(),
        "",
        "【任职要求】",
        (p.get("requirement") or "").strip(),
    ]
    return "\n".join(x for x in parts if x is not None).strip()


def collect(limit_per_kw: int = 15, sleep_s: float = 0.8) -> int:
    db.init_db()
    seen: set = set()
    saved = 0
    for kw in KEYWORDS:
        posts = search(kw, limit=limit_per_kw)
        n_new = 0
        for p in posts:
            jid = str(p.get("id"))
            if not jid or jid in seen:
                continue
            seen.add(jid)
            url = f"https://jobs.bytedance.com/campus/position/{jid}/detail"
            if db.jd_exists_by_url(url):
                continue
            raw = to_raw_text(p)
            if len(raw) < 80:
                continue
            db.save_jd(JdRecord(
                jd_id=db.new_id("JD"), raw_text=raw, source_type="url_fetch",
                source_url=url, ingested_by="collector-bytedance",
                status="raw", credibility="P1",
            ))
            n_new += 1
            saved += 1
        print(f"  {kw:8} 返回 {len(posts):3} 条，新增 {n_new:3} 条")
        time.sleep(sleep_s)
    print(f"\n共新增 JD 原文 {saved} 条（去重后候选 {len(seen)} 个）")
    return saved


def _extract_one(row) -> Dict[str, Any]:
    """单条 JD 的完整处理：LLM 拆解 → 校验 → 组装 Position。返回结果摘要。"""
    jd_id, raw = row["jd_id"], row["raw_text"]
    ex, meta = extractor.extract_from_jd(raw)
    if ex is None:
        return {"jd_id": jd_id, "ok": False, "reason": f"LLM 失败：{meta.get('error','')[:60]}"}
    val = extractor.validate_extraction(ex, raw)
    if val["reject_all"]:
        return {"jd_id": jd_id, "ok": False, "reason": "全部需求无原文支撑，拒绝"}
    pos_id = db.new_id("POS")
    reqs = []
    for i, q in enumerate(extractor.to_requirements(ex, pos_id, raw), start=1):
        if not q["text"]:
            continue
        reqs.append(Requirement(
            req_id=f"{pos_id}-REQ-{i:02d}", kind=q["kind"], category=q["category"],
            text=q["text"], evidence_span=q["evidence_span"],
            confidence=q["confidence"], team_confidence=q["team_confidence"]))
    pos = Position(position_id=pos_id, canonical_name=ex.get("canonical_name") or "未命名岗位",
                   industry=ex.get("industry", ""), variant_names=ex.get("variant_names", []),
                   work_scenarios=ex.get("work_scenarios", []),
                   stressors=ex.get("stressors", []), requirements=reqs,
                   source_jd_ids=[jd_id])
    return {"jd_id": jd_id, "ok": True, "position": pos, "raw": raw,
            "passed": val["passed"], "total": val["total"]}


def extract_and_store(n: int, workers: int = 4) -> None:
    """对库里尚未解析的 JD 跑 LLM 拆解并入库。默认 4 并发。"""
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor, as_completed
    con = sqlite3.connect(db.DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT jd_id, raw_text, source_url FROM jd_records WHERE status='raw'"
        " ORDER BY created_at DESC LIMIT ?", (n,)).fetchall()
    con.close()
    if not rows:
        print("没有待解析的 JD。")
        return
    print(f"开始解析 {len(rows)} 条 JD（{workers} 并发）…")
    done = failed = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_extract_one, r): r for r in rows}
        for i, fut in enumerate(as_completed(futs), start=1):
            try:
                res = fut.result()
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"  ✗ 异常：{e}")
                continue
            if res["ok"]:
                db.save_position(res["position"])
                db.update_jd_status(res["jd_id"], "confirmed", "")
                done += 1
                print(f"  [{i}/{len(rows)}] ✓ {res['jd_id']} → {res['position'].canonical_name}"
                      f"｜{len(res['position'].requirements)} 条需求"
                      f"（校验 {res['passed']}/{res['total']}）")
            else:
                db.update_jd_status(res["jd_id"], "rejected", res["reason"][:200])
                failed += 1
                print(f"  [{i}/{len(rows)}] ✗ {res['jd_id']} {res['reason']}")
    print(f"\n完成：成功 {done} 个，失败 {failed} 个。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=15, help="每个关键词抓取条数")
    ap.add_argument("--extract", type=int, default=0, help="额外解析 N 条 JD 入库")
    ap.add_argument("--workers", type=int, default=4, help="并发数")
    a = ap.parse_args()
    collect(limit_per_kw=a.limit)
    if a.extract:
        extract_and_store(a.extract, a.workers)
