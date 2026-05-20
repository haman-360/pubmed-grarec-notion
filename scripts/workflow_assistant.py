from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT_SUMMARIES = ROOT / "input" / "chatgpt_summaries"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def main() -> None:
    print("PubMed GraRec Notion helper")
    print("==========================")
    pmid = ask_pmid()

    while True:
        print()
        print(f"PMID: {pmid}")
        print("1. JSONをdry-runで確認する")
        print("2. JSONをNotionへ登録/更新する")
        print("3. ChatGPT画像をPMID名へリネームする")
        print("4. グラレコ画像をNotionへ反映する")
        print("5. PMIDを変更する")
        print("q. 終了")
        choice = input("> ").strip().lower()

        if choice == "1":
            run(["scripts/import_chatgpt_summary.py", "--pmid", pmid, "--dry-run"])
        elif choice == "2":
            run(["scripts/import_chatgpt_summary.py", "--pmid", pmid, "--notion"])
        elif choice == "3":
            rename_grarec(pmid)
        elif choice == "4":
            update_graphic(pmid)
        elif choice == "5":
            pmid = ask_pmid()
        elif choice in {"q", "quit", "exit"}:
            break
        else:
            print("番号を選んでください。")


def ask_pmid() -> str:
    candidates = sorted(INPUT_SUMMARIES.glob("PMID_*_chatgpt.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if candidates:
        print("見つかったChatGPT精読JSON:")
        for index, path in enumerate(candidates[:5], start=1):
            print(f"{index}. {path.name}")
    value = input("PMIDを入力してください: ").strip()
    if value in {str(index) for index in range(1, min(len(candidates), 5) + 1)}:
        return candidates[int(value) - 1].stem.split("_")[1]
    if value.isdigit():
        return value
    if value and value.isdecimal():
        return value
    if value.isnumeric():
        return value
    if not value and candidates:
        return candidates[0].stem.split("_")[1]
    raise SystemExit("PMIDが入力されませんでした。")


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


if __name__ == "__main__":
    main()
