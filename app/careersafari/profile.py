"""
用户画像端：问卷定义 + 画像组装。

三条设计铁律（9/27 讨论确立）：
  1. **先约束后偏好** —— 先问筛子（硬约束），再问兴趣。反过来问会得到一堆
     无法用于筛选的信息。
  2. **自述与证据分离** —— 能力的「会说」和「做过」必须分栏存储。
     否则后端匹配是在拿用户的自我感觉对企业的硬要求。
  3. **不确定是合法输出** —— 系统必须能说「这项未知，无法判断」。
     这是与通用聊天机器人的分界线。

人设（9/27 会议确认）：**大学初期（大一 / 研一）· 方向未定型**。
共同点不是学历层次，而是「还没形成稳定职业方向，需要提前规划」。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .schema import (
    MAJOR_DIRECTIONS,
    PROGRAM_MILESTONES,
    JOB_SPECIFIC_SKILLS,
    TRANSFERABLE_SKILLS,
    COMMON_DEGREE_COURSES,
    SCHEMA_VERSION,
    EvidenceType,
    HardConstraint,
    InterestProfile,
    Profile,
    SkillItem,
    UnknownItem,
    ValueWeights,
)

# ---------------------------------------------------------------- 问卷定义

PERSONA_OPTIONS = [
    "大一 · 方向未定型",
    "研一 · 方向未定型",
    "大二/大三 · 方向未定型",
    "其他",
]

REGION_OPTIONS = ["上海", "北京", "深圳", "广州", "杭州", "南京", "苏州", "成都", "其他"]

DEALBREAKER_OPTIONS = [
    "长期出差", "高频加班/996", "纯销售性质", "体力劳动占比高",
    "需要频繁公开演讲", "工作地点不固定", "无明显不可接受项",
]

# RIASEC 六型，每型 3 题，共 18 题。取自霍兰德职业兴趣理论的结构化施测方式。
RIASEC_QUESTIONS: List[Dict[str, str]] = [
    # R 现实型
    {"dim": "R", "text": "我喜欢动手操作实物、工具或设备，而不是只在纸面上讨论"},
    {"dim": "R", "text": "我更愿意做户外或现场的技术工作，而不是长时间坐在电脑前"},
    {"dim": "R", "text": "修理、组装、调试一件具体的东西会让我有成就感"},
    # I 研究型
    {"dim": "I", "text": "我喜欢追根问底，把一个问题的机制弄清楚比应用它更重要"},
    {"dim": "I", "text": "面对一堆数据，我会自然地想找出其中的规律"},
    {"dim": "I", "text": "我更享受独立思考与研究，而不是组织协调别人"},
    # A 艺术型
    {"dim": "A", "text": "我喜欢自由发挥、没有标准答案的表达方式"},
    {"dim": "A", "text": "我对审美、设计、文字或音乐有较强的敏感度"},
    {"dim": "A", "text": "在规则严格的流程里工作会让我感到压抑"},
    # S 社会型
    {"dim": "S", "text": "帮助别人解决问题会让我获得明显的满足感"},
    {"dim": "S", "text": "我擅长察觉他人的情绪并做出回应"},
    {"dim": "S", "text": "我喜欢教学、辅导、协调这类以人为对象的工作"},
    # E 企业型
    {"dim": "E", "text": "我喜欢说服别人接受我的观点"},
    {"dim": "E", "text": "我对商业机会、市场竞争、如何把东西卖出去感兴趣"},
    {"dim": "E", "text": "我愿意为了结果承担风险和压力，而不是追求安稳"},
    # C 常规型
    {"dim": "C", "text": "我喜欢有条理、有明确规则和流程的工作"},
    {"dim": "C", "text": "把混乱的信息整理成清晰的结构让我感到舒适"},
    {"dim": "C", "text": "我对数据准确性、文档规范性有较高的自我要求"},
]

VALUE_DIMENSIONS = [
    ("salary", "薪酬回报"),
    ("stability", "稳定可控"),
    ("growth", "成长空间"),
    ("autonomy", "自主性"),
    ("impact", "影响力与意义"),
    ("balance", "工作生活平衡"),
]

MBTI_CAVEAT = (
    "MBTI 的重测信度约五成（同一人两次测试约一半概率落入不同类型），"
    "且对工作绩效的预测力较弱（Pittenger, 1993）。此处仅作自我反思的辅助标签，"
    "不作为方向判定依据。"
)


def questionnaire() -> Dict[str, Any]:
    """前端据此渲染表单。前端不自己发明字段。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "persona_options": PERSONA_OPTIONS,
        "regions": REGION_OPTIONS,
        "dealbreakers": DEALBREAKER_OPTIONS,
        "riasec_questions": RIASEC_QUESTIONS,
        "value_dimensions": VALUE_DIMENSIONS,
        "mbti_caveat": MBTI_CAVEAT,
        "transferable_skills": TRANSFERABLE_SKILLS,
        "job_specific_skills": JOB_SPECIFIC_SKILLS,
        "major_directions": MAJOR_DIRECTIONS,
        "common_degree_courses": COMMON_DEGREE_COURSES,
        "program_milestones": PROGRAM_MILESTONES,
    }


