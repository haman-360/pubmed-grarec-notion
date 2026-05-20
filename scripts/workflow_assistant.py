from __future__ import annotations

import subprocess
import sys
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT_SUMMARIES = ROOT / "input" / "chatgpt_summaries"
PENDING_SUMMARIES = INPUT_SUMMARIES / "pending"
DONE_SUMMARIES = INPUT_SUMMARIES / "done"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def main() -> None:
    print("PubMed GraRec Notion helper")
    print("==========================")
    pmid = ask_pmid()

    while True:
        print()
        print(f"PMID: {pmid}")
        print("1. Notion登録前プレビュー")
        print("2. 精読JSONをNotionへ登録/更新")
        print("3. ChatGPT画像をPMID名に整理")
        print("4. 画像をGitHub Pagesへ公開")
        print("5. グラレコ画像をNotionに表示")
        print("6. この精読JSONを処理済み(done)へ移動")
        print("7. PMIDを変更する")
        print("q. 終了")
        choice = input("> ").strip().lower()

        if choice == "1":
            run(["scripts/import_chatgpt_summary.py", "--pmid", pmid, "--dry-run"])
        elif choice == "2":
            run(["scripts/import_chatgpt_summary.py", "--pmid", pmid, "--notion"])
        elif choice == "3":
            rename_grarec(pmid)
        elif choice == "4":
            publish_grarec(pmid)
        elif choice == "5":
            update_graphic(pmid)
        elif choice == "6":
            move_summary_to_done(pmid)
        elif choice == "7":
            pmid = ask_pmid()
        elif choice in {"q", "quit", "exit"}:
            break
        else:
            print("番号を選んでください。")


def ask_pmid() -> str:
    candidates = summary_candidates()
    if candidates:
        print("未処理のChatGPT精読JSON:")
        for index, (pmid, path) in enumerate(candidates[:5], start=1):
            print(f"{index}. PMID {pmid}  ({path.relative_to(INPUT_SUMMARIES)})")
    else:
        print("未処理JSONが見つかりません。PMIDを直接入力することもできます。")
    value = input("PMIDを入力してください: ").strip()
    if value in {str(index) for index in range(1, min(len(candidates), 5) + 1)}:
        return candidates[int(value) - 1][0]
    if value.isdigit():
        return value
    if value and value.isdecimal():
        return value
    if value.isnumeric():
        return value
    if not value and candidates:
        return candidates[0][0]
    raise SystemExit("PMIDが入力されませんでした。")


def summary_candidates() -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    paths = list(PENDING_SUMMARIES.glob("*.json")) + list(INPUT_SUMMARIES.glob("*.json"))
    for path in sorted(paths, key=lambda value: value.stat().st_mtime, reverse=True):
        pmid = pmid_from_summary_file(path)
        if pmid:
            candidates.append((pmid, path))
    return candidates


def pmid_from_summary_file(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    for key, value in data.items():
        if str(key).lower() == "pmid" and value:
            return str(value)
    parts = path.stem.split("_")
    for part in parts:
        if part.isdigit():
            return part
    return path.stem if path.stem.isdigit() else ""


def rename_grarec(pmid: str) -> None:
    print("画像ファイルを直接指定する場合はパスを入力してください。")
    print("空欄ならDownloadsとimages/から最新画像を選びます。")
    source = input("画像パス: ").strip()
    command = ["scripts/rename_latest_grarec.py", "--pmid", pmid]
    if source:
        command.extend(["--source", source])
    run(command)


def update_graphic(pmid: str) -> None:
    image_path = latest_grarec_path(pmid)
    if image_path:
        print(f"使用する画像: {image_path}")
        value = input("この画像でNotionへ反映しますか？ [Y/n]: ").strip().lower()
        if value in {"", "y", "yes"}:
            run(["scripts/update_graphic_url.py", "--pmid", pmid, "--image-path", image_path.as_posix()])
            return
    image_path_text = input("画像パスを入力してください: ").strip()
    if not image_path_text:
        print("画像パスがないため中止しました。")
        return
    run(["scripts/update_graphic_url.py", "--pmid", pmid, "--image-path", image_path_text])


def publish_grarec(pmid: str) -> None:
    image_path = latest_grarec_path(pmid)
    if not image_path:
        print("PMID名のグラレコ画像が見つかりません。先に3を実行してください。")
        return

    print(f"公開する画像: {image_path}")
    print("この操作は画像ファイルだけをgit addし、commitしてoriginへpushします。")
    value = input("続けますか？ [y/N]: ").strip().lower()
    if value not in {"y", "yes"}:
        print("中止しました。")
        return

    branch = git_output(["branch", "--show-current"]) or "main"
    if not run_command(["git", "add", image_path.as_posix()]):
        return
    if not run_command(["git", "commit", "-m", f"Add grarec image for PMID {pmid}"]):
        return
    if not run_command(["git", "push", "origin", branch]):
        return
    print("GitHub Pagesへの反映には少し時間がかかることがあります。反映後に5を実行してください。")


def move_summary_to_done(pmid: str) -> None:
    source = find_active_summary_path(pmid)
    if not source:
        print("未処理フォルダに該当JSONが見つかりません。すでにdoneへ移動済みかもしれません。")
        return

    DONE_SUMMARIES.mkdir(parents=True, exist_ok=True)
    destination = DONE_SUMMARIES / source.name
    if destination.exists():
        destination = DONE_SUMMARIES / f"{source.stem}_{pmid}{source.suffix}"
    source.replace(destination)
    print(f"処理済みに移動しました: {destination.relative_to(INPUT_SUMMARIES)}")


def find_active_summary_path(pmid: str) -> Path | None:
    for candidate_pmid, path in summary_candidates():
        if candidate_pmid == str(pmid):
            return path
    return None


def latest_grarec_path(pmid: str) -> Path | None:
    candidates = [
        path.relative_to(ROOT)
        for path in (ROOT / "images").rglob(f"PMID_{pmid}_grarec.*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (ROOT / path).stat().st_mtime)


def run(args: list[str]) -> None:
    print()
    print("$ python3 " + " ".join(args))
    subprocess.run([sys.executable, *args], cwd=ROOT, check=False)


def run_command(args: list[str]) -> bool:
    print()
    print("$ " + " ".join(args))
    result = subprocess.run(args, cwd=ROOT, check=False)
    if result.returncode != 0:
        print("ここで止まりました。上のエラーを確認してください。")
        return False
    return True


def git_output(args: list[str]) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, check=False, capture_output=True, text=True)
    return result.stdout.strip()


if __name__ == "__main__":
    main()
