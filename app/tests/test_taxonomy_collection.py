"""Regression cases from real admission failures and resumable collection costs."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from careersafari import families, ingestion
from careersafari.taxonomy import boundary_problems
from test_matching_knowledge import positions
from test_automatic_evidence import example
import collect_evidence
import audit_families


class TaxonomyCollectionTests(unittest.TestCase):
    def test_algorithm_tasks_not_promoted_to_business_analysis(self):
        tasks = ["制定电商平台算法策略促进生态发展", "建设成长预测算法能力支持商家入驻"]
        self.assertTrue(boundary_problems("商业/经营分析", tasks))
        rows = positions("商业/经营分析")
        for p in rows:
            p["work_scenarios"] = tasks
        self.assertEqual(families.aggregate(rows), [])
        # An analysis report mentioning algorithms is not silently rejected.
        self.assertEqual(boundary_problems("数据分析", tasks+["输出算法效果分析报告"]), [])

    def test_verifier_receives_task_definitions_not_literal_label_rule(self):
        raw, ex = example()
        ex.update(family="运筹优化与仿真", family_span="分析业务指标并输出分析报告，负责专题1。")
        captured = []
        def check(system, payload, **kwargs):
            data = json.loads(payload)
            captured.append(data)
            return {"ok": True, "content": json.dumps({"items": [
                {"claim_id": c["claim_id"], "verdict": "unsupported" if c["kind"]=="family" else "supported"}
                for c in data["claims"]]})}
        with patch.object(ingestion.llm, "chat", side_effect=check):
            self.assertFalse(ingestion.verify(ex, raw)[0])
        self.assertIn("命中其中之一即可", captured[0]["family_definitions"]["运筹优化与仿真"])

    def test_resume_skips_ready_and_quarantined_sources(self):
        records = [{"url": f"https://example.org/{i}", "stage": "实习", "company": "甲",
                    "title": "数据分析", "published_date": "2026-09-01", "discovery_family": "数据分析"} for i in range(3)]
        existing = [{"jd_id": str(i), "source_url": r["url"]} for i, r in enumerate(records)]
        with patch.object(collect_evidence.db, "list_jds", return_value=existing), \
             patch.object(collect_evidence.db, "get_assessment", side_effect=lambda i: {"state": "review" if i=="1" else "ready"} if i!="2" else None), \
             patch.object(collect_evidence.db, "list_positions", return_value=[]):
            chosen = collect_evidence.select_records(records, 10)
        self.assertEqual([r["url"] for r in chosen], [records[2]["url"]])

    def test_missing_independent_employer_precedes_more_same_employer(self):
        rows = positions()
        for p in rows:
            if p["admission"]["company"]=="丙":
                p["admission"]["company"]="乙"
        records = [{"url": f"https://example.org/new-{c}", "stage": "实习", "company": c,
                    "title": "数据分析", "published_date": "2026-09-01", "discovery_family": "数据分析"} for c in ("甲", "丁")]
        with patch.object(collect_evidence.db, "list_jds", return_value=[]), \
             patch.object(collect_evidence.db, "list_positions", return_value=rows):
            chosen = collect_evidence.select_records(records, 1)
        self.assertEqual(chosen[0]["company"], "丁")

    def test_malformed_supplemental_audit_never_reaffirms_admission(self):
        with patch.object(audit_families.llm, "chat", return_value={"ok": True, "content": '{"items":[]}'}):
            result = audit_families.check_batch(positions()[:2])
        self.assertTrue(all(i["verdict"]=="unknown" and not i["response_valid"] for i in result))


if __name__ == "__main__":
    unittest.main()
