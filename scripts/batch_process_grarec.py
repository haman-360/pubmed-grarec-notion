from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from import_chatgpt_summary import (
    normalize_chatgpt_summary,
    read_json,
    remember_notion_page,
)
from upload_to_notion import (
    notion_credentials_from_env,
    update_notion_page_cover_and_graphic_url,
    upsert_chatgpt_summary_page,
)


ROOT = Path(__file__).resolve().parents[1]
INPUT_SUMMARIES = ROOT / "input" / "chatgpt_summaries"
PENDING_SUMMARIES = INPUT_SUMMARIES / "pending"
DONE_SUMMARIES = INPUT_SUMMARIES / "done"
NOTION_PAGES = ROOT / "output" / "notion_pages.json"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
DEFAULT_BASE_URL = "https://haman-360.github.io/pubmed-grarec-notion"


@dataclass
class BatchItem:
    pmid: str
    summary_path: Path
    destination_path: Path
    source_image_path: Path | None = None
    image_action: str = "missing"
    notion_action: str = "pending"
    notion_page_id: str = ""
    graphic_url: str = ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch import pending ChatGPT summaries and grarec images.")
    parser.add_argument("--dry-run", action="store_true", help="Show planned actions without changing files, git, or Notion.")
    parser.add_argument("--yes", action="store_true", help="Run without interactive confirmation.")
    parser.add_argument("--year", type=int, default=datetime.now().year, help="Destination image year. Defaults to current year.")
    parser.add_argument("--month", type=int, default=datetime.now().month, help="Destination image month. Defaults to current month.")
    parser.add_argument("--image-dir", default="", help="Directory containing newly saved ChatGPT images. Defaults to images/YYYY/MM.")
    parser.add_argument("--base-url", default=os.getenv("GITHUB_PAGES_BASE_URL", DEFAULT_BASE_URL), help="Public base URL.")
    parser.add_argument("--no-git", action="store_true", help="Skip git add/commit/push for images.")
    parser.add_argument("--no-commit-processed", action="store_true", help="Do not commit/push processed summary files.")
    parser.add_argument("--no-move-done", action="store_true", help="Do not move processed summaries to done/.")
    parser.add_argument("--commit-message", default="Add grarec images for pending papers", help="Git commit message for images.")
    parser.add_argument("--processed-commit-message", default="Archive processed grarec summaries", help="Git commit message for processed summaries.")
    args = parser.parse_args()

    image_dir = Path(args.image_dir) if args.image_dir else ROOT / "images" / f"{args.year:04d}" / f"{args.month:02d}"
    if not image_dir.is_absolute():
        image_dir = ROOT / image_dir

    items = build_batch_items(args.year, args.month, image_dir, args.base_url)
    if not items:
        print("pending/ に処理対象JSONがありません。")
        return

    print_plan(items, image_dir, args)
    missing = [item for item in items if item.image_action == "missing"]
    if missing:
        print()
        print("画像が見つからないPMIDがあります。画像を保存してから再実行してください。")
        raise SystemExit(1)

    if args.dry_run:
        return
    if not args.yes and not confirm("この内容で一括処理しますか？ [y/N]: "):
        print("中止しました。")
        return

    token, database_id = notion_credentials_from_env()
    if not token or not database_id:
        raise SystemExit("NOTION_TOKEN and NOTION_DATABASE_ID are required in .env.")

    changed_images: list[Path] = []
    for item in items:
        changed = prepare_image(item)
        if changed:
            changed_images.append(item.destination_path)

    if changed_images and not args.no_git:
        publish_images(changed_images, args.commit_message)
    elif not changed_images:
        print("新規公開する画像はありませんでした。")

    for item in items:
        summary = normalize_chatgpt_summary(read_json(item.summary_path))
        page = upsert_chatgpt_summary_page(summary, database_id, token, append_children_to_existing=False)
        remember_notion_page(item.pmid, page)
        item.notion_action = str(page.get("import_action") or "")
        item.notion_page_id = str(page.get("id") or "")
        page_id = item.notion_page_id
        if not page_id:
            raise RuntimeError(f"Notion page id missing for PMID {item.pmid}")
        update_notion_page_cover_and_graphic_url(page_id, database_id, token, item.graphic_url)
        print(f"Notion反映: PMID {item.pmid} ({item.notion_action}) {item.graphic_url}")

    if not args.no_move_done:
        moved_summaries: list[tuple[Path, Path]] = []
        for item in items:
            moved_summaries.append((item.summary_path, move_summary_to_done(item.summary_path)))
        if not args.no_commit_processed:
            commit_processed_files(moved_summaries, args.processed_commit_message)

    print("一括処理が完了しました。")


def build_batch_items(year: int, month: int, image_dir: Path, base_url: str) -> list[BatchItem]:
    summaries = sorted(PENDING_SUMMARIES.glob("*.json"), key=lambda path: path.stat().st_mtime)
    items: list[BatchItem] = []
    for summary_path in summaries:
        summary = normalize_chatgpt_summary(read_json(summary_path))
        pmid = str(summary["pmid"])
        destination = ROOT / "images" / f"{year:04d}" / f"{month:02d}" / f"PMID_{pmid}_grarec.png"
        item = BatchItem(pmid=pmid, summary_path=summary_path, destination_path=destination)
        item.graphic_url = f"{base_url.rstrip('/')}/{quote(destination.relative_to(ROOT).as_posix())}"
        items.append(item)

    assign_images(items, image_dir)
    for item in items:
        item.graphic_url = f"{base_url.rstrip('/')}/{quote(item.destination_path.relative_to(ROOT).as_posix())}"
        item.notion_page_id = known_page_id(item.pmid)
        item.notion_action = "update" if item.notion_page_id else "create_or_update"
    return items


