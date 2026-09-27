"""
能力 Taxonomy + 培养方案映射。

这是整个「求职建议」功能的地基，也是本产品相对通用 AI 唯一的结构性优势：
**通用模型不知道南京大学工程管理学院管科学硕开什么课、哪个学期开、几个学分。**

两条设计纪律：
  1. 匹配在 canonical capability id 上做，不做模糊字符串匹配。
     「熟悉 SQL」和「能独立写多表 JOIN」必须落到同一个 CAP-SQL，否则比对结果是假的。
  2. 课程映射是**人工整理的本地事实表**，不是模型生成的。
     模型不知道这些课，让它编就等于把最独特的优势变成幻觉来源。

数据来源：工程管理学院 1201 管理科学与工程学科学术学位硕士研究生培养方案（2026级）
"""
from __future__ import annotations

from typing import Dict, List, Optional

# ---------------------------------------------------------------- 能力 taxonomy
# aliases 按特异性从高到低排列，匹配时取第一个命中。
CAPABILITIES: List[Dict] = [
    {"id": "SQL", "name": "SQL 取数", "aliases": [
        "sql", "多表", "join", "窗口函数", "取数", "查询优化"]},
    {"id": "PYTHON", "name": "Python / R 数据处理", "aliases": [
        "python", "r语言", "r 语言", "pandas", "numpy", "数据处理"]},
    {"id": "STATS", "name": "统计分析与计量", "aliases": [
        "统计", "计量", "回归", "假设检验", "推断统计", "spss", "sas"]},
    {"id": "OPT", "name": "最优化建模", "aliases": [
        "最优化", "线性规划", "整数规划", "运筹", "优化建模", "凸优化"]},
    {"id": "VIZ", "name": "数据可视化", "aliases": [
        "可视化", "tableau", "powerbi", "power bi", "看板", "dashboard", "图表"]},
    {"id": "EXCEL", "name": "Excel 与透视表", "aliases": [
        "excel", "透视表", "vlookup", "电子表格"]},
    {"id": "ABTEST", "name": "实验设计与分析", "aliases": [
        "a/b", "ab实验", "ab 实验", "实验设计", "因果推断"]},
    {"id": "METRIC", "name": "业务指标体系", "aliases": [
        "指标体系", "北极星", "指标搭建", "指标监控", "异动"]},
    {"id": "FIN", "name": "财务与金融分析", "aliases": [
        "财务", "财报", "估值", "金融", "投资学", "衍生品", "风险管理", "var",
        "投资组合", "固收"]},
    {"id": "FORECAST", "name": "需求预测", "aliases": [
        "需求预测", "时间序列", "arima", "移动平均", "指数平滑", "随机过程"]},
    {"id": "INVOPT", "name": "库存与补货优化", "aliases": [
        "库存", "安全库存", "eoq", "补货", "周转"]},
    {"id": "ERP", "name": "ERP / APS 系统", "aliases": [
        "erp", "aps", "sap", "系统操作"]},
    {"id": "SYSANALYSIS", "name": "信息系统分析与设计", "aliases": [
        "信息系统", "系统分析", "系统设计", "需求分析", "数据库设计"]},
    {"id": "ML", "name": "机器学习基础", "aliases": [
        "机器学习", "监督学习", "分类模型", "聚类", "特征工程"]},
    {"id": "SIM", "name": "仿真与计算实验", "aliases": [
        "仿真", "计算实验", "蒙特卡洛", "多智能体", "agent 建模仿真"]},
    {"id": "PROJMGMT", "name": "项目管理与推进", "aliases": [
        "项目管理", "进度控制", "优先级", "里程碑", "计划制定"]},
    {"id": "WRITING", "name": "书面表达与学术写作", "aliases": [
        "报告撰写", "写作", "文档", "论文写作", "实证研究", "研究方法"]},
    {"id": "COMM", "name": "沟通汇报与表达", "aliases": [
        "沟通", "汇报", "表达", "演讲", "跨部门沟通", "结论表达"]},
    {"id": "COLLAB", "name": "协作与多方推进", "aliases": [
        "协作", "团队合作", "推进", "协调", "对接业务"]},
    {"id": "PRESSURE", "name": "抗压与节奏适应", "aliases": [
        "抗压", "压力", "节奏快", "高强度", "多任务"]},
    {"id": "ENGLISH", "name": "英语能力", "aliases": [
        "英语", "英文", "学术英语", "english"]},
]

