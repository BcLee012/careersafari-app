"""培养方向—候选岗位族覆盖台账。映射是本地产品假设，不是培养方案承诺的就业去向。"""
from . import families

CATALOG_VERSION = "curriculum-families-1"
DIRECTIONS = {
    "金融服务与金融工程": ["金融分析与研究", "风控合规", "数据分析", "商业/经营分析"],
    "复杂工程与智能管理": ["运筹优化与仿真", "项目管理", "智能系统与信息系统", "战略分析"],
    "大数据分析与决策": ["数据分析", "商业/经营分析", "数据工程", "金融分析与研究", "产品运营"],
    "运营与供应链管理": ["供应链计划", "采购", "物流履约", "运筹优化与仿真", "商业/经营分析"],
    "智能系统管理": ["智能系统与信息系统", "产品经理", "数据工程", "项目管理", "产品运营"],
}

# 一对多的检索候选对应；不能把美国经理职业的经验门槛投射给国内学生。
ONET_CROSSWALK = {
    "金融分析与研究": ["13-2051.00", "13-2099.01"],
    "数据工程": ["15-1243.01", "15-1243.00"],
    "运筹优化与仿真": ["15-2031.00"],
    "智能系统与信息系统": ["15-1211.00", "15-1299.08"],
    "数据分析": ["15-2051.01", "15-2051.00"],
    "商业/经营分析": ["13-1111.00", "15-2051.01"],
    "战略分析": ["13-1111.00", "13-1161.00"],
    "供应链计划": ["13-1081.02", "13-1081.00"],
    "采购": ["13-1023.00"],
    "物流履约": ["13-1081.00", "13-1081.01"],
    "产品运营": ["13-1161.00"],
    "产品经理": ["13-1082.00", "13-1161.00", "15-1211.00"],
    "项目管理": ["13-1082.00"],
    "风控合规": ["13-2054.00", "13-1041.00", "13-2099.04"],
}


def directions_for(family):
    return [name for name, names in DIRECTIONS.items() if family in names]


def coverage_report(positions, today=None):
    groups = {(g["family"], g["stage"]): g for g in families.aggregate(positions, today)}
    rows = []
    for family, codes in ONET_CROSSWALK.items():
        for stage in ("实习", "校招"):
            g = groups.get((family, stage), {})
            members = g.get("members", [])
            rows.append({"family": family, "stage": stage, "directions": directions_for(family),
                "onet_candidates": codes, "candidate_count": g.get("candidate_count", 0),
                "jd_count": g.get("jd_count", 0), "employer_count": g.get("employer_count", 0),
                "eligible": g.get("eligible", False),
                "missing_jds": max(0, families.MIN_JDS - g.get("jd_count", 0)),
                "missing_employers": max(0, families.MIN_EMPLOYERS - g.get("employer_count", 0)),
                "reasons": g.get("gate_reasons", ["尚无通过当前核验的岗位证据"]),
                "sources": [{"company": p["admission"]["company"], "url": p["admission"]["source_url"],
                             "published_date": p["admission"]["published_date"], "jd_id": p["source_jd_ids"][0]}
                            for p in members]})
    return {"catalog_version": CATALOG_VERSION, "directions": DIRECTIONS, "rows": rows,
            "eligible_count": sum(r["eligible"] for r in rows), "target_count": len(rows),
            "note": "候选覆盖不代表已有有效岗位画像；6份/3家为运营门槛，不证明市场代表性。"}
