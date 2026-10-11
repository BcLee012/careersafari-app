"""岗位族 × 招聘阶段的多企业画像；不选择最高分 JD 充当整个岗位族。"""
from __future__ import annotations

import calendar
import hashlib
import re
from datetime import date
from .curriculum import JOB_FAMILIES, capabilities_of, capability_name
from .ingestion import PIPELINE_VERSION
from .extractor import _normalize
from urllib.parse import urlparse, parse_qsl
from .taxonomy import boundary_problems

MIN_JDS = 6
MIN_EMPLOYERS = 3
MIN_CAPABILITY_EMPLOYERS = 2


def employer_key(name):
    # 保守别名表只合并明确同一企业，不让 AI 发明独立雇主数量。
    aliases = {"字节跳动": "bytedance", "bytedance": "bytedance", "北京字节跳动科技有限公司": "bytedance",
               "百度": "baidu", "百度公司": "baidu", "baidu": "baidu",
               "美团": "meituan", "meituan": "meituan"}
    name = re.sub(r"\s+", "", name or "").lower()
    return aliases.get(name, name)


def recent(published, today):
    try:
        d = date.fromisoformat(published)
    except (ValueError, TypeError):
        return False
    year = today.year - 2
    cutoff = today.replace(year=year, day=min(today.day, calendar.monthrange(year, today.month)[1]))
    return cutoff <= d <= today


def aggregate(positions, today=None):
    today = today or date.today()
    groups = {}
    known = {f["family"] for f in JOB_FAMILIES}
    for p in positions:
        a = p.get("admission", {})
        # 旧数据保留可浏览，但不能靠标题重新归类后参与匹配。
        if not (a.get("current") and a.get("state") == "ready" and
                a.get("pipeline_version") == PIPELINE_VERSION and a.get("source_checked") and
                a.get("stage") in ("实习", "校招") and a.get("family") in known):
            continue
        if boundary_problems(a.get("family"), p.get("work_scenarios", [])):
            continue
        key = (a["family"], a["stage"])
        group = groups.setdefault(key, {"family": key[0], "stage": key[1], "candidates": {}, "members": [], "seen_urls": set(), "seen_bodies": set()})
        digest = a.get("content_hash")
        if not digest:
            continue
        # 内容去重跨 URL/企业同样生效，避免转载增大证据量。
        parsed = urlparse(a.get("source_url", ""))
        query = tuple(sorted((k,v) for k,v in parse_qsl(parsed.query) if not k.lower().startswith("utm_") and k.lower() not in {"tracking", "ref", "from"}))
        identity_url = (parsed.netloc.lower(), parsed.path.rstrip("/"), query)
        body = _normalize(" ".join(p.get("work_scenarios", []) + [r["text"] for r in p["requirements"]]))
        identity_body = (employer_key(a.get("company")), body)
        if digest in group["candidates"] or identity_url in group["seen_urls"] or identity_body in group["seen_bodies"]:
            continue
        group["seen_urls"].add(identity_url)
        group["seen_bodies"].add(identity_body)
        group["candidates"][digest] = p
        if a.get("company") and recent(a.get("published_date"), today):
            group["members"].append(p)
    out = []
    for key, g in sorted(groups.items()):
        members = g["members"]
        employers = {employer_key(p["admission"]["company"]) for p in members}
        matrix = {}
        employer_totals = {}
        for p in members:
            ek = employer_key(p["admission"]["company"])
            employer_totals[ek] = employer_totals.get(ek, 0) + 1
        for p in members:
            a = p["admission"]
            for r in p["requirements"]:
                if r.get("confidence") != "external" or r.get("category") not in ("技能", "工具", "软能力"):
                    continue
                for cid in capabilities_of(r["text"]):
                    item = matrix.setdefault(cid, {"capability": cid, "capability_name": capability_name(cid),
                                                  "employers": set(), "evidence": [], "jd_weights": {}})
                    ek = employer_key(a["company"])
                    item["employers"].add(ek)
                    # Preferred/bonus requirements remain weaker than ordinary requirements;
                    # hard/soft describes capability type, not mandatory status.
                    strength = 0.5 if re.search(r"优先|加分|加分项|preferred|a plus", r["text"], re.I) else 1.0
                    key_jd = (ek, p["position_id"])
                    item["jd_weights"][key_jd] = max(item["jd_weights"].get(key_jd, 0), strength)
                    item["evidence"].append({"req_id": r["req_id"], "position_id": p["position_id"],
                        "jd_id": p["source_jd_ids"][0], "company": a["company"], "text": r["text"],
                        "span": "\n".join(r.get("evidence_spans") or [r.get("evidence_span", "")]), "source_url": a["source_url"],
                        "published_date": a["published_date"]})
        for item in matrix.values():
            per_employer = {ek: sum(v for (employer, _), v in item["jd_weights"].items() if employer == ek) / n
                            for ek, n in employer_totals.items()}
            item["weight"] = round(sum(per_employer.values()) / len(employers), 6) if employers else 0
            item["weight_basis"] = "各雇主内部去重JD出现频次的均值；优先/加分要求权重减半，工程规则待校准"
            item.pop("jd_weights")
            item["employer_count"] = len(item.pop("employers"))
            item["scope"] = "cross_employer" if item["employer_count"] >= MIN_CAPABILITY_EMPLOYERS else "scenario_specific"
        common = [i for i in matrix.values() if i["scope"] == "cross_employer"]
        reasons = []
        if len(members) < MIN_JDS:
            reasons.append(f"近24个月有效去重 JD {len(members)}/{MIN_JDS} 份")
        if len(employers) < MIN_EMPLOYERS:
            reasons.append(f"独立企业 {len(employers)}/{MIN_EMPLOYERS} 家")
        if not common:
            reasons.append("尚无至少两家企业支持的可比对能力")
        out.append({"family_id": "FAM-" + hashlib.sha256('|'.join(key).encode()).hexdigest()[:12],
                    "family": g["family"], "stage": g["stage"], "candidate_count": len(g["candidates"]),
                    "jd_count": len(members), "employer_count": len(employers),
                    "eligible": not reasons, "gate_reasons": reasons, "matrix": list(matrix.values()),
                    "members": members,
                    "note": "6份JD/3家企业为团队运营门槛，不证明行业代表性；共现不等于行业必备。"})
    return out
