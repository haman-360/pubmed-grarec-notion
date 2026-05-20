from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEARCH_DIRS = [Path.home() / "Downloads", ROOT / "images"]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Rename the latest ChatGPT grarec image to the PMID-based repository path.")
    parser.add_argument("--pmid", required=True, help="PMID used in the destination file name.")
    parser.add_argument("--source", help="Source image path. Defaults to the newest image in Downloads or images/.")
    parser.add_argument("--search-dir", action="append", help="Directory to search for the newest image. Can be repeated.")
    parser.add_argument("--year", type=int, default=datetime.now().year, help="Destination year. Defaults to current year.")
    parser.add_argument("--month", type=int, default=datetime.now().month, help="Destination month. Defaults to current month.")
    parser.add_argument("--copy", action="store_true", help="Copy instead of moving the source file.")
    parser.add_argument("--force", action="store_true", help="Overwrite destination if it already exists.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without moving or copying.")
    parser.add_argument("--base-url", default=os.getenv("GITHUB_PAGES_BASE_URL", "https://haman-360.github.io/pubmed-grarec-notion"), help="Public base URL for the repository.")
    args = parser.parse_args()

    source = resolve_source(args.source, args.search_dir)
    destination = ROOT / "images" / f"{args.year:04d}" / f"{args.month:02d}" / f"PMID_{args.pmid}_grarec{source.suffix.lower()}"
    public_url = f"{args.base_url.rstrip('/')}/{quote(destination.relative_to(ROOT).as_posix())}"

    print(f"source: {source}")
    print(f"destination: {destination}")
    print(f"public_url: {public_url}")

    if args.dry_run:
        return
    if destination.exists() and not args.force:
        raise SystemExit(f"Destination already exists: {destination}. Pass --force to overwrite.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if args.copy:
        destination.write_bytes(source.read_bytes())
    else:
        source.replace(destination)


def resolve_source(source: str | None, search_dirs: list[str] | None) -> Path:
    if source:
        path = Path(source)
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            raise SystemExit(f"Source image not found: {path}")
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            raise SystemExit(f"Source is not a supported image file: {path}")
        return path

    candidates: list[Path] = []
    dirs = [Path(value) for value in search_dirs] if search_dirs else DEFAULT_SEARCH_DIRS
    for directory in dirs:
        if not directory.exists():
            continue
        candidates.extend(path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)
    if not candidates:
        raise SystemExit("No image files found. Pass --source or --search-dir.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


if __name__ == "__main__":
    main()
