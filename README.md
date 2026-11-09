# PubMed GraRec Notion

PMIDから論文本文を取得し、精読JSONを作り、**ChatGPT Webで自分が生成したグラレコを確認してNotionに保存する**ためのリポジトリです。

## 現在の流れ

```text
pubmed_abstractで「入手希望」を保存 → 希望PMIDをTXT出力
  ↓ TXTをこのアプリに指定
手元PDFの照合 → 無料本文の探索・PDF取得 → 成否・候補・エラーを台帳に記録
  ↓ 本文を入手・識別できた論文
APIで精読・論文分類 → Master JSON → 画像ごとのGraphic JSON・Web依頼文
  ↓ Web依頼文を1テーマずつChatGPT Webへ渡す
自分で画像生成 → PNG取り込み → 原文・JSON・画像を確認して承認 → 明示操作でNotionへ
```

既定は `manual_web`。画像APIは使いません。精読には別途API利用料がかかります。
無料本文の探索はPMC → Unpaywall → Europe PMC → 出版社の順です。一般の検索エンジンを横断する仕組みではなく、すべての本文を取得できるとは限りません。候補のみ・取得エラー・無料版未確認を区別して履歴を残します。
本文がない論文をAbstractだけで全文精読したことにはしません。取得結果は `pubmed_abstract` の「入手済み」へ自動で書き戻しません。

## Reviewと原著でプロンプトは違う？

**精読の指示は論文種別に応じて内容を変え、画像のデザイン指示は共通にしています。自分でReview用・原著用のプロンプトファイルを選ぶ必要はありません。**

| JSONの `paper_type` | 種類 | 精読・分割の扱い |
|---|---|---|
| `original_article` | 原著 | PICO・解析集団・主要結果を保持。原則1枚 |
| `systematic_review` | 系統的レビュー／メタ解析 | 該当するPICO・統合効果量・異質性・確実性。単一疑問なら長文でも1枚 |
| `narrative_review` | ナラティブ／スコーピングレビュー | 章立て・臨床疑問を重視。独立テーマがあれば複数枚。PICOは強制しない |
| `guideline_consensus` | ガイドライン／コンセンサス | 推奨の対象・条件・例外・根拠の強さ・合意率を区別。テーマ別に分割可能 |
| `other_unknown` | その他・判別不確か | 分類・章立ての確認まで画像準備を止める |

新規精読ではAIが本文から分類し、Master JSONの `classification_reason` に理由を保存します。自動判定なので人が原文と照合します。
**Master JSONと各Graphic JSONの両方に `paper_type` が入ります。** Review専用フォルダやファイル名で区別する方式ではありません。

| ファイル | 用途 |
|---|---|
| `master.json` | 論文全体・分類理由・章立て・分割方針を確認する |
| `graphics/00_main.json` など | 画像1枚分。`pmid`・`paper_type`・`topic_id` で対応を確認する |
| **`ready_for_web/00_main_prompt.md`** など | **Webへ渡すもの**。対象Graphic JSONとデザイン指示がすでに含まれる |
| `plan.json` | 分割・統合を変更するときの設定。通常は編集不要 |

