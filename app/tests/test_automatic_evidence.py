"""固定响应与临时数据库的回归检查；不调用外部模型/网站。"""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from careersafari import db, extractor, families, ingestion, match, profile, advice
from careersafari.schema import JdRecord, Position, Requirement


def example(company="甲企业", seq=1, stage="实习", published="2026-09-01"):
    task = f"分析业务指标并输出分析报告，负责专题{seq}。"
    requirement = "熟悉 SQL；具备沟通能力。"
    raw = f"公司名称：{company}\n岗位名称：数据分析{stage}\n职位类型：{stage}\n发布日期：{published}\n工作职责：{task}\n任职要求：{requirement}"
    ex = {"company": company, "company_span": f"公司名称：{company}",
          "title": f"数据分析{stage}", "job_type": stage, "job_type_span": f"职位类型：{stage}",
          "published_date": published, "published_date_span": f"发布日期：{published}",
          "family": "数据分析", "family_span": task, "work_scenarios": [task], "stressors": [],
          "requirements": [{"kind": "hard", "category": "工具", "text": "熟悉 SQL", "evidence_span": "熟悉 SQL"},
                           {"kind": "soft", "category": "软能力", "text": "具备沟通能力", "evidence_span": "具备沟通能力"}]}
    return raw, ex


def verifier_ok(system, payload, **kwargs):
    data = json.loads(payload)
    return {"ok": True, "model": "fixed-verifier", "content": json.dumps({"items": [
        {"claim_id": c["claim_id"], "verdict": "supported", "reason": "固定测试依据"} for c in data["claims"]]})}


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = str(Path(self.temp.name) / "test.db")
        db.init_db()
        self.fetch = patch.object(ingestion.fetcher, "fetch_document", side_effect=lambda url: ({"raw_text":self.raw,"html":"","final_url":url}, "", []))
        self.fetch.start()
        self.chat = patch.object(ingestion.llm, "chat", side_effect=verifier_ok)
        self.chat.start()
        self.raw, self.ex = example()

    def tearDown(self):
        self.chat.stop()
        self.fetch.stop()
        db.DB_PATH = self.old_path
        self.temp.cleanup()

    def ingest(self, raw=None, ex=None, url="https://example.org/jd/1", **kwargs):
        self.raw = raw or self.raw
        return ingestion.ingest(self.raw, url, supplied=ex or self.ex, **kwargs)

    def test_valid_input_auto_stores_and_cached_submission_does_not_call_again(self):
        first = self.ingest()
        self.assertEqual(first["state"], "ready")
        with patch.object(ingestion.llm, "chat", side_effect=AssertionError("应复用缓存")), \
             patch.object(ingestion.fetcher, "fetch_document", side_effect=AssertionError("应复用来源")):
            second = self.ingest()
        self.assertTrue(second["cached"])
        self.assertEqual(first["position_id"], second["position_id"])
        self.assertEqual(len(db.list_positions(None)), 1)
        self.assertTrue(db.list_positions()[0]["admission"]["current"])

    def test_valid_span_but_invented_sql_level_is_quarantined(self):
        ex = copy.deepcopy(self.ex)
        ex["requirements"][0]["text"] = "独立编写 JOIN 和窗口函数"
        def deny(system, payload, **kwargs):
            if system != ingestion.VERIFY_PROMPT:
                return {"ok": False, "content": "", "error": "无法修复"}
            response = verifier_ok(system, payload)
            data = json.loads(response["content"])
            next(i for i in data["items"] if i["claim_id"] == "req-0").update(verdict="unsupported", reason="原文未提到")
            response["content"] = json.dumps(data)
            return response
        with patch.object(ingestion.llm, "chat", side_effect=deny):
            r = self.ingest(ex=ex)
        self.assertEqual(r["state"], "review")
        self.assertEqual(db.list_positions(), [])
        self.assertEqual(len(db.list_assessments()), 1)

    def test_missing_span_never_downgrades_to_team(self):
        ex = copy.deepcopy(self.ex)
        ex["requirements"][0]["evidence_span"] = "不存在的引用"
        self.assertTrue(extractor.validate_extraction(ex, self.raw)["reject_all"])
        rows = extractor.to_requirements(ex, "P", self.raw)
        self.assertEqual(len(rows), 1)
        self.assertTrue(all(q["confidence"] == "external" for q in rows))

    def test_empty_and_malformed_requirements_rejected(self):
        for req in ([], {}, None):
            self.assertTrue(extractor.validate_extraction({"requirements": req}, self.raw)["reject_all"])
        bad = copy.deepcopy(self.ex)
        bad["family"] = ["数据分析"]
        ok, result = ingestion.verify(bad, self.raw)
        self.assertFalse(ok)

    def test_incomplete_ai_review_cannot_pass(self):
        with patch.object(ingestion.llm, "chat", return_value={"ok": True, "content": '{"items":[]}', "model": "fixed"}):
            ok, _ = ingestion.verify(self.ex, self.raw)
        self.assertFalse(ok)

    def test_one_repair_then_success(self):
        bad = copy.deepcopy(self.ex)
        bad["requirements"][0]["evidence_span"] = "错误"
        calls = []
        def repair(system, payload, **kwargs):
            calls.append(system)
            if system == ingestion.VERIFY_PROMPT:
                return verifier_ok(system, payload)
            return {"ok": True, "content": json.dumps(self.ex), "model": "fixed-repair"}
        with patch.object(ingestion.llm, "chat", side_effect=repair):
            result = self.ingest(ex=bad)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(calls.count(extractor.SYSTEM_PROMPT), 1)

    def test_unavailable_verifier_does_not_retry_or_admit(self):
        with patch.object(ingestion.llm, "chat", return_value={"ok": False, "error": "服务不可用", "content": ""}) as call:
            r = self.ingest()
        self.assertEqual(r["state"], "review")
        self.assertEqual(call.call_count, 1)

    def test_source_mismatch_no_ai_call(self):
        with patch.object(ingestion.fetcher, "fetch_document", return_value=({"raw_text":"完全不同的正文","html":"","final_url":"https://example.org/jd/1"}, "", [])), \
             patch.object(ingestion.llm, "chat", side_effect=AssertionError("来源不一致不应调用模型")):
            r = self.ingest()
        self.assertEqual(r["state"], "review")

    def test_source_receipt_allows_js_page_snapshots(self):
        db.save_jd(JdRecord(jd_id="JD-source", raw_text=self.raw, source_type="url_fetch",
                            source_url="https://example.org/jd/1"))
        db.save_source_snapshot("JD-source", self.raw, "https://example.org/jd/1", "bytedance_api")
        with patch.object(ingestion.fetcher, "fetch_document", side_effect=AssertionError("已有采集快照")):
            r = self.ingest(jd_id="JD-source")
        self.assertEqual(r["state"], "ready")

    def test_social_stage_excluded(self):
        raw, ex = example(stage="社招")
        r = self.ingest(raw, ex)
        self.assertEqual(r["state"], "excluded")
        self.assertEqual(db.list_positions(), [])

    def test_db_write_cannot_accept_invalid_external_reference(self):
        db.save_jd(JdRecord(jd_id="JD", raw_text=self.raw, source_type="test"))
        with self.assertRaises(ValueError):
            db.save_position(Position(position_id="P", canonical_name="数据分析", source_jd_ids=["JD"],
                requirements=[Requirement(req_id="Q", category="工具", text="SQL", evidence_span="伪造")]))
        self.assertEqual(db.list_positions(), [])

    def populate(self, stages=("实习",)):
        for stage in stages:
            for i in range(6):
                raw, ex = example(company=["甲企业", "乙企业", "丙企业"][i // 2], seq=i+1, stage=stage)
                self.ingest(raw, ex, f"https://example.org/{stage}/{i}")
        return db.list_positions(None)

    def test_family_threshold_stage_separation_and_shared_capabilities(self):
        positions = self.populate(("实习", "校招"))
        groups = families.aggregate(positions, date(2026, 10, 10))
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(g["eligible"] and g["jd_count"] == 6 and g["employer_count"] == 3 for g in groups))
        self.assertEqual({g["stage"] for g in groups}, {"实习", "校招"})
        self.assertTrue(all(m["employer_count"] == 3 for g in groups for m in g["matrix"]))

    def test_duplicate_content_url_and_body_do_not_inflate_family_count(self):
        positions = self.populate()
        duplicate = copy.deepcopy(positions[0])
        duplicate["admission"]["content_hash"] = "changed-header"
        duplicate["admission"]["source_url"] += "?tracking=1"
        groups = families.aggregate(positions + [duplicate], date(2026, 10, 10))
        self.assertEqual(groups[0]["jd_count"], 6)

    def test_missing_date_and_legacy_rows_cannot_fill_gate(self):
        positions = self.populate()
        positions[0]["admission"]["published_date"] = ""
        positions.append({"canonical_name": "数据分析", "admission": {}})
        g = families.aggregate(positions, date(2026, 10, 10))[0]
        self.assertFalse(g["eligible"])
        self.assertEqual(g["jd_count"], 5)
        self.assertEqual(g["candidate_count"], 6)

    def test_single_employer_capability_is_scenario_only(self):
        positions = self.populate()
        positions[0]["requirements"].append({"req_id": "one-python", "text": "熟悉 Python", "category": "工具", "confidence": "external", "evidence_span": "熟悉 Python"})
        matrix = families.aggregate(positions, date(2026, 10, 10))[0]["matrix"]
        self.assertEqual(next(m for m in matrix if m["capability"] == "PYTHON")["scope"], "scenario_specific")

    def test_missing_skills_are_unknown_not_gap_or_low_ability(self):
        positions = self.populate()
        prof = profile.build_profile("test", {"skills": []})
        report = match.match_all(positions, prof, {})
        self.assertEqual(len(report[0]["unknown"]), 2)
        self.assertEqual(report[0]["gaps"], [])
        result = advice.build_advice(report, prof, {})
        self.assertEqual(result["positions"][0]["verdict"], "需补充信息")
        self.assertEqual(result["learning_plan"], [])
        self.assertIsNone(result["positions"][0]["verified_coverage"])

    def test_checkbox_is_not_verified_and_practical_reference_is_separate(self):
        a = profile.build_profile("test", {"skills": [{"name": "SQL", "verified": True, "evidence_text": "听过 SQL 课程"}]})
        self.assertFalse(a.skills[0].verified)
        self.assertEqual(a.skills[0].evidence_state, "course_or_self_report")
        b = profile.build_profile("test", {"skills": [{"name": "SQL", "evidence_source": "practical", "evidence_text": "负责取数", "artifact_reference": "作品-1"}]})
        self.assertEqual(b.skills[0].evidence_state, "artifact_provided")
        result = match.match_all(self.populate(), b, {})
        self.assertEqual(len(result[0]["matched"]), 1)
        self.assertEqual(len(result[0]["unknown"]), 1)

    def test_degree_enrolment_and_related_major_not_automatically_met(self):
        degree = match._threshold_check({"req_id": "Q", "category": "学历", "text": "硕士及以上"}, {"grade": "研一"})
        major = match._threshold_check({"req_id": "Q", "category": "专业", "text": "计算机相关专业"}, {"major": "管理科学与工程"})
        self.assertEqual(degree["status"], "unknown")
        self.assertEqual(major["status"], "unknown")

    def test_recheck_failure_removes_previously_admitted_data(self):
        self.ingest()
        with patch.object(ingestion.llm, "chat", return_value={"ok": False, "error": "失败", "content": ""}):
            r = self.ingest(retry=True)
        self.assertEqual(r["state"], "review")
        self.assertFalse(db.list_positions()[0]["admission"]["current"])
        self.assertEqual(families.aggregate(db.list_positions(), date(2026, 10, 10)), [])

    def test_api_authentication_auto_ingestion_and_legacy_confirm_gate(self):
        from fastapi.testclient import TestClient
        from careersafari.main import app
        from careersafari.security import parse_limiter
        parse_limiter._hits.clear()
        headers = {"X-Admin-Token": "test-only-token"}
        with patch.dict(os.environ, {"ADMIN_TOKEN": "test-only-token"}), TestClient(app) as client:
            denied = client.post("/api/jd/ingest", json={"raw_text": self.raw})
            self.assertEqual(denied.status_code, 401)
            self.assertEqual(db.list_jds(), [])
            with patch.object(extractor, "extract_from_jd", return_value=(self.ex, {"model": "fixed"})):
                result = client.post("/api/jd/ingest", headers=headers,
                    json={"raw_text": self.raw, "source_url": "https://example.org/jd/1"}).json()
            self.assertEqual(result["state"], "ready")
            payload = {"raw_text": self.raw, "source_url": "https://example.org/jd/1",
                       "extraction": self.ex, "requirements": [{"kind": "hard", "category": "工具", "text": "Spark", "evidence_span": "不存在", "confidence": "external"}]}
            with patch.object(ingestion.llm, "chat", return_value={"ok": False, "content": "", "error": "不可修复"}):
                tampered = client.post("/api/jd/confirm", headers=headers, json=payload).json()
            self.assertEqual(tampered["state"], "review")
            self.assertFalse(db.list_positions()[0]["admission"]["current"])
            self.assertEqual(client.get("/api/jd/reviews", headers=headers).json()["reviews"][0]["state"], "review")
            result = client.post("/api/profile", json={"user_id": "test", "answers": {"skills": []}}).json()
            self.assertEqual(result["advice"]["positions"], [])
            self.assertEqual(client.get("/").status_code, 200)

    def test_query_job_ids_are_not_removed_as_tracking(self):
        positions = self.populate()
        for i, p in enumerate(positions):
            p["admission"]["source_url"] = f"https://example.org/detail?jobUnionId={i}"
        self.assertEqual(families.aggregate(positions, date(2026, 10, 10))[0]["jd_count"], 6)

    def test_negated_or_occasional_travel_does_not_exclude(self):
        from careersafari.curriculum import dealbreaker_conflict
        self.assertFalse(dealbreaker_conflict("长期出差", "无需出差")[0])
        self.assertFalse(dealbreaker_conflict("长期出差", "偶尔出差")[0])
        self.assertTrue(dealbreaker_conflict("长期出差", "需要长期出差")[0])

    def test_legacy_database_migration_preserves_position_and_is_repeatable(self):
        import sqlite3
        old = db.DB_PATH
        legacy = str(Path(self.temp.name) / "legacy.db")
        with sqlite3.connect(legacy) as conn:
            conn.execute("CREATE TABLE positions (position_id TEXT PRIMARY KEY,canonical_name TEXT NOT NULL,industry TEXT,variant_names TEXT,work_scenarios TEXT,stressors TEXT,source_jd_ids TEXT,schema_version TEXT,created_at TEXT)")
            conn.execute("INSERT INTO positions VALUES ('OLD','数据分析','','[]','[]','[]','[]','0.1.0','2026-09-01')")
        try:
            db.DB_PATH = legacy
            db.init_db()
            db.init_db()
            rows = db.list_positions()
            self.assertEqual(rows[0]["position_id"], "OLD")
            self.assertFalse(rows[0]["admission"]["current"])
            self.assertEqual(families.aggregate(rows), [])
        finally:
            db.DB_PATH = old

    def test_date_window_includes_24_calendar_months_and_rejects_future(self):
        self.assertTrue(families.recent("2024-10-10", date(2026, 10, 10)))
        self.assertFalse(families.recent("2024-10-09", date(2026, 10, 10)))
        self.assertFalse(families.recent("2026-10-11", date(2026, 10, 10)))


if __name__ == "__main__":
    unittest.main()
