"""
JD → 原子化需求 的抽取与校验。

这是整个项目「可验证性」主张的最小可运行实现，对应 9/22 方案的三条硬约束：
  1. 模型只能基于给定的 JD 原文输出，不许引入外部知识；
  2. 每条拆出的需求必须带 evidence_span（原文片段）；
  3. **程序在展示前自动检查**：evidence_span 不在原文里的，标记为「无原文支撑」并拒绝直接采信。

为什么校验 evidence_span 是关键：
  如果 LLM 把「熟悉 SQL」脑补成「要求有 3 年 Spark 经验」，而没有任何机制能发现，
  那么后面所有匹配都是在一个被污染的数据集上跑。校验不是锦上添花，是数据管线的地基。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from . import llm

SYSTEM_PROMPT = """你是一个岗位信息结构化引擎。你的唯一任务是把用户提供的一则招聘 JD 原文，
拆解成「可被单独验证」的原子化需求。

硬性规则（违反任何一条即任务失败）：
1. 只能使用用户给出的 JD 原文里出现过的事实。禁止引入任何外部知识、常识或推测。
   原文没说的，就不要写。
2. 每一条需求必须附 evidence_span：**从 JD 原文中逐字复制**的、支撑该需求的那一句（或那一小段）。
   不许改写、不许概括、不许翻译。如果找不到支撑句，就不要输出这条需求。
3. 原子化判据：拆到「能否被单独验证」为止。
   - ❌「熟悉 SQL」不可验证；✅「能独立编写多表 JOIN 查询」可验证。
   - ❌「沟通能力好」不可验证；✅「需向业务方汇报分析结论」可验证。
4. 硬性要求（kind=hard）：学历、专业、证书、特定技能、工具、工作年限。
   软能力（kind=soft）：沟通、协作、抗压、钻研、学习能力等。
   二者必须分开，不许混在一条里。
5. work_scenarios 填「这个岗位真实在做什么」，要具体到场景，不要抄 JD 的套话。
6. stressors 填「这份工作的压力来源」，只能从原文明确提到的内容推断，原文没提就留空数组。
7. 不确定的地方，写在 extraction_notes 里，不要伪装成确定。
8. 全部用简体中文输出，除专有名词外不要中英混排。

输出 JSON，结构如下（不要输出任何 JSON 之外的内容）：
{
  "company": "公司名，原文没有则空字符串",
  "title": "岗位名称，照抄原文",
  "location": "工作地点",
  "job_type": "校招/实习/社招/未知",
  "canonical_name": "标准化岗位名，如「商业分析师」",
  "industry": "行业，如 金融/互联网/制造/未知",
  "variant_names": ["原文中该岗位的其他叫法"],
  "work_scenarios": ["具体工作场景1", "具体工作场景2"],
  "stressors": ["压力来源1"],
  "requirements": [
    {
      "kind": "hard 或 soft",
      "category": "学历/专业/证书/技能/工具/经验/软能力 之一",
      "text": "可单独验证的需求描述",
      "evidence_span": "JD 原文中逐字复制的支撑句"
    }
  ],
  "extraction_notes": "你没把握的地方；没有则空字符串"
}"""


def _normalize(s: str) -> str:
    """归一化：去空白、统一标点，用于 evidence_span 的子串匹配。"""
    s = (s or "").replace("　", " ").replace("\u3000", " ")
    s = re.sub(r"\s+", "", s)
    table = str.maketrans({
        "，": ",", "。": ".", "；": ";", "：": ":", "？": "?", "！": "!",
        "（": "(", "）": ")", "【": "[", "】": "]", "「": '"', "」": '"',
        "‘": "'", "’": "'", "“": '"', "”": '"', "、": ",",
    })
    return s.translate(table).lower()


def extract_from_jd(raw_text: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    返回 (extraction, meta)。
    extraction 为 None 表示失败；meta 含 mock 标记与错误信息。
    """
    if not llm.is_configured():
        return llm.mock_extract(raw_text), {"mock": True, "error": "STEPFUN_API_KEY 未配置"}

    # 实测：推理模型对一条 ~500 字 JD 会产生 8000–10000 token 的 reasoning，
    # max_tokens 必须显著大于 reasoning 才有空间放 content，否则 content 为空。
    r = llm.chat(SYSTEM_PROMPT, f"请拆解下面这则 JD：\n\n{raw_text}",
                  temperature=0.1, max_tokens=16384)
    if not r["ok"]:
        return None, {"mock": False, "error": r["error"]}

    data = llm.parse_json_loose(r["content"])
    if not isinstance(data, dict):
        return None, {"mock": False, "error": "LLM 输出不是合法 JSON"}

    data.setdefault("requirements", [])
    data["_mock"] = False
    return data, {"mock": False, "error": "", "model": r.get("model", "")}


