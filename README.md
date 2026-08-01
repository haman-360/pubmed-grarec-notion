# PubMed GraRec Notion

PubMedで見つけた論文を、Notionの `PubMed Watchlist - 論文DB` にグラレコ画像つきで管理するための作業用repositoryです。

## まず作るもの

Notionに以下のデータベースを作成します。

- データベース名: `PubMed Watchlist - 論文DB`
- メインビュー: Table
- 追加ビュー: Gallery
- Gallery名: `画像ギャラリー`
- Galleryのカードプレビュー: `Graphic Image` またはページカバー

詳しいプロパティ定義は [notion/database_schema.md](notion/database_schema.md) を参照してください。

## 最初の運用

1. PubMedで論文を選ぶ。
2. PMIDを [input/pmids.txt](input/pmids.txt) に記録する。
3. AI要約とグラレコ用プロンプトを作る。
4. グラレコ画像を `images/YYYY/MM/PMID_xxxxxxxx.png` に保存する。
5. 画像を公開URL化する。
6. Notion DBに論文ページを作り、画像URLと要約を入れる。
7. 医師が原文確認後、`Human Checked` をオンにする。
8. 処理が終わった精読JSONは `input/chatgpt_summaries/done/` に移す。

運用の詳細は [docs/workflow.md](docs/workflow.md) を参照してください。

## 自動化のMVP

このrepositoryには、PMIDを入力すると以下を半自動で行うPythonスクリプトを入れています。

- PubMedからタイトル、abstract、journal、year、DOIを取得
- 日本語要約とグラレコ用プロンプトを生成
- 手動指定した画像URLをNotionへ登録
- Notionページのカバー画像または画像プロパティにグラレコを設定

APIキーは `.env` に保存し、GitHubには送信しません。必要な環境変数は [.env.example](.env.example) を参照してください。

## 半額BatchでPMID/PDFから自動処理

急がない論文精読はOpenAI Batch APIへ送り、通常APIより50%安い料金で処理します。処理は原則24時間以内です。
「半額になる時間帯」を待つ方式ではなく、24時間枠の非同期Batchとして明示的に送ることで割引が適用されます。ChatGPTの契約とは別にOpenAI APIキーとAPI利用料金が必要です。

標準フロー:

```text
PMIDまたはPDF
→ PubMed/PMC取得とNotion重複確認（OpenAI課金なし）
→ gpt-5.6-terraのBatchへ投入
→ 翌日に結果回収
→ JSON駆動のグラレコPNGを生成
→ PMIDでNotionをupsertし、PNGをNotionへ直接アップロード
```

1. [.env.example](.env.example)をコピーして`.env`を作り、`OPENAI_API_KEY`、`NOTION_TOKEN`、`NOTION_DATABASE_ID`を設定します。
2. PMIDは [input/pmids.txt](input/pmids.txt) に1行1件で書きます。
3. 手元のPDFを使う場合は `input/pdfs/PMID_42115808.pdf` のような名前で置きます。

まず、OpenAI APIを呼ばずに取得可否、Notion重複、推定Batch料金を確認します。

```bash
python3 scripts/process_papers.py prepare
```

問題なければ半額Batchへ投入します。既定では最大5論文、1論文の推定上限は`$0.50`です。

```bash
python3 scripts/process_papers.py submit
```

既定の料金設定（入力 `$1.00`/100万token、出力 `$6.00`/100万token）では、入力3万token・出力上限8千tokenの例は約`$0.078`です。料金改定時は`.env`の単価を変更してください。実料金は完了後のusageから記録されます。

`prepare`の論文探索はPubMed/PMCだけを使うためOpenAI APIキーを消費しません。取得元がない論文は`NO_SOURCE`、Notionに画像付きで存在する論文は`SKIP_EXISTS`となり、Batchへ入りません。`submit`にも最大件数、論文ごとの推定額上限、最終確認があります。

後から状況を確認します。

```bash
python3 scripts/process_papers.py status
```

完了結果を回収し、グラレコを作成します。ここまではNotionを変更しません。

```bash
python3 scripts/process_papers.py resume
```

Notionまで登録・更新する場合:

```bash
python3 scripts/process_papers.py resume --notion
```

同じPMIDがNotionにあり、すでに画像もある場合は既定でスキップします。画像がない既存ページは新規作成せず更新します。再精読したい場合は次のように準備します。

```bash
python3 scripts/process_papers.py prepare --update-existing
```