_BY_ID = {c["id"]: c for c in CAPABILITIES}

# 本质上是软能力的能力。is_hard 由**能力**决定，不由某条 JD 的措辞决定——
# 否则「对接业务部门，推动结论落地」会被规则分类器误判成硬技能，
# 于是「补协作能力」会挤进「可借培养方案弥补的硬缺口」清单里。
SOFT_CAPABILITIES = {"COMM", "COLLAB", "PRESSURE"}


def capabilities_of(text: str) -> List[str]:
    """
    返回一段文本涉及的全部能力 id（去重，保持 taxonomy 顺序）。

    为什么不能只取第一个：「熟练使用 Excel 与 SQL，能独立编写多表 JOIN 查询」
    同时要求 Excel 和 SQL 两项。只归一类会让用户已验证的 Excel 不计入匹配，
    同时漏报一个缺口。匹配引擎的正确输入是能力集合，不是单个能力。
    """
    if not text:
        return []
    low = text.lower()
    out: List[str] = []
    for cap in CAPABILITIES:
        if any(alias in low for alias in cap["aliases"]):
            out.append(cap["id"])
    return out


def capability_of(text: str) -> Optional[str]:
    """兼容旧接口：返回第一个命中的能力 id。"""
    caps = capabilities_of(text)
    return caps[0] if caps else None


def capability_name(cid: str) -> str:
    return _BY_ID.get(cid, {}).get("name", cid)


# ---------------------------------------------------------------- 培养方案映射
# 数据来源：培养方案附表。semester / teacher / credits 均照抄原文。
# 空列表 = 该能力在培养方案内无对应课程 —— 这是**诚实的空缺**，不是遗漏：
# 软能力无法从一门课获得，必须靠实践。

