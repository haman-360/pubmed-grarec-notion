#!/bin/zsh
cd "$(dirname "$0")" || exit 1
echo "PubMed GraRec（API精読 → Web画像生成 → 画像承認 → Notion）"
if [[ ! -x .venv/bin/python ]]; then
  echo "先に SetupPaperPipeline.command を実行してください。"
  read
  exit 1
fi
.venv/bin/python scripts/process_papers.py menu
result=$?
echo "終了するにはReturnキーを押してください。"
read
exit $result
