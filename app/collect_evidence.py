"""Bounded multi-employer collection, resumable admission, and honest coverage export.

Example: .venv/bin/python collect_evidence.py --discover --ingest-limit 48 --workers 3
Uses the configured business database unless CAREERSAFARI_DB is set explicitly.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import requests
from bs4 import BeautifulSoup

from careersafari import db, fetcher, ingestion, source_adapters, families
from careersafari.coverage import coverage_report, ONET_CROSSWALK
from careersafari.curriculum import job_family
from careersafari.schema import JdRecord

OUT = Path(__file__).resolve().parent / "data" / "evidence" / "2026-10-11"
KEYWORDS = ["数据分析", "商业分析", "经营分析", "数据开发", "运筹", "系统分析", "金融", "战略",
            "供应链", "采购", "物流", "产品运营", "产品经理", "项目管理", "合规"]


def _request(url, payload=None, params=None):
    headers = {"User-Agent": fetcher.USER_AGENTS[0], "Referer": url, "Accept": "application/json, text/html",
               "Content-Type": "application/json"}
    r = requests.request("POST" if payload is not None else "GET", url, headers=headers,
                         json=payload, params=params, timeout=30, allow_redirects=False)
    r.raise_for_status()
    if 300 <= r.status_code < 400:
        raise ValueError("Official endpoint redirected; adapter must be rechecked")
    if len(r.content) > 5_000_000:
        raise ValueError("Source response too large")
    return r


def collect_byte():
    docs, errors, seen = [], [], set()
    api = "https://jobs.bytedance.com/api/v1/search/job/posts"
    for keyword in KEYWORDS:
        try:
            result = _request(api, {"keyword": keyword, "limit": 20, "offset": 0, "portal_type": 2,
                "recruitment_id_list": ["2"], "job_category_id_list": [], "job_function_id_list": [], "recruit_project_id_list": []}).json()
            rows = (result.get("data") or {}).get("job_post_list") or []
            for post in rows:
                jid = str(post.get("id", ""))
                if not jid or jid in seen or source_adapters.byte_stage(post) not in ("实习", "校招"):
                    continue
                seen.add(jid)
                doc = source_adapters.document({"provider": "bytedance", "record": post},
                        f"https://jobs.bytedance.com/campus/position/{jid}/detail", api)
                doc["discovery_query"] = keyword
                docs.append(doc)
        except (requests.RequestException, ValueError, KeyError) as e:
            errors.append({"provider": "字节跳动", "query": keyword, "error": str(e)[:300]})
        time.sleep(0.35)
    return docs, errors


def collect_jd():
    docs, errors, seen = [], [], set()
    api = "https://campus.jd.com/api/wx/position/page?type=present"
    # Two bounded pages cover the currently observed 124 campus posts.
    for page in range(2):
        try:
            data = _request(api, {"pageSize": 100, "pageIndex": page, "parameter": {"positionName": "", "planIdList": [],
                "jobDirectionCodeList": [], "workCityCodeList": [], "positionDeptList": []}}).json()
            rows = (data.get("body") or {}).get("items") or []
        except (requests.RequestException, ValueError) as e:
            errors.append({"provider": "京东", "error": str(e)[:300]}); continue
        for listing in rows:
            jid = listing.get("publishId")
            name = listing.get("positionName", "")
            if not jid or jid in seen or not any(k in name for k in ["数据", "产品", "供应链", "采购", "物流", "财务", "项目", "运筹", "经营", "风险", "合规", "战略", "研究", "系统"]):
                continue
            seen.add(jid)
            url = f"https://campus.jd.com/api/wx/position/detail/{jid}"
            try:
                result = _request(url, {}).json()
                if not result.get("success"):
                    raise ValueError("Detail API did not confirm success")
                doc = source_adapters.document({"provider": "jd", "listing": listing, "detail": result.get("body") or {}}, url, api)
                doc["public_listing_url"] = "https://campus.jd.com/#/jobs"
                docs.append(doc)
            except (requests.RequestException, ValueError, KeyError) as e:
                errors.append({"provider": "京东", "url": url, "error": str(e)[:300]})
            time.sleep(0.25)
    return docs, errors


def _baidu_ssr(html):
    soup = BeautifulSoup(html, "html.parser")
    for s in soup.find_all("script"):
        t = s.get_text()
        m = re.search(r"window\.__INITIAL_DATA__\s*=\s*", t)
        if m:
            t = re.sub(r'"(?:\\.|[^"\\])*"|\bundefined\b', lambda x: "null" if x.group() == "undefined" else x.group(), t[m.end():])
            return json.JSONDecoder().raw_decode(t)[0]
    raise ValueError("Baidu list has no SSR payload")


def collect_meituan():
    docs, errors, seen = [], [], set()
    api = "https://zhaopin.meituan.com/api/official/job/getJobList"
    for kw in KEYWORDS:
        try:
            data = _request(api, {"page": {"pageNo": 1, "pageSize": 20}, "jobShareType": "1", "keywords": kw,
                "jobType": [{"code": "1", "subCode": ["1"]}, {"code": "2", "subCode": ["2", "6"]}],
                "typeCode": ["1", "2", "6"]}).json()
            for item in (data.get("data") or {}).get("list", []):
                jid = str(item.get("jobUnionId") or "")
                if not jid or jid in seen or str(item.get("jobType")) not in ("1", "2"):
                    continue
                seen.add(jid)
                endpoint = "https://zhaopin.meituan.com/api/official/job/getJobDetail"
                result = _request(endpoint, {"jobUnionId": jid, "jobShareType": "1"}).json()
                if result.get("status") != 1 or not result.get("data"):
                    errors.append({"provider": "美团", "id": jid, "error": "详情响应未成功"}); continue
                url = f"https://zhaopin.meituan.com/web/position/detail?jobUnionId={jid}&jobShareType=1"
                doc = source_adapters.document({"provider": "meituan", "record": result["data"]}, url, endpoint)
                doc["discovery_query"] = kw
                docs.append(doc)
                time.sleep(0.2)
        except (requests.RequestException, ValueError, KeyError) as e:
            errors.append({"provider": "美团", "query": kw, "error": str(e)[:300]})
        time.sleep(0.3)
    return docs, errors


def collect_baidu():
    docs, errors, seen = [], [], set()
    for stage in ("INTERN", "GRADUATE"):
        for kw in ("数据", "产品", "供应链", "采购", "项目", "金融", "运筹", "战略"):
            try:
                page = _request("https://talent.baidu.com/jobs/list", params={"recruitType": stage, "search": kw})
                data = _baidu_ssr(page.text).get("listData", {})
                if data.get("recruitType") != stage:
                    raise ValueError("List did not retain requested recruitment stage")
                if data.get("keyWord") != kw:
                    raise ValueError("List did not retain search keyword")
                for post in data.get("listDetailData", []):
                    jid = post.get("postId")
                    if not jid or jid in seen:
                        continue
                    seen.add(jid)
                    # List filters can change; reject irrelevant discovery candidates locally.
                    if not any(k in post.get("name", "") for k in KEYWORDS + ["数据", "产品", "计划"]):
                        continue
                    url = f"https://talent.baidu.com/jobs/detail/{stage}/{jid}"
                    response = _request(url)
                    doc = fetcher._document(response.text, url, url, "http")
                    docs.append(doc)
            except (requests.RequestException, ValueError, KeyError) as e:
                errors.append({"provider": "百度", "query": kw, "stage": stage, "error": str(e)[:300]})
            time.sleep(0.3)
    return docs, errors


def discover(output):
    output.mkdir(parents=True, exist_ok=True)
    errors, documents = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for name, fut in [(f.__name__, pool.submit(f)) for f in (collect_byte, collect_jd, collect_baidu, collect_meituan)]:
            docs, errs = fut.result()
            errors.extend(errs); documents.extend(docs)
            print(name, len(docs), "sources", len(errs), "errors", flush=True)
    records = []
    for doc in documents:
        url = doc["requested_url"]
        key = hashlib.sha256(url.encode()).hexdigest()[:20]
        (output / f"{key}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2))
        facts = doc.get("metadata", {}).get("facts", {})
        title = facts.get("title", {}).get("value", "")
        records.append({"file": f"{key}.json", "url": url, "title": title,
                        "company": facts.get("company", {}).get("value", ""),
                        "stage": facts.get("job_type", {}).get("value", ""),
                        "published_date": facts.get("published_date", {}).get("value", ""),
                        "discovery_family": job_family(title), "classification_status": "title_hint_only"})
    # URL identity dedup precedes model admission; final family remains the task-based AI result.
    records = list({r["url"]: r for r in records}.values())
    (output / "manifest.json").write_text(json.dumps({"captured_at": datetime.now(timezone.utc).isoformat(),
        "records": records, "errors": errors}, ensure_ascii=False, indent=2))
    return records


def _ingest_one(args):
    path, database = args
    db.DB_PATH = database
    doc = json.loads(Path(path).read_text())
    url = doc["requested_url"]
    db.save_capture(url, doc)
    result = ingestion.ingest(doc["raw_text"], url, "url_fetch", user_id="official-multisource-collector")
    return {"url": url, "title": doc.get("metadata", {}).get("facts", {}).get("title", {}).get("value"), **result}


def ingest_records(records, output, limit, workers):
    db.init_db()
    selected = select_records(records, limit, output)
    history_path = output / "admission-results.json"
    results = json.loads(history_path.read_text()) if history_path.exists() else []
    (output / "selected-batch.json").write_text(json.dumps(selected, ensure_ascii=False, indent=2))
    print("selected", len(selected), "new unchecked sources", flush=True)
    with ProcessPoolExecutor(max_workers=max(1, min(workers, 3))) as pool:
        futs = [pool.submit(_ingest_one, (str(output / r["file"]), db.DB_PATH)) for r in selected]
        for fut in as_completed(futs):
            try:
                result = fut.result()
            except Exception as e:
                result = {"state": "worker_error", "error": str(e)[:300]}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            history_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    return results


def select_records(records, limit, output=None):
    """Resume without rechecking reviewed sources; prioritise coverage gaps.

    Discovery hints schedule collection only. Predicted counts cannot admit a family.
    """
    existing = db.list_jds(100000)
    assessed, checked_bodies = set(), set()
    for r in existing:
        if r.get("status") == "checking":
            assessed.add(r["source_url"])
        a = db.get_assessment(r["jd_id"])
        if a:
            assessed.add(r["source_url"])
            assessed.add(a.get("details", {}).get("source_url", ""))
            if output:
                checked_bodies.add(ingestion.content_hash(db.get_jd(r["jd_id"])["raw_text"]))
    groups = {(g["family"], g["stage"]): g for g in families.aggregate(db.list_positions(None))}
    predicted = {key: {"n": g["jd_count"], "employers": {families.employer_key(p["admission"]["company"]) for p in g["members"]}}
                 for key, g in groups.items()}
    buckets = {}
    queued_bodies = set(checked_bodies)
    for r in records:
        if r["url"] in assessed:
            continue
        if r["stage"] not in ("实习", "校招"):
            continue
        if not families.recent(r.get("published_date"), datetime.now().date()):
            continue
        if output:
            doc = json.loads((output / r["file"]).read_text())
            digest = ingestion.content_hash(doc["raw_text"])
            if digest in queued_bodies:
                continue
            queued_bodies.add(digest)
        # Avoid known out-of-catalogue algorithm/backend titles in the bounded queue.
        # Original source remains stored; this does not classify or delete the JD.
        if re.search(r"算法|后端|前端", r["title"]) and not re.search(r"运筹|规划求解", r["title"]):
            continue
        buckets.setdefault((r["discovery_family"], r["stage"], r["company"]), []).append(r)
    selected = []
    available_employers = {}
    for family, stage, company in buckets:
        available_employers.setdefault((family, stage), set()).add(families.employer_key(company))
    while any(buckets.values()) and len(selected) < limit:
        def priority(key):
            family, stage, company = key
            forecast = predicted.get((family, stage), {"n": 0, "employers": set()})
            known = family in ONET_CROSSWALK
            employers = forecast["employers"]
            eligible = groups.get((family, stage), {}).get("eligible", False)
            attainable = len(employers | available_employers.get((family, stage), set())) >= families.MIN_EMPLOYERS
            employer_gap = len(employers) < families.MIN_EMPLOYERS and families.employer_key(company) not in employers
            count_gap = forecast["n"] < families.MIN_JDS
            return (not known, eligible, not attainable, not employer_gap, not count_gap,
                    abs(families.MIN_JDS-forecast["n"]) if count_gap else forecast["n"], family, stage, company)
        key = min((k for k in buckets if buckets[k]), key=priority)
        r = buckets[key].pop(0)
        selected.append(r)
        forecast = predicted.setdefault(key[:2], {"n": 0, "employers": set()})
        forecast["n"] += 1
        forecast["employers"].add(families.employer_key(key[2]))
    return selected


def register_raw(records, output):
    """Persist unchecked snapshots as raw, never as matching evidence."""
    added = 0
    for r in records:
        if db.jd_exists_by_url(r["url"]):
            continue
        doc = json.loads((output / r["file"]).read_text())
        jd_id = db.new_id("JD")
        db.save_jd(JdRecord(jd_id=jd_id, raw_text=doc["raw_text"], source_url=r["url"],
                           source_type="url_fetch", ingested_by="official-multisource-collector", credibility="P3"))
        db.save_capture(r["url"], doc)
        db.save_source_snapshot(jd_id, doc["raw_text"], r["url"], "url_fetch", doc)
        added += 1
    return added


def export_coverage(output):
    report = coverage_report(db.list_positions(None))
    (output / "coverage.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    lines = ["# 首批岗位覆盖台账", "", report["note"], "", "|岗位族|阶段|近期JD|企业|准入|缺JD|缺企业|", "|---|---|---:|---:|---|---:|---:|"]
    for r in report["rows"]:
        lines.append(f"|{r['family']}|{r['stage']}|{r['jd_count']}|{r['employer_count']}|{'可比对' if r['eligible'] else '待补齐'}|{r['missing_jds']}|{r['missing_employers']}|")
    (output / "coverage.md").write_text("\n".join(lines)+"\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--ingest-limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--register-raw", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    records = discover(args.output) if args.discover else json.loads((args.output / "manifest.json").read_text())["records"]
    db.init_db()
    if args.register_raw:
        print("registered raw", register_raw(records, args.output))
    if args.ingest_limit > 0:
        ingest_records(records, args.output, args.ingest_limit, args.workers)
    db.init_db(); report = export_coverage(args.output)
    print("collected", len(records), "eligible family/stage", report["eligible_count"], "/", report["target_count"])
