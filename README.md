# PubMed GraRec Notion — 手動・OneDrive版

PDF → JSON → PNG → 確認・Notionへの登録を、自分で1工程ずつ進めます。
**論文処理にAPIキーは使いません。自宅と職場のMacは、OneDrive上の同じファイルと進捗記録を使います。**

## 起動

`PubMed-grareko-notion.app` または `PubMedGraRecAuto.command` を開きます。`PubMedGraRec.command` も同じ画面を開きます。

カードと論文一覧の基本構成を維持し、区分は次の6つに変更しました。

- PDF取得待ち
- JSON作成待ち
- PNG作成待ち
- PNG確認・手動登録待ち
- 手動登録完了
- 要確認・競合

有料APIへの依頼、自動回収、予算設定、Notionへの自動送信は起動経路から外しました。`.env`は読みません。JSON/PDF/PNGの形式確認、ファイルコピー、依頼文準備、進捗記録はローカルで行います。

## 共通フォルダを設定する

各MacでOneDriveに同じアカウントでサインインし、同じ専用フォルダ `PubMed_GraRec` を同期します。
アプリの「このMacの設定」で、そのMac上の共通フォルダの絶対パスと端末名を指定します。
初回のみ空の専用フォルダを作成します。職場では、先に自宅で作成した `workspace.json` が同期されたフォルダを選んでください。

自宅Macの例:

```text
~/Library/CloudStorage/OneDrive-個人用/PubMed_GraRec
```

端末名の例: `自宅 MacBook Air`、`職場 Mac`。ローカル設定はGit管理外の `output/manual_settings.json` に保存し、他のMacへコピーしません。

既存データがあるMacでは「このMacの旧台帳からファイルをコピー」を1回実行します。旧台帳の最新選択中のPDF・JSON・テーマ別JSON・PNG・依頼文を実ファイルとしてコピーします。共有側に登録済みのPMIDは上書きしません。旧台帳・旧版・元ファイルは元の場所に保持します。

CLIで同じ設定を行う場合:

```sh
.venv/bin/python scripts/setup_manual_workspace.py \
  --root "$HOME/Library/CloudStorage/OneDrive-個人用/PubMed_GraRec" \
  --device-name "自宅 MacBook Air" --copy-legacy
```

職場では初回に `SetupPaperPipeline.command` で環境を準備し、アプリで同じ共有フォルダを選択します。Gitで渡すものはアプリのソース、OneDriveで渡すものは論文ファイルと進捗です。

## 普段の操作

1. PMIDを入力、またはPubMed Abstractの希望PMIDのTXTを選んで登録します。
2. 論文の「受け渡しフォルダ」を開き、本文PDFを `PDF/` へ保存します。取得・論文との対応確認は手動です。
3. 「JSON作成の依頼文をコピー」を押し、ChatGPT Webに貼り付け、一覧のPDFを添付します。完成した全文JSONを `JSON/master.json` に保存します。
4. アプリがJSONの形式・PMID・テーマと根拠参照を確認し、PNG作成待ちを記録します。「PNG作成の依頼文をコピー」でテーマを選んでWebへ渡します。
5. 完成PNGを `PNG/テーマID.png` に保存します。たとえば `main.png`。全テーマが揃うとPNG確認・手動登録待ちになります。
6. 原文・JSON・PNGの数値、対象、条件、限界を照合し、Notionなどへ手動でアップロードします。各論文の「確認・登録の記録」に内容・登録先URLを入力し、完了チェックを付けて記録します。
7. OneDriveアプリで同期完了を確認してから端末を切り替えます。職場では同じアプリを開き、続きの工程から作業します。

保存フォルダはアプリ起動時と約30秒ごとに読み取ります。「保存したファイルを確認・記録」でも確認できます。
処理の生成・外部への送信は行いません。保存済みの版は変更せず、差し替えたファイルは新しい版として保存します。PDFの差し替えでJSON以降、JSONの差し替えでPNG以降の現行選択と完了状態を解除し、前の版は履歴に保持します。

JSONは従来の `hybrid-v1` Master JSON形式です。自由形式のJSONや画像用JSONだけを新しい全文JSONとして取り込むことはできません。依頼文に必要なSchemaが含まれます。構文・対応の検証は医学的正確性の保証ではありません。

## 保存場所

```text
PubMed_GraRec/
  workspace.json
  papers/
    PMID_番号/
      受け渡し/
        PDF/                  入手したPDFを保存
        JSON/                 Webで作成したmaster.jsonを保存
        PNG/                  テーマID.pngを保存
      files/内容ハッシュ/     取り込み済みの実ファイル・依頼文（版ごとに保持）
      records/記録ID.json      工程・採用ファイル・端末・時刻・作業メモの履歴
  状況/
    端末ID/
      状況.txt                最終状況とファイルの場所
      状況.html               ローカルブラウザー向けの閲覧用一覧
      snapshot.json           同じ状況のデータ
```

