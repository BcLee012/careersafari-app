"""可复现的证据匹配：有作品引用 / 课程或自述 / 明确缺口 / 未知。
作品引用只说明提供了可检查材料，不证明真实贡献或能力达到 JD 程度。
"""
from __future__ import annotations
import re

from .curriculum import CURRICULUM, NO_COURSE_NOTE, SOFT_CAPABILITIES, capabilities_of, capability_name, dealbreaker_conflict
from . import families, ranking

CAPABILITY_CATEGORIES = {"技能", "工具", "软能力"}


def _user_cap_index(skills):
    idx = {}
    for s in skills:
        state = getattr(s, "evidence_state", "course_or_self_report")
        artifact = bool(state == "artifact_provided" and s.evidence_text.strip() and
                        getattr(s, "artifact_reference", "").strip())
        status = "gap" if getattr(s, "confirmed_gap", False) else (
                 "matched" if artifact else "unknown" if state == "unknown" else "unverified")
        item = {"status": status, "evidence_text": s.evidence_text, "name": s.name,
                "artifact_reference": getattr(s, "artifact_reference", ""), "rating": s.self_rating,
                "evidence_source": getattr(s, "evidence_source", "self_report")}
        strength = {"unknown": 0, "gap": 1, "unverified": 2, "matched": 3}
        for cid in capabilities_of(s.name):
            if cid not in idx or strength[status] > strength[idx[cid]["status"]]:
                idx[cid] = item
    return idx


def _threshold_check(req, answers):
    text, cat = req.get("text", ""), req.get("category", "")
    status, note = "unknown", "尚不能确定是否满足要求"
    if cat == "学历":
        degree = answers.get("attained_degree", "")
        ranks = {"本科": 1, "硕士": 2, "博士": 3}
        # Listed alternatives denote the minimum, not the highest named degree.
        required = next((ranks[d] for d in ("本科", "硕士", "博士") if d in text), None)
        if "优先" in text:
            return {"req_id": req["req_id"], "text": text, "status": "unknown", "note": "学历优先项，不作为排除条件"}
        if "在读" in text and str(answers.get("grade", "")).startswith("研") and "硕士" in text:
            status, note = "supported", "当前年级与硕士在读要求一致；其他条件仍需确认"
        elif any(w in text for w in ("在校", "在读", "应届", "毕业生")):
            note = "需核对在读或应届资格及毕业时间，不能直接以已取得学历排除"
        elif degree in ranks and required:
            enrolled = str(answers.get("grade", "")).startswith(("大", "研", "博"))
            completed_required = bool(re.search(r"已取得|须取得|必须取得|获得.{0,8}学位|毕业证|学位证", text))
            if ranks[degree] < required and enrolled and not completed_required:
                note = "已取得学历低于文字要求，但岗位未明确是否接受在读/预计毕业；保留资格待确认，不直接排除学生"
            else:
                status = "supported" if ranks[degree] >= required else "conflict"
                note = "已取得学历满足最低学历条件" if status == "supported" else "已取得学历低于明确学历要求（仅限此份JD）"
        else:
            note = "在读年级不能代替已取得学历；需确认岗位是否接受在读或应届"
    elif cat == "专业":
        major = str(answers.get("major", "")).strip()
        if major and major not in ("__unknown__", "__skip__") and major in text:
            status, note = "supported", f"原文明确包含你的专业（{major}）"
        else:
            note = "原文未明确列出该专业；“相关专业”不能自动确认对应关系"
    return {"req_id": req["req_id"], "text": text, "status": status, "note": note}


