"""Explicitly bound official industry sources; missing publication stays unknown."""
import json
import re
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse
from bs4 import BeautifulSoup

VERSION = "sector-api-1"
HOSTS = {"cicc.zhiye.com", "careers.citics.com", "careers.midea.com", "campus.sf-express.com", "www.kysec.cn", "wecruit.hotjob.cn"}
CICC_FIELD = "extmenhufabu_108394_705026142"
SDIC_TENANT = "SU625d4a0b2f9d24287db127c8"


def _day(value):
    if not isinstance(value, str):
        return ""
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return ""


def metadata(encoded, url):
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.hostname == "www.kysec.cn":
        return ky_metadata(encoded, url)
    try:
        payload = json.loads(encoded)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    p = urlparse(url)
    if p.scheme != "https" or p.hostname not in HOSTS:
        return {}
    q = parse_qs(p.query)
    post = payload.get("record")
    if not isinstance(post, dict):
        return {}
    facts, conflicts = {}, []

    def fact(key, value, span, locator):
        if isinstance(value, str) and value:
            facts[key] = {"value": value, "span": str(span), "locator": locator, "source_url": url}

    provider = payload.get("provider")
    if provider == "cicc" and p.hostname == "cicc.zhiye.com":
        routes = {"/custom/campusxq": "校招", "/custom/summerxq": "实习", "/custom/projectxq": "实习"}
        jid = q.get("jobAdId", [""])[0]
        if p.path not in routes or not jid or post.get("Id") != jid:
            return {}
        cat = str(post.get("CategoryId"))
        custom = payload.get("custom")
        custom = custom if isinstance(custom, dict) else {}
        fields = custom.get("CustomFields")
        fields = fields if isinstance(fields, dict) else {}
        flag = fields.get(CICC_FIELD) if custom.get("JobAdId") == jid else None
        stage = ""
        if cat == "3" and post.get("Category") == "项目实习" and post.get("Kind") == "实习":
            stage = "实习"
        elif cat == "2" and post.get("Category") == "校园招聘/暑期实习":
            stage = {"1": "校招", "2": "实习"}.get(flag, "")
        expected = "/custom/projectxq" if cat == "3" else "/custom/campusxq" if flag == "1" else "/custom/summerxq" if flag == "2" else ""
        if not stage or stage != routes[p.path] or p.path != expected:
            conflicts.append("中金招聘类别、项目标记与详情路径无法一致确定阶段")
        fact("company", "中金公司", "cicc.zhiye.com", "official employer tenant; recruitment declaration")
        fact("title", post.get("JobAdName"), post.get("JobAdName"), "GetJobAdInfo.Data.JobAdName")
        fact("job_type", stage, json.dumps({"Category": post.get("Category"), "Kind": post.get("Kind"), "custom": custom}, ensure_ascii=False), "official Category + exact JobAdId custom project flag")
        fact("published_date", _day(post.get("PostDate")), post.get("PostDate"), "GetJobAdInfo.Data.PostDate (not EndTime)")
    elif provider == "citics" and p.hostname == "careers.citics.com":
        jid = q.get("positionNo", [""])[0]
        if p.path != "/bmzyzwxq/index.html" or not jid or str(post.get("positionNo")) != jid:
            return {}
        fact("company", "中信证券", "careers.citics.com", "official employer host and recruitment declaration")
        fact("title", post.get("positionName"), post.get("positionName"), "getPositionInfo.positionInfo.positionName")
        # This adapter admits only the campus detail type; query parameters cannot prove stage.
        if payload.get("response_type") != "08" or post.get("type") != "校园招聘" or q.get("practice", [""])[0] != "0":
            conflicts.append("中信证券详情未确认校园招聘或存在实习路径歧义")
        else:
            fact("job_type", "校招", post["type"], "getPositionInfo.type=08 + positionInfo.type=校园招聘; campus route")
        # No publication field in the official response; no date derived from batch/year.
    elif provider == "midea" and p.hostname == "careers.midea.com":
        jid = q.get("positionId", [""])[0]
        if p.path != "/schoolOut/post/details" or not jid or post.get("positionId") != jid:
            return {}
        project = payload.get("project")
        project = project if isinstance(project, dict) else {}
        if post.get("projectRuleId") != project.get("projectRuleId") or post.get("projectType") != project.get("projectType"):
            conflicts.append("美的岗位与招聘项目身份不一致")
        kind = post.get("employementCategory")
        name = project.get("projectRuleName", "")
        name = name if isinstance(name, str) else ""
        stage = "实习" if kind == 4 and "实习" in name else "校招" if kind == 1 and "校园招聘" in name else ""
        if kind != project.get("employementCategory") or not stage:
            conflicts.append("美的招聘项目与雇佣类别无法一致确定阶段")
        fact("company", "美的集团", "careers.midea.com", "official employer host; all business units counted once")
        fact("title", post.get("projectPositionName"), post.get("projectPositionName"), "position/details.data.projectPositionName")
        fact("job_type", stage, f"{name}; employementCategory={kind}", "matched official project + position employment category")
        # Position codes and graduation windows are not publication dates.
    elif provider == "sf" and p.hostname == "campus.sf-express.com":
        m = re.fullmatch(r"/postDetail/(\d+)/?", p.path)
        if not m or str(post.get("id")) != m[1]:
            return {}
        name, kind = post.get("seasonName", ""), str(post.get("seasonType"))
        name = name if isinstance(name, str) else ""
        stage = "实习" if kind == "3" and "实习" in name else "校招" if kind == "2" and "校园招聘" in name else ""
        if not stage:
            conflicts.append("顺丰详情未明确确认实习或校园招聘项目")
        fact("company", "顺丰集团", "campus.sf-express.com", "official employer host; subsidiaries counted once")
        fact("title", post.get("positionName"), post.get("positionName"), "findById.positionName")
        fact("job_type", stage, f"{name}; seasonType={kind}", "findById.seasonName + seasonType; frontend distinguishes intern type=3")
        fact("updated_date", _day(post.get("updateDate")), post.get("updateDate"), "findById.updateDate; not publication")
        # createDate is record creation, updateDate may be automated refresh. Neither fills publication.
    elif provider == "sdic" and p.hostname == "wecruit.hotjob.cn":
        jid = q.get("postId", [""])[0]
        listing = payload.get("listing")
        listing = listing if isinstance(listing, dict) else {}
        if p.path != f"/{SDIC_TENANT}/pb/posDetail.html" or not jid or str(post.get("postId")) != jid:
            return {}
        # Employer's own website links this exact tenant; never trust another hotjob tenant.
        if listing.get("currentSuiteKey") != SDIC_TENANT[2:] or str(listing.get("postId")) != jid or post.get("company") != "国投证券股份有限公司":
            conflicts.append("国投证券企业租户、列表与详情身份不一致")
        stage = {12: "实习", 2: "校招"}.get(post.get("recruitType"), "")
        route = {"intern": "实习", "campus": "校招"}.get(q.get("postType", [""])[0], "")
        if not stage or stage != route or post.get("recruitType") != listing.get("recruitType"):
            conflicts.append("国投证券列表、详情与路径招聘阶段不一致")
        fact("company", "国投证券", post.get("company"), "official sdicsc.com.cn links exact tenant; group subsidiaries counted once")
        fact("title", post.get("postName"), post.get("postName"), "listPositionDetail.data.postName")
        fact("job_type", stage, str(post.get("recruitType")), "listPositionDetail.recruitType + matched listing and route")
        fact("published_date", _day(post.get("publishFirstDate")), post.get("publishFirstDate"), "listPositionDetail.publishFirstDate (not publishDate refresh or endDate)")
        fact("updated_date", _day(post.get("publishDate")), post.get("publishDate"), "listPositionDetail.publishDate; first publication retained separately")
    else:
        return {}
    return {"adapter": VERSION, "facts": facts, "conflicts": conflicts}