Batchジョブの状態、実トークン数、実料金は `output/jobs/PMID_<id>.json` に保存されます。Batchの結果順序が変わっても、PMID、PDFハッシュ、プロンプト版を含む`custom_id`で対応付けます。

グラレコは画像生成AIではなく、精読JSONをSwift/AppKitテンプレートへ流し込んで作ります。これにより日本語と数値を確定的に描画し、画像生成API料金を発生させません。

## 実行方法

PMID 41733080 は [input/pmids.txt](input/pmids.txt) に入れてあります。

コマンドを覚えずに進めたい場合は、repository直下の `PubMedGraRec.command` をダブルクリックします。
基本は、PMIDを選んだあとに上から順番に進めます。

```text
1. Notion登録前プレビュー
2. 精読JSONをNotionへ登録/更新
3. ChatGPT画像をPMID名に整理
4. 画像をGitHub Pagesへ公開
5. グラレコ画像をNotionに表示
6. この精読JSONを処理済み(done)へ移動
```

ただし、3以降はChatGPTでグラレコ画像を作ってから実行します。
まず精読JSONだけ登録したい場合は、1と2までで完了です。

Notionへ登録せず、要約JSONとグラレコプロンプトだけ作る場合:

```bash
python3 scripts/run_mvp.py
```

特定PMIDだけを指定する場合:

```bash
python3 scripts/run_mvp.py --pmid 41733080
```

画像URLを指定してNotionへ登録する場合:

```bash
python3 scripts/run_mvp.py --pmid 41733080 --graphic-url "https://example.com/PMID_41733080.png" --notion
```

`.env` に `NOTION_TOKEN` と `NOTION_DATABASE_ID` がない場合、`--notion` は使わず、まずローカル出力だけ確認してください。

出力先:

- `output/summaries/PMID_41733080.json`
- `output/prompts/PMID_41733080_graphic_prompt.txt`

## GitHub Pagesで画像をNotionへ反映する

GitHub Pagesを `main` ブランチの `/root` から公開すると、repository内の画像は以下のURLで参照できます。

```text
https://haman-360.github.io/pubmed-grarec-notion/images/2026/05/PMID_41733080_grarec.png
```

既存のNotionページに画像URLとページカバーを反映する場合:

```bash
python3 scripts/update_graphic_url.py --pmid 41733080
```

このスクリプトは [output/notion_pages.json](output/notion_pages.json) からNotionページIDを探し、`Graphic URL`、`Graphic Image`、ページカバーを更新します。Notion DBに `Graphic Image` プロパティがない場合は、存在するプロパティだけ更新します。

## ChatGPT精読JSONをNotionへ登録する

ChatGPTの「小児アップデート」プロジェクトで作成した精読JSONは、まず `input/chatgpt_summaries/pending/` に保存します。
ファイル名の例:

```text
input/chatgpt_summaries/pending/PMID_41733080_chatgpt.json
```

ファイル名は `PMID_41733080_chatgpt.json` が分かりやすいですが、`41733080.json` のような名前でもJSON内に `pmid` または `PMID` があれば読み取れます。
すでに `input/chatgpt_summaries/` 直下に置いたJSONも読み取れます。

まずNotionへ送る内容をローカルで確認します。
これはNotion登録前プレビューで、Notionはまだ変更されません。

```bash
python3 scripts/import_chatgpt_summary.py --pmid 41733080 --dry-run
```

問題なければNotion DBへ登録します。DB内に同じ `PMID` のページがある場合は新規作成せず更新します。

```bash
python3 scripts/import_chatgpt_summary.py --pmid 41733080 --notion
python3 scripts/import_chatgpt_summary.py --file input/chatgpt_summaries/PMID_41733080_chatgpt.json --notion
```

JSON内の `PMID`, `title`, `journal`, `year`, `doi`, `topic`, `one_line_summary`, `practice_change`, `human_checked` はNotion DBのプロパティへ登録します。
`one_line_summary` は既存DBの `Take Home Message` に入ります。
`topic` は配列でも文字列でも使えます。文字列の場合は `,`、`、`、`;`、`；` で区切るとNotionのmulti-selectに分割して登録します。
`PICO`, `figure_table_summary`, `main_results`, `safety`, `limitations`, `applicability_to_japanese_pediatric_clinic`, `tomorrow_action` はNotionページ本文に見出し付きで追加します。
`graphic_url` がある場合は `Graphic URL`, `Graphic Image`, ページカバーへ反映します。

