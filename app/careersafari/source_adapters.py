"""Official API records with retained response fields and conservative identity binding."""
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

VERSION = "official-api-1"


def _date(millis):
    if not isinstance(millis, (int, float)) or not 946684800000 < millis < 4102444800000:
        return ""
    return datetime.fromtimestamp(millis / 1000, timezone.utc).date().isoformat()


def byte_stage(post):
    node = post.get("recruit_type") or {}
    parent = node.get("parent") or {}
    # Social intern remains outside the selected campus population.
    if parent.get("name") == "社招":
        return "社招"
    if parent.get("name") != "校招":
        return ""
    if node.get("name") == "实习":
        return "实习"
    if node.get("name") == "正式":
        return "校招"
    return ""


def api_metadata(encoded, url):
    try:
        payload = json.loads(encoded)
    except (ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    p = urlparse(url)
    if p.scheme != "https":
        return {}
    facts, conflicts = {}, []
    def fact(key, value, span, locator):
        if isinstance(value, str) and value:
            facts[key] = {"value": value, "span": str(span), "locator": locator, "source_url": url}
    if p.hostname == "jobs.bytedance.com" and payload.get("provider") == "bytedance":
        m = re.fullmatch(r"/campus/position/(\d+)/detail/?", p.path)
        post = payload.get("record") or {}
        if not m or str(post.get("id")) != m[1]:
            return {}
        fact("company", "字节跳动", "jobs.bytedance.com", "official host identity")
        fact("title", post.get("title"), post.get("title"), "API job_post_list[].title")
        fact("job_type", byte_stage(post), json.dumps(post.get("recruit_type"), ensure_ascii=False), "API recruit_type.name + parent.name")
        fact("published_date", _date(post.get("publish_time")), post.get("publish_time"), "API publish_time, milliseconds UTC")
    elif p.hostname == "campus.jd.com" and payload.get("provider") == "jd":
        # This is the exact official detail API URL (POST); public landing URL is recorded separately.
        m = re.fullmatch(r"/api/wx/position/detail/(\d+)", p.path)
        post, listing = payload.get("detail") or {}, payload.get("listing") or {}
        if not m or str(post.get("publishId")) != m[1] or post.get("publishId") != listing.get("publishId"):
            return {}
        if post.get("reqId") != listing.get("reqId") or post.get("positionName") != listing.get("positionName"):
            conflicts.append("京东列表与详情的岗位身份不一致")
        fact("company", "京东", "campus.jd.com", "official host identity; group subsidiaries counted once")
        fact("title", post.get("positionName"), post.get("positionName"), "detail.body.positionName")
        stage = {"应届生": "校招", "实习生": "实习", "实习": "实习"}.get(post.get("recruitType"), "")
        fact("job_type", stage, post.get("recruitType"), "detail.body.recruitType")
        if not conflicts:
            fact("published_date", _date(listing.get("publishTime")), listing.get("publishTime"), "matched listing.body.items[].publishTime, milliseconds UTC")
    elif p.hostname == "zhaopin.meituan.com" and payload.get("provider") == "meituan":
        post = payload.get("record") or {}
        jid = parse_qs(p.query).get("jobUnionId", [""])[0]
        if p.path != "/web/position/detail" or not jid or str(post.get("jobUnionId")) != jid:
            return {}
        fact("company", "美团", "zhaopin.meituan.com", "official host identity")
        fact("title", post.get("name"), post.get("name"), "detail.data.name")
        # Values confirmed against official frontend constants (1 应届, 2 实习, 3 社招).
        stage = {"1": "校招", "2": "实习", "3": "社招"}.get(str(post.get("jobType")), "")
        fact("job_type", stage, post.get("jobType"), "detail.data.jobType; official frontend enum")
        fact("published_date", _date(post.get("firstPostTime")), post.get("firstPostTime"), "detail.data.firstPostTime, milliseconds UTC")
        fact("updated_date", _date(post.get("refreshTime")), post.get("refreshTime"), "detail.data.refreshTime, milliseconds UTC")
    else:
        return {}
    return {"adapter": VERSION, "facts": facts, "conflicts": conflicts}


def document(payload, url, api_url):
    encoded = json.dumps(payload, ensure_ascii=False)
    metadata = api_metadata(encoded, url)
    facts = metadata.get("facts", {})
    if not facts.get("title") or not facts.get("company"):
        raise ValueError("Official record identity could not be bound")
    if payload["provider"] == "bytedance":
        p = payload["record"]
        tasks, requirements = p.get("description", ""), p.get("requirement", "")
    elif payload["provider"] == "jd":
        p = payload["detail"]
        tasks, requirements = p.get("workContent", ""), p.get("qualification", "")
    else:
        p = payload["record"]
        tasks, requirements = p.get("jobDuty", ""), p.get("jobRequirement", "")
        if p.get("precedence"):
            requirements += "\n【优先条件】\n" + p["precedence"]
    fields = [("公司名称", "company"), ("岗位名称", "title"), ("职位类型", "job_type"), ("发布日期", "published_date")]
    text = "\n".join(f"{label}：{facts[key]['value']}" for label, key in fields if key in facts)
    text += f"\n【工作职责】\n{tasks}\n【任职要求】\n{requirements}"
    return {"raw_text": text, "html": encoded, "final_url": url, "requested_url": url,
            "api_url": api_url, "method": "official_api", "metadata": metadata,
            "adapter_version": VERSION, "captured_at": datetime.now(timezone.utc).isoformat()}