def _classify(matrix, idx):
    bins = {"matched": [], "unverified": [], "gaps": [], "unknown": []}
    for item in matrix:
        cid = item["capability"]
        u = idx.get(cid)
        status = u["status"] if u else "unknown"
        base = {**item, "is_hard": cid not in SOFT_CAPABILITIES,
                "courses": CURRICULUM.get(cid, []), "remediable_in_program": bool(CURRICULUM.get(cid)),
                "note": "作品引用尚未核实贡献与熟练度" if status == "matched" else (
                        "未提供信息，不能推断能力不足" if status == "unknown" else NO_COURSE_NOTE if not CURRICULUM.get(cid) else "")}
        if u:
            base.update(user_evidence=u["evidence_text"], artifact_reference=u["artifact_reference"],
                        evidence_source=u["evidence_source"])
        bins["gaps" if status == "gap" else status].append(base)
    return bins


def match_position(position, profile, answers):
    """单 JD 检查，用于约束提示与明细；不作为岗位族代表或正式排名。"""
    matrix, thresholds, incomparable = {}, [], []
    for req in position["requirements"]:
        if req.get("confidence") != "external":
            continue
        cat = req.get("category")
        if cat in ("学历", "专业"):
            thresholds.append(_threshold_check(req, answers))
            continue
        caps = capabilities_of(req.get("text", "")) if cat in CAPABILITY_CATEGORIES else []
        if not caps:
            incomparable.append({"req_id": req["req_id"], "text": req["text"], "reason": "信息不足或尚未建立受控对应关系"})
        for cid in caps:
            row = matrix.setdefault(cid, {"capability": cid, "capability_name": capability_name(cid), "evidence": []})
            row["evidence"].append({"req_id": req["req_id"], "text": req["text"], "span": req.get("evidence_span", "")})
    bins = _classify(list(matrix.values()), _user_cap_index(profile.skills))
    raw_tasks = " ".join(position.get("work_scenarios", []) + position.get("stressors", []))
    excluded, reasons, warnings = False, [], []
    for constraint in profile.constraints:
        if constraint.key == "dealbreaker":
            ex, warn = dealbreaker_conflict(constraint.value, raw_tasks)
            if ex:
                excluded = True
                reasons.append(warn)
            elif warn:
                warnings.append(warn)
    for threshold in thresholds:
        if threshold["status"] == "conflict":
            excluded = True
            reasons.append(threshold["note"] + "（仅限此份JD）")
    return {**bins, "position_id": position["position_id"], "canonical_name": position["canonical_name"],
            "excluded": excluded, "exclude_reason": "；".join(reasons), "constraint_warnings": warnings,
            "thresholds": thresholds, "incomparable": incomparable,
            "coverage": None, "verified_coverage": None, "n_comparable": len(matrix)}


def match_all(positions, profile, answers):
    idx = _user_cap_index(profile.skills)
    report = []
    target_stage = answers.get("target_stage") or "实习"
    for family in families.aggregate(positions):
        if family["stage"] != target_stage:
            continue
        checks = [match_position(p, profile, answers) for p in family["members"]]
        allowed = [p for p, check in zip(family["members"], checks) if not check["excluded"]]
        excluded = bool(checks) and not allowed
        # 特定公司工作方式不传播成整个岗位族的必然属性。
        if allowed and len(allowed) != len(family["members"]):
            compatible = families.aggregate(allowed)
            family = compatible[0]
        common = [m for m in family["matrix"] if m["scope"] == "cross_employer"]
        bins = _classify(common, idx) if family["eligible"] and not excluded else {
               "matched": [], "unverified": [], "gaps": [], "unknown": []}
        scenario = [m for m in family["matrix"] if m["scope"] == "scenario_specific"]
        report.append({**family, **bins, "excluded": excluded,
                       "exclude_reason": "；".join(dict.fromkeys(c["exclude_reason"] for c in checks if c["excluded"])),
                       "constraint_warnings": list(dict.fromkeys(w for c in checks for w in c["constraint_warnings"])),
                       "thresholds": [t for c in checks for t in c["thresholds"]],
                       "scenario_specific": scenario, "n_comparable": len(common),
                       "coverage": None, "verified_coverage": None})
    return ranking.rank(report, answers)
