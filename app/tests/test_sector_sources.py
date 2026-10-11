"""Industry intake regression: identity, ambiguous projects, absent publication and resume."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from careersafari import fetcher, sector_sources, families
import collect_sectors
from test_matching_knowledge import positions


class SectorSourcesTests(unittest.TestCase):
    def test_broad_research_titles_do_not_spend_target_cohort_queue(self):
        for title in ("健康研究工程师", "水处理技术研究工程师", "AI算法应用研究工程师"):
            self.assertFalse(collect_sectors.target_candidate({"company": "美的集团", "title": title}))
        for title in ("产销计划", "供应链管理", "战略采购", "算法工程师-运筹优化"):
            self.assertTrue(collect_sectors.target_candidate({"company": "美的集团", "title": title}))
        self.assertFalse(collect_sectors.target_candidate({"company": "中金公司", "title": "博士后研究员"}))

    def cicc(self, route="campusxq", flag="1"):
        url = f"https://cicc.zhiye.com/custom/{route}?jobAdId=test-id&jc=2"
        payload = {"provider": "cicc", "record": {"Id": "test-id", "JobAdName": "研究岗", "Category": "校园招聘/暑期实习", "CategoryId": "2", "Kind": "全职", "PostDate": "2026-09-03T17:03:37", "EndTime": "2027-01-01", "Duty": "研究行业并输出报告", "Require": "熟悉财务分析"},
                   "custom": {"JobAdId": "test-id", "CustomFields": {sector_sources.CICC_FIELD: flag}}}
        return payload, url

    def test_cicc_combined_category_requires_exact_project_flag(self):
        p, url = self.cicc()
        m = fetcher.source_metadata(json.dumps(p), url)
        self.assertEqual(m["facts"]["job_type"]["value"], "校招")
        self.assertEqual(m["facts"]["published_date"]["value"], "2026-09-03")
        p["custom"] = {}
        m = sector_sources.metadata(json.dumps(p), url)
        self.assertTrue(m["conflicts"])
        self.assertNotIn("job_type", m["facts"])

    def test_summer_and_project_intern_routes_are_not_interchangeable(self):
        p, url = self.cicc("summerxq", "2")
        self.assertEqual(sector_sources.metadata(json.dumps(p), url)["facts"]["job_type"]["value"], "实习")
        self.assertTrue(sector_sources.metadata(json.dumps(p), url.replace("summerxq", "campusxq"))["conflicts"])
        p["record"].update(CategoryId="3", Category="项目实习", Kind="实习")
        self.assertTrue(sector_sources.metadata(json.dumps(p), url)["conflicts"])
        self.assertFalse(sector_sources.metadata(json.dumps(p), url.replace("summerxq", "projectxq"))["conflicts"])

    def test_cicc_other_tenant_and_job_id_cannot_supply_facts(self):
        p, url = self.cicc()
        self.assertEqual(sector_sources.metadata(json.dumps(p), url.replace("cicc.zhiye.com", "other.zhiye.com")), {})
        self.assertEqual(sector_sources.metadata(json.dumps(p), url.replace("test-id", "wrong")), {})

    def test_midea_project_must_match_and_position_code_is_not_date(self):
        url = "https://careers.midea.com/schoolOut/post/details?positionId=one"
        p = {"provider": "midea", "record": {"positionId": "one", "projectRuleId": "project", "projectType": "1", "projectPositionName": "采购", "employementCategory": 1, "projectPositionDto": {"positionCode": "S2026100900001", "jobResponsibility": "供应商寻源", "jobRequirement": "熟悉采购管理"}},
             "project": {"projectRuleId": "project", "projectType": "1", "projectRuleName": "2027届校园招聘", "employementCategory": 1, "graduationStartDate": 1767196800000}}
        doc = sector_sources.document(p, url, "official")
        self.assertNotIn("published_date", doc["metadata"]["facts"])
        self.assertNotIn("发布日期", doc["raw_text"])
        p["project"]["projectRuleId"] = "wrong"
        self.assertTrue(sector_sources.metadata(json.dumps(p), url)["conflicts"])

    def test_sf_creation_and_refresh_do_not_become_publication(self):
        p = {"provider": "sf", "record": {"id": 10, "positionName": "物流规划", "seasonType": "2", "seasonName": "2027届秋季校园招聘", "createDate": "2026-08-12", "updateDate": "2026-10-11"}}
        url = "https://campus.sf-express.com/postDetail/10"
        m = sector_sources.metadata(json.dumps(p), url)
        self.assertNotIn("published_date", m["facts"])
        self.assertEqual(m["facts"]["updated_date"]["value"], "2026-10-11")
        p["record"].update(seasonType="3", seasonName="日常实习生招聘")
        self.assertEqual(sector_sources.metadata(json.dumps(p), url)["facts"]["job_type"]["value"], "实习")
        p["record"]["seasonName"] = "2027届校园招聘"
        self.assertTrue(sector_sources.metadata(json.dumps(p), url)["conflicts"])

    def test_citics_query_cannot_override_social_or_unknown_detail_type(self):
        url = "https://careers.citics.com/bmzyzwxq/index.html?positionNo=1&practice=0"
        p = {"provider": "citics", "response_type": "05", "record": {"positionNo": "1", "positionName": "研究", "type": "社会招聘"}}
        self.assertTrue(sector_sources.metadata(json.dumps(p), url)["conflicts"])
        p.update(response_type="08"); p["record"]["type"] = "校园招聘"
        self.assertEqual(sector_sources.metadata(json.dumps(p), url)["facts"]["job_type"]["value"], "校招")
        self.assertTrue(sector_sources.metadata(json.dumps(p), url.replace("practice=0", "practice=1"))["conflicts"])

    def test_hotjob_exact_employer_tenant_and_first_publication(self):
        url = f"https://wecruit.hotjob.cn/{sector_sources.SDIC_TENANT}/pb/posDetail.html?postId=one&postType=intern"
        p = {"provider": "sdic", "record": {"postId": "one", "company": "国投证券股份有限公司", "postName": "研究实习", "recruitType": 12, "publishFirstDate": "2026-06-17 09:00:00", "publishDate": "2026-09-29", "endDate": "3000-12-31"},
             "listing": {"postId": "one", "currentSuiteKey": sector_sources.SDIC_TENANT[2:], "recruitType": 12}}
        m = fetcher.source_metadata(json.dumps(p), url)
        self.assertEqual(m["facts"]["published_date"]["value"], "2026-06-17")
        self.assertEqual(m["facts"]["updated_date"]["value"], "2026-09-29")
        self.assertEqual(sector_sources.metadata(json.dumps(p), url.replace(sector_sources.SDIC_TENANT, "SUother")), {})
        p["listing"]["currentSuiteKey"] = "other"
        self.assertTrue(sector_sources.metadata(json.dumps(p), url)["conflicts"])

    def test_ky_html_job_identity_and_explicit_detail_stage(self):
        url = "https://www.kysec.cn/index.php?a=showjob&catid=851&id=123"
        html = '<title>实习生招募 - 开源证券股份有限公司</title><div class="tx-joinc-text"><h3>实习生招聘</h3><h1>研究岗实习生</h1><p>发布时间：2026-09-30</p></div><a id="shenqing" href="https://wecruit.hotjob.cn/SU654f2e0b3538bc6c4d600eab/pb/posDetail.html?postId=123&amp;postType=intern">申请</a>'
        self.assertEqual(fetcher.source_metadata(html, url)["facts"]["published_date"]["value"], "2026-09-30")
        self.assertEqual(fetcher.source_metadata(html, url.replace("id=123", "id=wrong")), {})
        self.assertTrue(fetcher.source_metadata(html, url.replace("catid=851", "catid=849"))["conflicts"])

    def test_malformed_optional_fields_fail_conservatively(self):
        p, url = self.cicc()
        for value in ([], "bad", {"JobAdId": "test-id", "CustomFields": "bad"}):
            p["custom"] = value
            self.assertTrue(sector_sources.metadata(json.dumps(p), url)["conflicts"])
        for encoded in ('[]', '{"record":[]}', 'null', 'bad'):
            self.assertEqual(sector_sources.metadata(encoded, url), {})

    def test_unknown_date_ready_records_still_do_not_pass_coverage(self):
        rows = positions()
        for row in rows:
            row["admission"]["published_date"] = ""
        groups = families.aggregate(rows)
        self.assertTrue(all(g["jd_count"] == 0 and not g["eligible"] for g in groups))

    def test_sector_queue_includes_unknown_dates_and_skips_already_reviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "new.json").write_text(json.dumps({"raw_text": "新岗位职责与要求"}))
            records = [{"url": "https://example.org/new", "file": "new.json", "company": "美的集团", "stage": "校招", "title": "采购", "published_date": ""},
                       {"url": "https://example.org/review", "file": "absent.json", "company": "顺丰集团", "stage": "校招", "title": "物流", "published_date": ""}]
            with patch.object(collect_sectors.db, "list_jds", return_value=[{"jd_id": "old", "source_url": records[1]["url"]}]), \
                 patch.object(collect_sectors.db, "get_assessment", return_value={"state": "review"}), \
                 patch.object(collect_sectors.db, "get_jd", return_value={"raw_text": "旧岗位"}):
                self.assertEqual(collect_sectors.select(records, path, 10), records[:1])


if __name__ == "__main__":
    unittest.main()
