"""Cold-start content ranking. Values describe evidence, never ability or hiring probability."""
from __future__ import annotations
from .coverage import directions_for

VERSION = "content-evidence-1"
SELF_REPORT_WEIGHT = 0.5  # Engineering prior, not a validated psychometric coefficient.
DIVERSITY_WINDOW = 0.08
GENERAL_CAPABILITIES = {"COMM", "COLLAB", "PRESSURE", "ENGLISH", "WRITING"}


def explain_score(row, answers):
    groups = {k: row.get(k, []) for k in ("matched", "unverified", "gaps", "unknown")}
    weights = {k: sum(x.get("weight", 1.0) for x in v) for k, v in groups.items()}
    total = sum(weights.values())
    lower = (weights["matched"] + SELF_REPORT_WEIGHT * weights["unverified"]) / total if total else 0.0
    upper = min(1.0, lower + (weights["unknown"] + (1-SELF_REPORT_WEIGHT)*weights["unverified"]) / total) if total else 1.0
    preference = answers.get("direction_interest") in directions_for(row["family"])
    specific = any(x["capability"] not in GENERAL_CAPABILITIES for k in ("matched", "unverified") for x in groups[k])
    return {"version": VERSION, "evidence_lower": round(lower, 6), "evidence_upper": round(upper, 6),
            "known_weight": round(total-weights["unknown"], 6), "total_weight": round(total, 6),
            "explicit_gap_weight": round(weights["gaps"], 6), "direction_preference": preference,
            "has_personal_evidence": bool(groups["matched"] or groups["unverified"]),
            "has_professional_evidence": specific,
            "interpretation": "按雇主平衡后的要求权重，比较个人提供的证据；区间表示信息不完整，不是能力分或录用概率。",
            "unmeasured": ["实际熟练度", "证据真实性与个人贡献", "价值观与工作方式的本地岗位依据"],
            "parameters_status": "工程初始规则，待真实样本校准"}


def _similarity(a, b):
    ca = {m["capability"] for m in a["matrix"] if m["scope"] == "cross_employer"}
    cb = {m["capability"] for m in b["matrix"] if m["scope"] == "cross_employer"}
    return len(ca & cb) / len(ca | cb) if ca | cb else 0.0


def rank(rows, answers):
    for row in rows:
        row["ranking"] = explain_score(row, answers)
    ordered = sorted(rows, key=lambda r: (r["excluded"], not r["eligible"],
        not r["ranking"]["has_professional_evidence"], not r["ranking"]["has_personal_evidence"], -r["ranking"]["evidence_lower"],
        not r["ranking"]["direction_preference"], r["family"]))
    ranked, pool = [], list(ordered)
    while pool:
        first = pool[0]
        # Diversity only among near-ties with actual local AND personal evidence.
        options = [first]
        if ranked and first["eligible"] and not first["excluded"] and first["ranking"]["has_personal_evidence"]:
            options = [r for r in pool if r["eligible"] and not r["excluded"]
                       and r["ranking"]["has_personal_evidence"]
                       and r["ranking"]["has_professional_evidence"] == first["ranking"]["has_professional_evidence"]
                       and r["ranking"]["direction_preference"] == first["ranking"]["direction_preference"]
                       and first["ranking"]["evidence_lower"]-r["ranking"]["evidence_lower"] <= DIVERSITY_WINDOW]
        chosen = min(options, key=lambda r: (max((_similarity(r, s) for s in ranked), default=0),
                                             -r["ranking"]["evidence_lower"], r["family"]))
        chosen["ranking"]["diversity_adjusted"] = chosen is not first
        ranked.append(chosen); pool.remove(chosen)
    return ranked
