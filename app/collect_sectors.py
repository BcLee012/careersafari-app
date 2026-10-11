"""Bounded official finance/manufacturing/logistics snapshots and resumable admission.

Run from app: .venv/bin/python collect_sectors.py --discover --register-raw --ingest-limit 48
Unknown-date records may be checked as role references, never counted by recent coverage.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlparse, parse_qs
import requests
from bs4 import BeautifulSoup

from collect_evidence import _request, _ingest_one, register_raw, export_coverage
from careersafari import db, ingestion, sector_sources, families
from careersafari.curriculum import job_family

OUT = Path(__file__).resolve().parent / "data/evidence/2026-10-11-sector-expansion"
TERMS = ["研究", "投资", "分析", "风险", "合规", "采购", "供应", "计划", "物流", "网络规划", "运筹", "产品", "项目", "数据", "系统", "数字化", "内控", "资产配置", "债券", "策略", "模型"]


def relevant(title):
    return any(k in title for k in TERMS)


def form_request(url, data):
    response = requests.post(url, data=data, timeout=30, allow_redirects=False)
    response.raise_for_status()
    if response.is_redirect or len(response.content) > 5_000_000:
        raise ValueError("Official form endpoint redirected or response exceeded bound")
    return response.json()


def target_candidate(record):
    """Queue only titles useful for this cohort; this never determines final family."""
    title = record.get("title", "")
    if any(k in title for k in ("博士后", "焊接", "材料", "电机", "传感器", "工艺", "后端", "前端")):
        return False
    if record.get("company") == "美的集团":
        return any(k in title for k in ("供应链", "产销计划", "采购", "物流", "战略", "财务", "金融", "风险", "合规", "数据", "产品", "项目", "信息系统", "运筹", "经营"))
    if "算法" in title or "应用开发" in title or title == "人工智能":
        return "运筹" in title
    return True


def collect_cicc():
    docs, errors, seen = [], [], set()
    base = "https://cicc.zhiye.com"
    portal = "1bb97a43-1bb9-42c2-b5c6-19ddc82c0cb9"
    for cat in (2, 3):
        for page in range(2):
            try:
                data = _request(base+"/api/Jobad/GetJobAdPageList", {"Category": [cat], "DisplayFields": ["Category", "Kind", "PostDate", "Org"], "PageIndex": page, "PageSize": 100, "PortalId": portal}).json()
                if data.get("Code") != 200:
                    raise ValueError("CICC list not successful")
                rows = [r for r in data.get("Data", []) if relevant(r.get("JobAdName", ""))]
                custom = {}
                if cat == 2 and rows:
                    result = _request(base+"/api/OpenCustomization/GetJobCustomFields", {"JobAdIds": [r["Id"] for r in rows], "CustomFields": [sector_sources.CICC_FIELD]}).json()
                    if result.get("Code") != 200:
                        raise ValueError("CICC project flags not available")
                    custom = {r["JobAdId"]: r for r in result.get("Data", [])}
                for row in rows:
                    jid = row.get("Id")
                    if not jid or jid in seen:
                        continue
                    seen.add(jid)
                    try:
                        endpoint = base+"/api/JobAd/GetJobAdInfo"
                        result = _request(endpoint, params={"jobAdId": jid, "category": 1, "displayFields": '["JobAdName","Duty","Require","Category","Kind","LocId","PostDate","Org"]', "PortalId": portal}).json()
                        if result.get("Code") != 200 or not result.get("Data"):
                            raise ValueError("CICC detail not successful")
                        post = result["Data"]
                        if post.get("Id") != jid or post.get("CategoryId") != row.get("CategoryId"):
                            raise ValueError("CICC list/detail identity conflict")
                        route = "projectxq" if cat == 3 else "summerxq" if (custom.get(jid, {}).get("CustomFields") or {}).get(sector_sources.CICC_FIELD) == "2" else "campusxq"
                        url = f"{base}/custom/{route}?jobAdId={jid}&jc={cat}&hideMenu=1"
                        doc = sector_sources.document({"provider": "cicc", "record": post, "custom": custom.get(jid, {}), "listing": row}, url, endpoint)
                        docs.append(doc)
                    except (ValueError, KeyError, TypeError, OSError) as e:
                        errors.append({"provider": "中金公司", "id": jid, "error": str(e)[:200]})
                    time.sleep(0.15)
                if (page+1)*100 >= data.get("Count", 0):
                    break
            except (ValueError, KeyError, TypeError, OSError) as e:
                errors.append({"provider": "中金公司", "category": cat, "page": page, "error": str(e)[:200]})
    return docs, errors


def collect_citics():
    docs, errors = [], []
    endpoint = "https://global-kong.citics.com/api/v1/recruit/"
    context = {"sysNo": "CSE001", "deptype": "Headquarter", "recruitType": "08", "practice": "0", "batchId": "63"}
    # Official frontend's campus request; the detail type independently confirms campus.
    try:
        data = form_request(endpoint+"getPositionList", {**context, "pageNo": 1, "pageSize": 100, "positionName": ""})
        if data.get("errorCode") != 0:
            raise ValueError("CITICS campus list not successful")
        for row in data.get("positionList", []):
            jid = str(row["positionNo"])
            try:
                result = form_request(endpoint+"getPositionInfo", {**context, "positionNo": jid})
                if result.get("errorCode") != 0 or not result.get("positionInfo"):
                    raise ValueError("CITICS detail not successful")
                post = result["positionInfo"]
                if str(post.get("positionNo")) != jid or post.get("deptNo") != row.get("deptNo"):
                    raise ValueError("CITICS list/detail identity conflict")
                url = f"https://careers.citics.com/bmzyzwxq/index.html?positionNo={jid}&deptNo={post['deptNo']}&deptype=Headquarter&recruitType=08&practice=0"
                docs.append(sector_sources.document({"provider": "citics", "record": post, "response_type": result.get("type"), "listing": row}, url, endpoint+"getPositionInfo"))
            except (ValueError, KeyError, TypeError, OSError) as e:
                errors.append({"provider": "中信证券", "id": jid, "error": str(e)[:200]})
            time.sleep(0.15)
    except (ValueError, KeyError, TypeError, OSError) as e:
        errors.append({"provider": "中信证券", "error": str(e)[:200]})
    return docs, errors


def collect_kysec():
    docs, errors, seen = [], [], set()
    for page in (1, 2):
        try:
            response = _request("https://www.kysec.cn/index.php", params={"m": "content", "c": "index", "a": "lists", "catid": 851, "page": page})
            response.encoding = "utf-8"
            soup = BeautifulSoup(response.text, "html.parser")
            for link in soup.find_all("a", href=True):
                url = link["href"]
                parsed = urlparse(url)
                query = parse_qs(parsed.query)
                if parsed.hostname != "www.kysec.cn" or query.get("a") != ["showjob"] or query.get("catid") != ["851"] or url in seen:
                    continue
                if not relevant(link.get_text()) or "销售" in link.get_text():
                    continue
                seen.add(url)
                try:
                    response = _request(url); response.encoding = "utf-8"
                    docs.append(sector_sources.ky_document(response.text, url))
                except (ValueError, KeyError, TypeError, OSError) as e:
                    errors.append({"provider": "开源证券", "url": url, "error": str(e)[:200]})
                time.sleep(0.2)
        except (ValueError, KeyError, TypeError, OSError) as e:
            errors.append({"provider": "开源证券", "page": page, "error": str(e)[:200]})
    return docs, errors


def collect_sdic():
    docs, errors = [], []
    tenant = sector_sources.SDIC_TENANT
    # Exact tenant verified from the employer's sdicsc.com.cn recruitment links.
    base = "https://wecruit.hotjob.cn/wecruit/positionInfo/"
    try:
        result = form_request(base+"listPosition/"+tenant, {"isFrompb": "true", "recruitType": 12, "pageSize": 50, "currentPage": 1})
        if str(result.get("state")) != "200":
            raise ValueError("SDIC intern list not successful")
        for row in (result.get("data") or {}).get("pageForm", {}).get("pageData", []):
            if not relevant(row.get("postName", "")) or any(k in row.get("postName", "") for k in ("销售", "业务经理")):
                continue
            try:
                result = form_request(base+"listPositionDetail/"+tenant, {"postId": row["postId"]})
                if str(result.get("state")) != "200":
                    raise ValueError("SDIC intern detail not successful")
                post = result.get("data") or {}
                url = f"https://wecruit.hotjob.cn/{tenant}/pb/posDetail.html?postId={row['postId']}&postType=intern"
                docs.append(sector_sources.document({"provider": "sdic", "record": post, "listing": row}, url, base+"listPositionDetail/"+tenant))
            except (ValueError, KeyError, TypeError, OSError) as e:
                errors.append({"provider": "国投证券", "id": row.get("postId"), "error": str(e)[:200]})
            time.sleep(0.15)
    except (ValueError, KeyError, TypeError, OSError) as e:
        errors.append({"provider": "国投证券", "error": str(e)[:200]})
    return docs, errors


def collect_midea():
    docs, errors = [], []
    base = "https://careers.midea.com/backend/school/position/common/"
    try:
        result = _request(base+"project/list", params={"status": 1}).json()
        if result.get("code") != "0":
            raise ValueError("Midea projects not available")
        # Graduate and daily intern routes; do not substitute cooperation/doctoral populations.
        projects = [r for r in result.get("data", []) if r.get("projectType") in ("1", "9")]
        for project in projects:
            for page in range(1, 10):
                data = _request(base+"position/list", {"projectRuleId": project["projectRuleId"], "keyword": "", "superiorIds": [], "recruitCategoryIds": [], "workPlaceCodes": [], "pageIndex": page, "pageSize": 20}).json()
                if data.get("code") != "0":
                    raise ValueError("Midea list not successful")
                page_data = data.get("data") or {}
                for row in page_data.get("data", []):
                    if not relevant(row.get("projectPositionName", "")):
                        continue
                    try:
                        result = _request(base+"position/details", {"positionId": row["positionId"]}).json()
                        if result.get("code") != "0":
                            raise ValueError("Midea detail not successful")
                        post = result.get("data") or {}
                        if post.get("positionId") != row["positionId"] or post.get("projectPositionId") != row.get("projectPositionId"):
                            raise ValueError("Midea list/detail identity conflict")
                        url = "https://careers.midea.com/schoolOut/post/details?positionId="+post["positionId"]
                        docs.append(sector_sources.document({"provider": "midea", "record": post, "project": project, "listing": row}, url, base+"position/details"))
                    except (ValueError, KeyError, TypeError, OSError) as e:
                        errors.append({"provider": "美的集团", "id": row.get("positionId"), "error": str(e)[:200]})
                    time.sleep(0.15)
                if page*20 >= page_data.get("total", 0):
                    break
    except (ValueError, KeyError, TypeError, OSError) as e:
        errors.append({"provider": "美的集团", "error": str(e)[:200]})
    return docs, errors


def collect_sf():
    docs, errors = [], []
    base = "https://campus.sf-express.com/api/web/position/"
    for page in (1, 2):
        try:
            result = _request(base+"query", params={"pageNum": page, "pageSize": 100}).json()
            for row in result.get("list", []):
                if not relevant(row.get("positionName", "")):
                    continue
                try:
                    jid = str(row["id"])
                    post = _request(base+"findById/"+jid).json()
                    if str(post.get("id")) != jid or post.get("seasonId") != row.get("seasonId"):
                        raise ValueError("SF list/detail identity conflict")
                    url = "https://campus.sf-express.com/postDetail/"+jid
                    docs.append(sector_sources.document({"provider": "sf", "record": post, "listing": row}, url, base+"findById/"+jid))
                except (ValueError, KeyError, TypeError, OSError) as e:
                    errors.append({"provider": "顺丰集团", "id": row.get("id"), "error": str(e)[:200]})
                time.sleep(0.15)
            if page*100 >= result.get("total", 0):
                break
        except (ValueError, KeyError, TypeError, OSError) as e:
            errors.append({"provider": "顺丰集团", "page": page, "error": str(e)[:200]})
    return docs, errors


PROVIDERS = {"cicc": collect_cicc, "citics": collect_citics, "midea": collect_midea, "sf": collect_sf,
             "kysec": collect_kysec, "sdic": collect_sdic}
PROVIDER_COMPANIES = {"cicc": "中金公司", "citics": "中信证券", "midea": "美的集团", "sf": "顺丰集团",
                      "kysec": "开源证券", "sdic": "国投证券"}


def discover(output, providers=None):
    output.mkdir(parents=True, exist_ok=True)
    docs, errors = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(PROVIDERS[name]) for name in (providers or PROVIDERS)]
        for fut in as_completed(futures):
            batch, errs = fut.result()
            docs.extend(batch); errors.extend(errs)
            print("provider completed", len(batch), "snapshots", len(errs), "errors", flush=True)
    previous = json.loads((output / "manifest.json").read_text()) if (output / "manifest.json").exists() else {}
    records = {r["url"]: r for r in previous.get("records", [])}
    for doc in docs:
        url = doc["requested_url"]
        filename = hashlib.sha256(url.encode()).hexdigest()[:20]+".json"
        (output / filename).write_text(json.dumps(doc, ensure_ascii=False, indent=2))
        facts = doc["metadata"]["facts"]
        title = facts["title"]["value"]
        records[url] = {"file": filename, "url": url, "title": title, "company": facts["company"]["value"],
                        "stage": facts.get("job_type", {}).get("value", ""), "published_date": facts.get("published_date", {}).get("value", ""),
                        "discovery_family": job_family(title), "classification_status": "title_hint_only", "publication_note": doc["publication_note"]}
    errors = list({json.dumps(e, sort_keys=True): e for e in previous.get("errors", [])+errors}.values())
    manifest = {"captured_at": datetime.now(timezone.utc).isoformat(), "records": list(records.values()), "errors": errors,
                "scope": "official finance/manufacturing/logistics; bounded discovery, not exhaustive"}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest["records"]


def select(records, output, limit):
    """Balanced source checks also include unknown dates; coverage gate remains unchanged."""
    checked, bodies = set(), set()
    for row in db.list_jds(100000):
        a = db.get_assessment(row["jd_id"])
        if a or row.get("status") == "checking":
            checked.add(row["source_url"])
            if a:
                checked.add(a.get("details", {}).get("source_url", ""))
            bodies.add(ingestion.content_hash(db.get_jd(row["jd_id"])["raw_text"]))
    buckets = {}
    for r in records:
        if r["url"] in checked or r["stage"] not in ("实习", "校招"):
            continue
        if r.get("published_date") and not families.recent(r["published_date"], datetime.now().date()):
            continue
        if not target_candidate(r):
            continue
        doc = json.loads((output / r["file"]).read_text())
        digest = ingestion.content_hash(doc["raw_text"])
        if digest in bodies:
            continue
        bodies.add(digest)
        buckets.setdefault((r["company"], r["stage"]), []).append(r)
    for rows in buckets.values():
        rows.sort(key=lambda r: (not any(k in r["title"] for k in ("研究", "供应链", "物流", "采购", "计划", "风险", "合规", "网络规划")), r["title"]))
    chosen = []
    while buckets and len(chosen) < limit:
        for key in list(buckets):
            chosen.append(buckets[key].pop(0))
            if not buckets[key]:
                del buckets[key]
            if len(chosen) >= limit:
                break
    return chosen


def ingest(records, output, limit, workers):
    chosen = select(records, output, limit)
    (output / "selected-batch.json").write_text(json.dumps(chosen, ensure_ascii=False, indent=2))
    history = output / "admission-results.json"
    results = json.loads(history.read_text()) if history.exists() else []
    print("selected", len(chosen), dict(Counter(r["company"] for r in chosen)), flush=True)
    with ProcessPoolExecutor(max_workers=max(1, min(3, workers))) as pool:
        for fut in as_completed([pool.submit(_ingest_one, (str(output / r["file"]), db.DB_PATH)) for r in chosen]):
            try:
                result = fut.result()
            except Exception as e:
                result = {"state": "worker_error", "error": str(e)[:200]}
            results.append(result)
            history.write_text(json.dumps(results, ensure_ascii=False, indent=2))
            print(json.dumps(result, ensure_ascii=False), flush=True)
    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, default=OUT)
    p.add_argument("--discover", action="store_true")
    p.add_argument("--providers", nargs="+", choices=PROVIDERS)
    p.add_argument("--register-raw", action="store_true")
    p.add_argument("--ingest-limit", type=int, default=0)
    p.add_argument("--workers", type=int, default=3)
    args = p.parse_args()
    records = discover(args.output, args.providers) if args.discover else json.loads((args.output / "manifest.json").read_text())["records"]
    db.init_db()
    if args.register_raw:
        print("registered raw", register_raw(records, args.output), flush=True)
    if args.ingest_limit:
        selected_records = [r for r in records if not args.providers or r["company"] in {PROVIDER_COMPANIES[p] for p in args.providers}]
        ingest(selected_records, args.output, args.ingest_limit, args.workers)
    report = export_coverage(args.output)
    print("sources", len(records), "eligible", report["eligible_count"], "/", report["target_count"], flush=True)
