"""
CareerSafari 数据契约 —— 单一真相源（single source of truth）

设计纪律（对应 9/22 方案「数据契约」一节）：
  1. 每个字段的类型 / 必填性 / 允许值都在这里定死，前端与后端都不许各自发明字段。
  2. 证据分级：external（外部来源）/ team（团队判断）/ user_stated（用户自述）/ unknown（未知）。
     —— 「宁可少写，不可编造」。
  3. 用户自述与「有证据」必须分栏：把「我会 SQL」和「我用 SQL 做过项目」当两种证据强度存。
  4. 未知必须是合法输出：unknown 不是失败状态，是一等公民。

修改本文件 = 修改契约 = 必须同步 bump SCHEMA_VERSION，并在 responses 表留版本号，
否则历史数据无法回溯「这条结论是基于哪版问卷得出的」。
"""
from __future__ import annotations

import datetime as _dt
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = "0.1.0"

# ---------------------------------------------------------------- 证据分级


class EvidenceType(str, Enum):
    EXTERNAL = "external"      # 外部来源（JD 原文、官方课程目录、招聘页面）
    TEAM = "team"              # 团队判断（必须带置信度与依据）
    USER_STATED = "user_stated"  # 用户自述
    UNVERIFIED = "unverified"  # 用户自述但无任何证据支撑
    UNKNOWN = "unknown"        # 明确未知 —— 合法输出，不是错误


# ---------------------------------------------------------------- 用户画像端


class HardConstraint(BaseModel):
    """硬约束：客观事实，构成筛子而非偏好。最先采集。"""

    key: str                                   # major / grade / region / deadline / dealbreaker
    label: str                                 # 人类可读名
    value: str                                 # 用户填的值
    evidence: EvidenceType = EvidenceType.USER_STATED
    note: str = ""


class SkillItem(BaseModel):
    """一项能力。verified 区分「有证据」与「仅自述」——这是本产品与通用聊天的分界线。"""

    name: str
    column: str = "transferable"   # transferable（通用可迁移）| job_specific（目标岗位相关）
    self_rating: Optional[int] = None          # 1-5，用户自评
    verified: bool = False                     # 是否有可验证证据
    evidence_text: str = ""                    # 「哪门课 / 哪个项目 / 哪段实习」
    evidence: EvidenceType = EvidenceType.UNVERIFIED

    @field_validator("evidence")
    @classmethod
    def _sync_evidence(cls, v: EvidenceType, info) -> EvidenceType:
        # 有证据_text 但没标 verified 的情况，由上层负责；这里只做一致性兜底
        return v


class InterestProfile(BaseModel):
    """RIASEC 兴趣码（ Holland 六型）。主结构；MBTI 仅作辅助标签。"""

    riasec_scores: Dict[str, int] = Field(default_factory=dict)  # R/I/A/S/E/C -> 0-100
    riasec_code: str = ""                    # 如 "IAS"，取前三
    mbti_hint: str = ""                      # 可选；界面必须标注信度局限
    mbti_caveat_shown: bool = False          # 是否已向用户展示「重测信度约五成」提示


class ValueWeights(BaseModel):
    """价值观权重。用于同分方向的排序，避免「分数一样但用户其实在意别的」。"""

    salary: int = 0        # 薪酬
    stability: int = 0     # 稳定
    growth: int = 0        # 成长空间
    autonomy: int = 0      # 自主性
    impact: int = 0        # 影响力/意义
    balance: int = 0       # 工作生活平衡


class UnknownItem(BaseModel):
    """显式登记的未知项。系统不许猜，只许登记。"""

    field: str
    reason: str = ""       # 用户为什么不确定（可选）