def ky_metadata(html, url):
    p = urlparse(url)
    q = parse_qs(p.query)
    jid = q.get("id", [""])[0]
    if p.scheme != "https" or p.hostname != "www.kysec.cn" or p.path != "/index.php" or q.get("a") != ["showjob"] or not jid:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    header = soup.select_one(".tx-joinc-text")
    link = soup.select_one("a#shenqing")
    if not header or not header.h1 or not link or not soup.title or "开源证券" not in soup.title.get_text():
        return {}
    apply = urlparse(link.get("href", ""))
    aq = parse_qs(apply.query)
    if apply.hostname != "wecruit.hotjob.cn" or not apply.path.startswith("/SU654f2e0b3538bc6c4d600eab/") or aq.get("postId") != [jid]:
        return {}
    stage = "实习" if q.get("catid") == ["851"] and aq.get("postType") == ["intern"] and header.h3 and "实习" in header.h3.get_text() else ""
    facts = {}
    def fact(key, value, span, locator):
        if value:
            facts[key] = {"value": value, "span": span, "locator": locator, "source_url": url}
    fact("company", "开源证券", soup.title.get_text(), "official employer host + HTML title")
    fact("title", header.h1.get_text(strip=True), header.h1.get_text(strip=True), ".tx-joinc-text h1; matched application postId")
    fact("job_type", stage, header.h3.get_text(strip=True) if header.h3 else "", "official detail heading + category + matched application type")
    text = header.get_text(" ", strip=True)
    match = re.search(r"发布时间\s*[：:]\s*(\d{4}-\d{2}-\d{2})", text)
    if match:
        fact("published_date", _day(match[1]), match[0], ".tx-joinc-text 发布时间")
    return {"adapter": VERSION, "facts": facts, "conflicts": [] if stage else ["开源证券详情未明确确认实习招聘阶段"]}