CURRICULUM: Dict[str, List[Dict[str, str]]] = {
    "SQL": [{"course": "大数据分析技术与应用", "code": "1201C1500", "semester": "第二学期",
             "teacher": "刘帆", "credits": "2", "note": "含数据库与 SQL 实操"}],
    "PYTHON": [{"course": "大数据分析技术与应用", "code": "1201C1500", "semester": "第二学期",
                "teacher": "刘帆", "credits": "2", "note": ""}],
    "STATS": [
        {"course": "高级应用统计", "code": "1201C1000", "semester": "第一学期",
         "teacher": "徐红利", "credits": "2", "note": "推断统计与建模基础"},
        {"course": "高级计量经济学", "code": "1201C0100", "semester": "第一学期",
         "teacher": "方立兵", "credits": "3", "note": "偏研究向，工作量较大"},
    ],
    "OPT": [{"course": "最优化理论与方法", "code": "1201B0100", "semester": "第一学期",
             "teacher": "徐薇", "credits": "2",
             "note": "B 类公共学位课，全方向必修", "mandatory": True}],
    "VIZ": [{"course": "大数据分析技术与应用", "code": "1201C1500", "semester": "第二学期",
             "teacher": "刘帆", "credits": "2", "note": "可视化部分需自学补充"}],
    "EXCEL": [],
    "ABTEST": [{"course": "管理学实证研究方法", "code": "1201C2100", "semester": "第二学期",
                "teacher": "李迁", "credits": "2", "note": "含研究设计与因果识别"}],
    "METRIC": [],
    "FIN": [
        {"course": "投资学", "code": "025102B01", "semester": "第一学期",
         "teacher": "朱洪亮", "credits": "2", "note": "金融服务与金融工程方向"},
        {"course": "财务报表分析", "code": "1201C1900", "semester": "第二学期",
         "teacher": "王国俊", "credits": "2", "note": "直接对口商业分析岗的报表阅读"},
        {"course": "金融衍生工具", "code": "1201C0300", "semester": "第一学期",
         "teacher": "刘海飞", "credits": "2", "note": ""},
    ],
    "FORECAST": [
        {"course": "应用随机过程", "code": "1201C1300", "semester": "第一学期",
         "teacher": "瞿慧", "credits": "2", "note": "预测方法的数学基础"},
        {"course": "运营与供应链管理", "code": "1201C1200", "semester": "第二学期",
         "teacher": "陈虹桥", "credits": "2", "note": "需求预测的业务场景"},
    ],
    "INVOPT": [
        {"course": "运营与供应链管理", "code": "1201C1200", "semester": "第二学期",
         "teacher": "陈虹桥", "credits": "2", "note": "库存策略与 S&OP"},
        {"course": "生产与运营管理", "code": "1201C1400", "semester": "第一学期",
         "teacher": "沈厚才", "credits": "2", "note": ""},
    ],
    "ERP": [{"course": "信息系统分析与设计", "code": "1201C1700", "semester": "第一学期",
             "teacher": "陈国华", "credits": "2", "note": "ERP 原理在系统设计模块"}],
    "SYSANALYSIS": [{"course": "信息系统分析与设计", "code": "1201C1700", "semester": "第一学期",
                     "teacher": "陈国华", "credits": "2", "note": ""}],
    "ML": [{"course": "人工智能、博弈与决策", "code": "1201C2300", "semester": "第一学期",
            "teacher": "占杨、吴钰炜", "credits": "2", "note": ""}],
    "SIM": [{"course": "计算实验原理与应用", "code": "1201C0600", "semester": "第二学期（双年）",
             "teacher": "陈国华", "credits": "2", "note": "双年开设，需注意选课窗口"}],
    "PROJMGMT": [{"course": "复杂工程与项目管理的理论及方法", "code": "1201C2200",
                  "semester": "第一学期", "teacher": "李迁", "credits": "2", "note": ""}],
    "WRITING": [
        {"course": "工程管理设计研究与论文写作", "code": "125604B004", "semester": "第一学期",
         "teacher": "宁延", "credits": "2", "note": "研究报告写作规范"},
        {"course": "管理学实证研究方法", "code": "1201C2100", "semester": "第二学期",
         "teacher": "李迁", "credits": "2", "note": ""},
    ],
    "ENGLISH": [{"course": "硕士生学术英语", "code": "10284A010", "semester": "第二学期",
                 "teacher": "—", "credits": "4",
                 "note": "A 类学位必修课，培养方案已安排，无需额外规划",
                 "mandatory": True}],
    # 以下为软能力 / 高度场景化能力：培养方案内无对应课程。
    # 明确留空并说明，而不是硬凑一门课 —— 这就是「宁可少写，不可编造」。
    "COMM": [],
    "COLLAB": [],
    "PRESSURE": [],
}

NO_COURSE_NOTE = ("培养方案内无直接对应课程。这类能力无法从一门课获得，"
                  "只能通过课程项目、竞赛或实习中的真实协作获得。")

# 培养方案硬节点（来自培养方案第六、七节）
PROGRAM_MILESTONES: List[Dict[str, str]] = [
    {"semester": "第3学期", "event": "中期考核 + 论文开题",
     "note": "方向基本锁定，此后转换成本陡增"},
    {"semester": "第5学期初", "event": "论文中期检查", "note": "提交初稿框架与核心研究内容"},
    {"semester": "第6学期4月", "event": "查重 + 送审", "note": "重复率 ≥10% 须一周内修改重交"},
    {"semester": "第6学期5月", "event": "论文答辩", "note": "未通过则延期答辩"},
]


# ---------------------------------------------------------------- 硬约束规则
# 用确定性规则把岗位要求与用户约束对照，产出「被排除」及理由。
# 排除的理由比推荐的理由更重要 —— 它证明系统真的在读约束，不是在客套。