class Profile(BaseModel):
    """一份完整的用户画像快照。"""

    profile_id: str
    user_id: str
    persona: str = ""                       # 人设标签，如「管科研一·方向未定型」
    constraints: List[HardConstraint] = Field(default_factory=list)
    skills: List[SkillItem] = Field(default_factory=list)
    interest: InterestProfile = Field(default_factory=InterestProfile)
    values: ValueWeights = Field(default_factory=ValueWeights)
    unknowns: List[UnknownItem] = Field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    created_at: str = Field(default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds"))


# ---------------------------------------------------------------- 职场端（JD 沉淀）


class Requirement(BaseModel):
    """
    一条原子化需求。

    原子化判据（关键）：**拆到「能否被单独验证」为止**。
    「熟悉 SQL」不是原子项（不可验证）；「能独立写多表 JOIN 查询」才是。
    这个判据直接决定后端匹配能不能做——拿不可验证的条目比对用户自评，比出来的数是假的。
    """

    req_id: str                       # POS-0001-REQ-01
    kind: str = "hard"                # hard（硬性：学历/专业/证书/技能/工具）| soft（软能力）
    category: str                     # 学历 / 专业 / 证书 / 技能 / 工具 / 经验 / 软能力
    text: str                         # 可单独验证的需求描述
    evidence_span: str = ""           # 对应 JD 原文哪一句 —— 没有这一列就无法验证 LLM 有没有添油加醋
    confidence: EvidenceType = EvidenceType.EXTERNAL
    team_confidence: Optional[float] = None   # confidence=team 时必填，0-1


class Position(BaseModel):
    """一个标准化岗位。一条 JD 解析成一个 position。"""

    position_id: str                   # POS-0001
    canonical_name: str                # 标准化岗位名，如「商业分析师」
    industry: str = ""                 # 金融 / 互联网 / 制造 …
    variant_names: List[str] = Field(default_factory=list)  # 原始 JD 里的各种叫法
    work_scenarios: List[str] = Field(default_factory=list)  # 真实工作场景（原子化）
    stressors: List[str] = Field(default_factory=list)       # 压力来源（9/23 会议明确要求）
    requirements: List[Requirement] = Field(default_factory=list)
    source_jd_ids: List[str] = Field(default_factory=list)
    schema_version: str = SCHEMA_VERSION


class JdRecord(BaseModel):
    """一条 JD 原文留存。append-only，永不修改，只追加。"""

    jd_id: str                         # JD-0001
    raw_text: str
    source_type: str                   # manual_paste / file_upload / url_fetch / seed
    source_url: str = ""
    captured_date: str = Field(default_factory=lambda: _dt.date.today().isoformat())
    ingested_by: str = "anonymous"
    status: str = "raw"                # raw / parsed / confirmed / rejected
    credibility: str = "P1"            # P1 企业官方 / P2 招聘平台 / P3 二手转载 / P4 UGC
    parse_error: str = ""


# ---------------------------------------------------------------- LLM 抽取结果（中间态，不落库）


class JdExtraction(BaseModel):
    """LLM 从 JD 原文抽出的结构化结果。人工确认后才变成 Position。"""

    company: str = ""
    title: str = ""
    location: str = ""
    job_type: str = ""                 # 校招 / 实习 / 社招
    canonical_name: str = ""           # LLM 建议的标准岗位名
    industry: str = ""
    variant_names: List[str] = Field(default_factory=list)
    work_scenarios: List[str] = Field(default_factory=list)
    stressors: List[str] = Field(default_factory=list)
    requirements: List[Dict[str, Any]] = Field(default_factory=list)
    # requirements 每项: {kind, category, text, evidence_span}
    extraction_notes: str = ""         # LLM 自述哪些地方没把握


# ---------------------------------------------------------------- 种子数据：管科五方向


MAJOR_DIRECTIONS: List[Dict[str, Any]] = [
    {
        "code": "MSE-FIN",
        "name": "金融服务与金融工程",
        "core_courses": ["投资学", "金融衍生工具", "高级计量经济学", "高级应用统计",
                         "大数据分析技术与应用", "财务报表分析"],
    },
    {
        "code": "MSE-BIGDATA",
        "name": "大数据分析与决策",
        "core_courses": ["决策理论与决策行为", "计算实验原理与应用", "高级计量经济学",
                         "高级应用统计", "大数据分析技术与应用", "信息系统分析与设计",
                         "人工智能、博弈与决策"],
    },
    {
        "code": "MSE-COMPLEX",
        "name": "复杂工程与智能管理",
        "core_courses": ["复杂工程与项目管理的理论及方法", "工程经济学", "高级计量经济学",
                         "高级应用统计", "大数据分析技术与应用", "管理学实证研究方法"],
    },
    {
        "code": "MSE-OSCM",
        "name": "运营与供应链管理",
        "core_courses": ["运营与供应链管理", "生产与运营管理", "应用随机过程",
                         "高级计量经济学", "高级应用统计", "大数据分析技术与应用",
                         "信息系统分析与设计"],
    },
    {
        "code": "MSE-ISM",
        "name": "智能系统管理",
        "core_courses": ["应用随机过程", "大数据分析技术与应用", "决策理论与决策行为",
                         "计算实验原理与应用", "高级应用统计", "人工智能、博弈与决策"],
    },
]

COMMON_DEGREE_COURSES: List[str] = [
    "最优化理论与方法", "系统方法与应用", "博弈论",
    "硕士生学术英语", "人工智能通识与应用", "研究生学术规范与学术诚信",
]

# 培养方案硬节点 —— 这是「先约束后偏好」原则最有力的现场证明：
# 一个研一学生的时间窗是被这些节点卡死的，不是被他的兴趣卡死的。
PROGRAM_MILESTONES: List[Dict[str, str]] = [
    {"semester": "第3学期", "event": "中期考核 + 论文开题", "note": "方向基本锁定，之后转换成本陡增"},
    {"semester": "第5学期初", "event": "论文中期检查", "note": "提交初稿框架与核心研究内容"},
    {"semester": "第6学期4月", "event": "查重 + 送审", "note": "重复率 ≥10% 须一周内修改重交"},
    {"semester": "第6学期5月", "event": "论文答辩", "note": "未通过则延期答辩"},
]

# 能力自评预设：两栏
TRANSFERABLE_SKILLS: List[str] = [
    "Excel 与数据透视表",
    "SQL 取数（多表 JOIN）",
    "Python / R 数据处理",
    "统计分析与建模",
    "最优化建模（线性规划/整数规划）",
    "书面表达与研究报告撰写",
    "口头汇报与跨部门沟通",
    "学术英语阅读与写作",
    "项目管理与优先级判断",
]

JOB_SPECIFIC_SKILLS: Dict[str, List[str]] = {
    "MSE-FIN": ["金融衍生品定价", "财务报表分析", "时间序列分析", "风险管理（VaR）", "量化策略回测"],
    "MSE-BIGDATA": ["机器学习基础（回归/分类）", "特征工程", "A/B 实验设计与分析", "Spark/分布式计算", "数据可视化"],
    "MSE-COMPLEX": ["项目进度与成本控制", "系统工程建模", "仿真与计算实验", "风险管理"],
    "MSE-OSCM": ["需求预测", "库存优化（EOQ/安全库存）", "S&OP 流程", "ERP/APS 系统", "物流网络规划"],
    "MSE-ISM": ["智能优化算法", "多智能体仿真", "信息系统分析与设计", "知识图谱基础"],
}
