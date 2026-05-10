# PubMed論文DB・グラレコ半自動化プロジェクト 申し送り

## 目的

PubMedで見つけた英語論文について、PMIDまたはabstractを起点に、要約・グラレコ画像・Notion論文DB登録を半自動化する。

最初から完全自動化は目指さず、医師が選んだ論文を対象に、Codexが処理を補助する形にする。論文の医学的解釈や診療変更の最終判断は医師が確認する。

## 最終的に作りたいもの

Notion上に「PubMed Watchlist - 論文DB」を作成し、各論文を1レコードとして管理する。

各レコードには、タイトル、PMID、ジャーナル、年、トピック、重要度、診療変更の必要性、日本語要約、臨床への影響、グラレコ画像URL、原文リンク、PDFリンクなどを保存する。

Notionのギャラリービューでは、グラレコ画像をカードのサムネイルとして表示し、論文を視覚的に一覧できるようにする。

## 半自動ワークフロー

1. 医師がPubMed検索で候補論文を選ぶ。
2. PMIDまたは論文情報を入力ファイルに記載する。
3. CodexがPubMedからabstractを取得する。
4. CodexがAI要約用プロンプトを使って、以下を生成する。
   - 日本語要約
   - なぜ重要か
   - 臨床への影響
   - 診療変更の必要性
   - グラレコ作成用プロンプト
5. 画像生成AIでグラレコ画像を作成する。
6. 作成した画像をGitHub repositoryへ保存する。
7. GitHub Pagesまたはraw URLで画像URLを作る。
8. Notion APIで論文DBにページを作成し、画像URLを登録する。
9. 医師がNotion上で内容と画像を確認し、必要なら修正する。

## なぜGitHubを使うか

Notion APIまたはNotion MCPでは、ローカル画像ファイルの直接アップロードが扱いにくい場合がある。そのため、画像ファイルはGitHubに保存し、公開URLをNotionの画像プロパティまたはカバー画像として参照する。

## 想定repository名

候補：

- pubmed-grarec-notion
- pubmed-watchlist-graphics
- pediatric-paper-graphics
- pubmed-visual-database

まずは `pubmed-grarec-notion` を推奨する。

## 推奨ディレクトリ構成

```text
pubmed-grarec-notion/
  README.md
  .env.example
  input/
    pmids.txt
    papers_sample.json
  output/
    summaries/
    prompts/
  images/
    2026/
      05/
        PMID_12345678.png
  scripts/
    fetch_pubmed.py
    generate_summary.py
    generate_image_prompt.py
    upload_to_notion.py
  notion/
    database_schema.md
  docs/
    workflow.md
```

## 入力ファイル例

`input/pmids.txt`

```text
12345678
23456789
34567890
```

または `input/papers_sample.json`

```json
[
  {
    "pmid": "12345678",
    "topic": "Infection",
    "priority_note": "小児外来で抗菌薬適正使用に関係しそう"
  }
]
```

## Notion DB 推奨プロパティ

Notion側のデータベース名は「PubMed Watchlist - 論文DB」とする。

推奨プロパティは以下。

| プロパティ名 | 型 | 用途 |
|---|---|---|
| Title | Title | 論文タイトル |
| PMID | Text | PubMed ID |
| PubMed URL | URL | PubMedへのリンク |
| DOI | Text | DOI |
| Journal | Select | 雑誌名 |
| Year | Number | 出版年 |
| Published Date | Date | 出版日 |
| Topic | Multi-select | Allergy, Infection, Asthmaなど |
| Subtopic | Multi-select | RSV, UTI, Food allergyなど |
| Study Type | Select | RCT, Review, Guideline, Meta-analysisなど |
| Read Status | Status | 未読、要確認、完了 |
| Impact | Select | High, Medium, Low |
| Practice Change | Select | Yes, No, Unclear |
| Why Important | Text | なぜ重要か |
| Clinical Impact | Text | 臨床への影響 |
| Summary JP | Text | 日本語要約 |
| Take Home Message | Text | 1行メッセージ |
| Graphic URL | URL | GitHub上の画像URL |
| Graphic Image | Files & media | 画像表示用。URL埋め込みでも可 |
| PDF URL | URL | PDFまたは出版社リンク |
| NotebookLM | Checkbox | NotebookLM登録済み |
| Blog Candidate | Checkbox | ブログ化候補 |
| LINE Candidate | Checkbox | LINE配信候補 |
| Created By AI | Checkbox | AI生成済み |
| Human Checked | Checkbox | 医師確認済み |

## Notionギャラリービュー設定

ギャラリービュー名は「画像ギャラリー」とする。

カードプレビューは `Graphic Image` またはページカバー画像を指定する。

カードに表示するプロパティは、最初は以下に絞る。

- Read Status
- Topic
- Journal
- Year
- Impact
- Practice Change

## AI要約の出力形式

各論文について、以下のJSONまたはMarkdownを生成する。

