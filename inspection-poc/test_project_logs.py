import unittest

from project_logs import project


class ProjectionTests(unittest.TestCase):
    def row(self):
        return {
            "poc_schema": "copilot-model-v1",
            "time": "2026-11-05T00:00:00Z",
            "exchange_id": "11223344-1122-3344-5566-112233445566",
            "host": "api.enterprise.githubcopilot.com",
            "method": "POST",
            "path": "/chat/completions?secret=DO_NOT_LOG",
            "status": 200,
            "model_capture_status": "captured",
            "requested_model_raw": "exact-model-name",
            "model_source": "request.body.model",
            "authorization": "DO_NOT_LOG",
            "request.body": "DO_NOT_LOG",
        }

    def test_exact_model_and_whitelist(self):
        result = project(self.row(), {"client": "cli", "run_id": "synthetic"})
        self.assertEqual(result["requested_model_raw"], "exact-model-name")
        self.assertEqual(result["path"], "/chat/completions")
        self.assertNotIn("DO_NOT_LOG", str(result))
        self.assertIsNone(result["response_model_raw"])

    def test_llm_request_and_response_model(self):
        row = self.row()
        row.update(model_source="llm.requestModel", response_model_raw="served-model-2026")
        result = project(row, {})
        self.assertEqual(result["model_source"], "llm.requestModel")
        self.assertEqual(result["response_model_raw"], "served-model-2026")

    def test_unknown_model_source_is_rejected(self):
        row = self.row()
        row["model_source"] = "DO_NOT_LOG"
        result = project(row, {})
        self.assertEqual(result["model_capture_status"], "expression_error")
        self.assertNotIn("DO_NOT_LOG", str(result))

    def test_413_without_body_is_preserved(self):
        row = self.row()
        row.update(status=413, model_capture_status="http_413_no_body_captured",
                   requested_model_raw=None, model_source=None)
        result = project(row, {})
        self.assertEqual(result["status"], 413)
        self.assertEqual(result["model_capture_status"], "http_413_no_body_captured")
        self.assertIsNone(result["requested_model_raw"])

    def test_expression_failure_is_visible(self):
        row = self.row()
        del row["model_capture_status"]
        result = project(row, {})
        self.assertEqual(result["model_capture_status"], "expression_error")
        self.assertIsNone(result["requested_model_raw"])

    def test_unexpected_record_is_not_persisted(self):
        self.assertIsNone(project({"level": "warn", "message": "DO_NOT_LOG"}, {}))

    def test_reject_arbitrary_model_text(self):
        row = self.row()
        row["requested_model_raw"] = "DO NOT LOG THIS PAYLOAD\n"
        result = project(row, {})
        self.assertEqual(result["model_capture_status"], "invalid_model_identifier")
        self.assertIsNone(result["requested_model_raw"])

    def test_bad_host(self):
        row = self.row()
        row["host"] = "example.com"
        with self.assertRaises(ValueError):
            project(row, {})


if __name__ == "__main__":
    unittest.main()
