from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from fetch_pubmed import fetch_pubmed_article
from generate_summary import generate_graphic_prompt, generate_summary
from upload_to_notion import create_notion_page, notion_credentials_from_env


ROOT = Path(__file__).resolve().parents[1]
INPUT_PMIDS = ROOT / "input" / "pmids.txt"
OUTPUT_SUMMARIES = ROOT / "output" / "summaries"
OUTPUT_PROMPTS = ROOT / "output" / "prompts"


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch PubMed articles and prepare Notion records.")
    parser.add_argument("--pmid", action="append", help="PMID to process. Can be repeated.")
    parser.add_argument("--pmids-file", default=str(INPUT_PMIDS), help="File with one PMID per line.")
    parser.add_argument("--topic", action="append", help="Topic to apply to all records. Can be repeated.")
    parser.add_argument("--graphic-url", default="", help="Graphic image URL for the Notion cover/property.")
    parser.add_argument("--notion", action="store_true", help="Create pages in Notion when credentials are configured.")
    parser.add_argument("--email", default=os.getenv("NCBI_EMAIL", ""), help="Email passed to NCBI Entrez.")
    args = parser.parse_args()

    pmids = args.pmid or read_pmids(Path(args.pmids_file))
    if not pmids:
        raise SystemExit("No PMID found. Add one to input/pmids.txt or pass --pmid.")

    OUTPUT_SUMMARIES.mkdir(parents=True, exist_ok=True)
    OUTPUT_PROMPTS.mkdir(parents=True, exist_ok=True)

    created_pages = []
    for pmid in pmids:
        article = fetch_pubmed_article(pmid, email=args.email or None)
        summary = generate_summary(article, topic=args.topic)
        summary["graphic_url"] = args.graphic_url

        summary_path = OUTPUT_SUMMARIES / f"PMID_{article.pmid}.json"
        prompt_path = OUTPUT_PROMPTS / f"PMID_{article.pmid}_graphic_prompt.txt"
        write_json(summary_path, summary)
        prompt_path.write_text(generate_graphic_prompt(summary), encoding="utf-8")

        print(f"Wrote {summary_path}")
        print(f"Wrote {prompt_path}")

        if args.notion:
            token, database_id = notion_credentials_from_env()
            if not token or not database_id:
                raise SystemExit("NOTION_TOKEN and NOTION_DATABASE_ID are required in .env for --notion.")
            page = create_notion_page(summary, database_id, token, graphic_url=args.graphic_url)
            created_pages.append({"pmid": article.pmid, "notion_page_id": page.get("id"), "url": page.get("url")})
            print(f"Created Notion page: {page.get('url')}")

    if created_pages:
        write_json(ROOT / "output" / "notion_pages.json", created_pages)


def read_pmids(path: Path) -> list[str]:
    pmids = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        pmids.append(value)
    return pmids


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