def assign_images(items: list[BatchItem], image_dir: Path) -> None:
    all_images = [
        path
        for path in image_dir.glob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    used: set[Path] = set()

    for item in items:
        existing = existing_pmid_image(item.pmid)
        if existing:
            item.source_image_path = existing
            item.destination_path = existing
            item.image_action = "use_existing"
            used.add(existing)
            continue

        named = newest_image_containing_pmid(all_images, item.pmid, used)
        if named:
            item.source_image_path = named
            item.destination_path = destination_with_suffix(item.destination_path, named.suffix)
            item.image_action = "rename"
            used.add(named)

    missing = [item for item in items if item.image_action == "missing"]
    unnamed = [
        path
        for path in sorted(all_images, key=lambda value: value.stat().st_mtime)
        if path not in used and not looks_like_pmid_grarec(path)
    ]
    if missing and len(unnamed) >= len(missing):
        for item, image_path in zip(missing, unnamed):
            item.source_image_path = image_path
            item.destination_path = destination_with_suffix(item.destination_path, image_path.suffix)
            item.image_action = "rename_by_mtime"
            used.add(image_path)


def existing_pmid_image(pmid: str) -> Path | None:
    candidates = [
        path
        for path in (ROOT / "images").rglob(f"PMID_{pmid}_grarec.*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def newest_image_containing_pmid(images: list[Path], pmid: str, used: set[Path]) -> Path | None:
    candidates = [path for path in images if path not in used and pmid in path.name]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def looks_like_pmid_grarec(path: Path) -> bool:
    return path.name.startswith("PMID_") and "_grarec" in path.stem


def destination_with_suffix(destination: Path, suffix: str) -> Path:
    return destination.with_suffix(suffix.lower())


def known_page_id(pmid: str) -> str:
    if not NOTION_PAGES.exists():
        return ""
    pages = json.loads(NOTION_PAGES.read_text(encoding="utf-8"))
    for page in pages:
        if str(page.get("pmid")) == str(pmid):
            return str(page.get("notion_page_id") or "")
    return ""


def print_plan(items: list[BatchItem], image_dir: Path, args: argparse.Namespace) -> None:
    print("pending全件 一括処理プレビュー")
    print("============================")
    print(f"画像検索: {image_dir}")
    print(f"GitHub Pages base URL: {args.base_url.rstrip('/')}")
    print(f"dry-run: {args.dry_run}")
    print()
    for item in items:
        source = item.source_image_path.relative_to(ROOT) if item.source_image_path and item.source_image_path.is_relative_to(ROOT) else item.source_image_path
        destination = item.destination_path.relative_to(ROOT)
        summary = item.summary_path.relative_to(ROOT)
        print(f"PMID {item.pmid}")
        print(f"  JSON: {summary}")
        print(f"  画像: {item.image_action} {source or '(missing)'} -> {destination}")
        print(f"  Notion: {item.notion_action}")
        print(f"  URL: {item.graphic_url}")


def confirm(prompt: str) -> bool:
    return input(prompt).strip().lower() in {"y", "yes"}


def prepare_image(item: BatchItem) -> bool:
    if not item.source_image_path:
        raise RuntimeError(f"Image missing for PMID {item.pmid}")
    if item.image_action == "use_existing":
        return False
    item.destination_path.parent.mkdir(parents=True, exist_ok=True)
    if item.destination_path.exists():
        raise RuntimeError(f"Destination already exists: {item.destination_path}")
    shutil.move(item.source_image_path.as_posix(), item.destination_path.as_posix())
    print(f"画像整理: {item.source_image_path} -> {item.destination_path}")
    return True


def publish_images(images: list[Path], commit_message: str) -> None:
    relative_paths = [path.relative_to(ROOT).as_posix() for path in images]
    run_command(["git", "add", *relative_paths])
    if not staged_changes():
        print("画像のgit差分はありませんでした。")
        return
    run_command(["git", "commit", "-m", commit_message])
    branch = git_output(["branch", "--show-current"]) or "main"
    run_command(["git", "push", "origin", branch])


def move_summary_to_done(path: Path) -> Path:
    DONE_SUMMARIES.mkdir(parents=True, exist_ok=True)
    destination = DONE_SUMMARIES / path.name
    if destination.exists():
        destination = DONE_SUMMARIES / f"{path.stem}_{datetime.now().strftime('%Y%m%d%H%M%S')}{path.suffix}"
    path.replace(destination)
    print(f"処理済みへ移動: {destination.relative_to(INPUT_SUMMARIES)}")
    return destination


def commit_processed_files(moved_summaries: list[tuple[Path, Path]], commit_message: str) -> None:
    paths: list[str] = []
    for source, destination in moved_summaries:
        paths.append(source.relative_to(ROOT).as_posix())
        paths.append(destination.relative_to(ROOT).as_posix())
    if NOTION_PAGES.exists():
        paths.append(NOTION_PAGES.relative_to(ROOT).as_posix())
    run_command(["git", "add", "-A", *paths])
    if not staged_changes():
        print("処理済みJSONのgit差分はありませんでした。")
        return
    run_command(["git", "commit", "-m", commit_message])
    branch = git_output(["branch", "--show-current"]) or "main"
    run_command(["git", "push", "origin", branch])


def staged_changes() -> bool:
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT, check=False)
    return result.returncode != 0


def git_output(args: list[str]) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, check=False, capture_output=True, text=True)
    return result.stdout.strip()


def run_command(args: list[str]) -> None:
    print("$ " + " ".join(args))
    subprocess.run(args, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
