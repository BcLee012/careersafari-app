"""
确定性匹配引擎：用户画像 × 岗位要求 → 差距报告。

为什么不让 LLM 做这一步：
  匹配是这套产品的立论基础。如果用 LLM 匹配，同一个用户两次提交可能得到不同结果，
  且无法解释「为什么说我缺 SQL」。确定性规则可复现、可测试、可逐条解释——
  「为什么推荐这个」必须有真实答案。

输出四类 + 一个诚实的覆盖率：
  匹配（有证据）  岗位要求的能力，用户有可验证证据
  待验证（仅自述）岗位要求的能力，用户自述会但无证据
  缺口           岗位要求的能力，用户完全没有
  无法比对        需求无法归入任何能力（如「有实习经历优先」这类经验要求）

覆盖率公式公开透明，并明确标注为**团队判断**——不伪装成客观度量。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .curriculum import (CURRICULUM, NO_COURSE_NOTE, SOFT_CAPABILITIES,
                         capability_name, capabilities_of, dealbreaker_conflict,
                         job_family)

# 只对这些类别的需求做能力比对。学历/专业/证书/经验走单独的门槛检查。
CAPABILITY_CATEGORIES = {"技能", "工具", "软能力"}


def _user_cap_index(skills: List[Any]) -> Dict[str, Dict[str, Any]]:
    """capability_id -> {verified, evidence_text, name, rating}"""
    idx: Dict[str, Dict[str, Any]] = {}
    for s in skills:
        for cid in capabilities_of(s.name):
            cur = idx.get(cid)
            # 同一能力多处自述时，取证据最强的那条
            if cur is None or (s.verified and not cur["verified"]):
                idx[cid] = {"verified": bool(s.verified),
                            "evidence_text": s.evidence_text,
                            "name": s.name, "rating": s.self_rating}
    return idx


def _threshold_check(req: Dict[str, Any], answers: Dict[str, Any]) -> Optional[str]:
    """学历/专业类硬门槛的确定性检查。返回提示或 None。"""
    text = str(req.get("text", ""))
    cat = req.get("category", "")
    if cat == "学历":
        if "硕士" in text and str(answers.get("grade", "")).startswith("研"):
            return "你的年级满足硕士学历要求。"
        if "博士" in text:
            return "该岗位要求博士学历，与你当前阶段不符。"
        if "本科" in text and str(answers.get("grade", "")).startswith("研"):
            return "该岗位本科即可，你的学历高于门槛。"
    if cat == "专业":
        major = str(answers.get("major", ""))
        if major and (major in text or "相关专业" in text):
            return f"你的专业（{major}）在该岗位的专业要求范围内。"
    return None


def match_position(position: Dict[str, Any], profile, answers: Dict[str, Any]) -> Dict[str, Any]:
    """对单个岗位做完整匹配。position 为 db.list_positions() 返回的结构。"""
    skills_idx = _user_cap_index(profile.skills)
    jd_text = " ".join(str(r.get("text", "")) for r in position["requirements"])

    matched, unverified, gaps, incomparable, thresholds = [], [], [], [], []

    for req in position["requirements"]:
        cat = req.get("category", "")
        if cat in ("学历", "专业"):
            note = _threshold_check(req, answers)
            if note:
                thresholds.append({"req_id": req["req_id"], "text": req["text"], "note": note})
            continue
        if cat not in CAPABILITY_CATEGORIES:
            incomparable.append({"req_id": req["req_id"], "text": req["text"],
                                 "reason": f"类别「{cat}」不走能力比对（经验/证书类需人工判断）"})
            continue

        caps = capabilities_of(req.get("text", ""))
        if not caps:
            incomparable.append({"req_id": req["req_id"], "text": req["text"],
                                 "reason": "无法归入既有能力 Taxonomy，未做比对"})
            continue

        # 一条需求可能要求多项能力（「熟练使用 Excel 与 SQL」），逐项分别比对
        for cid in caps:
            u = skills_idx.get(cid)
            base = {"req_id": req["req_id"], "capability": cid,
                    "capability_name": capability_name(cid),
                    "requirement": req["text"],
                    "evidence_span": req.get("evidence_span", ""),
                    "confidence": req.get("confidence", "external"),
                    "is_hard": cid not in SOFT_CAPABILITIES}

            if u and u["verified"]:
                matched.append({**base, "user_evidence": u["evidence_text"]})
            elif u:
                unverified.append({**base, "user_rating": u["rating"]})
            else:
                courses = CURRICULUM.get(cid, [])
                gaps.append({**base, "courses": courses,
                             "remediable_in_program": bool(courses),
                             "note": "" if courses else NO_COURSE_NOTE})

    comparable = len(matched) + len(unverified) + len(gaps)
    coverage = round((len(matched) + len(unverified)) / comparable, 3) if comparable else None
    verified_coverage = round(len(matched) / comparable, 3) if comparable else None

    # 硬约束排除（hard 命中才排除；soft 只提示）
    excluded, exclude_reason = False, ""
    constraint_warnings: List[str] = []
    for c in profile.constraints:
        if c.key == "dealbreaker":
            ex, warn = dealbreaker_conflict(c.value, jd_text)
            if ex:
                excluded, exclude_reason = True, warn
                break
            if warn:
                constraint_warnings.append(warn)

    # 未知项的关联影响
    unknown_impact = []
    gap_caps = {g["capability"] for g in gaps}
    for u in profile.unknowns:
        for cid in capabilities_of(u.field):
            if cid in gap_caps:
                unknown_impact.append({"unknown": u.field, "capability": capability_name(cid)})

    # 只在该岗位出现、用户却未提及的能力里，挑可补课的作为行动项
    actions = [g for g in gaps if g["remediable_in_program"]]

    return {
        "position_id": position["position_id"],
        "canonical_name": position["canonical_name"],
        "job_family": job_family(position["canonical_name"]),
        "industry": position["industry"],
        "excluded": excluded,
        "exclude_reason": exclude_reason,
        "matched": matched,
        "unverified": unverified,
        "gaps": gaps,
        "incomparable": incomparable,
        "thresholds": thresholds,
        "unknown_impact": unknown_impact,
        "constraint_warnings": constraint_warnings,
        "n_actions": len(actions),
        "coverage": coverage,
        "verified_coverage": verified_coverage,
        "n_comparable": comparable,
        "score_note": ("coverage =（匹配 + 待验证）/ 可比对需求数；"
                       "verified_coverage = 匹配 / 可比对需求数。"
                       "两者都是**团队定义的启发式指标**，不是标准化测评分数。"),
    }


def match_all(positions: List[Dict[str, Any]], profile, answers: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = [match_position(p, profile, answers) for p in positions]
    # 未被硬约束排除的排前面；同组内按「已验证覆盖率」降序
    out.sort(key=lambda r: (r["excluded"], -(r["verified_coverage"] or 0)))
    return out
