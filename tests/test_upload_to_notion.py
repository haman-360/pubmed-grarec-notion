from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from upload_to_notion import AI_SECTION_TITLE, ai_generated_toggle, build_chatgpt_summary_payload  # noqa: E402


class NotionPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.summary = {
            "pmid": "42115808",
            "title": "論文タイトル",
            "journal": "Journal",
            "year": "2026",
            "topic": ["小児科"],
            "study_type": "RCT",
            "one_line_summary": "要点",
            "summary_jp": "日本語要約",
            "pico": "PICO",
            "evidence_notes": "Table 2",
            "source_level": "user_pdf",
        }

    def test_generated_section_is_owned_toggle(self) -> None:
        toggle = ai_generated_toggle(self.summary)
        self.assertEqual(toggle["type"], "toggle")
        self.assertEqual(toggle["toggle"]["rich_text"][0]["text"]["content"], AI_SECTION_TITLE)
        headings = [
            block[block["type"]]["rich_text"][0]["text"]["content"]
            for block in toggle["toggle"]["children"]
            if block["type"] == "heading_2"
        ]
        self.assertIn("Evidence Notes", headings)
        self.assertIn("Source Level", headings)

    def test_payload_sets_known_database_properties(self) -> None:
        schema = {
            "Title": {"type": "title"},
            "PMID": {"type": "rich_text"},
            "Study Type": {"type": "select"},
            "Take Home Message": {"type": "rich_text"},
            "Summary JP": {"type": "rich_text"},
        }
        payload = build_chatgpt_summary_payload(self.summary, "database", {"properties": schema})
        properties = payload["properties"]
        self.assertEqual(properties["PMID"]["rich_text"][0]["text"]["content"], "42115808")
        self.assertEqual(properties["Study Type"]["select"]["name"], "RCT")
        self.assertEqual(properties["Summary JP"]["rich_text"][0]["text"]["content"], "日本語要約")


if __name__ == "__main__":
    unittest.main()
