#!/bin/zsh
cd "$(dirname "$0")"
if [[ -x .venv/bin/python ]]; then
  .venv/bin/python scripts/workflow_assistant.py
else
  python3 scripts/workflow_assistant.py
fi
echo ""
echo "終了するにはReturnキーを押してください。"
read