処理が終わったJSONは `PubMedGraRec.command` の `6. この精読JSONを処理済み(done)へ移動` で `input/chatgpt_summaries/done/` に移します。
`done/` に移すと、次回以降の候補一覧には出にくくなりますが、元データとして残ります。

## ChatGPTとCodexの役割分担

この運用では、ChatGPTとCodexを以下のように使い分けます。

- ChatGPT: 論文精読JSONを作成する。
- ChatGPT: 精読JSONや論文内容をもとに、グラレコ画像を作成する。
- Codex: JSONファイルをrepositoryへ保存し、dry-runでNotion送信内容を確認する。
- Codex: Notion DBへ精読結果を登録し、既存PMIDがあれば更新する。
- Codex: グラレコ画像URLを `Graphic URL`, `Graphic Image`, ページカバーへ反映する。

グラレコをChatGPTへ依頼するときの例:

```text
この論文精読JSONをもとに、小児科医がNotionで一覧したときに内容を一目で把握できるグラレコ画像を作成してください。

条件:
- 横長 16:9
- 日本語
- 論文タイトル、PMID、Journal、Yearを入れる
- PICO、主な結果、診療への示唆、注意点を視覚的に整理する
- 数値やカットオフはJSONの内容に忠実にする
- 誇張した結論にしない
- 小児クリニックでの実用性が伝わる構成にする
```

ChatGPTで画像を作成したら、PNGとしてrepository内の `images/YYYY/MM/` に保存し、CodexでNotionへ反映します。
ChatGPTが作る画像ファイル名は `ChatGPT Image ...png` のような名前でかまいません。
Codex側でPMIDベースの名前へ整えます。

## ChatGPT精読ページに後からグラレコを追加する

ChatGPT精読JSONを先にNotionへ登録し、あとからグラレコ画像を追加する場合の流れです。
`PubMedGraRec.command` を使う場合は、以下の順番で進めます。

```text
JSONを input/chatgpt_summaries/pending/ に入れる
-> 1. Notion登録前プレビュー
-> 2. 精読JSONをNotionへ登録/更新
-> ChatGPTでグラレコ画像を作る
-> 画像をDownloadsまたはimages/に保存
-> 3. ChatGPT画像をPMID名に整理
-> 4. 画像をGitHub Pagesへ公開
-> 5. グラレコ画像をNotionに表示
-> 6. この精読JSONを処理済み(done)へ移動
```

1. グラレコ画像をrepository内へ保存する。

```text
images/2026/05/PMID_42115808_grarec.png
```

ChatGPTが作成した画像を `Downloads` や `images/` に保存したあと、以下で最新画像をPMID名へリネームできます。

```bash
python3 scripts/rename_latest_grarec.py --pmid 42115808
```

特定の画像ファイルを指定する場合:

```bash
python3 scripts/rename_latest_grarec.py \
  --pmid 42115808 \
  --source "images/2026/05/ChatGPT Image May 20, 2026, 08_08_04 PM.png"
```

2. GitHub Pagesなどで画像を公開する。

`PubMedGraRec.command` の `4. 画像をGitHub Pagesへ公開` を選ぶと、PMID名に整理した画像だけを `git add`、`commit`、`push` します。
GitHub Pagesへの反映には少し時間がかかることがあります。

公開URLの例:

```text
https://haman-360.github.io/pubmed-grarec-notion/images/2026/05/PMID_42115808_grarec.png
```

3. 既存のNotionページへ画像URLを反映する。

`PubMedGraRec.command` の `5. グラレコ画像をNotionに表示` を選ぶと、PMIDからNotionページを探して画像URLを反映します。
手動で実行する場合は以下のコマンドでも同じことができます。

PMID 42115808 の例:

```bash
python3 scripts/update_graphic_url.py \
  --pmid 42115808 \
  --image-path images/2026/05/PMID_42115808_grarec.png
```

このコマンドは既存ページの `Graphic URL`, `Graphic Image`, ページカバーを更新します。
PMIDからNotionページを探すため、通常は `--page-id` は不要です。
画像ファイルをGitHub Pagesの標準URL以外で公開している場合は、`--base-url` を指定します。

```bash
python3 scripts/update_graphic_url.py \
  --pmid 42115808 \
  --image-path images/2026/05/PMID_42115808_grarec.png \
  --base-url https://example.com/pubmed-grarec-notion
```
