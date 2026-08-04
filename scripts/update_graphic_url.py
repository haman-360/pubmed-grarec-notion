from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote

from upload_to_notion import find_notion_page_by_pmid, notion_credentials_from_env, update_notion_page_cover_and_graphic_url


ROOT = Path(__file__).resolve().parents[1]
NOTION_PAGES = ROOT / "output" / "notion_pages.json"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Update an existing Notion page with a GitHub Pages graphic URL.")
    parser.add_argument("--pmid", required=True, help="PMID used to find the Notion page and image.")
    parser.add_argument("--page-id", help="Notion page ID. Defaults to output/notion_pages.json lookup.")
    parser.add_argument("--image-path", help="Repository-relative image path.")
    parser.add_argument(
        "--additional-image-path",
        action="append",
        default=[],
        help="Additional image shown after the preferred image. Can be repeated.",
    )
    parser.add_argument(
        "--prefer-web",
        action="store_true",
        help="Use PMID_<id>_grarec_web as cover and keep the automatic image second.",
    )
    parser.add_argument("--base-url", help="GitHub Pages base URL. Defaults to GITHUB_PAGES_BASE_URL or haman-360 Pages URL.")
    args = parser.parse_args()

    token, database_id = notion_credentials_from_env()
    if not token or not database_id:
        raise SystemExit("NOTION_TOKEN and NOTION_DATABASE_ID are required in .env.")

    page_id = args.page_id or find_page_id(args.pmid)
    if args.prefer_web:
        image_path = find_grarec_path(args.pmid, "web")
        additional_paths = [find_grarec_path(args.pmid, "auto")]
    else:
        image_path = normalize_image_path(args.image_path) if args.image_path else find_latest_grarec_path(args.pmid)
        additional_paths = [normalize_image_path(path) for path in args.additional_image_path]
    base_url = (args.base_url or os.getenv("GITHUB_PAGES_BASE_URL") or "https://haman-360.github.io/pubmed-grarec-notion").rstrip("/")
    graphic_url = f"{base_url}/{quote(image_path)}"
    additional_urls = [f"{base_url}/{quote(path)}" for path in additional_paths]

    page = update_notion_page_cover_and_graphic_url(page_id, database_id, token, graphic_url, additional_urls)
    print(json.dumps({"pmid": args.pmid, "page_id": page.get("id"), "url": page.get("url"), "graphic_urls": [graphic_url, *additional_urls]}, ensure_ascii=False, indent=2))


def find_page_id(pmid: str) -> str:
    if NOTION_PAGES.exists():
        pages = json.loads(NOTION_PAGES.read_text(encoding="utf-8"))
        for page in pages:
            if str(page.get("pmid")) == str(pmid):
                page_id = page.get("notion_page_id")
                if page_id:
                    return page_id

    token, database_id = notion_credentials_from_env()
    if not token or not database_id:
        raise SystemExit("NOTION_TOKEN and NOTION_DATABASE_ID are required in .env.")
    page = find_notion_page_by_pmid(pmid, database_id, token)
    if page and page.get("id"):
        return page["id"]
    raise SystemExit(f"No Notion page found for PMID {pmid}. Pass --page-id.")


def find_latest_grarec_path(pmid: str) -> str:
    image_root = ROOT / "images"
    candidates = [
        path
        for path in image_root.rglob(f"PMID_{pmid}_grarec.*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    if not candidates:
        raise SystemExit(f"No grarec image found for PMID {pmid}. Pass --image-path.")
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    return latest.relative_to(ROOT).as_posix()


def find_grarec_path(pmid: str, variant: str) -> str:
    suffix = "_web" if variant == "web" else ""
    candidates = [
        path
        for path in (ROOT / "images").rglob(f"PMID_{pmid}_grarec{suffix}.*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    if not candidates:
        label = "Web版" if variant == "web" else "自動版"
        raise SystemExit(f"{label}の画像が見つかりません: PMID {pmid}")
    return max(candidates, key=lambda path: path.stat().st_mtime).relative_to(ROOT).as_posix()


def normalize_image_path(image_path: str) -> str:
    path = Path(image_path).expanduser()
    if path.is_absolute():
        try:
            return path.relative_to(ROOT).as_posix()
        except ValueError as error:
            raise SystemExit(f"Image path must be inside this repository: {path}") from error
    return path.as_posix()


if __name__ == "__main__":
    main()
