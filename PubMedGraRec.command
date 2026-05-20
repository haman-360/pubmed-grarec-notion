#!/bin/zsh
cd "$(dirname "$0")"
python3 scripts/workflow_assistant.py
echo ""
echo "終了するにはReturnキーを押してください。"
read
