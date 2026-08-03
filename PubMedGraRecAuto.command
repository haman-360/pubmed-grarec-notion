#!/bin/zsh
cd "$(dirname "$0")"

echo "PubMed GraRec → Notion 全自動処理"
echo "================================"
echo "料金確認後、Batch完了まで待機してNotionへ登録します。"
echo "待機中はこのTerminalを閉じないでください。Macのスリープは可能です。"
echo ""

python3 scripts/process_papers.py auto
result=$?

echo ""
if [[ $result -eq 0 ]]; then
  echo "処理を終了しました。"
else
  echo "処理が途中で停止しました。上のメッセージを確認してください。"
fi
echo "終了するにはReturnキーを押してください。"
read
exit $result
