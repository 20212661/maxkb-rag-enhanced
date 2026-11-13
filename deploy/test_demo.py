import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("import_demo", Path(__file__).resolve().parents[1] /
                                            "demo-data/import_demo.py")
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)


class DemoTests(unittest.TestCase):
    def test_pending_scan_with_no_paragraphs_waits_for_embedding(self):
        pending = {"name": "scan.pdf", "paragraph_count": 0, "status": "0", "mineru_parse_status": "pending"}
        ready = {**pending, "paragraph_count": 3, "status": "2", "mineru_parse_status": "succeeded"}
        api = Mock()
        api.request.side_effect = [[pending], [ready]]
        with patch.object(demo.time, "sleep") as sleep:
            self.assertEqual(demo.wait_documents(api, "/knowledge/demo", ["scan.pdf"], 60), [ready])
        sleep.assert_called_once_with(5)
        self.assertEqual(api.request.call_count, 2)

    def test_failed_or_fallback_parse_fails_acceptance(self):
        for status in ("failed", "fallback"):
            api = Mock()
            api.request.return_value = [{"name": "scan.pdf", "paragraph_count": 3,
                                         "status": "2", "mineru_parse_status": status}]
            with self.subTest(status=status), self.assertRaises(demo.APIError):
                demo.wait_documents(api, "/knowledge/demo", ["scan.pdf"], 60)

    def test_failed_embedding_fails_acceptance(self):
        api = Mock()
        api.request.return_value = [{"name": "scan.pdf", "paragraph_count": 3,
                                     "status": "3", "mineru_parse_status": "succeeded"}]
        with self.assertRaises(demo.APIError):
            demo.wait_documents(api, "/knowledge/demo", ["scan.pdf"], 60)

    def test_duplicate_named_resources_are_rejected(self):
        with self.assertRaises(ValueError):
            demo.named([{"name": "demo"}, {"name": "demo"}], "demo")

    def test_missing_knowledge_binding_is_rejected_after_publish(self):
        api = Mock()
        api.request.side_effect = [[], {"id": "app"}, {"knowledge_id_list": []}, [{"id": "version"}]]
        with self.assertRaisesRegex(demo.APIError, "knowledge binding"):
            demo.prepare_application(api, "/workspace/default", "knowledge", "model")


if __name__ == "__main__":
    unittest.main()
