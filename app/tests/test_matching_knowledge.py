"""Behavioral regressions for cold-start matching, source binding and local retrieval."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from careersafari import match, profile, families, ranking, onet, source_adapters, fetcher, advice
from careersafari.coverage import coverage_report, ONET_CROSSWALK
from careersafari.curriculum import capabilities_of, CAPABILITIES
from careersafari.ingestion import PIPELINE_VERSION


def positions(family="数据分析", skills=("SQL", "Python"), employer_copies=2):
    rows = []
    for employer in ("甲", "乙", "丙"):
        for i in range(employer_copies if employer == "甲" else 2):
            key = f"{family}-{employer}-{i}"
            rows.append({"position_id": key, "source_jd_ids": [key], "canonical_name": family,
                "work_scenarios": [f"分析业务并产出报告，专题 {key}"], "stressors": [],
                "requirements": [{"req_id": key+str(j), "text": s, "category": "工具", "confidence": "external", "evidence_span": s}
                                 for j, s in enumerate(skills)],
                "admission": {"current": True, "state": "ready", "pipeline_version": PIPELINE_VERSION,
                    "source_checked": True, "family": family, "stage": "实习", "company": employer,
                    "published_date": "2026-09-01", "content_hash": key, "source_url": f"https://example.org/{key}"}})
    return rows


class MatchingKnowledgeTests(unittest.TestCase):
    def test_long_role_no_longer_wins_by_raw_evidence_count(self):
        rows = positions("数据分析", ("SQL", "Python")) + positions("产品经理", ("SQL", "Python", "Excel", "沟通", "协作", "统计"))
        p = profile.build_profile("test", {"skills": [{"name": s} for s in ("SQL", "Python", "Excel")]})
        result = match.match_all(rows, p, {})
        # Old raw-count sort would choose product (3 vs 2); normalized evidence chooses data (all 2).
        self.assertEqual(result[0]["family"], "数据分析")
        self.assertLess(result[1]["ranking"]["evidence_lower"], result[0]["ranking"]["evidence_lower"])

    def test_unknown_and_explicit_gap_keep_distinct_intervals(self):
        rows = positions()
        unknown = match.match_all(rows, profile.build_profile("test", {}), {})[0]
        gap = match.match_all(rows, profile.build_profile("test", {"skills": [{"name": "SQL", "confirmed_gap": True}]}), {})[0]
        self.assertEqual(unknown["gaps"], [])
        self.assertEqual(unknown["ranking"]["evidence_upper"], 1)
        self.assertLess(gap["ranking"]["evidence_upper"], 1)
        self.assertFalse(unknown["ranking"]["has_personal_evidence"])

    def test_generic_communication_alone_cannot_outrank_professional_evidence(self):
        rows = positions("数据分析", ("SQL", "Python", "统计", "沟通")) + positions("产品运营", ("沟通",))
        p = profile.build_profile("test", {"skills": [{"name": "SQL"}, {"name": "沟通"}]})
        report = match.match_all(rows, p, {})
        self.assertEqual(report[0]["family"], "数据分析")
        self.assertGreater(report[1]["ranking"]["evidence_lower"], report[0]["ranking"]["evidence_lower"])
        self.assertFalse(report[1]["ranking"]["has_professional_evidence"])

    def test_employer_volume_does_not_dominate_requirement_weight(self):
        def weight(copies):
            rows = positions(employer_copies=copies)
            for p in rows:
                if p["admission"]["company"] == "丙":
                    p["requirements"] = [r for r in p["requirements"] if r["text"] != "Python"]
            return next(x["weight"] for x in families.aggregate(rows, date(2026,10,11))[0]["matrix"] if x["capability"] == "PYTHON")
        self.assertEqual(weight(2), weight(20))

    def test_preferred_requirement_remains_weaker(self):
        rows = positions()
        for p in rows:
            p["requirements"][1]["text"] = "Python 优先"
        matrix = {m["capability"]: m for m in families.aggregate(rows)[0]["matrix"]}
        self.assertLess(matrix["PYTHON"]["weight"], matrix["SQL"]["weight"])

    def test_missing_onet_and_mbti_never_change_ranking(self):
        rows = positions()+positions("产品经理", ("Excel", "沟通"))
        def rank(mbti):
            answers = {"mbti": mbti, "skills": [{"name": "SQL"}]}
            return [(r["family"], r["ranking"]) for r in match.match_all(rows, profile.build_profile("t", answers), answers)]
        self.assertEqual(rank("INTJ"), rank("ESFP"))
        with patch.dict(os.environ, {"CAREERSAFARI_ONET_DB": "/nonexistent/onet.db"}):
            self.assertFalse(onet.status()["available"])
            self.assertEqual(onet.retrieve("数据分析", "SQL"), [])
            self.assertEqual(rank("INTJ"), rank("ESFP"))

    def test_minimum_degree_alternatives_and_preference_not_excluded(self):
        req = {"req_id": "r", "category": "学历", "text": "本科、硕士、博士均可"}
        self.assertEqual(match._threshold_check(req, {"attained_degree": "本科"})["status"], "supported")
        req["text"] = "硕士优先"
        self.assertNotEqual(match._threshold_check(req, {"attained_degree": "本科"})["status"], "conflict")

    def test_explicit_degree_conflict_is_jd_specific(self):
        rows = positions()
        rows[0]["requirements"].append({"req_id": "degree", "category": "学历", "text": "硕士及以上", "confidence": "external"})
        p = profile.build_profile("t", {})
        report = match.match_all(rows, p, {"attained_degree": "本科"})[0]
        self.assertFalse(report["excluded"])
        self.assertEqual(report["jd_count"], 5)
        self.assertFalse(report["eligible"])

    def test_enrolled_student_not_excluded_by_ambiguous_degree_wording(self):
        answers = {"attained_degree": "本科", "grade": "研一", "target_stage": "实习"}
        req = {"req_id": "r", "category": "学历", "text": "硕士及以上学历"}
        self.assertEqual(match._threshold_check(req, answers)["status"], "unknown")
        req["text"] = "必须已取得硕士学位"
        self.assertEqual(match._threshold_check(req, answers)["status"], "conflict")

    def test_taxonomy_does_not_invent_programming_or_finance(self):
        self.assertNotIn("PYTHON", capabilities_of("数据处理与清洗"))
        self.assertNotIn("SQL", capabilities_of("从接口取数"))
        self.assertNotIn("FIN", capabilities_of("variables"))
        self.assertIn("PROCUREMENT", capabilities_of("供应商寻源"))

    def test_new_professional_form_options_map_to_their_own_capability(self):
        for cap in CAPABILITIES:
            self.assertIn(cap["id"], capabilities_of(cap["name"]))
        options = profile.questionnaire()["job_specific_skills"]
        self.assertIn("采购与供应商管理", options["MSE-OSCM"])
        self.assertIn("产品需求分析与设计", options["MSE-ISM"])

    def test_coverage_includes_unfilled_family_stage_cells(self):
        report = coverage_report(positions())
        self.assertEqual(len(report["rows"]), 28)
        self.assertEqual(report["eligible_count"], 1)
        self.assertEqual({r["family"] for r in report["rows"]}, set(ONET_CROSSWALK))

    def test_empty_report_keeps_all_candidate_directions_without_recommendations(self):
        p = profile.build_profile("t", {})
        report = advice.build_advice([], p, {})
        self.assertEqual(report["positions"], [])
        self.assertEqual(len(report["candidate_families"]), 14)
        self.assertTrue(all(r["jd_count"]==0 for r in report["candidate_families"]))

    def test_source_type_tree_disambiguates_regular_campus_and_social(self):
        post = {"id": "123", "title": "数据分析", "publish_time": 1786938172000,
                "description": "数据分析", "requirement": "SQL", "recruit_type": {"name": "正式", "parent": {"name": "校招"}}}
        url = "https://jobs.bytedance.com/campus/position/123/detail"
        doc = source_adapters.document({"provider": "bytedance", "record": post}, url, "official")
        self.assertEqual(fetcher.source_metadata(doc["html"], url)["facts"]["job_type"]["value"], "校招")
        post["recruit_type"]["parent"]["name"] = "社招"
        self.assertEqual(source_adapters.byte_stage(post), "社招")
        self.assertEqual(fetcher.source_metadata(doc["html"], url.replace("/123/", "/124/")), {})
        self.assertEqual(fetcher.source_metadata(doc["html"], url.replace("bytedance.com", "bytedance.com.evil.org")), {})

    def test_jd_date_requires_same_identity_in_both_responses(self):
        p = {"provider": "jd", "listing": {"publishId": 10, "reqId": 2, "positionName": "数据分析", "publishTime": 1786938172000},
             "detail": {"publishId": 10, "reqId": 3, "positionName": "数据分析", "recruitType": "应届生"}}
        meta = source_adapters.api_metadata(json.dumps(p), "https://campus.jd.com/api/wx/position/detail/10")
        self.assertTrue(meta["conflicts"])
        self.assertNotIn("published_date", meta["facts"])


if __name__ == "__main__":
    unittest.main()