Web向けのデザインは [grarec-v2.md](prompts/grarec-v2.md) を共用し、種類別の独立した画像テンプレートには分かれていません。依頼文はPICOがnullなら作らない、合意率を治療効果と混同しない等を指示します。構図まで種類別に固定する実装ではありません。
旧JSONは `study_type`／`study_design` から暫定分類します。旧Review／Consensus・分類不明は `structure_review_required` で止まり、原文を確認して分割設定を指定する必要があります。手順は [分割・統合の変更](docs/AUTOMATION.md#分割統合の変更) を参照してください。

## 保存先の早見表

以下のパスは、それぞれのリポジトリ直下からの相対パスです。
このMacではPMID出力側が `/Users/thama/Documents/GitHub/pubmed_abstract`、グラレコ側が `/Users/thama/Documents/GitHub/pubmed-grarec-notion` です。

| 対象 | 保存先・操作 |
|---|---|
| 希望PMIDのTXT（Mac出力） | **pubmed_abstract側の** `ledger_private/exports/pmids-日時-識別子.txt`。`希望PMIDを出力.command` で保存するとフォルダが開く |
| 希望PMIDのTXT（旧GAS画面） | `pmids.txt` をブラウザーでダウンロード。保存場所はブラウザー設定による（通常はDownloads） |
| このアプリへ渡すPMID | 上記TXTをメニュー1で指定。コピー不要。空欄時だけ `input/pmids.txt` を使う |
| 自分で入手したPDF | `input/pdfs/` に置く。別フォルダは `--pdf-dir` で指定。元ファイルは移動・改名・削除しない |
| 自動取得したPDF | `<台帳フォルダ>/downloads/<URLのハッシュ>.pdf` |
| PDFの管理コピー | `<台帳フォルダ>/pdfs/<内容のSHA256>.pdf`。元パスと論文との対応は台帳に記録 |
| 精読結果 | `<台帳フォルダ>/artifacts/<版ID>/review.json` |
| Master・Graphic・Web依頼文 | `<台帳フォルダ>/artifacts/<版ID>/web/<内容ハッシュ>/` 以下。下図を参照 |
| 見やすいWeb用コピー | メニュー12で、例えば **`output/PMID_12345678/web-v1/`** を指定。自動の既定先ではなく、自分で指定するコピー先 |
| 自分が生成したグラレコ | まずDownloads等へPNG保存 → メニュー13で指定するとWeb用管理領域の `images/` へコピー |
| 取得成否・候補・エラー・承認履歴 | `<台帳フォルダ>/ledger.sqlite3`。メニュー2「状況」、3「要確認」、9「CSV」で確認 |
| 以前の手動運用のデータ | `input/chatgpt_summaries/pending/`・`done/`、`output/summaries/`、`images/YYYY/MM/`。現行台帳とは別の旧保存先 |

JSONは**論文種別ごとではなく、処理版と画像テーマごと**に保存します。
`output/` の書き出しはスナップショットです。後からPNGを取り込んでも以前のコピーは更新されず、進捗の正本は台帳です。

```text
<台帳フォルダ>/
  ledger.sqlite3                 取得・処理・画像・承認・費用等の正本
  downloads/                     Webから取得したPDF
  pdfs/                          PDFの管理コピー
  artifacts/<版ID>/
    review.json                  精読結果
    web/<内容ハッシュ>/
      master.json                論文全体（paper_type・分類理由もここ）
      plan.json                  分割設定
      graphics/00_main.json      画像1枚分（paper_type・topic_id付き）
      ready_for_web/00_main_prompt.md  Webへ渡す依頼文（JSON込み）
      images/PMID_<PMID>_<topic_id>_<hash>.png  取り込んだPNG
      manifest.json              画像の対応・進捗
```

### このMacでは試験台帳と既定台帳が別です

2026-11-10の読み取り確認では、前回の試験記録は **`.paper_pipeline-pilot/`** にあり、既定の **`.paper_pipeline/` は未作成**でした。PMID 39678704の最新処理版には、承認・Notion同期済みのWeb画像が記録されています。通常のAPI生成は品質確認のため停止中です。

`PubMedGraRecAuto.command` は、設定がなければ `.paper_pipeline/` を使います。**前回の続きには、リポジトリ直下のTerminalで次を実行してください。**

```sh
.venv/bin/python scripts/process_papers.py --state-dir .paper_pipeline-pilot menu
```

すべての操作で同じ台帳を使います。CLIの `--state-dir` はサブコマンドより前に置きます。別台帳を開いても既存データは消えませんが、前回の記録が表示されません。
台帳指定の優先順は `--state-dir` → `PAPER_PIPELINE_HOME`（環境変数／`.env`）→ 既定の `.paper_pipeline/` です。
通常起動も試験台帳に揃える場合は、既存 `.env` の他の行を保持し、`PAPER_PIPELINE_HOME=.paper_pipeline-pilot` を設定できます。今回の整理では設定を変更していません。

既定台帳、試験台帳、`output/` はGit管理外です。**Git pushだけではPDF・JSON・画像・台帳は別のMacへ移りません。** メニュー10のバックアップを使います。管理領域の直接編集・移動ではなく、コピー書き出しと取り込みを使ってください。

## 普段の操作

初回環境準備は `SetupPaperPipeline.command`。API・予算設定は [初回設定](docs/AUTOMATION.md#初回設定) を参照してください。
新規環境の通常起動は `PubMedGraRecAuto.command`、このMacで既存記録を続ける場合は上記の試験台帳指定で起動します。

| 順番 | 操作 | 結果・確認すること |
|---|---|---|
| 1 | pubmed_abstractの `希望PMIDを出力.command` → Googleログイン → 「希望PMIDをTXTへ保存」 | `ledger_private/exports/` が開く。保存済みの入手希望を出力する |
| 2 | グラレコ側の **1 新規取り込み・実行** → TXTをドラッグして指定 | PDF照合・探索・履歴保存。予算・停止状態に問題がなければAPI精読へ進む |
| 3 | **2 状況**／**3 要確認** | PDF未取得、PDF識別待ち、API待ち・品質停止などを確認する |
| 4 | 精読後、**12 Web用ファイル準備** → PMID → 分割設定は通常空欄 → 新規コピー先を指定 | `output/PMID_<PMID>/web-v1` 等へ書き出す。既存フォルダは上書きしない |
| 5 | **`ready_for_web/*_prompt.md` の内容をChatGPT Webへ渡す** | JSONの追加貼付は不要。複数テーマなら1依頼につき1ファイル・1枚 |
| 6 | 画像をPNG保存 → **13 PNG取り込み** | PMID・`topic_id`・PNGパスを指定。topic_idはGraphic JSON／manifestで確認（原著は通常 `main`） |
| 7 | 原文・JSON・PNGの対応、数値、条件、限界を照合 → **14 画像承認・却下** | 確認者と確認内容を記録。保存だけでは承認されない |
| 8 | Notionに登録する場合だけ **15 承認済み画像をNotionへ送信** | 同じ論文DBへ直接添付。GitHub Pagesでの画像公開は不要 |

PNGは横長16:9または3:2に対応し、自動加工しません。元画像は残り、差し替えには再承認が必要です。
画像の承認とNotionの `Human Checked`（論文全体の確認）は別で、画像承認だけでは自動でオンになりません。
既存JSONはメニュー12から再利用できます。未取得PDFの精読や品質停止の解除が自動で行われるわけではありません。

## プログラムの役割

普段の入口は **`PubMedGraRecAuto.command`**。旧プログラムは互換用に残しています。既存PDF・JSON・画像の場所は変えていません。

| ファイル／フォルダ | 役割・使いどころ |
|---|---|
| `SetupPaperPipeline.command` | 初回のPython環境準備 |
| `PubMedGraRecAuto.command` | **現行メニューの起動**（使う台帳に注意） |
| `scripts/process_papers.py` | 現行CLIの入口。`paper_pipeline/cli.py` へ転送。旧処理の関数も互換用に同居 |
| `scripts/paper_pipeline/cli.py` | メニュー、取り込み・再開・書き出し等の司令役 |
| `scripts/paper_pipeline/sources.py`・`pubmed.py` | 書誌照合、無料本文の探索、PDF取得 |
| `scripts/paper_pipeline/batches.py`・`direct.py` | API精読。Batch処理と通常API試験 |
| `scripts/paper_pipeline/graphics.py` | 分類・分割用スキーマ、Master／Graphic／Web依頼文、PNG取り込み・承認 |
| `scripts/paper_pipeline/store.py` | 台帳、原本コピー、履歴、予算、CSV、バックアップ |
| `scripts/paper_pipeline/notion.py` | 承認済み画像・JSONをNotionへ同期 |
| `scripts/paper_pipeline/transport_audit.py` | 画像API通信の診断用。通常のWeb運用では操作不要 |
| `prompts/grarec-v2.md` | 現行の共通画像デザイン指示。v1は旧版 |
| `PubMedGraRec.command`・`scripts/workflow_assistant.py` | **旧手動メニュー**。現行メニューとは番号・意味が異なる |
| `scripts/import_chatgpt_summary.py`・`rename_latest_grarec.py`・`update_graphic_url.py`・`batch_process_grarec.py` | 旧JSON取り込み、画像整理・公開・Notion更新。現行の承認台帳を通らない経路を含む |
| `scripts/run_mvp.py`・`generate_summary.py`・`fetch_pubmed.py`・`openai_batch.py`・`upload_to_notion.py`・`render_grarec.swift` | 旧MVP・旧描画と共通部品。現行コードから参照するものもあり、まとめて削除・移動しない |
| `tests/` | ローカル回帰テスト。通常の操作時は実行不要 |

## 詳細資料・確認状況

- [AUTOMATION.md](docs/AUTOMATION.md)：初回設定、CLI、分割変更、承認、Notion同期、復元、過去の試験・調査記録。
- [旧手動運用・旧Batch方式](docs/LEGACY_WORKFLOWS.md)：旧READMEを退避。現行操作の案内としては使いません。
- [workflow.md](docs/workflow.md)：旧手動運用の背景・確認項目。
- [Notionプロパティ定義](notion/database_schema.md)、[環境変数の例](.env.example)。既存 `.env` を上書きしないでください。
- `pubmed_notion_grarec_handoff.md`：初期の引き継ぎ資料。現在の操作はこのREADMEとAUTOMATION.mdを優先します。

2026-11-08までの確認では、ローカルテスト99件成功。PMID 39678704の実API精読、Web画像の取り込み・承認・既存Notionギャラリー登録を確認済みです。
画像APIが無関係な写真を返した原因は未解決で、画像APIの自動運用は停止を維持しています。Review／Consensusの全文・全声明の網羅性や、画像の医学的正確性は別途、人による確認が必要です。
2026-11-10の整理では案内文書のみ変更し、有料API実行・Notion更新・データ移動・停止解除は行っていません。
