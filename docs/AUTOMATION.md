> **この文書は旧方式の記録です。** 2026-11-13に現行OneDrive版へJSON Batchと、PNG確認後のNotion登録ボタンを接続しました。旧台帳の自動処理・定期回収・Push通知・PNG生成APIは再開していません。現行の操作は [README](../README.md) を参照してください。

# ハイブリッド運用：API精読 → Web画像生成 → 承認 → Notion

## 2026-11-12 iPhoneの状況画面をOneDrive版へ接続

本人専用の既存サイト・認証・URLを維持し、Macが画面に表示する6区分の件数をそのまま共有するように変更。受け渡しファイルの検証エラーによる「要確認」も含め、別の台帳や分類ロジックで再集計しない。共有は別スレッドで行い、失敗時は約30秒後に再試行。新しい観測を優先し、論文内容・PMID・パス・メモを送信せず、旧API台帳・有料API・自動回収・Push通知には接続しない。

iPhone画面は既存のデザインを保ち、PNG作成待ち／PNG確認・手動登録待ちを分離。約30秒間隔と画面復帰時に再取得し、2分以上更新がない場合は最後の状況であることを表示。OneDrive版へ切り替えた後の旧方式の報告は拒否する。Mac画面にも共有成功・未設定・再試行を表示。

検証：Python全167テスト、サイト側9テスト、両画面のJavaScript構文チェックが成功。393px幅の表示を確認。11月13日にMacアプリを再起動し、公開サイトの実データが総数53・PDF待ち42・JSON待ち7・PNG待ち1・確認待ち1・完了1・要確認1でMacの出力と一致、共有時刻も更新されることを確認した。サイトは既存の本人専用設定とURLを維持して更新済み。実際のiPhone上での再読み込み確認は利用者側で行う。


操作と保存先を最初に確認する場合は [README](../README.md) を参照してください。このページは詳細設定・CLI・試験記録を含みます。旧手動・旧Batch方式の旧READMEは [LEGACY_WORKFLOWS.md](LEGACY_WORKFLOWS.md) に保存しています。

## 2026-11-11 Batch運用・Web全文JSONの取り込み

アプリの精読は既存の `/batches`・`/v1/responses`・24h経路を継続し、設定に `mode=batch` を明記。通常APIへのフォールバックは行わない。旧エラー「初回5論文/$20」は実台帳では5論文の累計制限だった。累計5論文制限を撤廃し、期間$20（明示された既存production設定は$80）・1版$4・確認済み残高・期限・品質停止・再送抑止を維持した。停止理由は期間予算・残高・1版上限に分離し、使用/予約額と今回の予約額を表示する。画面は旧エラーを履歴として説明し、台帳の過去エラーは書き換えない。

