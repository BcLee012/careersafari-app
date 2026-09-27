"""
建议生成层。

分层原则（这是本产品的核心方法论，不是装饰）：
  第 1 层  确定性计算 —— match.py 算出的差距。可复现、可逐条解释。
  第 2 层  模板化叙述 —— 把差距翻译成用户能行动的话。仍然是确定性的。
  第 3 层  LLM 润色（可选）—— 只允许引用前两层给出的 item，
           生成后校验所有被引用的 req_id / course code 真实存在，
           否则降级回第 2 层。绝不放大 LLM 的决策权。

为什么这样分：如果让 LLM 直接生成建议，它会把不存在说成存在、
把「团队判断」说成客观事实、并且每次结果都不一样。前两层把事实钉死，
第三层只负责措辞。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from . import llm
from .curriculum import capability_name, job_family

ADVICE_SYSTEM = """你是职业规划建议的撰写者。用户会给你一份**已经算好的差距报告**（JSON）。
你的任务只是把它写成通顺的中文建议。

硬性规则：
1. 只能使用报告里出现的item。禁止引入报告外的任何岗位、课程、数据或市场事实。
2. 提到一门课时，必须原样使用报告给出的课程名与开课学期。
3. 提到一项能力缺口时，必须说明它来自哪个岗位的哪条要求。
4. 报告里标注「团队判断」的，你必须原样保留这个标注，不许改成客观陈述。
5. 不允许说「你适合」「你有天赋」这类无法从报告推出的断言。
6. 输出 JSON：{"narrative": "..."}，不要输出别的内容。"""


# ---------------------------------------------------------------- 第 2 层：确定性叙述


def build_advice(report: List[Dict[str, Any]], profile, answers: Dict[str, Any]) -> Dict[str, Any]:
    all_report = report
    # 未排除的原始岗位（保留全部岗位的缺口信息，供能力去重与缺口统计使用）
    viable_report = [r for r in report if not r["excluded"]]

    # ---- 岗位结论 ----
    # 两个必须先处理的问题：
    # 1) 同一岗位族只保留代表。否则「商业分析师/战略商业分析师/商业分析Leader」
    #    会占掉前三名，用户看到同一个方向的三张脸。
    # 2) 小样本陷阱：只有 2 条可比对需求的岗位，2 条全中就得到 100% 覆盖率，
    #    从而打败有 15 条要求、缺 3 条的大样本岗位。这不是"证据更充分"，是题目更少。
    #    因此代表选取要求 n_comparable >= MIN_COMPARABLE，否则该族标记「样本不足」。
    MIN_COMPARABLE = 5
    by_family: Dict[str, List[Dict[str, Any]]] = {}
    for r in report:
        by_family.setdefault(r.get("job_family", "其他"), []).append(r)

    family_rows: List[Dict[str, Any]] = []
    for fam, rows in by_family.items():
        big = [r for r in rows if r["n_comparable"] >= MIN_COMPARABLE]
        pool = big or rows
        rep = max(pool, key=lambda r: (r["verified_coverage"] or 0, r["n_comparable"]))
        family_rows.append({
            "job_family": fam, "rep": rep,
            "family_size": len(rows),
            "n_big_sample": len(big),
            "excluded": all(r["excluded"] for r in rows),
            "exclude_reason": next((r["exclude_reason"] for r in rows if r["excluded"]), ""),
            # 同族多个岗位会触发同一条软性提示，去重后再截断
            "constraint_warnings": list(dict.fromkeys(
                w for r in rows for w in r.get("constraint_warnings", [])))[:2],
        })
    family_rows.sort(key=lambda x: (x["excluded"],
                                    -(x["rep"]["verified_coverage"] or 0)))

    positions = []
    for i, fr in enumerate(family_rows, start=1):
        r = fr["rep"]
        thin = r["n_comparable"] < MIN_COMPARABLE
        if fr["excluded"]:
            verdict = "已排除"
        elif thin:
            verdict = "样本不足"
        elif r["verified_coverage"] >= 0.6 and not r["gaps"]:
            verdict = "证据充分"
        elif r["verified_coverage"] >= 0.4:
            verdict = "可行，需补证"
        else:
            verdict = "差距较大"
        why = []
        if fr["excluded"]:
            why.append(fr["exclude_reason"])
        elif thin:
            why.append(f"该族代表岗位仅 {r['n_comparable']} 条需求可比对，样本太少，"
                       f"不足以判断匹配度（族内 {fr['family_size']} 个岗位，"
                       f"其中 {fr['n_big_sample']} 个达到可比对门槛）。")
        else:
            why.append(f"{len(r['matched'])} 条有证据，{len(r['unverified'])} 条仅自述，"
                       f"{len(r['gaps'])} 条缺口（代表岗位 {r['canonical_name']}）")
        why += fr["constraint_warnings"]
        positions.append({
            "rank": i, "position_id": r["position_id"], "name": r["canonical_name"],
            "job_family": fr["job_family"], "industry": r["industry"],
            "verdict": verdict, "why": why,
            "coverage": r["coverage"], "verified_coverage": r["verified_coverage"],
            "family_size": fr["family_size"], "n_comparable": r["n_comparable"],
        })

    viable = [p for p in positions if p["verdict"] != "已排除"]
    excluded = [p for p in positions if p["verdict"] == "已排除"]

    # 学习建议：跨岗位汇总缺口，按「被几个岗位需要」排序
    gap_index: Dict[str, Dict[str, Any]] = {}
    for r in viable_report:
        for g in r["gaps"]:
            cid = g["capability"]
            if cid not in gap_index:
                gap_index[cid] = {"capability": cid, "capability_name": capability_name(cid),
                                  "needed_by": [], "courses": g["courses"],
                                  "remediable_in_program": g["remediable_in_program"],
                                  "note": g["note"], "req_ids": [],
                                  "is_hard": g.get("is_hard", True)}
            if r["canonical_name"] not in gap_index[cid]["needed_by"]:
                gap_index[cid]["needed_by"].append(r["canonical_name"])
            gap_index[cid]["req_ids"].append(g["req_id"])

    # 排序：被越多岗位需要 > 硬要求 > 软要求 > 培养方案内能否补
    ordered = sorted(gap_index.values(),
                     key=lambda x: (any(c.get("mandatory") for c in x["courses"]),
                                    -len(x["needed_by"]), not x["is_hard"],
                                    not x["remediable_in_program"]))
    # 拆两组：能靠选课补的硬缺口 / 只能靠实践获得的软能力。
    # 混在一起会把「补沟通能力」排到「补 SQL」前面——听起来合理，实际不可执行。
    learning = ordered
    learning_hard = [x for x in ordered if x["is_hard"]]
    learning_soft = [x for x in ordered if not x["is_hard"]]

    # 两周行动项：从学习建议 + 待验证项 + 未知项派生，全部具体可执行
    actions: List[Dict[str, str]] = []
    for item in [x for x in learning if x["is_hard"]][:3]:
        if item["remediable_in_program"]:
            c = item["courses"][0]
            actions.append({
                "action": f"补「{item['capability_name']}」：选《{c['course']}》"
                          f"（{c['semester']} · {c['teacher']} · {c['credits']} 学分）",
                "validates": f"是否真的需要/愿意投入该能力——被 {len(item['needed_by'])} 个目标岗位要求",
                "cost": "一学期课程投入",
                "source": f"req_ids: {', '.join(item['req_ids'][:3])}",
            })
        else:
            actions.append({
                "action": f"补「{item['capability_name']}」：培养方案内无对应课程，"
                          f"需通过课程项目/竞赛/实习中的真实协作获得",
                "validates": f"被 {len(item['needed_by'])} 个目标岗位要求，但无法靠选课解决",
                "cost": "一个完整项目周期",
                "source": f"req_ids: {', '.join(item['req_ids'][:3])}",
            })

    seen_caps = {a.get("capability") for a in actions}
    for r in viable_report:
        for u in r["unverified"]:
            if u["capability"] in seen_caps:
                continue
            seen_caps.add(u["capability"])
            actions.append({
                "capability": u["capability"],
                "action": f"把「{u['capability_name']}」从仅自述变成有证据："
                          f"两周内产出一个可展示的成果（项目/报告/作品）",
                "validates": f"你自评会这门能力，但没有任何证据支撑；"
                             f"{r['canonical_name']} 岗位要求它",
                "cost": "约 10–15 小时",
                "source": f"req_id: {u['req_id']}",
            })

    for u in profile.unknowns[:3]:
        actions.append({
            "action": f"验证未知项「{u.field}」：找一位在读/从业的学长做一次 30 分钟访谈",
            "validates": "把 unknown 转成 user_stated，降低整体判断的不确定性",
            "cost": "一次约谈",
            "source": "unknowns",
        })

    # 按能力去重：同一能力被 3 个岗位要求，不等于 3 个缺口
    caps_matched, caps_unverified, caps_gap = set(), set(), set()
    for r in viable_report:
        for m in r["matched"]:
            caps_matched.add(m["capability"])
        for u in r["unverified"]:
            caps_unverified.add(u["capability"])
        for g in r["gaps"]:
            caps_gap.add(g["capability"])
    n_matched, n_unverified, n_gaps = len(caps_matched), len(caps_unverified), len(caps_gap)

    # 证据强度评级 —— 把「诚实性」本身变成用户可用的结论
    total = n_matched + n_unverified + n_gaps
    ratio = (n_matched / total) if total else 0
    if total == 0:
        strength, strength_note = "无法评估", "没有可比对的能力要求。"
    elif ratio >= 0.6:
        strength, strength_note = "较高", f"{total} 条可比对要求中 {n_matched} 条有可验证证据。"
    elif ratio >= 0.3:
        strength, strength_note = "中等", (f"{total} 条中仅 {n_matched} 条有证据，"
                                          f"{n_unverified} 条是你的自述——建议先补证再下判断。")
    else:
        strength, strength_note = "低", (f"{total} 条中仅 {n_matched} 条有证据。"
                                        f"当前结论主要建立在自述之上，参考价值有限。")

    headline = _headline(viable, excluded, strength, all_report)

    return {
        "headline": headline,
        "positions": positions,
        "learning_plan": learning,
        "learning_hard": learning_hard,
        "learning_soft": learning_soft,
        "two_week_actions": actions[:6],
        "evidence_strength": {"level": strength, "note": strength_note,
                              "matched": n_matched, "unverified": n_unverified, "gaps": n_gaps},
        "excluded": [{"name": r["canonical_name"], "reason": r["exclude_reason"]}
                     for r in excluded],
        "generated_by": "deterministic",
    }


def _headline(viable: List[Dict[str, Any]], excluded: List[Dict[str, Any]],
              strength: str, all_report: Optional[List[Dict[str, Any]]] = None) -> str:
    if not viable:
        return ("所有候选岗位都被你的硬约束排除。这不是坏消息——它说明约束在起作用。"
                "建议先放松一条最刚性的约束，再重新评估。")
    best = viable[0]
    fam = best.get("job_family", "其他")
    n_fam = sum(1 for x in (all_report or viable) if x.get("job_family") == fam)
    if excluded:
        return (f"{len(excluded)} 个岗位族被你的硬约束排除；剩余 {len(viable)} 个方向中，"
                f"「{fam}」的证据基础相对最好"
                f"（代表岗位 {best.get('name')}，已验证覆盖率 {best.get('verified_coverage')}）。")
    return (f"在 {len(viable)} 个候选方向中，「{fam}」当前证据最充分"
            f"（代表岗位 {best.get('name')}，已验证覆盖率 {best.get('verified_coverage')}）。"
            f"你的整体证据强度：{strength}。")


# ---------------------------------------------------------------- 第 3 层：LLM 润色（可选）


def polish_with_llm(advice: Dict[str, Any]) -> Dict[str, Any]:
    """
    用 LLM 把确定性结论改写得更易读。失败或无 key 时原样返回第 2 层结果。
    生成后会校验：LLM 提到的课程名/req_id 必须存在于原报告中，否则丢弃其输出。
    """
    if not llm.is_configured():
        advice["generated_by"] = "deterministic"
        advice["llm_status"] = "not_configured"
        return advice

    import json
    allowed_courses = set()
    allowed_reqs = set()
    for item in advice["learning_plan"]:
        for c in item["courses"]:
            allowed_courses.add(c["course"])
        allowed_reqs.update(item["req_ids"])
    for a in advice["two_week_actions"]:
        for c in re.findall(r"《([^》]+)》", a["action"]):
            allowed_courses.add(c)
        for r in re.findall(r"REQ-\d+", a.get("source", "")):
            allowed_reqs.add(r)

    payload = {
        "headline": advice["headline"],
        "positions": advice["positions"],
        "learning_plan": [{"capability_name": i["capability_name"],
                           "needed_by": i["needed_by"],
                           "courses": i["courses"],
                           "remediable_in_program": i["remediable_in_program"]}
                          for i in advice["learning_plan"][:5]],
        "two_week_actions": advice["two_week_actions"],
        "evidence_strength": advice["evidence_strength"],
        "excluded": advice["excluded"],
    }

    # 润色只是改写文字，不需要深度推理：低 effort 能显著减少 reasoning token 占用
    # 润色失败不是故障：确定性引擎本就是主路径，LLM 只负责措辞。
    # 但要区分「服务不可用」和「这次没生成成功」——后者放大 token 上限重试一次再降级。
    narrative = ""
    last_err = ""
    for mt in (4096, 8192):
        r = llm.chat(ADVICE_SYSTEM, json.dumps(payload, ensure_ascii=False),
                     temperature=0.3, effort="low", max_tokens=mt)
        if not r["ok"]:
            last_err = r["error"]
            break
        data = llm.parse_json_loose(r["content"])
        if isinstance(data, dict) and str(data.get("narrative", "")).strip():
            narrative = str(data["narrative"])
            break
        # 解析失败多半是叙述过长被 max_tokens 截断导致 JSON 不完整
        last_err = "LLM 输出被 token 上限截断或格式不符"

    if not narrative:
        advice["generated_by"] = "deterministic"
        advice["llm_status"] = "unavailable"
        advice["llm_note"] = (f"LLM 润色未启用：{last_err or '未知原因'}。"
                              "建议由确定性匹配引擎生成，结果不受影响。")
        return advice


    # 生成后校验：只允许出现报告里存在的课程与 req_id
    invented_courses = [c for c in re.findall(r"《([^》]+)》", narrative)
                        if c not in allowed_courses]
    invented_reqs = [x for x in re.findall(r"REQ-\d+", narrative) if x not in allowed_reqs]
    if invented_courses or invented_reqs:
        advice["generated_by"] = "deterministic"
        advice["llm_status"] = "rejected"
        advice["llm_note"] = ("LLM 叙述中引用了报告外的课程或需求，已丢弃并回退确定性版本。"
                              "这正是生成后校验的作用。")
        return advice

    advice["narrative"] = narrative
    advice["generated_by"] = "llm_polished"
    advice["llm_status"] = "ok"
    return advice
