"""把岗位族证据匹配结果转成探索报告，缺少信息时不输出能力不足结论。"""
from __future__ import annotations
from . import onet
from .coverage import ONET_CROSSWALK


def build_advice(report, profile, answers):
    positions, candidates, learning, actions = [], [], {}, []
    counts = {k: set() for k in ("matched", "unverified", "gaps", "unknown")}
    exclusions = []
    for r in report:
        if r["excluded"]:
            exclusions.append({"name": r["family"], "reason": r["exclude_reason"]})
            continue
        if not r["eligible"]:
            candidates.append({"job_family": r["family"], "stage": r["stage"],
                               "jd_count": r["jd_count"], "employer_count": r["employer_count"],
                               "candidate_count": r["candidate_count"], "reasons": r["gate_reasons"],
                               "occupational_reference": onet.family_context(r["family"])})
            continue
        for k in counts:
            counts[k].update(i["capability"] for i in r[k])
        overlap = len(r["matched"]) + len(r["unverified"])
        verdict = ("有探索依据" if r.get("ranking", {}).get("has_professional_evidence") else "通用能力有交集") if overlap else "需补充信息"
        why = [f"{r['jd_count']} 份有效 JD，来自 {r['employer_count']} 家企业；按多份原文汇总。",
               f"提供作品引用 {len(r['matched'])} 项，课程或自述 {len(r['unverified'])} 项，"
               f"未知 {len(r['unknown'])} 项，明确缺口 {len(r['gaps'])} 项。"]
        why.append("按各企业均衡支持的能力要求比较证据，避免要求写得多的方向自动排在前面。")
        if verdict == "通用能力有交集":
            why.append("目前交集来自沟通、协作、表达或英语等通用能力；需要补充该方向的专业/工具经历，才能形成方向判断。")
        if r.get("ranking", {}).get("direction_preference"):
            why.append("与你填写的培养方向兴趣有交集；该对应为待验证的本地映射。")
        if r.get("ranking", {}).get("diversity_adjusted"):
            why.append("与前一方向证据接近，同时提供不同的能力组合供你比较。")
        uncertain = [t for t in r["thresholds"] if t["status"] != "supported"]
        if uncertain:
            why.append("部分学历、专业或其他资格条件尚需确认；本报告不保证符合投递条件。")
        positions.append({"rank": len(positions) + 1, "position_id": r["family_id"],
            "name": r["family"], "job_family": r["family"], "stage": r["stage"], "verdict": verdict,
            "why": why + r["constraint_warnings"], "family_size": r["jd_count"], "employer_count": r["employer_count"],
            "n_comparable": r["n_comparable"], "coverage": None, "verified_coverage": None,
            "matrix": [{**item, "status": k} for k in counts for item in r[k]],
            "scenario_specific": r["scenario_specific"], "thresholds": r["thresholds"],
            "note": r["note"], "ranking": r.get("ranking", {}),
            "occupational_reference": onet.family_context(r["family"])})
        for g in r["gaps"]:
            cid = g["capability"]
            entry = learning.setdefault(cid, {**g, "needed_by": [], "req_ids": []})
            if r["family"] not in entry["needed_by"]:
                entry["needed_by"].append(r["family"])
            entry["req_ids"] += [e["req_id"] for e in g["evidence"]]
    seen_families = {r["family"] for r in report}
    for family in ONET_CROSSWALK:
        if family not in seen_families:
            candidates.append({"job_family": family, "stage": answers.get("target_stage") or "实习",
                               "jd_count": 0, "employer_count": 0, "candidate_count": 0,
                               "reasons": ["尚无通过当前检查的近期岗位证据"],
                               "occupational_reference": onet.family_context(family)})
    positions = positions[:3]
    # 推荐所展示方向中先补信息/补证；未知不能直接变成补课任务。
    seen = set()
    for p in positions:
        for item in sorted(p["matrix"], key=lambda i: (i["status"] != "unknown", i["capability"])):
            if item["capability"] in seen or item["status"] not in ("unknown", "unverified"):
                continue
            seen.add(item["capability"])
            actions.append({"action": f"补充「{item['capability_name']}」的经历：做过什么、负责哪一步、产出是什么；没有经历可明确说明。",
                            "validates": "区分未填写、学习过和实际做过，不预判能力不足",
                            "cost": "填写已有经历与可分享的作品引用",
                            "source": "、".join(e["req_id"] for e in item["evidence"][:3])})
    learning_rows = list(learning.values())
    for item in learning_rows[:3]:
        actions.append({"action": f"为明确缺口「{item['capability_name']}」安排一个小型练习，再检查产出。",
                        "validates": "检查是否开始形成可展示的实践材料",
                        "cost": "按能力选择小型任务", "source": "、".join(item["req_ids"][:3])})
    if not positions:
        headline = ("当前尚无满足证据门槛的岗位画像，暂不做正式匹配。"
                    "你的画像已保留；候选方向可浏览，需补足有效 JD、企业来源或发布日期。")
    elif all(p["verdict"] == "需补充信息" for p in positions):
        headline = "已有可比对的岗位画像，但个人信息不足。先补充经历，再形成探索方向。"
    elif not any(p["verdict"] == "有探索依据" for p in positions):
        headline = "目前只发现通用能力交集，尚不足以确定探索方向。请补充专业、工具或岗位任务相关的经历。"
    else:
        names = "、".join(p["name"] for p in positions if p["verdict"] == "有探索依据")
        headline = f"可以优先探索：{names}。依据是已提供经历与岗位要求的交集，尚不能判断熟练度或录用概率。"
    return {"headline": headline, "positions": positions, "candidate_families": candidates,
            "coverage_note": "结论仅在已有足够证据的岗位画像中比较；缺证方向尚未参与，不能据此确定全部职业中的最优选择。",
            "learning_plan": learning_rows, "learning_hard": [i for i in learning_rows if i["is_hard"]],
            "learning_soft": [i for i in learning_rows if not i["is_hard"]], "two_week_actions": actions[:6],
            "evidence_strength": {"level": "待核实" if counts["matched"] else "信息待补充",
                                  "note": "作品引用不等于已验证掌握，未知不等于缺口。",
                                  **{k: len(v) for k, v in counts.items()}},
            "excluded": exclusions, "generated_by": "deterministic", "llm_status": "not_needed"}


def polish_with_llm(advice):
    """保留旧调用接口；模板已能输出可读报告，不为措辞重复消耗模型额度。"""
    return advice