Batch予約額に30分キャッシュ書き込みの1.25倍を考慮。精読usageの換算は通常入力・キャッシュ読込（0.05倍）・書込（1.25倍）を区別する。過去の保存済みactual値は変更せず、画面の精読換算額は保存usage・当該版単価で計算し直す。新規回収分からはactualにも新計算を適用。最終的な請求はプロバイダーで照合する。公式料金は [Pricing](https://developers.openai.com/api/docs/pricing) と [モデル](https://developers.openai.com/api/docs/models/gpt-6.1-sol) を2026-11-11に確認。

各PMIDフォルダに `web_input/master.json` と `web_input/images/<topic_id>.png` の受け取り先を追加。「WebでJSON作成：依頼文をコピー」は原本PDF添付・分類・根拠・複数テーマ・hybrid-v1スキーマ・原本ハッシュを含む依頼文を保存/コピーする。「保存したJSONを取り込む」はネットワーク不要。厳密な形式・PMID/DOI/タイトル/既知の誌名と年・原本ハッシュ・テーマと根拠参照を検証し、元JSONのコピーを含む新処理版を作る。Web由来・未確認を記録し、通常の `prepare_web` → Graphic → PNG承認 → Notion経路に接続する。原本・旧版・承認履歴は保持し、同じJSONは冪等、変更時は新しい版とする。進行中/結果不明のAPI要求がある論文は取り込まない。古い待機版が後からBatchに入らないよう、新版の準備成功後に旧版をsupersededとする。

従来の共通 `input/web_images/` と新しい論文別PNG保存先を同じ一覧で扱う。2箇所に同じ論文・テーマの画像がある場合は自動選択せず、1枚への整理を案内する。保存のみではAPI依頼・Notion送信・画像承認はしない。

検証：136テスト成功。合成データで2テーマのWeb JSON取り込みからPNG承認・模擬Notion登録まで通過。再取り込み、改訂履歴、誤PMID/PDF、根拠欠落、API結果不明、コピー後のJSON変更、二重PNG、6本目のBatch処理、期間金額上限維持、キャッシュ課金計算を確認。実Mac画面でBatch見積もり・Web作成ボタンと保存先案内の表示を確認。追加の実機コピー操作はMacロックのため未確認（コピー内容は模擬クリップボードで検証）。台帳7テーブルの全レコード不変を確認。保全確認はGit管理外の `output/web-import-before.json`。実データを使った有料APIの新規送信・Notion実送信は行わない。

## 2026-11-11 テーマ別のChatGPT依頼文コピー

論文一覧に「コピーするテーマ」の選択欄と「ChatGPT用の依頼文をコピー」を追加。準備済みの有効なGraphicごとに日本語テーマ名を表示し、指定した1テーマのデザイン指示＋JSONをmacOSのクリップボードへUTF-8でコピーする。選択は一覧の自動更新後も維持する。成功時はPMIDとテーマ名、⌘Vによる貼り付け案内を表示。テーマがない論文は従来の「Web用準備」を利用する。

コピー前に最新処理版・有効テーマ・JSONハッシュを照合し、Master/Graphic JSON、保存済み指示、準備済み依頼文の整合性を検証する。依頼文の生成書式は準備とコピー検証で共有し、既存ファイルの内容は変更しない。コピーは既存ファイルの読み取りとクリップボードへの書き込みのみで、API送信・画像生成・承認状態変更は行わない。

検証：129テスト成功。テーマ別JSONの分離、準備済みファイルとの完全一致、古いテーマ選択・改変された依頼文・ファイル欠落・クリップボードエラーを確認した。実MacでPMID 39924148の「睡眠転帰」と「受容性」を順に選択し、コピー後の貼り付けにそれぞれのテーマIDとJSON末尾が含まれ、別テーマのJSONが混入しないことを確認。貼り付け確認用の未送信入力欄は空へ戻した。台帳7テーブルの不変を照合し、有料API要求・Notion実送信なし。保全確認の基準はGit管理外の `output/copy-prompt-before.json`。

## 2026-11-10 進捗カードの絞り込み・JSON対象の明示

上部を登録総数と相互に重複しない6工程に変更した。カードで論文一覧を絞り込み、リセットで全件表示する。画像数は下部へ分離し、論文数と画像枚数が混ざらないようにした。旧「処理済み」は「Notion登録完了」と明記。PDF未取得でも取得エラー・保留があれば「要確認・保留」に分類する。

JSONボタンは候補のPMID・論文名・チェック欄を開き、利用者が1〜5件を選んで実行する。JSON作成済み・処理中・保留・未確定送信・再試行上限等を除外する。画面表示後にPDFや進捗が変わった選択は、送信前に再検証して拒否する。Batchの版準備と送信に任意の対象指定を追加し、画面からは選択した論文の最新版だけを渡す。従来CLIの全体処理は従来どおり。検索→JSONのボタンも検索後に候補確認を開き、画面で対象を示す前に課金依頼しない。

検証：125テスト成功。全6工程の件数合計、選択外の論文に処理版を作らない・送信しない、古い選択・送信不明・上限超過・重複選択の拒否を含む。実Mac画面で全53本→Notion登録完了1本→リセット53本→JSON候補7本の表示、6件選択時の実行不可を確認。実台帳はPDF取得待ち41、JSON作成待ち7、作成中0、画像作成・登録待ち2、Notion登録完了1、要確認2。台帳7テーブルの全レコード不変を確認。有料API・Notion実送信なし。保全確認の基準はGit管理外の `output/dashboard-filters-before.json`。

## 2026-11-10 JSON作成と画像API停止の分離・Abstract TXT選択

予算設定に `analysis_suspended_reason`／`image_suspended_reason` を追加。前者はJSON作成、後者は画像APIだけを止める。従来の `suspended_reason` は全工程の停止として引き続き有効。通常の画面・Batch・Directで工程別の停止を適用し、予算更新時も3種類の停止理由を保持する。画像APIを止めてもmanual_webのJSON作成とWeb依頼文準備は進む。残額確認24時間・期間・予約額・1版$4等の制限は変更していない。

利用者のJSON再開指示に基づき、このMacの既存台帳をバックアップして、既知の画像品質問題による全工程停止を画像工程だけの停止へ変更した。変更前後の理由と指示を台帳に記録。ほかの全工程停止を起動時に自動解除する仕組みではない。

PMID追加欄にPubMed Abstractの出力TXTを明記し、ネイティブファイル選択の初期位置を隣接リポジトリの `pubmed_abstract/ledger_private/exports/` に設定。存在しなければDownloadsを開く。実際の出力TXTを選んで、PMIDが入力欄へ読み込まれるところまでMac画面で確認した。今回の試験ではファイル全件の登録は実行していない。

検証：119テスト成功。画像停止中のJSONのみ実行、全体／精読停止の維持、残額期限切れの拒否、予算更新時の停止保持を含む。アプリのビルドと実画面表示を確認。

実サービスでの1件試験：PMID **39924148**（思春期の不眠症に対するデジタルCBTの系統的レビュー／メタ解析）の19ページ・1,645,588 bytesのPDFをPMCから取得・識別した。利用者が提示した現在の請求画面で残額と自動チャージOFFを確認して台帳を更新し、この1件だけDirect精読を実行。本文入力65,678 tokens／出力10,639 tokens、設定単価による計算額$0.237746（請求額の独立照合ではない）。`READY_FOR_WEB` に到達し、`papers/PMID_39924148/` にPDF・review.json・master.json・2テーマのGraphic JSONとWeb依頼文を保存した。分類は `systematic_review`。テーマは睡眠転帰と完了率・受容性。構造検証と識別子の一致を確認したが、臨床的な内容の人による確認は未実施で、Human Checkedはfalseを保持。新規精読要求1件、画像API要求0件、Notion実送信なし。

バックアップと取得・保全記録はGit管理外の `output/json-resume-20261110/`。既存台帳の論文11件を含む全既存レコード（停止設定を除く）と元ファイル67件の不変を照合した。

## 2026-11-10 Macアプリ画面

`PubMed-grareko-notion.app` を追加。`PubMedGraRecAuto.command` はアプリをビルドして開く入口に変更。既存CLIは `scripts/process_papers.py menu` で利用できる。AppKit／WKWebViewのウィンドウと、127.0.0.1のランダムポートだけで待ち受けるPythonバックエンドを使う。操作APIには起動ごとのトークンとHostの照合を要求し、外部公開・CORS許可はしない。アプリ終了でバックエンドと自動回収も終了する。

登録数・PDF未取得・精読JSONあり・最新処理版の画像登録状況、論文一覧、未検索PMID検索、JSON Batch依頼／回収、検索→JSON依頼、TXT読み込み、予算更新、フォルダ表示、Notion DBを開く操作を一画面に配置した。新規依頼は1回最大5件で、予算・品質停止・曖昧な再送の保護は既存コードを使う。自動回収は利用者がオンにした場合だけ毎分実行し、追加の有料依頼は行わない。未取得の再検索は既存CLIで行える。

PNG受け取り先はGit管理外の `input/web_images/`。`PMID_<番号>_<topic>.png` を最新Graphic JSONと対応付け、登録済みか表示する。確認済みチェック・確認者・確認内容を入力して一括登録すると、PNG取り込み→画像承認→Notion同期を実行する。表示後にPNG・JSONが変わった場合は再確認を要求。途中失敗は項目ごとに表示して履歴を保持する。Human Checkedは自動変更しない。既存のNotion全体ではなく、この台帳の最新処理版の件数を表示する。

検証：全117テスト成功（新規7件）。未検索対象の選択、PMID一括入力の事前検証、PNG対応・変更検出・承認必須・登録済み再送防止、予算更新時の品質停止保持、ローカルAPIの認証を確認。Macアプリを実際に開き、登録4件・処理済み1件・PDF未取得1件・JSONあり3件等の表示と一覧更新を確認した。既存台帳全レコードと以前のバックアップに含まれる管理ファイル57件の不変を照合。実API精読・Notion実送信は今回行っておらず、実サービスでの新画面からの一連の処理は未確認。

## 2026-11-10 起動先の引き継ぎ・PMID別フォルダ

`PubMedGraRecAuto.command` の通常起動で既存台帳を検出し、前回の記録を開く。選択した起動先を `output/app_state.json` に保存する。優先順は明示的な `--state-dir` → `PAPER_PIPELINE_HOME` → 保存済みの起動先 → 既存の記録 → 新規の既定台帳。既存台帳の探索は読み取り専用で、空の既定台帳より記録のある旧試験台帳を優先する。記録のある台帳が複数あり起動先が未指定の場合だけ、初回の明示指定を要求する。保存済みの台帳が見つからない場合は新規作成へ切り替えず停止する。

起動時・各操作後に `<台帳>/papers/PMID_<番号>/` へ確認用コピーを作り、リポジトリ直下の `papers/` 内のPMID別リンクから開けるようにした。メニュー16は全論文または指定PMIDのフォルダをFinderで開く。メニュー12の追加書き出し先は空欄でよい。
原本・JSON・PNG・台帳の既存パス、原本ハッシュ、承認、費用、停止設定は変更しない。コピーは処理版ごとの `versions/` に保持し、通常使うファイル名は最新処理版への相対リンクにする。未完了の新版には旧版のWeb依頼文を表示しない。manifest・状態の更新前スナップショットも残す。コピーに手動変更があれば自動上書きしない。既存の `papers/` 内のファイルを保持し、同名の通常フォルダや利用者のリンクは置き換えない。
`papers` はGit管理外。実体は台帳配下なのでメニュー10のバックアップ対象に含まれる。復元した台帳を `--state-dir <復元先> menu` で一度開けば、以後は通常起動で再利用する。旧手動運用の入力JSONや公開画像は元の場所に保持し、自動で解析・承認しない。

検証：新規11件を含む全110テスト成功。起動先の再利用、空台帳・複数台帳・保存先消失、PDF/JSON/PNGコピー、承認・画像差し替え・過去状態の保持、新版の旧依頼文の非表示、利用者ファイルの保護を確認。ランチャーを実データで2回起動してメニュー終了まで確認し、既存64ファイルのハッシュ、台帳の全レコード、既存papersフォルダのファイル不変を照合した。4件のPMID別フォルダを作成済み。変更前バックアップと照合記録はGit対象外の `output/workspace-upgrade-20261110-221825/`。有料API実行・Notion実送信・品質停止の解除は行っていない。

## 2026-11-10 運用案内の整理（上記改修前）

READMEを現行の操作順・保存先・論文分類・JSON用途・プログラム役割の案内に整理した。旧READMEの手順はLEGACY_WORKFLOWS.mdへ移し、既存の相対リンクを修正した。実装・プロンプト・設定・データの変更はない。
pubmed_abstractの現行Mac出力コードを読み、希望PMIDの保存先が `ledger_private/exports/pmids-日時-識別子.txt` であることを確認した。旧GAS画面の `pmids.txt` ダウンロードとは区別する。
このMacでは既存台帳が `.paper_pipeline-pilot/`、既定の `.paper_pipeline/` は未作成。試験台帳の最新PMID 39678704はWeb画像の承認・Notion同期済みとして記録され、通常のAPI生成停止設定は維持されている。今回のNotionライブ再確認、有料API実行、停止解除は行っていない。
検証：関連するハイブリッド運用の既存テスト32件成功（通信は模擬応答）。更新文書4件の相対リンク、旧READMEの退避内容の保全、`git diff --check` を確認した。

## 2026-11-08 現行の操作

既定は `manual_web`。解析モデル・画像のデザイン指示v2は維持し、医学的内容と画像生成の責務を分離した。
`auto` は画像待ちで終了する。Notionへの送信は `sync-notion`、または明示的な `--notion` 指定時のみ。
これ以降の例はリポジトリ直下で実行する。`--state-dir` はサブコマンドの前に置く。
現在は省略時に前回の保存先・既存台帳を引き継ぐ。下の旧試験コマンドには再現用の `--state-dir` が残っているが、通常のメニュー操作では指定不要。

### 新しい論文

1. `SetupPaperPipeline.command` で環境を準備し、下段の初回設定に従ってAPI・予算を設定する。
2. `PubMedGraRecAuto.command` の1でPDF/PMIDを取り込み、精読Batchを開始する。画像生成APIは使わない。
3. 精読結果が検証されるとMaster、Graphic、Web用Markdown、manifestを自動出力する。
4. メニュー2で状態と出力先を確認する。メニュー12で利用しやすい新規フォルダへ書き出すこともできる。
5. ChatGPT Webの「グラレコ作成」Projectに、該当する `ready_for_web/*_prompt.md` の内容を渡す。このMarkdownには対象Graphic JSONが埋め込まれている。複数テーマを一度に混ぜず、1依頼につき1枚を生成する。
6. PNGを保存し、メニュー13でPMID・topic_id・ファイルを明示して取り込む。
7. 原文・JSON・画像を人が照合し、メニュー14で承認／却下と確認内容を記録する。
8. メニュー15で対象論文の承認済み画像だけをNotionへ送る。生成・保存しただけでは送信しない。

分類と分割案は精読と同じAPI要求で生成する。Originalは原則1枚、Systematic Reviewは単一疑問なら長文でも1枚、Narrative/Scoping ReviewとGuideline/Consensusは独立した臨床テーマを使う。ページ数で分割しない。Overviewは任意、テーマ数は固定しない。
保守的なページ換算が入力上限を超える場合は、既存の全文トークン計数経路で実入力を確認する。実入力も上限超過なら停止し、PDFを自動切り捨てしない。

### 品質停止中の1論文だけを画像APIで試す

有料画像生成を明示して承認した場合に限り、既存の精読JSONから新版を作成して1枚生成できる。

```sh
.venv/bin/python scripts/process_papers.py --state-dir .paper_pipeline-pilot regenerate 39678704 \
  --stage image --image-generation-mode api
.venv/bin/python scripts/process_papers.py --state-dir .paper_pipeline-pilot direct \
  --paper 39678704 --allow-image-api --resume-image-only --yes
```

`--resume-image-only` は1論文・既存JSON・api設定・画像API明示許可が必要。品質停止設定を保持し、通常の予算・残額確認期限を適用する。精読API、複数論文、`--notion`、`--resume-analysis-only` は併用不可。失敗・応答不明を含め、同じ版の画像要求を自動再送しない。`regenerate` を繰り返すと別の有料生成となるため、再試行目的では実行しない。

画像は `artifacts/<版ID>/grarec.png` に保存し、Web画像と同じ承認待ちに進む。人が原文・JSON・PNGを確認して `review-image --approve` した後、`sync-notion` で登録する。実際に送信した要求と応答は `http_audit/` に記録する。

2026-11-08：PMID 39678704の既存fulltext-v4 JSONから、通常画像APIを1回実行。HTTP 200・指定寸法PNGを得たが、返却画像は論文と無関係な寝室の写真でIMAGE_REJECTEDとした。20,237文字のプロンプトの送信前後の本文一致を監査記録で確認した一方、応答usageの入力は8 tokens。設定単価による画像費用計算は$0.04243（請求額の独立確認ではない）。短い原著でも内容不一致が再現したが、サーバー内部の原因は未確定。追加生成・Notion書き込みなし。Notion接続の読み取り確認ではAutomation ID項目がなく、スキーマは変更していない。元の生成物15ファイルのハッシュ・予算・品質停止設定の不変を確認した。詳細はGit管理外の `output/PMID_39678704/api-image-trial-v1/trial-report.json`。ローカルテスト97件成功。

### 保存済みJSONを再利用する（API不要）

```sh
.venv/bin/python scripts/process_papers.py --state-dir .paper_pipeline-pilot prepare-web \
  --paper 41064908 --export output/PMID_41064908/web-v1
```

`--export` は任意。既存の出力先には上書きしない。出力コピーのmanifestは書き出し時点の状態であり、進捗の正本はSQLite台帳。
`--revision` で版IDを固定でき、省略時は最新の処理版を使う。元の `review.json`、PDF、不合格PNG、API履歴を変更しない。
旧JSONの不足フィールドは空欄／未確認として扱い、根拠の引用を新しく作らない。
旧Originalは1枚分を準備するが、Review/Consensusや分類不明は `structure_review_required` で止め、Masterと分割設定のたたき台だけを出す。

管理領域の構造：

```text
artifacts/<revision_id>/
  review.json                 既存精読結果（保持）
  web/<bundle_hash>/
    master.json               論文全体・章構造・根拠・分割方針
    plan.json                 編集用の分割設定
    graphics/00_main.json     画像1枚の入力
    ready_for_web/00_main_prompt.md
    images/PMID_<pmid>_<topic_id>_<hash>.png
    manifest.json
```

### 分割・統合の変更

`plan.json` を管理領域外へコピーし、`topics` と `visualization_strategy` を編集する。
`topics` の `result_indices` / `recommendation_indices` はMaster内の0始まりの番号。すべての結果・声明を少なくとも1テーマへ割り当てる。
`source_sections` は `document_structure.section_id` を参照する。テーマごとに中心メッセージとPractice Changeの判断を保持する。
複数テーマは `mode: topics`、1テーマは `mode: single`。`include_overview` で俯瞰画像を選択する。
分類・章構造が未確認なら、原文を確認して `paper_type`、`classification_reason`、`document_structure` も修正する。

```sh
.venv/bin/python scripts/process_papers.py --state-dir .paper_pipeline-pilot prepare-web \
  --paper 42835691 --plan /path/to/edited-plan.json
```

新しいMasterを明示的に取り込む場合は `--master /path/to/master.json`。書誌情報・原本ハッシュを照合し、旧JSONを上書きしない。
Masterには `schema_version: hybrid-v1`、5種の `paper_type`、章構造、臨床疑問、結果と根拠、推奨・専門家合意、Overview、テーマ、分割理由を持つ。
非該当PICOと論文全体に付与できないPractice Changeはnullを許容する。Yes/No/Unclearの意味は変更しない。
推奨の対象・条件・例外・合意率・エビデンスの強さは別フィールド。Delphiの同意率を治療効果量や確実性に変換しない。

計画を再適用しても、同じ内容のGraphic・承認済みPNGは再利用する。内容が変わったGraphicだけ新規扱いにする。
削除されたテーマは非アクティブになり、ファイル・承認履歴は残る。Notionへ既に送った旧版は削除しない。

### PNGの取り込み・確認

```sh
.venv/bin/python scripts/process_papers.py --state-dir .paper_pipeline-pilot import-image \
  --paper 41064908 --topic main --source '/path/to/ChatGPT Image.png'

# 原文・JSON・画像を人が照合した後だけ実行する
.venv/bin/python scripts/process_papers.py --state-dir .paper_pipeline-pilot review-image \
  --paper 41064908 --topic main --approve --reviewer '確認者名' \
  --note '論文対応・数値・解析集団・条件・限界・画像品質を確認'

# 別論文の混入などがあれば却下する
.venv/bin/python scripts/process_papers.py --state-dir .paper_pipeline-pilot review-image \
  --paper 41064908 --topic main --reject --reviewer '確認者名' --note '別論文の内容が混入'
```

PNG実形式・破損・横長16:9または3:2・重複を確認する。2048×1152、1600×900、1536×1024などを受け付け、Web出力の整数画素への丸め差（高さ換算1px以内、例1672×941）も許容する。自動切り抜きや変形はせず、PNGのバイト列をそのまま保存する。
元画像はコピーで保持。差し替え時はハッシュ付き別名で保存し、旧画像を上書きしない。同一PNGを別Graphicへ流用する操作は拒否する。
承認はGraphic JSONとPNGのハッシュの組に結び付く。差し替えは再承認が必要。管理ファイルを直接編集した場合は送信を止める。
ファイル検証は画像内の医学的正確性を保証しない。人による確認は必須。

状態は `READY_FOR_WEB → IMAGE_REVIEW_REQUIRED → IMAGE_APPROVED → COMPLETED`。
PNG取り込み・保存が完了した時点で `IMAGE_REVIEW_REQUIRED` を永続化する。不合格は `IMAGE_REJECTED`。
`COMPLETED` はNotion上の添付を再取得して確認した状態で、医学的な妥当性の保証ではない。

### Notion登録（実送信する操作）

```sh
.venv/bin/python scripts/process_papers.py --state-dir .paper_pipeline-pilot sync-notion --paper 41064908
```

1論文1ページを維持し、承認済みテーマごとに版領域を追記する。Master JSON、Graphic JSON、PNGと出典を添付する。
未承認・却下画像は送らず、部分承認なら承認済み分だけを送る。全テーマの同期が終わるまで論文全体を完了にしない。
既存のページカバー・臨床プロパティ・手書きメモ・Human Checkedは保護する。新規ページのプロパティはMasterに基づき、最初の承認画像をカバーに使う。 既存ギャラリーはGraphic Image（ファイル型）をカード画像に使うため、この欄が空の場合は承認済みPNGを同じアップロードIDで設定し、再取得して確認する。すでに画像がある場合は上書きしない。
画像承認を論文全体の確認と見なさず、ページのHuman Checkedは自動でオンにしない。
`Automation ID` がなければ停止し、DBプロパティを自動追加しない。新しいDBや画像ごとの別ページは不要。

2026-11-08：Web PNGの3:2取り込みに対応。16:9と同様に高さ換算1px以内の丸め差を許容し、正方形・縦長・4:3・範囲外の比率は拒否する。PNGは加工せずコピーし、差し替え履歴と承認待ちを維持する。回帰テスト98件成功。

PMID 39678704のWeb画像（1536×1024）を新しいmanual_web版に取り込み、IMAGE_REVIEW_REQUIREDで保存。元PNGと保存PNGのバイト一致、以前の生成物23ファイル・費用履歴・予算設定の保持を確認。バックアップは `output/PMID_39678704/before-web-image-import/`、確認用コピーと記録は `output/PMID_39678704/web-image-import-v1/`（Git管理外）。画像承認・画像API生成・Notionへの論文登録は未実施。

既存Notion論文DBには、明示的な依頼に基づきAutomation ID（テキスト／APIではrich_text）のみを追加した。既存25項目の定義は維持し、ページ・添付は作成・更新していない。以後のsync-notionでもDB項目の自動追加は行わない。

### 保持・再開・API画像生成

- 初めて旧SQLite台帳を開いたとき、`schema_backups/` にDBのコピーを作ってから画像管理テーブルを追加する。原本・生成物・費用履歴は保持する。
- 通常の `backup` はDBと管理ファイルをまとめてコピーする。復元手順は下段を参照。
- 画像の履歴と承認記録はSQLite、対応表はmanifestに保存。CLIエラーは `errors/` に原因を残す。
- `quality_wait` や費用の停止設定を勝手に解除しない。ローカルの `prepare-web` はAPIを呼ばないため、精読結果が既にあれば利用できる。
- 画像APIコードと通信監査は残す。利用には `regenerate ... --stage image --image-generation-mode api` と、実行時の `--allow-image-api` の両方が必要。有料。現在のAPI経路は1画像のみで、複数テーマはmanual_webを使う。API画像にも同じ人の承認を要求する。
- 旧手動スクリプトは互換機能として残るが、新しい画像承認ゲートを通らない。ハイブリッド運用は上記コマンド／メニュー12〜15を使う。

### 2026-11-08 Web実画像での取り込み調整

PDFから直接作成されたPMID 41064908のWeb画像は1672×941pxだったため、高さ換算1px以内の16:9の丸め差を許容した。画像の変形・再生成はしない。境界値、4:3等の拒否、原本のバイト一致、承認待ち維持を検証し、全93テスト成功。JSONを入力に使っていない既存画像は、保存済み精読JSON・原文との一致を人が確認してから承認する。

### 品質停止を維持した1論文の精読再試験

画像の品質停止中に精読だけを再試験する場合は、対象1件を明示し、manual_webの新版を作成してから限定再開する。

```sh
.venv/bin/python scripts/process_papers.py --state-dir .paper_pipeline-pilot regenerate 39678704 --stage analysis
.venv/bin/python scripts/process_papers.py --state-dir .paper_pipeline-pilot direct \
  --paper 39678704 --resume-analysis-only
```

通常料金の精読APIを使用する。`--resume-analysis-only` は1論文のみで、`--allow-image-api` / `--notion` と併用できない。
台帳の `suspended_reason` は消さず、指定版の精読予約だけを許可する。残額確認24時間、期間、1版$4、初回5論文/$20等の制限は維持し、限定再開と保持した停止理由を要求履歴へ記録する。
完了後はWeb用ファイル準備までで停止する。画像生成・画像承認・Notion送信は別工程。
2026-11-08：通常停止、限定再開、画像/Notion/複数対象の拒否、停止設定と予算制限の維持を含め95テスト成功。

同日、PMID 39678704（MT-12、13ページのOriginal Article）を通常APIで1回精読。入力32,733／出力10,589トークン、設定単価からの計算費用$0.171356（請求画面未照合）。fulltext-v4/hybrid-v1の実応答がスキーマ・書誌・結果19件の根拠対応検証を通過し、単一テーマmain、OverviewなしでREADY_FOR_WEBに到達した。PDFの患者フロー・主要評価図・安全性表をスポット確認したが、全文の医学的承認ではない。

Web用一式は `output/PMID_39678704/web-trial-v1/`、実行前バックアップは `output/PMID_39678704/before-analysis-trial/`。既存生成物のハッシュと予算・品質停止設定の不変、新たな画像API要求0件を確認。Notion実送信なし。画像品質はWeb生成後に評価する。この論文はReviewではないため、長いReviewでJSONからの画像生成が失敗するという仮説は今回の結果だけでは検証できない。

### ハイブリッド改修時の検証記録

92件のローカルテスト成功。既存67件を新しい承認手順に合わせて更新し、A〜E、分割/統合、条件・根拠の保持、API抑止、画像差し替え、改変検知、部分Notion同期、再開、旧DBバックアップ、全文計数を追加検証した。APIは模擬応答。
PMID 41064908の保存済み8結果から1Graphicを準備。PMID 42835691は初期状態で構造確認待ち、保存済み10結果を7テーマに再編成する試験案ではすべての結果・根拠を保持した。
実データの試験は `output/hybrid-evaluation-20261108/` の独立台帳で行い、元PDF・JSON・試験台帳のハッシュ不変を確認。試験案は全文の章立て・全声明の網羅性を保証しない。生成物はGit対象外。
実APIでの新スキーマ抽出、画像の医学的妥当性、Web画像品質、ライブNotion連携は未検証。今回の有料生成・Notion実送信は0件。
画像失敗を論文長のせいと確定していない。過去の `input_tokens=8` と送信・応答対応の問題は独立した未解決課題として保持する。

---

## 従来の設定詳細・試験記録

以下には改修前の二段階Batch等の記録を含む。現行の既定動作・承認手順・実送信条件は上記を優先する。

# PMID・PDFからNotionまで（継続台帳版）

## 初回設定

1. `SetupPaperPipeline.command` をダブルクリックする。Python 3.9以上とインターネット接続が必要。`.venv` に依存ライブラリをインストールする。
2. 既存の `.env` に必要な設定を追記する。既存ファイルを `.env.example` で上書きしない。
   - `OPENAI_API_KEY`
   - `NOTION_TOKEN`、`NOTION_DATABASE_ID`
   - `UNPAYWALL_EMAIL`、推奨：`NCBI_EMAIL`、必要なら `NCBI_API_KEY`
   - 複数データソースがあるNotion DBのみ `NOTION_DATA_SOURCE_ID`
3. Notionの対象DBをIntegrationに接続する。既存のプロパティ定義は `notion/database_schema.md` を参照。現行版は `Automation ID`（rich_text）の存在を確認し、なければ停止する。DB構造は自動変更しない。Galleryはページカバーをプレビューにする。
4. OpenAIの請求画面で **APIに使えるクレジットの残額・期限・請求期間・購入済み残高・自動チャージ無効** を確認する。ChatGPTの契約名だけで付与額を推定しない。
5. `PubMedGraRecAuto.command` → **5 予算設定**。請求期間開始、失効日時（例 `2026-12-01T00:00:00+09:00`）、確認した残額、他用途に確保する額を入力する。残額$100で他用途$20を確保する場合、本体は最大$80。初回は通常運用への切り替えに `n` と答え、5論文/$20枠を使う。

予算設定は残額を確認した時点から24時間有効。期限まで50時間未満、確認期限切れ、残額不明では新規Batchを止める。既に送信したBatchの回収とNotion登録は続けられる。自動チャージ設定をアプリが変更することはない。モデルへのアクセスは送信前に読み取りAPIで確認し、利用不可なら停止する。

初回の実サービス評価は未実施。実際のPDF取得率、文字・数値の正確さ、組織のモデル利用権限、実費、Notion接続先・容量を5件で確認してから通常運用へ進む。

## 普段の操作

1. `pubmed_abstract` から希望PMIDをTXTで出力する（1行1PMID）。本アプリから元リポジトリへ書き戻さない。
2. 手元PDFは `input/pdfs/` に置く。元ファイルは移動・改名・削除しない。別フォルダはCLIの `--pdf-dir` で指定できる。
3. `PubMedGraRecAuto.command` → **1 新規取り込み・実行**。TXTをドラッグして指定する。空欄は `input/pmids.txt`。
4. 初めに手元PDFを照合し、未取得・非保留の全論文を再探索する。入手できた論文から見積もりを示し、予算内で解析Batch → Web用ファイル準備を進める。画像生成・承認・Notion送信は冒頭の現行手順に従う。
5. 30分間隔で回収する。Terminalを閉じても台帳は残る。次回 **4 続きから実行** で再探索せず未完了工程を再開する。Macのスリープ中はローカルの回収が止まる。各工程24時間枠なので全体は48時間を超えうる。
6. **3 要確認** に手動候補URL、識別待ちPDF、改訂版、エラー、未確定費用を表示する。無料候補を手動取得したら `input/pdfs/` に置き、1を実行する。

最初の10件から3件を自動取得し2件を手動取得した後、20件を追加すると、探索対象は残り5件＋20件＝25件。手動2件が未取得なら27件になる。TXTに含まれない過去PMIDも消えない。取得失敗だけで「有料」と断定しない。本文PDFがなければ解析せず、抄録に置き換えない。

### 識別・保留・改訂版

- **6 PDF識別**：確認待ちPDFの内部IDと対応する論文PMID/内部IDを指定する。未収載論文はタイトルを確認し、PMID/DOI空欄でも登録できる。ファイル名の数字だけで自動確定しない。
- **7 保留・解除**：`hold` は一時保留、`exclude` は対象外、`release` は再開。既に送信済みの要求はキャンセルせず結果を保管する。
- 同じPDFはハッシュで重複防止。本文が違うPDFは別原本として保存し、再解析を自動開始しない。
- **8 再生成**：`analysis` は全文から、`image` は既存の精読JSONから。改訂PDFの原本IDも指定可能。既存の未完了待機版は `superseded` として保存される。進行中・結果不明のOpenAI要求がある場合は再生成できない。
- **11 旧版を対象化**：移行した旧記録は初期状態で保留。内容と旧Batchを確認した論文だけ明示的に対象化する。

後日PMIDが判明した未収載論文は、内部IDを変えず識別子を追加できる。

```sh
.venv/bin/python scripts/process_papers.py link INTERNAL_ID --pmid 12345678 --doi 10.1234/example
```

識別子が別の既存論文を指す場合は自動統合せず停止する。論文・原本の内部IDは状況画面とCSVで確認できる。

## CLI

リポジトリ直下で実行する。`--state-dir` はサブコマンドの**前**に置く。

```sh
# 取得・照合・移行・見積もりのみ。OpenAI送信・Notion更新はしない
.venv/bin/python scripts/process_papers.py prepare --pmids-file /path/pmids.txt --pdf-dir /path/pdfs

# ネットワーク探索も省略（ローカル台帳への保存は行う）
.venv/bin/python scripts/process_papers.py prepare --offline

# 次工程のBatchを投入。予約超過なら予算待ちにする
.venv/bin/python scripts/process_papers.py submit

# 回収のみ：有料要求の新規送信なし
.venv/bin/python scripts/process_papers.py resume

# 回収して次の精読工程を投入し、承認済み画像のみNotion登録（実送信あり）
.venv/bin/python scripts/process_papers.py resume --advance --notion

# 新規入力と再探索を含む一連の処理
.venv/bin/python scripts/process_papers.py auto --pmids-file /path/pmids.txt

# 30分ごとの回収・次工程・Notion登録を再開（再探索なし）
.venv/bin/python scripts/process_papers.py auto --resume-only

.venv/bin/python scripts/process_papers.py status
.venv/bin/python scripts/process_papers.py review
.venv/bin/python scripts/process_papers.py export /path/papers.csv
.venv/bin/python scripts/process_papers.py backup /path/new-backup-folder
```

`submit` / `auto` / `resume --advance` の `--yes` は対話確認を省略するだけで、予算制限を解除しない。`--max-papers` は1巡あたりの投入上限であり、初回評価の5論文制限は台帳全体に対して適用する。`resume --no-render` は旧CLI互換で、回収のみと同じ動作。

## 状態・保存場所

正本は選択中の台帳フォルダの `ledger.sqlite3`。起動先は上記の自動引き継ぎに従う。新規環境の既定先だけが `.paper_pipeline/` で、既存記録をそこへ移動する必要はない。`PAPER_PIPELINE_HOME` または `--state-dir` で明示指定も可能。普段はリポジトリ直下の `papers/PMID_<番号>/` からファイルを確認する。実行中のSQLiteをOneDrive等で直接同期しない。

| 場所 | 内容 |
|---|---|
| `ledger.sqlite3` | 論文、原本、探索履歴、処理版、API要求、費用予約、Notion対応、予算設定 |
| `pdfs/<SHA256>.pdf` | 入手・確認した原本のコピー |
| `artifacts/<版ID>/review.json` | 精読JSON、根拠位置、論文ID、原本ハッシュ、生成版・モデル情報 |
| `artifacts/<版ID>/grarec.png` | 2048×1152 PNG |
| `batches/` | 送信JSONLと受信結果。秘密鍵は含めない |
| `legacy/` | 旧ジョブ・JSON・画像・原本・Batch結果の保存コピー |

入手状況、処理状態、保留/対象外は別管理。`completed` は本文・JSON・PNGのNotion再取得確認まで済んだという意味で、医師の原著確認済みという意味ではない。全ての新生成版は `human_checked=false`。

探索順はPMC Cloud Service → Unpaywall → Europe PMC → 正規出版社。PMCは公式S3 HTTPS経路を使用。候補URL、版、優先度、理由、失敗を履歴保存する。投稿稿や撤回フラグは確認待ち。暗号化・破損・HTML・49MiB超PDFは自動解析しない。OCRは行わない。

## 費用と再送

初期モデルは全文 `gpt-6.1-sol`（medium/high、最大出力16,000トークン）、画像 `gpt-image-2.5-sunburst`（high、2048×1152、1枚）。両方Batchで、失敗を分離するため1要求につき1Batchを作成する。モデルを黙って切り替えない。画像指示は `prompts/grarec-v1.md` を採用し、内容・ハッシュを生成版に保存する。

- 既定は期間あたり$20。既存のproduction設定は期間あたり$80。累計件数制限なし。いずれも1処理版$4（両工程・再試行を合算）。アプリで1回に選べるのは最大5本。
- PDF予約額：抽出文字のUTF-8バイト数＋1ページ16,384トークン分＋指示/スキーマを入力規模として、入力のキャッシュ書込係数1.25と出力16,000を合わせ料金表×1.5。入力概算272,000トークン超は自動投入しない。本文は切り捨てない。
- 画像予約額：固定設定の出力余裕枠$1.50＋プロンプトのUTF-8バイト数を入力上限の近似として料金×1.5。これは実測料金でもプロバイダーによる保証上限でもない。5件評価で実測と比較する。見積もり不能時は投入しない。
- 料金表の基準日とモデル設定を生成版に保存し、送信前にSQLiteトランザクションで予約する。使用量取得後に計算額へ置き換える。usage不明、結果欠落、結果不明は予約を保持する。
- 請求期間を変えても未確定予約は持ち越す。他アプリの同時消費は把握できない。残額確認後の台帳内支出と未確定予約を差し引いて投入する。
- 明確な429/5xx失敗のみ自動再試行1回。品質改善は自動反復しない。結果が不明な送信はBatch一覧の要求ID/入力ファイルIDと照合し、見つからなければ確認待ち。
- APIリクエスト前の読取は最大3回。Notionへの追加も送信意図を先に保存し、再取得で見つかった内容を再利用する。

APIのusageを取得できなかった場合、請求画面で当該要求の実費を確認できた場合だけ手動精算する。費用を精算しても、処理結果の不明状態は勝手に解消しない。

```sh
.venv/bin/python scripts/process_papers.py settle ATTEMPT_ID --actual 0.25 --note '請求画面の当該要求を照合'
```

モデル設定/料金表は `scripts/paper_pipeline/batches.py` の `config()` / `RATES` に集約。旧 `OPENAI_MODEL` 等の環境変数は新フローでは使わない。料金改定時は設定を更新し、旧予約・旧処理版は当時の設定を維持する。

## Notionの保護と確認

APIバージョンは `2026-03-11`。database IDから単一data sourceを解決し、Automation ID・PMID・正規化DOIで既存ページを探す。複数候補なら追加せず確認待ち。

1論文1ページ内の `AI精読・未確認 [版ID]` 領域へ本文・根拠・JSON・PNGをまとめる。既存ページの確認チェック、カバー、臨床プロパティ、画像、メモを上書きしない（紐付け用Automation IDのみ補完）。新規ページだけ未確認状態とカバーを設定する。

途中保存領域の本文が利用者に編集されている場合、元の領域を残して「保存版2」等を追加し、同じJSON・PNGを再利用する。再解析課金は発生しない。Notionのページ単位の `Human Checked` は以前の版の確認を指すことがあるため、新版の確認状態は版内の注記と分けて扱う。確認後の版内注記の更新は利用者がNotionで行う。

直接アップロードの容量はワークスペースの制限を取得し、不明なら5MiB、初期版の単一ファイル転送は最大20MiB。超過時はNotion待ち。自動縮小・公開URLへの切り替えはしない。新自動化はGit commit/push/Pages公開処理を呼ばない。

ページ・領域・本文・添付の送信結果が不明で、再取得でも見つからない場合は自動追加を止める。まずNotionと台帳の版IDを照合する。台帳の意図フラグを無闇に消すと重複しうるため、原因確認が必要。

## 移行・互換・復元

初回 `prepare` または `migrate` で旧ジョブ、生成JSON、Notionページ対応表、Batch manifest、画像をコピーして台帳へ登録する。元データは変更しない。移行済み論文は `legacy_review` とし、旧版を新仕様の処理済みとは判定しない。旧Batchは `resume` で結果を `legacy/results/` へ回収する。旧版の内容・費用を確認してから `activate` または `regenerate` で新版を開始する。旧記録の精読結果を新方式へ自動再投入しない。

`PubMedGraRec.command` の手動JSON・画像登録は互換機能として維持する。旧手動登録にはプロパティ更新・公開操作があるので、新自動化の確認済み情報保護を使う場合は `PubMedGraRecAuto.command` を利用する。

バックアップはSQLiteのbackup APIで整合したDBと管理配下の生成物を新規フォルダへコピーする。復元手順：

1. 全ての実行を終了する。
2. バックアップフォルダ全体を別のローカルフォルダへコピーする（現台帳を上書きしない）。
3. `.env` の `PAPER_PIPELINE_HOME` をそのコピー先に変更するか、CLIに `--state-dir /path/restored` を付ける。
4. `status` で論文・費用・未完了要求を確認し、`resume` で照合してから再開する。

古いバックアップを復元した場合、バックアップ取得後に送信した要求・Notion追加が未記録の可能性がある。旧台帳とAPI履歴の照合前に新規投入しない。同じ台帳を同時使用する二重起動はロックで拒否する。

## 検証と初回5件評価

ローカル回帰テスト：

```sh
.venv/bin/python -m unittest discover -s tests -v
```

テストは架空PMID、生成した空白PDF、模擬APIで実行する。実アカウントの残額確認・有料呼び出し・Notion更新を伴わない。

2026-11-07実装時：既存16件＋新規37件、合計53件成功。Python構文、zsh起動スクリプト、CLIヘルプも確認。Notion登録途中の応答喪失、旧ページ対応表・Batch IDの移行、画像だけの再生成、復元後SQLite整合性を含む。

5件評価には短い原著、表の多い原著、レビュー、ガイドライン、手元PDFを含める。各件で記録するもの：原本ハッシュ、版ID、取り違え、主要数値/群/単位/結論、PNG日本語・数値、初回採用可否、再生成回数、Batch実費、Notionの重複・既存情報、再開結果。

合格基準：全件で取り違えなし、数値等を原著と照合可能、重大な画像誤記なし、初回画像4/5以上を採用、残りも1回以内の再生成で採用、Notionの重複/消失/確認済み変更なし、中断後の完了工程再課金なし、5件$20以内かつ20件/月$80以内。未達なら通常運用へ移行しない。合格後だけ予算設定の「通常運用」を選ぶ。

## 参照仕様（2026-11-07確認）

- [OpenAI Batch](https://developers.openai.com/api/docs/guides/batch)
- [OpenAI画像生成](https://developers.openai.com/api/docs/guides/image-generation)
- [OpenAI料金](https://developers.openai.com/api/docs/pricing)
- [ChatGPT APIクレジット](https://developers.openai.com/api/docs/guides/chatgpt-api-credits)
- [PMC Cloud Service](https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/)
- [Notion data sources移行](https://developers.notion.com/guides/get-started/upgrade-guide-2025-09-03)
- [Notionファイル](https://developers.notion.com/guides/data-apis/working-with-files-and-media)

`pubmed.py` の書誌取得は同じ管理者の `pubmed-pdf-fetcher` から取り込み、探索・照合は新台帳向けに実装した。このリポジトリ単体で動作し、他2リポジトリの実行・import・変更は不要。

## 2026-11-08：Instruction v2と4件の通常API試験

実装検証：57件のローカルテスト成功。通常API2工程、標準料金精算、対象上限、応答不明時の再送防止、旧指示の保持を模擬APIで確認。実アカウントによる4件の生成は対象指定・残額確認後に行う。

新規生成は `prompts/grarec-v2.md` を使用する。提供されたv2.0の視覚規則（色鉛筆/水彩風、イラスト約60%、主役のイラスト、可変構図）に、旧版の入力制約・PICO・主要結果・Safety・Practice Change・医学的正確性を統合した。旧版の配色・固定的レイアウトは重ねて指示しない。既存処理版に保存されたプロンプトは保持するため、指示変更だけで旧画像を再生成しない。

以下は改修前の試験記録。現行の `direct` はResponses API → Web用ファイル準備が既定で、Images APIには明示的な設定と許可が必要。完了工程は再利用し、曖昧な失敗は予約を保持して停止する。標準料金を使用し、初回$20・1版$4・残額確認24時間の制限は維持する。予約額は保証上限ではない。下記のPMID・パス・期間・残額は自分の値へ置き換える。

1. `SetupPaperPipeline.command` を実行し、`.env` にAPIキー等を設定する。
2. 試験用の管理領域とPDF入力フォルダを作る。PDF2件だけを `inbox` へコピーする（原本はそのまま）。

```sh
mkdir -p .paper_pipeline-pilot/inbox
```

3. PMID2件を指定し、取得・識別・見積もりだけを行う。過去の旧記録は取り込まない。

```sh
.venv/bin/python scripts/process_papers.py --state-dir .paper_pipeline-pilot prepare \
  --skip-legacy --pmid 12345678 --pmid 23456789 --pdf-dir .paper_pipeline-pilot/inbox
```

4. `review` で4件の対応を確認する。手元PDFを自動同定できなければ `identify` を使う。PMID・DOIのないPDFもタイトルを確認すれば内部IDで扱える。PDF待ちのPMIDは試験に進まず保持する。別経路の同じ論文は1件として扱う。

```sh
.venv/bin/python scripts/process_papers.py --state-dir .paper_pipeline-pilot review
.venv/bin/python scripts/process_papers.py --state-dir .paper_pipeline-pilot identify PDF内部ID --paper 対応PMID
# PubMed未収載の場合
.venv/bin/python scripts/process_papers.py --state-dir .paper_pipeline-pilot identify PDF内部ID --title '確認した論文タイトル'
```

5. 試験台帳の予算を設定する。既存運用や他アプリで消費した分を含め、請求画面の現在残額を確認する。下記日付・金額は例である。

```sh
.venv/bin/python scripts/process_papers.py --state-dir .paper_pipeline-pilot budget \
  --start 2026-11-08 --end 2026-12-08T00:00:00+09:00 --remaining 100 --other-usage 20 \
  --no-auto-recharge --purchased-checked
```

6. 特定した4論文を明示して通常APIで生成する。手元PDFの論文はPMIDでも内部IDでも指定可能。既存Batch履歴のある処理版は切り替えず停止する。

```sh
.venv/bin/python scripts/process_papers.py --state-dir .paper_pipeline-pilot direct \
  --paper 12345678 --paper 23456789 --paper PDF論文1の内部ID --paper PDF論文2の内部ID
```

7. `.paper_pipeline-pilot/artifacts/<版ID>/review.json` と `grarec.png` を確認する。初回はローカル保存のみ。採用できたら同じコマンドに `--notion` を付けて再実行する。完成したJSON・画像は再課金せず、Notion登録へ進む。

通常APIはBatch割引を適用しないため、同じトークン数では今回のモデル設定で料金が2倍になる。生成には数分以上かかる場合もある。タイムアウトを理由に自動再送しない。試験台帳は本体と別のため、本体台帳と費用を自動合算しない。通常運用へはこの台帳を `PAPER_PIPELINE_HOME` に指定して継続するか、試験結果の移行方針を決めてから移行する。4件を別の空台帳へ再投入すると再課金になりうる。

## 将来の週次／隔週起動（設計案・未登録）

Mac上の処理は `launchd` のログインユーザー用ジョブで起動できる。起動間隔は7日/14日の設定値にする。選んだPMIDの固定TXTを `pubmed_abstract` が出力し、本体が読み取る構成を第一候補とする。選択前の候補まで自動処理せず、出力元の書き戻しも行わない。出力先パスと、どの選択状態のPMIDを取り込むかは要確定。

探索の起動とBatch回収は分ける。探索は週次/隔週、送信済みBatchの回収・画像Batch投入は未完了がある間だけ定期的に実行し、Notion完了で止める。回収まで週次にすると、二段階の完了に数週間かかりうる。Macの電源・ログイン・ネットワーク・スリープに左右される。常時稼働が必要なら別の実行環境を検討する。

現状の「残額確認は24時間有効」という規則では、完全無人の毎週新規投入は止まる。週次起動前の残額確認を維持するか、残額を自動で確認できる手段／期限付きの専用予算運用を設計してから無人化する。今回この制限の緩和・OSジョブ登録・自動起動は行っていない。

### 2026-11-08 実ファイルでの事前確認

見出し `PMID` 付きTXTを受け付けるよう修正した（本文中の不正行は引き続き拒否）。出版社PDFで `10.` やDOI末尾の数字が改行されている場合に復元し、識別子とタイトルの両方で照合する。別論文・訂正文のDOIが先に出現してもタイトル不一致で紐付けない。CFF Type1フォントの抽出に必要なfontToolsを依存へ追加した。回帰テスト59件成功。

試験入力・ダウンロードPDF・試験台帳・ログはコミットしない。試験中の詳細は `.paper_pipeline-pilot/` に保存する。実例では取得・識別済みでも保守的な入力予約が長文閾値を超えるPDFがあり、現状は自動投入を停止する。予算・全文の扱いを確認せず、本文切り捨てや見積もり制限の緩和はしない。

クレジット確認先は [API Platform Billing](https://platform.openai.com/settings/organization/billing/overview)。Usageの支出額・予算表示を無料クレジット残額と見なさない。

### 通常API試験の全文トークン計数

通常API試験は、PDF・プロンプト・JSONスキーマを含む実際の入力をOpenAIの `POST /responses/input_tokens` で計数してから、最大出力16,000トークンと標準料金で費用を予約する。計数要求から生成専用の `max_output_tokens` と `store` を除外する。本文は省略しない。計数失敗時や272,000入力トークン超の場合は投入せず予算待ちとする。

これにより、ローカルのページ数ベース概算が長文閾値を超えても、実際の全文入力が閾値内なら通常API試験を進められる。例：53ページの試験PDFはAPI計数で134,837入力トークンだった。`prepare` のみではOpenAIへPDFを送信せず、従来の保守的な概算を表示する。

公式仕様：[トークン計数](https://developers.openai.com/api/docs/guides/token-counting)、[計数API](https://developers.openai.com/api/reference/resources/responses/subresources/input_tokens/methods/count)。追加回帰テストを含め61件成功。

### 試験画像が不合格の場合

PNGの形式・寸法が正しくても、内容がグラレコとして採用できるとは限らない。画像を目視確認し、無関係な画像等が出た場合は自動で再生成せず、応答と入力・usageを保管して調査する。品質保留の理由は試験予算の `suspended_reason` に保存でき、この設定中は全工程の新規費用予約と通常API試験を停止する。残額の記録自体は変更しない。処理途中で止めた版は `quality_wait` として保持する。

直接API要求も今後は `direct/<要求ID>_request.json` に送信前保存する（秘密鍵は含めない、Git管理外）。品質保留の解除・再生成は、原因と試験画像を確認した後に明示して行う。今回の実サービス試験の詳細・費用・採否は管理領域内の `RESULTS.md` と `pilot-report.json` に保存し、ソースのGitコミットには含めない。

### 画像APIの送受信記録

通常APIによる画像生成では、Requestsの `prepare_request()` で確定したJSONバイト列を先に保存し、同じPreparedRequestを `Session.send()` で一度だけ送信する。`X-Client-Request-Id` は要求ごとのUUIDとし、リダイレクトは無効。応答後は `response.request.body` と送信前本文をバイト単位で照合し、最終URLの変更・リダイレクト・本文不一致で停止する。

保存先は管理領域の `http_audit/<client-request-id>/`。要求本文・文字数・SHA-256・メソッド・送信先・Content-Type/Length、attempt/revision ID、応答本文・usage・generation ID・画像SHA-256、受信時刻、サーバーの `x-request-id` / `openai-processing-ms` / `openai-version` 等を保存する。存在しないヘッダーは推測せずnullとして記録する。ディレクトリは0700、ファイルは0600。認証・Cookieヘッダーは保存せず、応答ヘッダーは許可リスト方式。応答URLのuserinfo/queryも記録しない。

タイムアウトではClient Request IDと例外クラスを保存し、自動再送しない。監査保存先はGit管理外。これらはクライアント側の送信証拠であり、サーバー内部の処理まで証明するものではない。

2026-11-08：元の失敗attemptに対応する `_request.json` が残っていないことを再確認。送信前保存処理の追加前の事象だった。現在のコードに記録処理があることを、当時の送信証拠があることと混同しない。追加生成なしで67件のテスト成功（正しい本文・ID照合、秘密情報の非記録、ファイル権限、302停止、本文改変停止、タイムアウト非再送を含む）。

参照：[OpenAIの要求IDによるデバッグ](https://developers.openai.com/api/reference/overview#debugging-requests)。

2026-11-08：PMID 39678704が既存論文DB・既存ギャラリーの表示対象であることを確認。空のGraphic Image欄へ登録済みPNGを設定し、ギャラリーから画像ありで取得できることを確認した。今後の同期も空欄だけを設定するよう修正。既存画像保護・Human Checked保持・同期再実行時の重複防止を含む99テスト成功。


## 2026-11-12：端末間の状況共有とJSON保存完了通知

本人専用の[共通画面](https://pubmed-progress-thama.fairkayak3.chatgpt.site)を追加。Macアプリ稼働中に約1分ごとに件数・更新日時を共有し、API精読JSONの回収・保存完了を登録端末へ標準Web Pushで通知します。既存台帳を読み取り専用で集計し、初回は過去完了分を通知しません。通信障害時は送信待ち記録を保持します。

有料通知サービス・新規AI呼び出しは追加していません。MacのDocuments保護でLaunchAgent方式は実行できなかったため解除し、既存アプリ内の別スレッドで動作させます。アプリ終了・スリープ中は共有・送信が止まります。既存の回収・承認・Notion登録の手順は維持します。

Python 149テスト、サイト6テスト成功。画面の390px・1024px幅、実データ同期、Macアプリの同期済み表示を確認しました。iPhone・iPadの通知登録・実機到達は利用者側で確認が必要です。手順・停止・復旧は [STATUS_SHARING.md](STATUS_SHARING.md) を参照してください。
