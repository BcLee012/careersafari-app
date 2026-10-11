"""Export an honest snapshot of collection, gates and synthetic student reports.

Reads the business database; never creates real student records or claims accuracy.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from careersafari import db, coverage, onet, match, profile, advice, ranking


def evaluate(sources, output):
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((sources / "manifest.json").read_text())
    records = manifest["records"]
    source_urls = {r["url"] for r in records}
    linked = [r for r in db.list_jds(100000) if r["source_url"] in source_urls]
    assessments = [db.get_assessment(r["jd_id"]) for r in linked]
    states = Counter(a["state"] if a else "raw" for a in assessments)
    positions = db.list_positions(None)
    result = coverage.coverage_report(positions)
    (output / "coverage.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    osnapshot = onet.status()
    summary = {"evaluated_at": datetime.now(timezone.utc).isoformat(),
               "discovered_sources": len(records), "employers": dict(Counter(r["company"] for r in records)),
               "stored_source_urls": len({r["source_url"] for r in linked}), "source_record_states": dict(states),
               "current_admitted_positions": sum(bool(p["admission"].get("current")) for p in positions),
               "eligible_family_stages": result["eligible_count"], "target_family_stages": result["target_count"],
               "ranking_version": ranking.VERSION, "onet_version": osnapshot.get("version"),
               "onet_tables": len(osnapshot.get("tables", [])),
               "onet_original_records": sum(t["rows"] for t in osnapshot.get("tables", [])),
               "onet_indexed_records": osnapshot.get("indexed_rows"),
               "limitations": ["四家企业样本有互联网企业偏差，不证明市场代表性", "AI检查不等于真实性/分类准确率已经验证", "合成学生案例不是15人试点或推荐准确率评估"]}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    cases = [
        {"name": "信息未补充", "answers": {}},
        {"name": "数据方向课程与实践", "answers": {"direction_interest": "大数据分析与决策", "skills": [
            {"name": "SQL", "evidence_source": "practical", "evidence_text": "合成案例：课程中编写查询", "artifact_reference": "虚构案例-SQL"},
            {"name": "Python", "evidence_source": "course", "evidence_text": "合成案例：学习过数据处理"},
            {"name": "统计"}]}},
        {"name": "只有沟通自述", "answers": {"skills": [{"name": "沟通"}]}},
        {"name": "产品需求与协作经历", "answers": {"direction_interest": "智能系统管理", "skills": [
            {"name": "产品需求分析", "evidence_source": "practical", "evidence_text": "合成案例：课程项目需求分析", "artifact_reference": "虚构案例-PRD"},
            {"name": "协作"}]}}
    ]
    evaluations = []
    for stage in ("实习", "校招"):
        for case in cases:
            answers = {"target_stage": stage, "persona": "研一 · 合成演示", **case["answers"]}
            p = profile.build_profile("synthetic-evaluation-only", answers)
            matched = match.match_all(positions, p, answers)
            adv = advice.build_advice(matched, p, answers)
            # These verify evidence discipline, not whether humans agree with ranking.
            if case["name"] == "信息未补充":
                assert all(not r["matched"] and not r["unverified"] and not r["gaps"] for r in matched)
            if case["name"] == "只有沟通自述":
                assert all(x["verdict"] != "有探索依据" for x in adv["positions"])
            alt = {**answers, "mbti": "ESFP"}
            another = match.match_all(positions, profile.build_profile("synthetic-evaluation-only", alt), alt)
            assert [(r["family"], r["ranking"]) for r in matched] == [(r["family"], r["ranking"]) for r in another]
            assert all(x["coverage"] is None and x["verified_coverage"] is None for x in adv["positions"])
            evaluations.append({"synthetic": True, "case": case["name"], "answers": answers,
                                "report": adv})
    (output / "synthetic-reports.json").write_text(json.dumps(evaluations, ensure_ascii=False, indent=2))
    lines = ["# 岗位覆盖与匹配实跑记录", "", f"快照时间：{summary['evaluated_at']}", "",
             f"发现 {len(records)} 个官方来源 URL，数据库记录状态为 {dict(states)}。发现量、检查通过量与可用岗位画像分别统计。",
             f"当前 {result['eligible_count']} / {result['target_count']} 个岗位族与阶段满足门槛。尚未完成全面岗位覆盖。", "",
             "## 实际覆盖", "", "|岗位族|阶段|有效去重JD|企业|状态|", "|---|---|---:|---:|---|"]
    for r in result["rows"]:
        lines.append(f"|{r['family']}|{r['stage']}|{r['jd_count']}|{r['employer_count']}|{'可比对' if r['eligible'] else '待补齐'}|")
    lines += ["", "## O*NET 与核验", "", f"O*NET {osnapshot.get('version')}：{summary['onet_tables']} 张表、{summary['onet_original_records']:,} 条原始记录；候选索引 {osnapshot.get('indexed_occupation_count')} 个职业、{osnapshot.get('indexed_rows'):,} 行。",
              "真实 RAG 问答见 [实跑记录](onet-rag-live.json)，原始记录核对见 [引用检查](onet-citation-checks.json)。第一批的补充分类审查见 [审查记录](family-audit.json)。", "",
              "## 合成学生检查", "", "|案例|阶段|展示方向及结论|", "|---|---|---|"]
    for r in evaluations:
        display = "；".join(p["name"]+"："+p["verdict"] for p in r["report"]["positions"]) or "无准入方向"
        lines.append(f"|{r['case']}|{r['answers']['target_stage']}|{display}|")
    lines += ["", "合成案例只检查未知不变缺口、通用能力不单独确定方向、MBTI不改变排序、不显示能力百分比。不能据此宣称推荐准确率。完整结构见 [案例输出](synthetic-reports.json)。", "",
              "## 验证与剩余工作", "", "63 项固定响应/临时数据库回归检查通过；页面流程通过，包含28项覆盖台账及已缓存的真实职业问答。Python/JavaScript语法、README同步与差异空白检查通过。",
              "四家企业来源集中于互联网业务，金融研究、部分供应链计划及信息系统等仍有覆盖缺口；需新增行业来源。排名参数、任务分类和中文检索还没有职场专家/真实学生的质量标注。15人两周试点尚未开展。", "",
              "大体积原始快照在本机忽略目录；现有业务库新增了原文及核验记录，处理前已备份。未提交或推送 Git。"]
    (output / "report.md").write_text("\n".join(lines)+"\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, default=Path("app/data/evidence/2026-10-11"))
    parser.add_argument("--output", type=Path, default=Path("docs/evaluations/2026-10-11-coverage"))
    args = parser.parse_args()
    db.init_db()
    print(json.dumps(evaluate(args.sources, args.output), ensure_ascii=False, indent=2))