def validate_extraction(extraction: Dict[str, Any], raw_text: str) -> Dict[str, Any]:
    """
    生成后校验。这是「程序在展示前自动检查」的实现。

    校验项：
      1. 每条 requirement 的 evidence_span 必须是原文的子串（归一化后）。
         找不到 → 标记 span_ok=False，该条**不得**被采信为 external 证据。
      2. requirement 必须同时有 kind / category / text，缺一项即不完整。
      3. kind 必须是 hard/soft，category 必须在允许集合内。
    返回 {total, passed, failed, items:[{...校验明细}], reject_all: bool}
    """
    norm_raw = _normalize(raw_text)
    allowed_categories = {"学历", "专业", "证书", "技能", "工具", "经验", "软能力"}
    items: List[Dict[str, Any]] = []
    passed = failed = 0

    for idx, req in enumerate(extraction.get("requirements", []) or []):
        if not isinstance(req, dict):
            failed += 1
            items.append({"idx": idx, "ok": False, "reason": "不是对象"})
            continue

        problems: List[str] = []
        kind = str(req.get("kind", "")).strip()
        category = str(req.get("category", "")).strip()
        text = str(req.get("text", "")).strip()
        span = str(req.get("evidence_span", "")).strip()

        if kind not in ("hard", "soft"):
            problems.append(f"kind 必须是 hard/soft，收到 {kind!r}")
        if category not in allowed_categories:
            problems.append(f"category 不在允许集合内：{category!r}")
        if not text:
            problems.append("text 为空")
        if not span:
            problems.append("缺少 evidence_span")
        elif _normalize(span) not in norm_raw:
            problems.append("evidence_span 在 JD 原文中找不到（疑似模型编造或改写）")

        if problems:
            failed += 1
            items.append({"idx": idx, "ok": False, "text": text, "problems": problems})
        else:
            passed += 1
            items.append({"idx": idx, "ok": True, "text": text})

    total = passed + failed
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "items": items,
        # 一条都没过 → 整体拒绝，不落库
        "reject_all": total > 0 and passed == 0,
        "note": ("全部需求均无原文支撑，已拒绝入库。" if total > 0 and passed == 0
                 else ("无需求被拆出。" if total == 0 else "")),
    }


def to_requirements(extraction: Dict[str, Any], position_id: str,
                    raw_text: str, start_index: int = 1) -> List[Dict[str, Any]]:
    """
    把校验通过的需求转成 requirement 行。
    未通过校验的条目不进入 external 证据，而是降级为 team（团队判断）并保留原文标记，
    由人工决定去留 —— 而不是静默丢弃或静默采信。
    """
    from .schema import EvidenceType

    norm_raw = _normalize(raw_text)
    out: List[Dict[str, Any]] = []
    n = start_index
    for req in extraction.get("requirements", []) or []:
        if not isinstance(req, dict):
            continue
        text = str(req.get("text", "")).strip()
        span = str(req.get("evidence_span", "")).strip()
        if not text:
            continue
        span_ok = bool(span) and _normalize(span) in norm_raw
        cat = normalize_category(str(req.get("category", "")))
        if not cat:
            # 类别无法归一到受控集合 → 该条不进库。宁可少一条，不可留脏数据。
            continue
        out.append({
            "req_id": f"{position_id}-REQ-{n:02d}",
            "kind": str(req.get("kind", "hard")).strip() or "hard",
            "category": cat,
            "text": text,
            "evidence_span": span,
            "confidence": EvidenceType.EXTERNAL.value if span_ok else EvidenceType.TEAM.value,
            "team_confidence": None if span_ok else 0.3,
            "span_ok": span_ok,
        })
        n += 1
    return out


