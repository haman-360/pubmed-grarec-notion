from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from upload_to_notion import (
    build_chatgpt_summary_payload,
    notion_credentials_from_env,
    upsert_chatgpt_summary_page,
)


ROOT = Path(__file__).resolve().parents[1]
INPUT_SUMMARIES = ROOT / "input" / "chatgpt_summaries"


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a ChatGPT close-reading JSON into Notion.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pmid", help="PMID. Reads input/chatgpt_summaries/PMID_<pmid>_chatgpt.json.")
    source.add_argument("--file", help="Path to a ChatGPT summary JSON file.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Print the Notion payload without writing to Notion.")
    mode.add_argument("--notion", action="store_true", help="Create or update the Notion page.")
    args = parser.parse_args()

    path = Path(args.file) if args.file else INPUT_SUMMARIES / f"PMID_{args.pmid}_chatgpt.json"
    if not path.is_absolute():
        path = ROOT / path
    summary = normalize_chatgpt_summary(read_json(path))

    if args.dry_run:
        payload = build_chatgpt_summary_payload(summary, "DRY_RUN_DATABASE_ID", database=fallback_database_schema())
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "source": str(path),
                    "pmid": summary.get("pmid"),
                    "notion_lookup": {"property": "PMID", "equals": str(summary.get("pmid", ""))},
                    "payload": payload,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    token, database_id = notion_credentials_from_env()
    if not token or not database_id:
        raise SystemExit("NOTION_TOKEN and NOTION_DATABASE_ID are required in .env for --notion.")

    page = upsert_chatgpt_summary_page(summary, database_id, token)
    print(
        json.dumps(
            {
                "pmid": summary.get("pmid"),
                "action": page.get("import_action"),
                "page_id": page.get("id"),
                "url": page.get("url"),
                "source": str(path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"ChatGPT summary JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_chatgpt_summary(data: dict[str, Any]) -> dict[str, Any]:
    normalized = {str(key).lower(): value for key, value in data.items()}
    aliases = {
        "pmid": ["pmid", "PMID"],
        "title": ["title"],
        "journal": ["journal"],
        "year": ["year"],
        "doi": ["doi", "DOI"],
        "topic": ["topic"],
        "one_line_summary": ["one_line_summary", "take_home_message"],
        "practice_change": ["practice_change"],
        "human_checked": ["human_checked"],
        "pico": ["pico", "PICO"],
        "figure_table_summary": ["figure_table_summary"],
        "main_results": ["main_results"],
        "safety": ["safety"],
        "limitations": ["limitations"],
        "applicability_to_japanese_pediatric_clinic": ["applicability_to_japanese_pediatric_clinic"],
        "tomorrow_action": ["tomorrow_action"],
        "graphic_url": ["graphic_url"],
    }

    result: dict[str, Any] = {}
    for target, names in aliases.items():
        for name in names:
            value = data.get(name)
            if value is None:
                value = normalized.get(str(name).lower())
            if value is not None:
                result[target] = value
                break

    if not result.get("pmid"):
        raise SystemExit("ChatGPT summary JSON must include PMID or pmid.")
    if not result.get("title"):
        raise SystemExit("ChatGPT summary JSON must include title.")
    return result


def fallback_database_schema() -> dict[str, Any]:
    return {
        "properties": {
            "Title": {"type": "title"},
            "PMID": {"type": "rich_text"},
            "PubMed URL": {"type": "url"},
            "DOI": {"type": "rich_text"},
            "Journal": {"type": "select"},
            "Year": {"type": "number"},
            "Topic": {"type": "multi_select"},
            "Practice Change": {"type": "select"},
            "Take Home Message": {"type": "rich_text"},
            "Graphic URL": {"type": "url"},
            "Graphic Image": {"type": "files"},
            "Created By AI": {"type": "checkbox"},
            "Human Checked": {"type": "checkbox"},
        }
    }


if __name__ == "__main__":
    main()
