"""
种子数据：灌入 3 条面向管科背景的 JD，让 demo 在无 LLM 时也能完整演示。
跑法：.venv/bin/python seed.py
这些是人工整理的真实岗位描述片段（evidence_span 为逐字原文），不是模型生成。
"""
from careersafari import db
from careersafari.schema import JdRecord, Position, Requirement, EvidenceType

JD_SAMPLES = [
    {
        "canonical_name": "商业分析师（金融方向）",
        "industry": "金融",
        "credibility": "P1",
        "raw": """岗位职责：
1. 负责业务数据的清洗、建模与可视化，输出经营分析报告；
2. 搭建并维护核心业务指标体系，监控异常波动并定位原因；
3. 对接业务部门与风险团队，推动数据结论落地为可执行动作。
任职要求：
本科及以上学历，管理科学与工程、统计学、计算机、金融学相关专业；
熟练使用 SQL，能独立编写多表 JOIN 与窗口函数查询；
熟悉 Python 数据分析栈与 Tableau；
有咨询公司或金融机构实习经历者优先；
具备良好的跨部门沟通协作能力与抗压性。""",
    },
    {
        "canonical_name": "供应链计划专员",
        "industry": "零售/制造",
        "credibility": "P1",
        "raw": """岗位职责：
1. 负责需求预测与库存计划制定，监控库存周转与缺货率；
2. 协同采购、生产、仓储推进 S&OP 月度例会；
3. 优化补货参数与安全库存水位，输出改善方案。
任职要求：
本科及以上学历，管理科学与工程、物流管理、工业工程相关专业；
熟悉需求预测方法（移动平均、指数平滑、ARIMA 至少其一）；
熟练使用 Excel 与 SQL，了解 ERP 或 APS 系统；
有供应链相关实习或课程项目经历者优先；
工作节奏快，需具备较强的多任务推进能力。""",
    },
    {
        "canonical_name": "数据分析师（互联网）",
        "industry": "互联网",
        "credibility": "P1",
        "raw": """岗位职责：
1. 负责用户行为数据的提取、分析与专题研究，支撑产品与运营决策；
2. 设计并分析 A/B 实验，评估策略效果并给出结论；
3. 建设数据看板，推动指标体系在产品侧落地。
任职要求：
本科及以上学历，统计学、数学、管理科学与工程、计算机相关专业；
熟练使用 SQL，能独立完成复杂取数与性能优化；
熟悉 Python 与至少一种可视化工具；
有数据分析相关实习或竞赛经历者优先；
要求具备较强的逻辑思维与结论表达能力。""",
    },
]


def seed() -> None:
    db.init_db()
    n = 0
    for s in JD_SAMPLES:
        if db.jd_exists_by_url("") and db.list_jds(1):
            pass
        jd_id = db.new_id("JD")
        db.save_jd(JdRecord(jd_id=jd_id, raw_text=s["raw"], source_type="seed",
                            credibility=s["credibility"], status="confirmed"))
        pos_id = db.new_id("POS")
        reqs = []
        for i, line in enumerate([l for l in s["raw"].splitlines() if l.strip()], start=1):
            body = line.strip()
            if not (body[0].isdigit() or body.startswith(("本科", "熟练", "熟悉", "有", "具备", "工作", "要求"))):
                continue
            kind = "soft" if any(k in body for k in
                                 ("沟通", "协作", "抗压", "学习能力", "钻研", "主动性",
                                  "责任心", "逻辑思维", "推进能力", "表达能力")) else "hard"
            cat = "学历" if "学历" in body else \
                  "专业" if "专业" in body else \
                  "经验" if any(k in body for k in ("实习", "经历", "优先")) else \
                  "软能力" if kind == "soft" else \
                  "工具" if any(k in body for k in ("SQL", "Python", "Excel", "Tableau",
                                                    "ERP", "APS", "可视化")) else "技能"
            reqs.append(Requirement(
                req_id=f"{pos_id}-REQ-{i:02d}", kind=kind, category=cat, text=body,
                evidence_span=body, confidence=EvidenceType.EXTERNAL.value))
        db.save_position(Position(position_id=pos_id, canonical_name=s["canonical_name"],
                                  industry=s["industry"], requirements=reqs,
                                  source_jd_ids=[jd_id]))
        n += 1
        print(f"  ✓ {s['canonical_name']}：{len(reqs)} 条需求")
    print(f"种子数据完成，共 {n} 个岗位。数据库：{db.DB_PATH}")


if __name__ == "__main__":
    seed()