# ---------------------------------------------------------------- 规则基线（无 LLM 降级）

# 确定性规则解析器：不依赖任何 LLM，用于两条用途——
#   ① LLM 不可用（无 key / 配额耗尽）时保证 demo 链路可跑通；
#   ② 作为 LLM 抽取质量的对照基线：同一则 JD，规则法能抓到多少、LLM 能抓到多少，
#      多出来的是不是有原文支撑。这本身就是一个可展示的评估角度。
# 诚实标注：rule_based 的 evidence_span 是逐字切片，因此校验必然通过，
# 但它证明的只是「原文里出现过这些词」，不等于「这些词构成对该岗位的要求」。

# 受控类别集合。LLM 偶尔会自造类别（实测出现过「语言能力」「兴趣」「标准」等），
# 必须在落库前归一化，否则前端筛选和统计全是脏数据。
CANONICAL_CATEGORIES = {"学历", "专业", "证书", "技能", "工具", "经验", "软能力"}

_CATEGORY_ALIASES = {
    "学历/专业": "学历", "学历专业": "学历", "教育背景": "学历",
    "专业要求": "专业", "专业背景": "专业",
    "技能/工具": "技能", "工具/技能": "工具", "技术栈": "工具", "技术": "工具",
    "语言能力": "技能", "语言": "技能", "英语": "技能",
    "行业经验": "经验", "工作经历": "经验", "实习经历": "经验",
    "知识": "技能", "专业知识": "技能",
    "兴趣": "软能力", "特质": "软能力", "素质": "软能力",
}


def normalize_category(cat: str) -> str:
    """把 LLM 输出的类别归一到受控集合。无法归类返回空字符串（调用方应丢弃该条）。"""
    c = (cat or "").strip()
    if c in CANONICAL_CATEGORIES:
        return c
    return _CATEGORY_ALIASES.get(c, "")


_RULES: List[tuple] = [
    ("hard", "学历", ["本科及以上", "硕士及以上", "博士", "本科以上", "硕士以上", "学历要求"]),
    ("hard", "专业", ["专业", "学科背景"]),
    ("hard", "证书", ["证书", "资格认证", "持证", "CFA", "CPA", "FRM", "PMP"]),
    ("hard", "工具", ["SQL", "Python", "R语言", "Excel", "Tableau", "PowerBI", "Power BI",
                      "SPSS", "SAS", "MATLAB", "SAP", "ERP", "Spark", "Linux", "Git"]),
    ("soft", "软能力", ["沟通", "协作", "团队合作", "抗压", "学习能力", "钻研", "主动性",
                        "责任心", "逻辑思维", "英文沟通"]),
    ("hard", "经验", ["实习经历", "工作经验", "年以上经验", "项目经验", "相关经验"]),
    ("hard", "技能", ["熟悉", "掌握", "熟练使用", "了解", "精通", "能够独立", "具备"]),
]


def rule_based_extract(raw_text: str) -> Dict[str, Any]:
    """把 JD 按句切分，用关键词规则归类。零依赖、零成本、完全可复现。"""
    sentences = [s.strip() for s in re.split(r"[。；;\n]", raw_text) if s.strip()]
    reqs: List[Dict[str, Any]] = []
    seen = set()
    for sent in sentences:
        for kind, category, keywords in _RULES:
            for kw in keywords:
                if kw in sent and (sent, category) not in seen:
                    seen.add((sent, category))
                    reqs.append({
                        "kind": kind,
                        "category": category,
                        "text": sent if len(sent) <= 80 else sent[:77] + "…",
                        "evidence_span": sent,
                    })
                    break
            else:
                continue
            break
    return {
        "company": "", "title": "", "location": "", "job_type": "",
        "canonical_name": "", "industry": "",
        "variant_names": [], "work_scenarios": [], "stressors": [],
        "requirements": reqs,
        "extraction_notes": (
            "规则基线（rule_based）：未调用 LLM。evidence_span 是逐字切片，"
            "校验必然通过，但它只证明「原文出现过这些词」，不构成语义理解。"
        ),
        "_rule_based": True,
    }
