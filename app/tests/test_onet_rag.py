import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from careersafari import rag, onet, source_adapters


class RagTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"CAREERSAFARI_ONET_DB": str(Path(self.temp.name)/"knowledge.db")})
        self.env.start()
        self.refs = [{"citation_id": "ONET-31.0:task_statements:1261", "soc": "13-1023.00",
                      "excerpt": "Research and evaluate suppliers based on price and quality.", "version": "31.0"}]
        self.retrieve = patch.object(onet, "retrieve", return_value=self.refs)
        self.retrieve.start()

    def tearDown(self):
        self.retrieve.stop(); self.env.stop(); self.temp.cleanup()

    def response(self, statements=None):
        return {"ok": True, "model": "fixed", "content": json.dumps({"statements": statements if statements is not None else [
            {"text": "该美国职业根据价格和质量评估供应商。", "citation_ids": [self.refs[0]["citation_id"]]}]})}

    def test_no_retrieved_evidence_makes_no_ai_call(self):
        with patch.object(onet, "retrieve", return_value=[]), patch.object(rag.llm, "chat") as chat:
            self.assertEqual(rag.answer("采购", "供应商")["status"], "insufficient_evidence")
            chat.assert_not_called()

    def test_fabricated_citation_rejected_before_verifier(self):
        generated = self.response([{"text": "需要MBA", "citation_ids": ["invented"]}])
        with patch.object(rag.llm, "chat", return_value=generated) as chat:
            result = rag.answer("采购", "资格")
            self.assertEqual(result["status"], "citation_check_failed")
            self.assertEqual(result["statements"], [])
            self.assertEqual(chat.call_count, 1)

    def test_real_citation_cannot_support_invented_statement(self):
        bad = self.response([{"text": "中国实习生必须有MBA", "citation_ids": [self.refs[0]["citation_id"]]}])
        checked = {"ok": True, "content": json.dumps({"items": [{"index": 0, "verdict": "unsupported"}]})}
        with patch.object(rag.llm, "chat", side_effect=[bad, checked]):
            result = rag.answer("采购", "资格")
            self.assertEqual(result["status"], "semantic_check_failed")
            self.assertEqual(result["statements"], [])

    def test_supported_response_is_cached_and_never_used_for_ranking(self):
        checked = {"ok": True, "content": json.dumps({"items": [{"index": 0, "verdict": "supported"}]})}
        with patch.object(rag.llm, "chat", side_effect=[self.response(), checked]) as chat:
            first = rag.answer("采购", "供应商")
            second = rag.answer("采购", "供应商")
            self.assertEqual(chat.call_count, 2)
            self.assertEqual(first["status"], "supported")
            self.assertTrue(second["cached"])
            self.assertFalse(second["ranking_use"])

    def test_invalid_question_type_fails_without_exception(self):
        self.assertEqual(rag.answer([], {})["status"], "invalid_input")

    def test_meituan_update_does_not_become_publication_date(self):
        payload = {"provider": "meituan", "record": {"jobUnionId": "123", "name": "采购实习生", "jobType": "2",
                      "refreshTime": 1786938172000, "firstPostTime": None}}
        meta = source_adapters.api_metadata(json.dumps(payload), "https://zhaopin.meituan.com/web/position/detail?jobUnionId=123")
        self.assertEqual(meta["facts"]["job_type"]["value"], "实习")
        self.assertNotIn("published_date", meta["facts"])
        self.assertIn("updated_date", meta["facts"])


if __name__ == "__main__":
    unittest.main()
