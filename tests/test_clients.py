import unittest
from unittest import mock

from ki_council.clients import GeminiClient, LLMError


def _gemini_response(finish_reason, parts=None, usage=None):
    candidate = {"finishReason": finish_reason}
    if parts is not None:
        candidate["content"] = {"parts": parts}
    response = {"candidates": [candidate]}
    if usage is not None:
        response["usageMetadata"] = usage
    return response


class GeminiEmptyContentTests(unittest.TestCase):
    """Regression: usage wurde frueher erst nach dem finish_reason-Check
    definiert -- MAX_TOKENS mit leeren parts crashte mit NameError statt
    LLMError (NameError ist nicht im except-Tupel und schlug roh durch)."""

    def setUp(self):
        self.client = GeminiClient(api_key="test-key", model="gemini-test")

    def _generate(self, response):
        with mock.patch("ki_council.clients._post_json", return_value=response):
            return self.client.generate("prompt", max_tokens=100)

    def test_max_tokens_with_thoughts_raises_llmerror_not_nameerror(self):
        response = _gemini_response(
            "MAX_TOKENS",
            parts=[],
            usage={"thoughtsTokenCount": 95, "promptTokenCount": 10},
        )
        with self.assertRaises(LLMError) as ctx:
            self._generate(response)
        self.assertIn("95 tokens", str(ctx.exception))
        self.assertIn("thinking model", str(ctx.exception))

    def test_max_tokens_without_thoughts_generic_message(self):
        response = _gemini_response("MAX_TOKENS", parts=[], usage={"promptTokenCount": 10})
        with self.assertRaises(LLMError) as ctx:
            self._generate(response)
        self.assertIn("max_tokens limit", str(ctx.exception))

    def test_max_tokens_without_usage_metadata(self):
        # Auch ganz ohne usageMetadata darf der Fehlerpfad nicht crashen
        response = _gemini_response("MAX_TOKENS", parts=[])
        with self.assertRaises(LLMError) as ctx:
            self._generate(response)
        self.assertIn("max_tokens limit", str(ctx.exception))

    def test_safety_block_raises_llmerror(self):
        response = _gemini_response("SAFETY", parts=[])
        with self.assertRaises(LLMError) as ctx:
            self._generate(response)
        self.assertIn("SAFETY", str(ctx.exception))

    def test_success_path_still_parses_tokens(self):
        response = _gemini_response(
            "STOP",
            parts=[{"text": "Antwort"}],
            usage={
                "promptTokenCount": 5,
                "candidatesTokenCount": 7,
                "totalTokenCount": 12,
            },
        )
        result = self._generate(response)
        self.assertEqual(result.content, "Antwort")
        self.assertEqual(result.tokens_prompt, 5)
        self.assertEqual(result.tokens_completion, 7)
        self.assertEqual(result.tokens_used, 12)


if __name__ == "__main__":
    unittest.main()
