from types import SimpleNamespace
import unittest

from verify_l1 import provider_usage


class ProviderUsageTests(unittest.TestCase):
    def test_api_usage(self):
        result = SimpleNamespace(llm_output={"token_usage": {"prompt_tokens": 120, "completion_tokens": 30,
                                                            "total_tokens": 150}}, generations=[])
        self.assertEqual(provider_usage(result), {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150})

    def test_stream_message_usage(self):
        message = SimpleNamespace(usage_metadata={"input_tokens": 50, "output_tokens": 10}, response_metadata={})
        result = SimpleNamespace(llm_output=None, generations=[[SimpleNamespace(message=message)]])
        self.assertEqual(provider_usage(result)["total_tokens"], 60)

    def test_missing_usage_stays_unknown(self):
        self.assertIsNone(provider_usage(SimpleNamespace(llm_output=None, generations=[])))


if __name__ == "__main__":
    unittest.main()
