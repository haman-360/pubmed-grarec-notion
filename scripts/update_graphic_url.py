from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote

from upload_to_notion import notion_credentials_from_env, update_notion_page_cover_and_graphic_url


ROOT = Path(__file__).resolve().parents[1]
NOTION_PAGES = ROOT / "output" / "notion_pages.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Update an existing Notion page with a GitHub Pages graphic URL.")
    parser.add_argument("--pmid", required=True, help="PMID used to find the Notion page and image.")
    parser.add_argument("--page-id", help="Notion page ID. Defaults to output/notion_pages.json lookup.")
    parser.add_argument("--image-path", help="Repository-relative image path.")
    parser.add_argument("--base-url", help="GitHub Pages base URL. Defaults to GITHUB_PAGES_BASE_URL or haman-360 Pages URL.")
    args = parser.parse_args()

    token, database_id = notion_credentials_from_env()
    if not token or not database_id:
        raise SystemExit("NOTION_TOKEN and NOTION_DATABASE_ID are required in .env.")

    page_id = args.page_id or find_page_id(args.pmid)
    image_path = args.image_path or f"images/2026/05/PMID_{args.pmid}_grarec.png"
    base_url = (args.base_url or os.getenv("GITHUB_PAGES_BASE_URL") or "https://haman-360.github.io/pubmed-grarec-notion").rstrip("/")
    graphic_url = f"{base_url}/{quote(image_path)}"

    page = update_notion_page_cover_and_graphic_url(page_id, database_id, token, graphic_url)
    print(json.dumps({"pmid": args.pmid, "page_id": page.get("id"), "url": page.get("url"), "graphic_url": graphic_url}, ensure_ascii=False, indent=2))


def find_page_id(pmid: str) -> str:
    if not NOTION_PAGES.exists():
        raise SystemExit("No output/notion_pages.json found. Pass --page-id.")
    pages = json.loads(NOTION_PAGES.read_text(encoding="utf-8"))
    for page in pages:
        if str(page.get("pmid")) == str(pmid):
            page_id = page.get("notion_page_id")
            if page_id:
                return page_id
    raise SystemExit(f"No Notion page found for PMID {pmid}. Pass --page-id.")


if __name__ == "__main__":
    main()
