from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from upload_to_notion import (  # noqa: E402
    AI_SECTION_TITLE,
    AI_SUMMARY_CALLOUT_PREFIX,
    ai_generated_review_blocks,
    ai_generated_toggle,
    build_graphic_update_payload,
    build_chatgpt_summary_payload,
)


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

    def test_japanese_summary_is_visible_in_callout(self) -> None:
        blocks = ai_generated_review_blocks(self.summary)
        self.assertEqual([block["type"] for block in blocks], ["callout", "toggle"])
        text = blocks[0]["callout"]["rich_text"][0]["text"]["content"]
        self.assertEqual(text, f"{AI_SUMMARY_CALLOUT_PREFIX}日本語要約")

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

    def test_graphic_update_keeps_web_and_auto_images(self) -> None:
        database = {"properties": {"Graphic URL": {"type": "url"}, "Graphic Image": {"type": "files"}}}
        web = "https://example.test/PMID_1_grarec_web.png"
        automatic = "https://example.test/PMID_1_grarec.png"
        payload = build_graphic_update_payload(database, web, [automatic])
        self.assertEqual(payload["cover"]["external"]["url"], web)
        self.assertEqual(payload["properties"]["Graphic URL"]["url"], web)
        files = payload["properties"]["Graphic Image"]["files"]
        self.assertEqual([item["external"]["url"] for item in files], [web, automatic])

    def test_graphic_filename_ignores_cache_query(self) -> None:
        database = {"properties": {"Graphic Image": {"type": "files"}}}
        payload = build_graphic_update_payload(database, "https://example.test/image.png?v=abc")
        self.assertEqual(payload["properties"]["Graphic Image"]["files"][0]["name"], "image.png")


if __name__ == "__main__":
    unittest.main()
