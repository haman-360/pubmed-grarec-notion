# PubMed GraRec Notion

PMIDから論文本文を取得し、精読JSONを作り、**ChatGPT Webで自分が生成したグラレコを確認してNotionに保存する**ためのリポジトリです。

## アプリを起動する

**`PubMed-grareko-notion.app` をダブルクリックしてください。** `PubMedGraRecAuto.command` からも同じ画面を開けます。アプリ本体はこのリポジトリ内に置き、Dockへ登録して使えます。別のMacでは環境準備後に `.command` を開くとmacOS標準のSwiftツールでアプリを作成します。

画面の「登録総数」が、このMacの台帳にある全論文数です。その内訳を「PDF取得待ち」「JSON作成待ち」「JSON作成中」「画像作成・登録待ち」「Notion登録完了」「要確認・保留」の6区分に分け、重複せず合計が総数になるよう表示します。カードを押すと論文一覧を絞り込み、「絞り込みをリセット」で全件に戻ります。取得エラー・識別確認・保留等は「要確認・保留」に入ります。

**JSONを作る対象は「JSON作成待ち」です。「Notion登録完了」は画像登録まで終わった論文で、再作成の対象ではありません。** 画像の登録数は下の「Webで作ったPNGをNotionへ」で枚数として別に表示します。いずれもこの台帳の記録に基づき、Notion全体の件数ではありません。

1. 「PMIDを追加する／PubMed AbstractのTXTを選ぶ」で、PubMed Abstractの `希望PMIDを出力.command` が作成した `pmids-日時-識別子.txt` を選び、内容を確認して「PMIDを登録」。ファイル選択は隣の `pubmed_abstract/ledger_private/exports/` から開きます（存在しない場合はダウンロードフォルダ）。別の場所に保存したTXTも選べます。
2. 「未検索PMIDを検索」で検索履歴がない論文を探索。「JSON作成待ち ○本の対象を確認」を押し、PMIDと論文名を見て1〜5本を選び、「選択した○本のJSONをBatchで作成（API課金）」で依頼します。最初は候補の先頭5本までにチェックが入ります。「検索後にJSON対象を確認」は検索後に同じ選択欄を開きます。選択した論文だけが対象で、一覧の絞り込み条件では送信対象は変わりません。予算や品質の条件で依頼できなかった場合は、一覧に理由を表示します。
3. 「精読結果を回収」でJSONを回収。自動回収をオンにすると、アプリを開いている間、依頼済みBatchを毎分確認します。閉じた後もAPI側のBatchは進み、次回回収できます。追加論文の課金依頼を無制限に繰り返す動作ではありません。
4. 「画像作成・登録待ち」を押し、論文の「コピーするテーマ」を選んで **「ChatGPT用の依頼文をコピー」** を押します。「コピーしました」と表示されたら、WebのChatGPTの入力欄へ **⌘V** で貼り付けます。デザイン指示と選んだ1テーマのJSONが含まれます。別テーマも同じ手順で1つずつコピーします。コピーだけではAPI課金・画像生成・承認は行いません。画像用ファイルは「画像用ファイルを準備」から再確認できます。「フォルダ」内の `ready_for_web/` からファイルを開く従来の方法も使えます。
5. 各論文の「Web作成フォルダ」を開き、`images/テーマID.png` としてPNGを保存します（例：`main.png`）。テーマIDは論文一覧に表示されます。従来の共通保存先 `input/web_images/PMID_番号_topic.png` も利用できます。同じテーマはどちらか一方に保存してください。保存フォルダの一覧は数秒おきに更新されます。
6. 表示された登録対象PNGを「画像を開く」で確認。原文・JSONとの照合済みチェック、確認者、確認内容を入力し、**「未登録PNGをまとめて承認・Notion登録」** を押します。取り込み・承認記録・アップロードを続けて実行します。登録済みの同じPNGは再送せず、失敗したものは履歴を保持して再実行できます。
7. 右上の「Notionを開く」で設定済みの論文DBを開きます。

