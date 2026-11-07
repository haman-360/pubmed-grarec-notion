#!/bin/zsh
cd "$(dirname "$0")" || exit 1
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
result=$?
echo "終了コード: $result / 設定方法: docs/AUTOMATION.md"
read "?Returnキーで閉じます。"
exit $result
