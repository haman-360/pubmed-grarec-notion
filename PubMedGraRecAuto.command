#!/bin/zsh
cd "$(dirname "$0")"
echo "PubMed GraRec → Notion（継続台帳・二段階Batch）"
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