確認後にPNG・JSONが変わった場合は再確認が必要です。画像を保存しただけでは自動承認しません。予算更新も画面内で行えますが、現在の品質停止は解除しません。旧メニューが必要な場合は `.venv/bin/python scripts/process_papers.py menu` で開けます。

## 他のMac・iPhone・iPadで状況を確認する

**[共通の状況画面](https://pubmed-progress-thama.fairkayak3.chatgpt.site)** を同じChatGPTアカウントで開きます。自宅Macのアプリを開いている間、登録件数・進捗・最新のJSON保存完了を約1分ごとに共有します。iPhone・iPadはSafariからホーム画面へ追加し、画面の「通知を有効にする」で保存完了通知を受け取れます。

処理担当は自宅Mac 1台です。別の端末へのPDF・JSON・台帳の複製は行いません。Macのスリープ中は最後の状況を表示し、同期と通知は復帰後です。**JSONの自動回収には従来どおりアプリを開いて自動回収をオンにしておく必要があります。** 詳細・停止方法・通知の確認は [状況共有の説明](docs/STATUS_SHARING.md) を参照してください。

## 急ぐ場合：JSONもChatGPT Webで作る

APIの結果を待たず、WebでJSONから作る経路も利用できます。アプリのAPIキーは使いません。

1. PDF取得済みの論文で **「WebでJSON作成：依頼文をコピー」** を押す。
2. WebのChatGPTに貼り付け、その論文の「フォルダ」にある **`source.pdf`** を添付する。原著・レビュー等の分類、複数テーマ、根拠位置、アプリ用のJSON形式が依頼文に含まれます。
3. 出力されたJSONを **「Web作成フォルダ」内の `master.json`** として保存する。パスは `papers/PMID_番号/web_input/master.json`。親フォルダにある確認用の `master.json` は編集しません。
4. **「保存したJSONを取り込む（API課金なし）」** を押す。対応するPMID・書誌情報・原本ハッシュ・形式・テーマの根拠参照を検証し、API精読と同じ画像作成待ちに進みます。エラーは画面に表示されます。内容の医学的正しさを自動保証する検証ではありません。
5. 「ChatGPT用の依頼文をコピー」で画像を1テーマずつ作る。PNGは **`papers/PMID_番号/web_input/images/テーマID.png`** に保存する。
6. 下の共通のPNG一覧で内容を確認し、「未登録PNGをまとめて承認・Notion登録」を押す。

アプリの依頼文で指定した全文JSONを取り込む方式です。自由形式のJSONや画像用だけのJSONはそのままでは取り込めません。同じJSONは二重登録せず、変更したJSONは別の処理版として保存し、以前のJSON・PNG・承認履歴を残します。API依頼中・送信結果不明の論文は先に回収・確認してください。Web作成のJSONやPNGを保存しただけでは、有料API依頼やNotion送信は始まりません。

## Batchと費用表示

アプリからのAPI精読は **Batchのみ（通常APIの半額・24時間枠）** です。通常APIへの自動切り替えはありません。画像はWebで作成します。[OpenAI Batch公式説明](https://developers.openai.com/api/docs/guides/batch)

2026-11-12から新規精読の出力上限は通常 **32,000**、複雑なガイドラインは **48,000** トークンです。PDF本文の長さと冒頭の診療領域（小児・妊娠・全身治療等）を使うローカルの目安判定で、判定理由を処理版に保存します。論文種別の最終判定ではなく、抽出できない図表や未認識の領域は判定できません。送信済み・完成済みの版は自動変更しません。上限には推論分も含み、実際に消費したトークンで課金されます。

長文入力は通常停止しますが、原本と費用を確認した特定の処理版に限り投入可能です。272,000入力トークン超では入力2倍・出力1.5倍の料金を予約額と実績額に反映し、期間$20・1処理版$4の条件も維持します。今回の再依頼も旧失敗版・費用・原本を残して新しい処理版として記録します。

「初回5論文/$20」という旧表示は、件数制限と金額制限をまとめた表示でした。2026-11-11に累計5論文の制限を廃止しました。1回に選ぶ件数は最大5本、既存の期間金額上限（現在$20）・1処理版$4・残額確認等は維持します。予算は自動で引き上げません。

対象確認欄の **Batch概算予約** は、入力見積もり・最大出力・キャッシュ書き込み・余裕を含む額です。実際の請求額ではありません。長文入力が272,000トークンを超える場合は別途自動投入を止め、Web経路を使えます。画面上部には保存済みusageから計算した精読費と、結果未確定の予約額を別々に表示します。実請求はOpenAIの請求画面で確認してください。[使用モデルの料金表](https://developers.openai.com/api/docs/pricing)

## 現在の流れ

```text
pubmed_abstractで「入手希望」を保存 → 希望PMIDをTXT出力
  ↓ TXTをこのアプリに指定
手元PDFの照合 → 無料本文の探索・PDF取得 → 成否・候補・エラーを台帳に記録
  ↓ 本文を入手・識別できた論文
Batch APIまたはChatGPT Webで精読・論文分類 → Master JSON → 画像ごとのGraphic JSON・Web依頼文
  ↓ Web依頼文を1テーマずつChatGPT Webへ渡す
自分で画像生成 → PNG取り込み → 原文・JSON・画像を確認して承認 → 明示操作でNotionへ
```

既定は `manual_web`。画像APIは使いません。Batch精読には別途API利用料がかかります。Web JSONの取り込みはアプリのAPI課金なしです。
**画像APIの品質停止とJSON作成の停止は別です。画像APIを停止したままJSONを作成できます。** 残額の確認が24時間を過ぎている場合は「予算の確認・更新」で現在の残額を更新してください。予算の更新で品質停止が解除されることはありません。
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

**`PubMedGraRecAuto.command` を開くだけで前回の記録を引き継ぎます。試験用・通常用の指定は不要です。**
起動時に既存の記録を自動選択し、次回の起動先を保存します。開くだけではAPI送信・Notion更新は行いません。

**PDF・JSON・画像は、リポジトリ直下の `papers/PMID_番号/` から確認します。** 画面の **論文フォルダを開く** からFinderで開けます。

```text
papers/
  PMID_39678704/
    案内.txt             論文名・論文種別・現在の状態・ファイルの説明
    source.pdf           現在の処理に使う原本PDF
    review.json          精読結果
    master.json          論文全体・分類理由・章立て
    plan.json            分割設定
    graphics/            画像1枚ごとのJSON
    ready_for_web/       ChatGPT Webへ渡す依頼文（JSON込み）
    images/              取り込んだPNG（承認状態はmanifest.jsonで確認）
    manifest.json        画像の対応・承認状態
    status.json          取得履歴・処理状態
    pdfs/                入手した各版のPDF
    versions/            以前の処理版・JSON・画像
```

未取得・未完了のファイルはまだ表示されません。新しい精読版が未完了の場合、旧版のWeb依頼文は `versions/` に残し、現在の依頼文としては表示しません。PMIDが未設定の論文は `PAPER_内部ID/` に保存します。

| 対象 | 保存先・操作 |
|---|---|
| 希望PMIDのTXT | pubmed_abstract側の `ledger_private/exports/pmids-日時-識別子.txt`。`希望PMIDを出力.command` で保存後、フォルダが開く |
| 旧GAS画面のPMID出力 | `pmids.txt` をブラウザーでダウンロード。場所はブラウザー設定による |
| このアプリへ渡すPMID | メニュー1で上記TXTを指定。コピー不要。空欄時だけ `input/pmids.txt` を使う |
| 自分で入手したPDF | `input/pdfs/` に置いてメニュー1から取り込む。元ファイルは保持し、取り込み後はPMID別フォルダでも確認できる |
| Webへ渡すファイル | `papers/PMID_番号/ready_for_web/*_prompt.md` |
| 自分で生成したグラレコ | Downloads等へPNG保存 → メニュー13から取り込む → `papers/PMID_番号/images/` で確認 |
| 取得成否・候補・エラー | メニュー2・3・9、および各PMIDフォルダの `status.json` |
| 任意の追加コピー | メニュー12で新規フォルダを指定した場合だけ書き出す。指定しなくてもPMID別フォルダを使える |

JSONは論文種別別ではなく、PMID・処理版・画像テーマごとに保存します。種別はJSONの `paper_type` と `案内.txt` で確認できます。

PMID別フォルダは `web_input/` を除き確認用コピーです。**既存の原本・JSON・画像・承認履歴は台帳側に保持し、移動・削除しません。** 各操作後と次回起動時に更新されます。表示先は最新の処理版に切り替わり、旧版や差し替え前のPNGも残ります。
コピーを直接編集しても台帳には反映しません。コピーの変更を検出した場合は自動で上書きせず更新を止めるため、分割設定は別の場所へ別名保存し、メニュー12で指定してください。PNGは `web_input/images/` または共通PNG保存先に置いてアプリで確認・登録できます。メニュー13での取り込みも利用できます。

`papers/` はGit管理外です。Git pushだけでは論文データ・台帳は別のMacへ移りません。メニュー10のバックアップを使います。
以前の手動運用の `input/chatgpt_summaries/`、`images/YYYY/MM/`、`output/` も元の場所に残します。これらを新規API処理や承認済み画像として自動登録する変更はありません。

## CLI補助メニュー（任意）

初回環境準備は `SetupPaperPipeline.command`。API・予算設定は [初回設定](docs/AUTOMATION.md#初回設定) を参照してください。
以下は `scripts/process_papers.py menu` で開く補助メニューの説明です。普段は上記のアプリ画面を使用します。

| 順番 | 操作 | 結果・確認すること |
|---|---|---|
| 1 | pubmed_abstractの `希望PMIDを出力.command` → Googleログイン → 「希望PMIDをTXTへ保存」 | `ledger_private/exports/` が開く。保存済みの入手希望を出力する |
| 2 | グラレコ側の **1 新規取り込み・実行** → TXTをドラッグして指定 | PDF照合・探索・履歴保存。予算・停止状態に問題がなければAPI精読へ進む |
| 3 | **2 状況**／**3 要確認** | PDF未取得、PDF識別待ち、API待ち・品質停止などを確認する |
| 4 | 精読後、**12 Web用ファイル準備** → PMID → 分割設定・追加コピー先は通常空欄 | PMID別フォルダを自動更新。メニュー16で開く |
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
| `PubMedGraRecAuto.command` | **現行メニューの起動**。前回の記録を自動的に開く |
| `scripts/process_papers.py` | 現行CLIの入口。`paper_pipeline/cli.py` へ転送。旧処理の関数も互換用に同居 |
| `scripts/paper_pipeline/cli.py` | メニュー、取り込み・再開・書き出し等の司令役 |
| `scripts/paper_pipeline/sources.py`・`pubmed.py` | 書誌照合、無料本文の探索、PDF取得 |
| `scripts/paper_pipeline/batches.py`・`direct.py` | API精読。Batch処理と通常API試験 |
| `scripts/paper_pipeline/graphics.py` | 分類・分割用スキーマ、Master／Graphic／Web依頼文、PNG取り込み・承認 |
| `scripts/paper_pipeline/store.py` | 台帳、原本コピー、履歴、予算、CSV、バックアップ |
| `scripts/paper_pipeline/workspace.py`・`library.py` | 前回の記録を選択・記憶し、PMID別の確認用フォルダを更新 |
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
2026-11-10：起動先の自動引き継ぎとPMID別フォルダを追加。通常のAPI生成停止設定は維持し、この変更で有料API実行・Notion更新は行っていません。
