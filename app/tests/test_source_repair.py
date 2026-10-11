"""真实失败对应的回归边界；网站与模型使用固定响应，不占用真实模型额度。"""
import copy
import json
import os
import unittest
from unittest.mock import patch
import test_automatic_evidence as fixtures
from careersafari import db, extractor, fetcher, ingestion


def page(raw, route="INTERN", stage=None, post_id="sample"):
    data = {"detailData": {"recruitType": stage or route, "postInfo": {
        "postId": post_id, "name": "数据分析实习", "publishDate": "2026-03-12",
        "updateDate": "2026-07-21"}, "unused": None}}
    payload = json.dumps(data, ensure_ascii=False).replace('"unused": null', '"unused": undefined')
    return '<html><title>百度校园招聘</title><body>'+raw+'<script>window.__INITIAL_DATA__ ='+payload+';</script></body></html>'


class SourceRepairTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.EvidenceTests()
        self.fixture.setUp()
        self.raw = self.fixture.raw.replace("公司名称：甲企业\n", "").replace("职位类型：实习\n", "")
        self.url = "https://talent.baidu.com/jobs/detail/INTERN/sample"
        self.doc = {"raw_text": self.raw, "html": page(self.raw), "final_url": self.url,
                    "requested_url": self.url, "method": "http"}

    def tearDown(self):
        self.fixture.tearDown()

    def run_jd(self, doc=None, raw=None, ex=None, url=None):
        doc = doc or self.doc
        with patch.object(fetcher, "fetch_document", return_value=(doc, "", [])):
            return ingestion.ingest(raw or self.raw, url or self.url, supplied=ex or self.fixture.ex)

    def test_official_fields_parse_and_publication_not_update_date(self):
        m = fetcher.source_metadata(self.doc["html"], self.url)
        self.assertEqual(m["facts"]["company"]["value"], "百度")
        self.assertEqual(m["facts"]["job_type"]["value"], "实习")
        self.assertEqual(m["facts"]["published_date"]["value"], "2026-03-12")
        self.assertEqual(m["facts"]["updated_date"]["value"], "2026-07-21")

    def test_fake_host_or_wrong_job_identity_cannot_provide_fields(self):
        self.assertEqual(fetcher.source_metadata(self.doc["html"], self.url.replace('talent.baidu.com','evil.talent.baidu.com')), {})
        self.assertEqual(fetcher.source_metadata(page(self.raw,post_id="different"), self.url), {})

    def test_official_company_overrides_department_and_is_audited(self):
        ex=copy.deepcopy(self.fixture.ex); ex["company"]="ACG";ex["company_span"]="ACG"
        r=self.run_jd(ex=ex)
        self.assertEqual(r["state"],"ready")
        a=db.get_assessment(r["jd_id"])["details"]
        self.assertEqual(a["attempts"][0]["model_extraction"]["company"],"ACG")
        self.assertEqual(db.list_positions()[0]["admission"]["company"],"百度")
        company=next(c for c in a["attempts"][0]["check"]["claims"] if c["claim_id"]=="company")
        self.assertEqual(company["basis"],"source")
        self.assertTrue(db.get_source_snapshot(r["jd_id"])["document"]["html"])

    def test_title_cannot_be_replaced_by_other_text_on_the_page(self):
        ex=copy.deepcopy(self.fixture.ex);ex["title"]="熟悉 SQL"
        source=fetcher.source_metadata(self.doc["html"],self.url)
        ex,_=ingestion._bind_source(ex,source)
        _,_,problems=ingestion._claims(ex,self.raw,source)
        self.assertIn("岗位名称与官方来源详情不一致",problems)

    def test_old_pipeline_is_browsable_but_not_current(self):
        self.run_jd()
        with db.connect() as con:
            p=con.execute('SELECT position_id,admission_json FROM positions').fetchone()
            admission=json.loads(p['admission_json']);admission['pipeline_version']='auto-evidence-1'
            con.execute('UPDATE positions SET admission_json=? WHERE position_id=?',(json.dumps(admission),p['position_id']))
        self.assertEqual(len(db.list_positions()),1)
        self.assertFalse(db.list_positions()[0]['admission']['current'])

    def test_social_routes_excluded_before_model(self):
        url=self.url.replace("INTERN","SOCIAL")
        doc={**self.doc,"html":page(self.raw,route="SOCIAL"),"final_url":url}
        with patch.object(ingestion.llm,"chat",side_effect=AssertionError("社招不应调用模型")), \
             patch.object(extractor,"extract_from_jd",side_effect=AssertionError("社招不应提取")):
            r=self.run_jd(doc=doc,url=url)
        self.assertEqual(r["state"],"excluded")
        self.assertEqual(db.get_assessment(r["jd_id"])["details"]["attempts"],[])

    def test_stage_conflict_is_quarantined(self):
        doc={**self.doc,"html":page(self.raw,stage="SOCIAL")}
        with patch.object(ingestion.llm,"chat",side_effect=AssertionError("冲突不应调用模型")):
            r=self.run_jd(doc=doc)
        self.assertEqual(r["state"],"review")
        self.assertIn("不一致",r["reasons"][0])

    def test_discontinuous_quotes_separately_checked_and_preserved(self):
        ex=copy.deepcopy(self.fixture.ex)
        ex["family_spans"]=["分析业务指标并输出分析报告", "熟悉 SQL"]
        ex["requirements"][0]["evidence_spans"]=["熟悉 SQL", "任职要求："]
        r=self.run_jd(ex=ex)
        self.assertEqual(r["state"],"ready")
        req=db.list_positions()[0]["requirements"][0]
        self.assertEqual(req["evidence_spans"],["熟悉 SQL","任职要求："])
        a=db.get_assessment(r["jd_id"])["details"]["attempts"][0]
        spans=next(c["spans"] for c in a["check"]["claims"] if c["claim_id"]=="family")
        self.assertTrue(all(s["raw_start"] is not None for s in spans))
        ex["requirements"][0]["evidence_spans"].append("编造的第三段")
        self.assertTrue(extractor.validate_extraction(ex,self.raw)["reject_all"])

    def test_fake_supplied_metadata_ignored(self):
        doc={**self.doc,"html":"","metadata":{"facts":{"company":{"value":"伪造企业"}}}}
        ex=copy.deepcopy(self.fixture.ex);ex["company"]="";ex["company_span"]=""
        with patch.object(ingestion.llm,"chat",side_effect=AssertionError("缺来源不做无效修正")):
            r=self.run_jd(doc=doc,ex=ex)
        self.assertEqual(r["state"],"review")

    def test_ids_resolve_original_text_and_unknown_ids_cannot_bypass(self):
        ex=copy.deepcopy(self.fixture.ex)
        segments=extractor.source_segments(self.raw)
        task=next(s for s in segments if "工作职责" in s["text"])
        requirement=next(s for s in segments if "任职要求" in s["text"])
        ex["family_evidence_ids"]=[task["id"]]
        ex["work_scenario_ids"]=[task["id"]]
        ex["stressor_ids"]=[]
        ex["requirements"][0]["evidence_ids"]=[requirement["id"]]
        r=self.run_jd(ex=ex)
        self.assertEqual(r["state"],"ready")
        self.assertEqual(db.list_positions()[0]["requirements"][0]["evidence_spans"],[requirement["text"]])
        ex["family_evidence_ids"]=["S99999"]
        resolved=extractor.resolve_references(ex,self.raw)
        self.assertIn("岗位分类引用编号无效",resolved["_reference_errors"])
        self.assertFalse(ingestion.verify(resolved,self.raw)[0])

    def test_recapture_changed_official_fields_invalidates_cache(self):
        db.save_capture(self.url,self.doc)
        first=ingestion.ingest(self.raw,self.url,supplied=self.fixture.ex)
        self.assertEqual(first["state"],"ready")
        updated={**self.doc,"html":self.doc["html"].replace('2026-03-12','2026-04-01')}
        db.save_capture(self.url,updated)
        second=ingestion.ingest(self.raw,self.url,supplied=self.fixture.ex)
        self.assertFalse(second["cached"])
        self.assertEqual(db.list_positions()[0]["admission"]["published_date"],"2026-04-01")

    def test_capture_cache_avoids_second_fetch_and_failure_resolves(self):
        db.save_capture(self.url,error="采集失败")
        self.assertEqual(len(db.source_failures()),1)
        db.save_capture(self.url,self.doc)
        self.assertEqual(db.source_failures(),[])
        with patch.object(fetcher,"fetch_document",side_effect=AssertionError("必须复用服务器采集")):
            r=ingestion.ingest(self.raw,self.url,supplied=self.fixture.ex)
        self.assertEqual(r["state"],"ready")

    def test_ui_api_distinguishes_failed_capture_from_empty_library(self):
        from fastapi.testclient import TestClient
        from careersafari.main import app
        from careersafari.security import parse_limiter
        parse_limiter._hits.clear()
        with patch.dict(os.environ,{"ADMIN_TOKEN":"test-only-token"}),TestClient(app) as client:
            with patch.object(fetcher,"fetch_document",return_value=(None,"静态与浏览器均失败",[])):
                data=client.post('/api/jd/fetch',json={"url":self.url}).json()
            self.assertFalse(data["ok"])
            q=client.get('/api/jd/reviews',headers={"X-Admin-Token":"test-only-token"}).json()
            self.assertEqual(len(q["source_failures"]),1)
            a=client.post('/api/advice',json={"answers":{}}).json()["advice"]
            self.assertIn("采集失败",a["headline"])
            ex=copy.deepcopy(self.fixture.ex);ex["company"]="";ex["company_span"]=""
            with patch.object(fetcher,"fetch_document",return_value=({**self.doc,"html":""},"",[])), \
                 patch.object(extractor,"extract_from_jd",return_value=(ex,{})):
                client.post('/api/jd/ingest',headers={"X-Admin-Token":"test-only-token"},json={"raw_text":self.raw,"source_url":self.url})
            a=client.post('/api/advice',json={"answers":{}}).json()["advice"]
            self.assertIn("1 份待确认",a["headline"])
            self.assertEqual(a["positions"],[])


if __name__ == '__main__': unittest.main()