`files/`・`records/`は編集せず、変更するファイルを`受け渡し/`へ保存します。PDFやJSONの新しい候補を同時に複数置いた場合は、自動で選ばず要確認にします。採用する候補1つを残し、他は共有フォルダ外の作業用フォルダへ移してください。取り込み済みの入力ファイルは自動削除しません。

共有側ではSQLiteもMac内のシンボリックリンクも使いません。各更新は固有名のファイルに追記します。同じPMIDを自宅と職場で並行更新すると、両方の版を残し「要確認・競合」に表示します。時刻だけで片方を上書きしません。両方のファイル・メモを確認し、採用する記録を明示して解決できます。

## iPhone・iPad、Macが停止中の場合

OneDriveの `papers/PMID_番号/files/` からPDF・JSON・PNG・依頼文を開き、Webでの作業に利用します。生成したファイルは同じ論文の `受け渡し/JSON` または `受け渡し/PNG` へ保存します。
進捗は `状況/端末ID/状況.txt` で確認できます。複数端末のファイルがある場合は本文の端末名・記録日時を確認します。HTMLのプレビューやリンク操作は端末・アプリに依存するため、iOSではTXTと実ファイルを基本にします。

**MacBook Airの常時起動は不要です。ただしMacを閉じる前にOneDriveへの同期が終わっている必要があります。**
自宅Macが停止中でも、職場Macのアプリが記録できます。両方のMacが停止中にiPhone/iPadで保存したファイルはOneDriveで共有されますが、工程記録は次にどちらかのMacでアプリを開いたときに更新します。

表示するのは「各Macが最後に記録した状態」であり、クラウド同期完了の保証ではありません。

### iPhoneで状況画面を見る

今までと同じ [PubMed 進捗](https://pubmed-progress-thama.fairkayak3.chatgpt.site) を開き、同じChatGPTアカウントでサインインします。現在のMacアプリと同じ6区分の件数・確認端末・最終確認時刻を表示します。

状況共有を設定したMacでアプリを開いている間、約30秒ごとに件数を共有し、iPhone側も約30秒ごとに読み直します。「いま更新する」でも再取得できます。ファイル保存から表示まで、通常は約1分が目安です。Mac終了・スリープ・通信切断中は最後に共有した状況を表示し、2分以上更新がなければ案内を出します。

共有するのは件数・端末名・確認時刻だけです。論文本文・PMID・ファイル・メモはサイトへ送りません。共有用の認証設定は各Mac内のGit管理外 `.remote_status/config.json` を使い、OneDriveへ置きません。このMacの既存設定を継続利用します。他のMacでも直接共有するには、そのMacにも専用の認証設定が必要です。未設定の場合はMac画面に「状況共有が未設定」と表示します。

JSONの自動回収・旧Push通知は再開しません。

Macで共有フォルダを「このデバイス上で常に保持」に設定すると、作業ファイルをオフラインでも使えます。[Microsoftの説明](https://support.microsoft.com/en-us/onedrive/save-disk-space-with-onedrive-files-on-demand-for-mac)
iOSの「ファイル」アプリにもOneDriveを追加できます。[Appleの説明](https://support.apple.com/en-us/102238)

## 過去の処理と検証

以前にAPIへ送信済みのジョブは、ローカルアプリの変更では取消されません。新アプリから問い合わせ・回収・取消は行いません。未確定記録がある論文にはその旨を表示し、新しい手動作業は別の共有記録として進めます。

旧API関連コードのうち、スキーマ・既存ファイル読取り・履歴の回帰テストに使う部品は残しています。旧コマンドの直接起動は停止メッセージで終了します。秘密情報は共有フォルダ・Gitへコピーしません。過去の説明は [AUTOMATION.md](docs/AUTOMATION.md) と [LEGACY_WORKFLOWS.md](docs/LEGACY_WORKFLOWS.md) に残っていますが、現行の操作はこのREADMEを優先します。

```sh
.venv/bin/python -m unittest discover -s tests
```

2026-11-12: 手動・OneDrive版へ変更。API送信のない操作、差し替え時の完了解除、別端末の同時更新・同期順序の逆転・未同期ファイル・古い画面からの更新拒否、旧台帳を変更しないコピーを含む163テストが成功しました。旧台帳53件をOneDriveへコピーし、Macアプリの表示・絞り込みを確認しました。職場Mac・iPhone・iPadの実機とOneDriveクラウド経由の同期確認は別途必要です。
