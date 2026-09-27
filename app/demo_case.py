"""
端到端演示案例：从零输入 → 求职意向定义 → 岗位匹配 → 学习建议。

这个脚本用一份贴近真实的问卷回答跑完整条链路，输出一份可直接用于
大组展示的报告。它同时是「环境可复现」的验收脚本——换个人填也能跑。

用法：.venv/bin/python demo_case.py
"""
from __future__ import annotations

import json

from careersafari import advice as advice_mod
from careersafari import db, match as match_mod, profile as profile_mod

# ---------------------------------------------------------------- 案例人设
# 管科研一 · 方向未定型 · 目标金融行业商业分析
# 刻意做成「证据强度参差」的样子：2 项有证据、2 项仅自述、1 项未知 ——
# 这样才能演示系统如何处理不确定性，而不是给一个漂亮的满分案例。
ANSWERS = {
    "persona": "研一 · 方向未定型",
    "major": "管理科学与工程",
    "grade": "研一",
    "direction_interest": "大数据分析与决策",
    "region": "上海",
    "timeline": "明年暑期实习前",
    "dealbreaker": "高频加班/996",
    "intern_goal": "金融行业商业分析",
    "skills": [
        {"name": "Excel 与数据透视表", "column": "transferable", "self_rating": 4,
         "verified": True, "evidence_text": "本科《统计学》课程大作业：用透视表完成区域销售结构分析"},
        {"name": "Python / R 数据处理", "column": "transferable", "self_rating": 3,
         "verified": True, "evidence_text": "《大数据分析技术与应用》课程项目：爬取并清洗 3 万条评论数据"},
        {"name": "SQL 取数（多表 JOIN）", "column": "transferable", "self_rating": 3,
         "verified": False, "evidence_text": ""},
        {"name": "统计分析与建模", "column": "transferable", "self_rating": 3,
         "verified": False, "evidence_text": ""},
        {"name": "财务报表分析", "column": "job_specific", "self_rating": 2,
         "verified": False, "evidence_text": ""},
        {"name": "数据可视化", "column": "job_specific", "self_rating": 2,
         "verified": False, "evidence_text": ""},
    ],
    "riasec": {"R": 9, "I": 15, "A": 9, "S": 12, "E": 12, "C": 12},
    "mbti": "INTJ",
    "mbti_caveat_ack": True,
    "values": {"salary": 25, "stability": 15, "growth": 35, "autonomy": 10,
               "impact": 5, "balance": 10},
    "unknowns": [
        {"field": "我是否真的喜欢这个方向（只有名称认知，没有真实体验）", "reason": "只上过课，没实习过"},
        {"field": "金融行业是否接受我的专业背景", "reason": "不确定管科在金融岗的竞争力"},
    ],
}


def bar(v, width=22):
    if v is None:
        return "─" * width
    n = int(round(v * width))
    return "█" * n + "░" * (width - n)


def main() -> None:
    print("=" * 78)
    print("CareerSafari 端到端演示")
    print("=" * 78)

    # ---------- 第 1 步：画像 ----------
    prof = profile_mod.build_profile("demo-case", ANSWERS)
    card = profile_mod.profile_card(prof, ANSWERS)
    print("\n【第 1 步】用户画像 → 求职意向定义卡")
    print("-" * 78)
    print(f"人设：{card['persona']}")
    print("\n硬约束（客观事实，划定可行范围）：")
    for k in card["hard_constraints"]:
        print(f"   {k['label']:<12} {k['value']:<20} [{k['evidence']}]")
    print(f"\n兴趣：RIASEC {card['interest']['riasec_code']}  {card['interest']['riasec_scores']}")
    print(f"       MBTI {card['interest']['mbti_hint']}（辅助标签，非判定依据）")
    print(f"价值观最高项：{card['top_value']['dimension']}（{card['top_value']['weight']} 分）")
    print("\n能力（自述与证据分离）：")
    for s in card["skills"]["verified"]:
        print(f"   ✓ {s['name']:<18} 证据：{s['evidence']}")
    for s in card["skills"]["unverified"]:
        print(f"   ○ {s['name']:<18} 仅自述，自评 {s['rating']}/5，无证据")
    print("\n显式未知（系统不猜）：")
    for u in card["unknowns"]:
        print(f"   ? {u['field']}")

    # ---------- 第 2 步：匹配 ----------
    positions = db.list_positions(limit=300)
    print(f"\n\n【第 2 步】与岗位知识库匹配（{len(positions)} 个真实 JD 沉淀的岗位）")
    print("-" * 78)
    report = match_mod.match_all(positions, prof, ANSWERS)

    # ---------- 第 3 步：建议 ----------
    adv = advice_mod.build_advice(report, prof, ANSWERS)
    adv = advice_mod.polish_with_llm(adv)

    print("\n【第 3 步】求职与学习建议")
    print("-" * 78)
    print(adv["headline"])
    print()
    es = adv["evidence_strength"]
    print(f"整体证据强度：{es['level']} —— {es['note']}")
    print(f"（匹配 {es['matched']} · 仅自述 {es['unverified']} · 缺口 {es['gaps']}，按能力去重）")
    print("\n方向结论（按岗位族聚合，同族只保留代表）：")
    for p in adv["positions"]:
        print(f"  {p['rank']:>2}. {p['job_family']:<12} {p['verdict']:<8}"
              f" {bar(p['verified_coverage'])} {p['verified_coverage']}"
              f"  (库内 {p['family_size']} 个同族岗位)")
        for w in p["why"]:
            print(f"      · {w}")

    print("\n可借培养方案弥补的硬缺口（按被需岗位数排序）：")
    for l in adv["learning_hard"]:
        cs = "；".join(f"《{c['course']}》{c['semester']}·{c['teacher']}·{c['credits']}学分"
                      for c in l["courses"]) or "培养方案内无对应课程"
        print(f"   · {l['capability_name']:<14} ← {len(l['needed_by'])} 个岗位 | {cs}")

    print("\n只能靠实践获得的能力（诚实标注：选课解决不了）：")
    for l in adv["learning_soft"]:
        print(f"   · {l['capability_name']:<14} ← {len(l['needed_by'])} 个岗位")

    print("\n接下来两周做什么：")
    for i, a in enumerate(adv["two_week_actions"], 1):
        print(f"   {i}. {a['action']}")
        print(f"      验证什么：{a['validates']}")
        print(f"      成本：{a['cost']}  来源：{a['source']}")

    print("\n" + "=" * 78)
    print(f"生成方式：{adv['generated_by']}"
          f"（LLM 状态：{adv.get('llm_status', '—')}）")
    print("=" * 78)

    with open("demo_case_output.json", "w", encoding="utf-8") as f:
        json.dump({"card": card, "advice": adv}, f, ensure_ascii=False, indent=2)
    print("\n完整结果已写入 demo_case_output.json")


if __name__ == "__main__":
    main()