```json
{
  "title": "",
  "pmid": "",
  "journal": "",
  "year": "",
  "topic": [],
  "study_type": "",
  "why_important": "",
  "clinical_impact": "",
  "practice_change": "Yes/No/Unclear",
  "practice_change_reason": "",
  "take_home_message": "",
  "summary_jp": ""
}
```

## グラレコ画像生成プロンプト方針

画像は、正確性を優先する。細かいp値や数値は誤生成しやすいため、画像内には原則として最小限の数値のみ入れる。

推奨スタイル：

- 日本語
- 医師向け
- 学会スライド風
- 白背景
- 青緑系アクセント
- 3カラムまたは4カラム構成
- 左から「対象」「介入/曝露」「主要結果」「外来での意味」
- アイコンを使う
- 文字は大きく読みやすく
- 論文タイトル、PMID、Journal、Yearを小さく記載

## グラレコ生成プロンプト雛形

```text
以下の論文情報をもとに、日本語の医師向けグラフィカルレコード画像を1枚作成してください。

目的：小児科外来医が30秒で内容を把握できること。

デザイン条件：
- 横長16:9
- 白背景
- 青・緑・グレーを基調
- 学会スライド風
- 文字は読みやすく大きめ
- アイコンと矢印を使う
- 3〜4ブロック構成
- 数字は提供されたものだけ使用し、推測で追加しない

構成：
1. 左上：論文タイトル、PMID、Journal、Year
2. 左：対象・研究デザイン
3. 中央：介入、曝露、比較、評価項目
4. 右：主要結果
5. 下段：小児科外来でのTake Home Message

論文情報：
タイトル：{{title}}
PMID：{{pmid}}
Journal：{{journal}}
Year：{{year}}
研究デザイン：{{study_type}}
対象：{{population}}
主要結果：{{main_findings}}
臨床への影響：{{clinical_impact}}
Take Home Message：{{take_home_message}}
```

## Notion API登録時の注意

Notion API token と database ID は `.env` に保存し、GitHubへpushしない。

`.env.example` の例：

```text
NOTION_TOKEN=
NOTION_DATABASE_ID=
OPENAI_API_KEY=
GITHUB_IMAGE_BASE_URL=
```

実際の `.env` は `.gitignore` に入れる。

## 最初のMVP

最初の完成目標は、PMIDを1つ指定すると以下まで動くこと。

1. PubMedからtitle、abstract、journal、year、doiを取得する。
2. 日本語要約をMarkdownまたはJSONで保存する。
3. グラレコ用プロンプトを生成する。
4. 画像ファイルを `images/年/月/PMID_xxxxxxxx.png` に保存する。
5. Notion DBに1ページ作成する。
6. NotionページにGitHub画像URL、PMID、要約を入れる。

## Codexへの依頼文

以下をCodexに依頼する。

```text
このrepositoryで、PubMed論文をNotion DBへ半自動登録するMVPを作ってください。

目的は、PMIDを入力するとPubMedからabstractを取得し、日本語要約とグラレコ用プロンプトを生成し、画像URLをNotion DBへ登録することです。

まずは完全自動画像生成までは不要です。最初のMVPでは、画像ファイルまたは画像URLを手動で指定できる形で構いません。

要件：
1. Pythonで実装してください。
2. 入力は `input/pmids.txt` としてください。
3. PubMed取得にはNCBI Entrez APIを使ってください。
4. 要約生成は、後でOpenAI APIを接続できるように関数を分離してください。API未設定時はダミー要約でも動くようにしてください。
5. Notion登録はNotion APIを使ってください。
6. `.env` から `NOTION_TOKEN` と `NOTION_DATABASE_ID` を読むようにしてください。
7. `.env.example` と `.gitignore` を作ってください。
8. 画像URLは最初は `input/papers_sample.json` またはコマンドライン引数から渡せるようにしてください。
9. README.mdにセットアップ手順と実行方法を書いてください。
10. まず1件のPMIDでテストできる形にしてください。

Notion DBのプロパティ名は以下に合わせてください。
Title, PMID, PubMed URL, DOI, Journal, Year, Published Date, Topic, Study Type, Read Status, Impact, Practice Change, Why Important, Clinical Impact, Summary JP, Take Home Message, Graphic URL, Created By AI, Human Checked

安全上の注意：
.envやAPIキーは絶対にGitHubへpushしないでください。
```

## 将来拡張

- PubMed検索式から候補を自動取得する。
- Topicを自動分類する。
- ImpactをAIで仮判定する。
- OpenAI画像生成APIでグラレコを自動生成する。
- GitHubへ画像を自動pushする。
- Notionページのcover imageに画像URLを設定する。
- NotebookLM投入用のMarkdownを自動出力する。
- 月次レビュー用のGoogle DocsまたはPDFを作成する。

## 運用上の注意

AIが生成した要約や画像は必ず医師が確認する。特に、N数、介入内容、アウトカム、p値、結論、診療変更の可否は誤りが入りやすいため、Human Checkedをオンにする前に原文確認する。

グラレコ画像は視認性と記憶補助を目的とし、正確な引用資料として単独使用しない。

