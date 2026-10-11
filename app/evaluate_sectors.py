"""Export this industry expansion independently of the earlier internet-source report."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from careersafari import db, coverage, families, fetcher
from collect_sectors import OUT, target_candidate

OUTPUT = Path(__file__).resolve().parents[1] / "docs/evaluations/2026-10-11-sector-sources"


def evaluate(sources=OUT, output=OUTPUT):
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((sources / "manifest.json").read_text())
    records = manifest["records"]
    by_url = {r["url"]: r for r in records}
    linked = [r for r in db.list_jds(100000) if r["source_url"] in by_url]
    scope_candidates = []
    # Export only: a queue heuristic is not authority to change an admission decision.
    for row in linked:
        a = db.get_assessment(row["jd_id"])
        r = by_url[row["source_url"]]
        if a and a["state"] == "ready" and not target_candidate(r):
            scope_candidates.append({"jd_id": row["jd_id"], "title": r["title"], "state": a["state"],
                                     "reason": "标题触发首批学生范围复查；本报告不修改审核结果"})
    (output / "scope-candidates.json").write_text(json.dumps(scope_candidates, ensure_ascii=False, indent=2))
    states = Counter()
    employers = {}
    metadata_checks = []
    for r in records:
        doc = json.loads((sources / r["file"]).read_text())
        m = fetcher.source_metadata(doc["html"], r["url"])
        assert not m.get("conflicts") and m.get("facts", {}).get("company", {}).get("value") == r["company"]
        assert m["facts"]["title"]["value"] == r["title"]
        assert m["facts"].get("published_date", {}).get("value", "") == r["published_date"]
        metadata_checks.append({"url": r["url"], "company": r["company"], "identity_bound": True, "published_date": r["published_date"]})
        entry = employers.setdefault(r["company"], {"sources": 0, "dated": 0, "recent": 0, "states": Counter()})
        entry["sources"] += 1
        entry["dated"] += bool(r["published_date"])
        entry["recent"] += families.recent(r["published_date"], datetime.now().date())
    for row in linked:
        a = db.get_assessment(row["jd_id"])
        state = a["state"] if a else "raw"
        states[state] += 1
        employers[by_url[row["source_url"]]["company"]]["states"][state] += 1
    positions = db.list_positions(None)
    new_positions = [p for p in positions if p["admission"].get("current") and p["admission"].get("source_url") in by_url]
    for p in new_positions:
        r = by_url[p["admission"]["source_url"]]
        assert p["admission"].get("published_date", "") == r["published_date"], "Publication cannot be invented from another source field"
    report = coverage.coverage_report(positions)
    baseline = json.loads((output.parents[0] / "2026-10-11-coverage/coverage.json").read_text())
    before = {(r["family"], r["stage"]): r for r in baseline["rows"]}
    summary = {"evaluated_at": datetime.now(timezone.utc).isoformat(), "new_source_urls": len(records),
               "employers": employers, "source_record_states": dict(states), "stored_source_urls": len(linked),
               "new_current_admitted_positions": len(new_positions),
               "new_ready_with_unknown_publication": sum(not p["admission"].get("published_date") for p in new_positions),
               "current_admitted_positions": sum(bool(p["admission"].get("current")) for p in positions),
               "previous_eligible_family_stages": baseline["eligible_count"], "eligible_family_stages": report["eligible_count"],
               "target_family_stages": report["target_count"], "discovery_errors": manifest["errors"],
               "ai_check_count": len(json.loads((sources / "admission-results.json").read_text())),
               "source_adapters_rechecked": len(metadata_checks),
               "scope_review_candidates": len(scope_candidates),
               "limitations": ["本轮金融机构集中于证券，未证明行业代表性", "供应链新增企业发布日期缺失，不能计入近期覆盖门槛", "AI 检查不等于员工核验或真实学生匹配质量验证"]}
    for name, value in [("summary.json", summary), ("coverage.json", report), ("source-checks.json", metadata_checks)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))
    lines = ["# 金融与供应链来源补充实跑", "", f"统计时间：{summary['evaluated_at']}", "",
             f"新增 {len(records)} 个官方岗位来源 URL，{len(linked)} 个已保留在业务库。完成 {summary['ai_check_count']} 次岗位提取/审查流程（不等于模型调用次数）；本次数据库快照状态为 {dict(states)}。",
             f"新增当前检查通过岗位 {len(new_positions)} 个，其中 {summary['new_ready_with_unknown_publication']} 个发布日期未知，只作为可浏览的岗位参考。",
             f"岗位族×阶段满足门槛的数量：{baseline['eligible_count']} → {report['eligible_count']} / {report['target_count']}。", "",
             "## 新增企业来源", "", "|企业集团|来源 URL|明确发布日期|日期在近24个月|检查通过|待确认|未检查|范围排除|", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for company, data in sorted(employers.items()):
        s = data["states"]
        lines.append(f"|{company}|{data['sources']}|{data['dated']}|{data['recent']}|{s.get('ready',0)}|{s.get('review',0)}|{s.get('raw',0)}|{s.get('excluded',0)}|")
    lines += ["", "## 金融与供应链的实际覆盖", "", "|岗位族|阶段|之前 JD/企业|现在 JD/企业|正式可比对|", "|---|---|---|---|---|"]
    for r in report["rows"]:
        if r["family"] not in ("金融分析与研究", "供应链计划", "采购", "物流履约", "风控合规", "运筹优化与仿真"):
            continue
        b = before[(r["family"], r["stage"])]
        lines.append(f"|{r['family']}|{r['stage']}|{b['jd_count']}/{b['employer_count']}|{r['jd_count']}/{r['employer_count']}|{'是' if r['eligible'] else '待补齐'}|")
    lines += ["", "门槛保持 6 份近期去重 JD、3 家独立企业，共同能力至少由 2 家企业支持。未用招聘季年份、创建时间、刷新时间或抓取时间补造发布日期。", "",
              "## 边界与可复核记录", "", "来源绑定已对本轮所有快照重算，并确认所有新增当前通过岗位的发布日期与官方字段完全一致。HTML、接口字段、招聘项目标记和原始 AI 提取/审查保存在本机快照及业务库。",
              f"本轮首次队列包含少量泛研发岗位；审查失败保持隔离，后续队列已收窄。当前 {len(scope_candidates)} 条检查通过记录触发标题范围复查，包括博士后候选；尚未处理，不应视为适合首批研一学生。导出报告未修改业务数据。", "",
              "实际源字段检查见 [来源检查](source-checks.json)，岗位台账见 [完整覆盖](coverage.json)，范围待复查见 [范围记录](scope-candidates.json)。企业入口与规则见 [行业来源说明](../../行业岗位来源.md)。", "",
              "金融来源集中于证券机构；美的、顺丰的新增岗位发布日期缺失，供应链正式匹配仍有缺口。下一轮应优先补充带首次发布时间的制造、零售及物流雇主。未开展15人两周试点，不能据此宣称推荐准确率。", "",
              "新来源未提交或推送 Git。数据库修改前已备份，原始快照位于本机忽略目录。"]
    (output / "report.md").write_text("\n".join(lines)+"\n")
    return summary


if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))
