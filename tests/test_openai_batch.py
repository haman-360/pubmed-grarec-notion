from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from openai_batch import _multipart_body, extract_response_text  # noqa: E402


class OpenAIBatchTests(unittest.TestCase):
    def test_extract_response_text_ignores_non_message_output(self) -> None:
        body = {
            "output": [
                {"type": "reasoning", "summary": []},
                {"type": "message", "content": [{"type": "output_text", "text": '{"pmid":"123"}'}]},
            ]
        }
        self.assertEqual(extract_response_text(body), '{"pmid":"123"}')

    def test_multipart_body_contains_fields_file_and_closing_boundary(self) -> None:
        body, boundary = _multipart_body(
            {"purpose": "batch"},
            {"file": ('unsafe".jsonl', b'{"x":1}\n', "application/jsonl")},
        )
        self.assertIn(b'name="purpose"', body)
        self.assertIn(b'filename="unsafe.jsonl"', body)
        self.assertIn(b'{"x":1}\n', body)
        self.assertTrue(body.endswith(f"--{boundary}--\r\n".encode()))


if __name__ == "__main__":
    unittest.main()
