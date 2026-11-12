#!/bin/zsh
cd "$(dirname "$0")" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  echo "先に SetupPaperPipeline.command を実行してください。"
  read
  exit 1
fi
.venv/bin/python scripts/organize_intake.py
intake_status=$?
echo "Enterキーで閉じます。"
read
exit "$intake_status"
