# PubMed GraRec Notion

PubMedで見つけた論文を、Notionの `PubMed Watchlist - 論文DB` にグラレコ画像つきで管理するための作業用repositoryです。

## まず作るもの

Notionに以下のデータベースを作成します。

- データベース名: `PubMed Watchlist - 論文DB`
- メインビュー: Table
- 追加ビュー: Gallery
- Gallery名: `画像ギャラリー`
- Galleryのカードプレビュー: `Graphic Image` またはページカバー

詳しいプロパティ定義は [notion/database_schema.md](/Users/thama/Documents/GitHub/pubmed-grarec-notion/notion/database_schema.md) を参照してください。

## 最初の運用

1. PubMedで論文を選ぶ。
2. PMIDを [input/pmids.txt](/Users/thama/Documents/GitHub/pubmed-grarec-notion/input/pmids.txt) に記録する。
3. AI要約とグラレコ用プロンプトを作る。
4. グラレコ画像を `images/YYYY/MM/PMID_xxxxxxxx.png` に保存する。
5. 画像を公開URL化する。
6. Notion DBに論文ページを作り、画像URLと要約を入れる。
7. 医師が原文確認後、`Human Checked` をオンにする。

運用の詳細は [docs/workflow.md](/Users/thama/Documents/GitHub/pubmed-grarec-notion/docs/workflow.md) を参照してください。

## 自動化のMVP

このrepositoryには、PMIDを入力すると以下を半自動で行うPythonスクリプトを入れています。

- PubMedからタイトル、abstract、journal、year、DOIを取得
- 日本語要約とグラレコ用プロンプトを生成
- 手動指定した画像URLをNotionへ登録
- Notionページのカバー画像または画像プロパティにグラレコを設定

APIキーは `.env` に保存し、GitHubには送信しません。必要な環境変数は [.env.example](/Users/thama/Documents/GitHub/pubmed-grarec-notion/.env.example) を参照してください。

## 実行方法

PMID 41733080 は [input/pmids.txt](/Users/thama/Documents/GitHub/pubmed-grarec-notion/input/pmids.txt) に入れてあります。

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

このスクリプトは [output/notion_pages.json](/Users/thama/Documents/GitHub/pubmed-grarec-notion/output/notion_pages.json) からNotionページIDを探し、`Graphic URL`、`Graphic Image`、ページカバーを更新します。Notion DBに `Graphic Image` プロパティがない場合は、存在するプロパティだけ更新します。