DEALBREAKER_RULES: List[Dict[str, Any]] = [
    # hard：明确写在 JD 里的硬性事实，命中即排除
    # soft：通用软性话术（抗压/节奏快/高强度几乎是所有 JD 的套话），只提示不排除
    # —— 把「抗压性」当成「996」的证据，会误杀掉本可行的选项。
    #    排除理由是这个系统后果最重的输出，宁可漏报不可错杀。
    {"dealbreaker": "长期出差", "hard": ["出差", "驻场", "外派", "外地办公"], "soft": [],
     "reason": "该岗位 JD 明确提到出差/驻场，与你的不可接受项冲突"},
    {"dealbreaker": "高频加班/996", "hard": ["加班", "996", "大小周", "单休"], "soft": ["抗压", "节奏快", "高强度"],
     "reason": "该岗位 JD 明确提到加班/单休等工时安排，与你的不可接受项冲突"},
    {"dealbreaker": "纯销售性质", "hard": ["销售", "客户开拓", "业绩指标", "拉新"], "soft": [],
     "reason": "该岗位含销售业绩要求，与你的不可接受项冲突"},
    {"dealbreaker": "需要频繁公开演讲",
     "hard": ["路演", "主持", "对外演讲"], "soft": ["汇报", "演讲"],
     "reason": "该岗位 JD 明确要求高频公开表达，与你的不可接受项冲突"},
    {"dealbreaker": "工作地点不固定",
     "hard": ["多地办公", "全国 base", "base 全国"], "soft": ["轮岗", "多地"],
     "reason": "该岗位工作地点不固定，与你的不可接受项冲突"},
]


def dealbreaker_conflict(dealbreaker: str, jd_text: str):
    """
    返回 (excluded, warning)。
      excluded —— 命中 hard 关键词，直接排除，附理由
      warning  —— 只命中 soft 关键词（JD 套话），提示但不排除
    """
    if not dealbreaker or dealbreaker in ("无明显不可接受项",):
        return False, ""
    for rule in DEALBREAKER_RULES:
        if rule["dealbreaker"] != dealbreaker:
            continue
        for kw in rule.get("hard", []):
            if kw in jd_text:
                return True, f"{rule['reason']}（命中关键词：「{kw}」）"
        for kw in rule.get("soft", []):
            if kw in jd_text:
                return False, (f"{rule['reason']}——但 JD 仅出现通用软性表述「{kw}」，"
                               f"未明确承诺工时/地点安排，因此**不排除**，仅提示你需要核实。")
    return False, ""


# ---------------------------------------------------------------- 岗位族归一化
# LLM 对同一类岗位会给出不同 canonical_name（商业分析师 / 战略商业分析师 / 商业分析Leader），
# 直接当独立岗位会让「被 N 个岗位要求」的统计虚高，结果列表也碎成一地。
# 用受控词表归到少数几个族，统计和展示都建立在族上。
JOB_FAMILIES: List[Dict[str, Any]] = [
    {"family": "数据分析", "keywords": ["数据分析", "数据挖掘", "风控数据", "风险数据", "数据运营"]},
    {"family": "商业/经营分析", "keywords": ["商业分析", "经营分析", "商业变现", "战略商业"]},
    {"family": "战略分析", "keywords": ["战略分析", "战略研究", "行业研究", "商业洞察"]},
    {"family": "供应链计划", "keywords": ["供应链计划", "计划专员", "s&op", "需求计划", "库存计划"]},
    {"family": "采购", "keywords": ["采购", "寻源", "供应商质量", "品类管理"]},
    {"family": "物流履约", "keywords": ["物流", "仓储", "履约", "关务", "跨境物流"]},
    {"family": "产品运营", "keywords": ["运营", "用户增长", "增长", "内容运营"]},
    {"family": "产品经理", "keywords": ["产品经理"]},
    {"family": "项目管理", "keywords": ["项目管理", "项目经理", "项目专员"]},
    {"family": "风控合规", "keywords": ["风控", "合规", "反欺诈", "审计"]},
]


def job_family(canonical_name: str) -> str:
    """把岗位名归到受控岗位族。无命中返回「其他」。"""
    n = (canonical_name or "").lower()
    for f in JOB_FAMILIES:
        if any(k.lower() in n for k in f["keywords"]):
            return f["family"]
    return "其他（未归入受控岗位族）"