# ---------------------------------------------------------------- 画像组装


def _as_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def build_profile(user_id: str, answers: Dict[str, Any]) -> Profile:
    """把表单原始回答组装成 Profile。原始回答另行落库，不经过这里的加工。"""
    persona = str(answers.get("persona", "")).strip()

    constraints: List[HardConstraint] = []

    def add_constraint(key: str, label: str, value: Any) -> None:
        v = str(value or "").strip()
        if v and v not in ("__unknown__", "__skip__"):
            constraints.append(HardConstraint(key=key, label=label, value=v))

    add_constraint("major", "专业/学科", answers.get("major"))
    add_constraint("grade", "年级", answers.get("grade"))
    add_constraint("direction_interest", "倾向的研究方向", answers.get("direction_interest"))
    add_constraint("region", "期望工作地", answers.get("region"))
    add_constraint("timeline", "必须落地的时间点", answers.get("timeline"))
    add_constraint("dealbreaker", "不可接受项", answers.get("dealbreaker"))
    add_constraint("intern_goal", "暑期实习目标", answers.get("intern_goal"))

    # 硬约束显式支持「未知」—— 未知是一等公民，不是失败状态
    for key, label in (("major", "专业/学科"), ("grade", "年级"), ("region", "期望工作地"),
                       ("timeline", "必须落地的时间点")):
        if str(answers.get(key, "")).strip() in ("__unknown__", "__skip__"):
            constraints.append(HardConstraint(
                key=key, label=label, value="未知",
                evidence=EvidenceType.UNKNOWN, note="用户明确表示不确定"))

    # ---- 能力：两栏 + 自述/有证据分离
    skills: List[SkillItem] = []
    for item in answers.get("skills", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        ev_text = str(item.get("evidence_text", "")).strip()
        verified = bool(item.get("verified")) and bool(ev_text)
        skills.append(SkillItem(
            name=name,
            column=str(item.get("column", "transferable")),
            self_rating=_as_int(item.get("self_rating"), 0) or None,
            verified=verified,
            evidence_text=ev_text,
            evidence=EvidenceType.USER_STATED if verified else EvidenceType.UNVERIFIED,
        ))

    # ---- 兴趣：RIASEC 为主，MBTI 为辅助
    scores: Dict[str, int] = {}
    raw_scores = answers.get("riasec", {}) or {}
    for dim in "RIASEC":
        scores[dim] = _as_int(raw_scores.get(dim), 0)
    code, riasec_note = _riasec_code(scores)

    interest = InterestProfile(
        riasec_scores=scores,
        riasec_code=code,
        mbti_hint=str(answers.get("mbti", "") or "").strip(),
        mbti_caveat_shown=bool(answers.get("mbti_caveat_ack")),
    )

    # ---- 价值观权重
    vw = answers.get("values", {}) or {}
    values = ValueWeights(**{k: _as_int(vw.get(k), 0) for k, _ in VALUE_DIMENSIONS})

    # ---- 未知项登记
    unknowns: List[UnknownItem] = []
    for u in answers.get("unknowns", []) or []:
        if isinstance(u, dict) and str(u.get("field", "")).strip():
            unknowns.append(UnknownItem(field=str(u["field"]).strip(),
                                        reason=str(u.get("reason", "")).strip()))

    return Profile(
        profile_id="",  # 由 db 层分配
        user_id=user_id,
        persona=persona,
        constraints=constraints,
        skills=skills,
        interest=interest,
        values=values,
        unknowns=unknowns,
    )


def profile_card(profile: Profile, answers: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    输出「求职意向定义卡」——人类可读 + 机器可读双格式。
    每一项都带证据等级标注：这是本产品与通用 AI 助手的分界线。
    """
    verified = [s for s in profile.skills if s.verified]
    unverified = [s for s in profile.skills if not s.verified]

    # 同分方向的排序提示：价值观权重最高的维度。
    # 并列时必须全部列出 —— 任意挑一个等于把排序建立在字典遍历顺序上，那是伪造信号。
    vd = {k: getattr(profile.values, k) for k, _ in VALUE_DIMENSIONS}
    top_value = _top_values(vd)

    return {
        "persona": profile.persona,
        "hard_constraints": [
            {"label": c.label, "value": c.value, "evidence": c.evidence.value}
            for c in profile.constraints
        ],
        "interest": {
            "riasec_code": profile.interest.riasec_code,
            "riasec_scores": profile.interest.riasec_scores,
            "mbti_hint": profile.interest.mbti_hint,
            "mbti_caveat": MBTI_CAVEAT if profile.interest.mbti_hint else "",
        },
        "skills": {
            "verified": [
                {"name": s.name, "column": s.column, "rating": s.self_rating,
                 "evidence": s.evidence_text, "evidence_type": s.evidence.value}
                for s in verified
            ],
            "unverified": [
                {"name": s.name, "column": s.column, "rating": s.self_rating,
                 "evidence_type": EvidenceType.UNVERIFIED.value}
                for s in unverified
            ],
        },
        "values": vd,
        "top_value": top_value,
        "unknowns": [{"field": u.field, "reason": u.reason} for u in profile.unknowns],
        "honesty_summary": _honesty_summary(profile, len(verified), len(unverified)),
        "consistency_warnings": consistency_warnings(answers or {}, profile),
        "schema_version": profile.schema_version,
    }


def _honesty_summary(profile: Profile, n_verified: int, n_unverified: int) -> List[str]:
    """把「我们知道什么/不知道什么」明说出来。这是产品的核心承诺。"""
    lines: List[str] = []
    n_constraints = len([c for c in profile.constraints if c.evidence != EvidenceType.UNKNOWN])
    lines.append(f"已确认硬约束 {n_constraints} 项（客观事实，直接决定可行范围）。")
    lines.append(f"能力项 {n_verified} 项有证据支撑，{n_unverified} 项仅为自述、未经任何验证。")
    if profile.unknowns:
        lines.append(f"显式登记未知 {len(profile.unknowns)} 项："
                     + "、".join(u.field for u in profile.unknowns)
                     + "。这些项目前无法判断，系统不做猜测。")
    else:
        lines.append("未登记任何未知项。")
    if profile.interest.mbti_hint and not profile.interest.mbti_caveat_shown:
        lines.append("⚠ MBTI 标签已填写但用户未确认信度提示，展示时必须附带局限说明。")
    return lines


# ---------------------------------------------------------------- 数据质量守卫


def _riasec_code(scores: Dict[str, int]) -> tuple[str, str]:
    """
    生成 RIASEC 兴趣码。

    关键守卫：**全维度同分时不产出兴趣码**。
    全部填一样的分（或随手全选中间值）时，排序结果完全由字典遍历顺序决定，
    输出一个「RIA」等于伪造一个不存在的测量信号。这种情况下必须明确说「未分化」。
    """
    vals = [v for v in scores.values() if v]
    if not vals or max(vals) == min(vals):
        return "", "各兴趣维度得分完全一致，未形成可区分的方向倾向；不生成兴趣码。"
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    code = "".join(d for d, _ in ranked[:3])
    spread = ranked[0][1] - ranked[-1][1]
    note = ""
    if spread <= 2:
        note = (f"最高与最低维度仅差 {spread} 分，区分度很低；"
                "该兴趣码仅供参考，不宜作为方向筛选依据。")
    return code, note


def _top_values(vd: Dict[str, int]) -> Dict[str, Any]:
    """返回并列最高项。并列时全部列出，不任意挑选。"""
    if not any(vd.values()):
        return {"dimension": "", "weight": 0, "tied": [], "note": "未填写价值观权重。"}
    mx = max(vd.values())
    tied = [k for k, v in vd.items() if v == mx]
    label = dict(VALUE_DIMENSIONS).get(tied[0], tied[0])
    return {
        "dimension": label if len(tied) == 1 else "",
        "weight": mx,
        "tied": [dict(VALUE_DIMENSIONS).get(k, k) for k in tied],
        "note": ("多个维度并列最高（" + "、".join(dict(VALUE_DIMENSIONS).get(k, k) for k in tied)
                 + "），无法排出单一优先级。价值观未分化，建议改为「把 100 分分配到 6 个维度」"
                   "的强制取舍形式。") if len(tied) > 1 else "",
    }


def consistency_warnings(answers: Dict[str, Any], profile: Profile) -> List[str]:
    """自相矛盾的输入要在展示前指出来，而不是静静落库。"""
    warns: List[str] = []
    persona = str(answers.get("persona", ""))
    grade = str(answers.get("grade", ""))
    if persona.startswith("大一") and grade and grade != "大一":
        warns.append(f"人设选了「{persona}」但年级填了「{grade}」，两者矛盾，请确认。")
    if persona.startswith("研一") and grade and not grade.startswith("研"):
        warns.append(f"人设选了「{persona}」但年级填了「{grade}」，两者矛盾，请确认。")
    if str(answers.get("timeline", "")).strip() in ("__unknown__", "__skip__") and \
            str(answers.get("intern_goal", "")).strip():
        warns.append("登记了「必须落地的时间点」为未知，却又填写了暑期实习目标——"
                     "没有时间窗的目标无法验证可执行性。")
    if not profile.unknowns and "方向未定型" in persona:
        warns.append("人设为「方向未定型」但未登记任何未知项。"
                     "对目标人群而言，通常至少有一项是无法自行判断的——建议补登。")
    return warns