def ky_document(html, url):
    meta = ky_metadata(html, url)
    if meta.get("conflicts") or not meta.get("facts", {}).get("title"):
        raise ValueError("KY official detail identity could not be bound")
    soup = BeautifulSoup(html, "html.parser")
    header, info, tasks = soup.select_one(".tx-joinc-text"), soup.select_one(".tx-joinc-right"), soup.select_one(".tx-contentss")
    if not tasks:
        raise ValueError("KY detail lacks tasks and requirements")
    raw = "公司名称：开源证券\n职位类型：实习\n" + header.get_text("\n", strip=True)
    if info:
        raw += "\n"+info.get_text("\n", strip=True)
    raw += "\n" + tasks.get_text("\n", strip=True)
    return {"raw_text": raw, "html": html, "requested_url": url, "final_url": url,
            "method": "http", "metadata": meta, "adapter_version": VERSION,
            "captured_at": datetime.now(timezone.utc).isoformat(), "publication_note": ""}


def document(payload, url, api_url):
    encoded = json.dumps(payload, ensure_ascii=False)
    meta = metadata(encoded, url)
    facts = meta.get("facts", {})
    if not facts.get("title") or not facts.get("company") or meta.get("conflicts"):
        raise ValueError("Industry source identity/stage could not be bound")
    post = payload["record"]
    provider = payload["provider"]
    if provider == "cicc":
        tasks, reqs = post.get("Duty", ""), post.get("Require", "")
    elif provider == "citics":
        tasks, reqs = post.get("positionDesc", ""), post.get("qualification", "")
    elif provider == "midea":
        inner = post.get("projectPositionDto") or {}
        tasks, reqs = inner.get("jobResponsibility", ""), inner.get("jobRequirement", "")
    elif provider == "sdic":
        tasks = post.get("workContent", "")
        reqs = "学历："+(post.get("education") or "")+"\n专业："+(post.get("subject") or "")+"\n"+(post.get("serviceCondition") or "")
    else:
        tasks, reqs = post.get("postDuty", ""), post.get("jobRequirement", "")
        reqs += "\n" + (post.get("otherRequirement") or "")
    clean = lambda s: BeautifulSoup(s or "", "html.parser").get_text("\n", strip=True)
    labels = [("公司名称", "company"), ("岗位名称", "title"), ("职位类型", "job_type"), ("发布日期", "published_date")]
    raw = "\n".join(f"{label}：{facts[key]['value']}" for label, key in labels if key in facts)
    raw += f"\n【工作职责】\n{clean(tasks)}\n【任职要求】\n{clean(reqs)}"
    if not clean(tasks) or not clean(reqs):
        raise ValueError("Industry source lacks tasks or requirements")
    return {"raw_text": raw, "html": encoded, "requested_url": url, "final_url": url,
            "api_url": api_url, "method": "official_api", "metadata": meta,
            "adapter_version": VERSION, "captured_at": datetime.now(timezone.utc).isoformat(),
            "publication_note": "" if "published_date" in facts else "官方响应未提供岗位发布日期，暂不计入近期证据门槛"}
