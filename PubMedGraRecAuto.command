#!/bin/zsh
cd "$(dirname "$0")" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  echo "先に SetupPaperPipeline.command を実行してください。"
  read
  exit 1
fi
if .venv/bin/python scripts/build_app.py; then
  open "PubMed-grareko-notion.app"
else
  echo "アプリを作成できませんでした。macOSの開発ツールを確認してください。"
  read
  exit 1
fi
