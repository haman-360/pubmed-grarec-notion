# PubMed論文DB 運用フロー

## 目的

PubMedで見つけた論文を、Notionの `PubMed Watchlist - 論文DB` に1論文1ページで登録し、グラレコ画像つきで一覧できるようにします。

## 最初の手動MVP

完全自動化の前に、まず以下の流れで運用します。

1. PubMedで気になる論文を見つける。
2. PMIDを控える。
3. 論文タイトル、Journal、Year、Abstractを確認する。
4. AIで日本語要約、臨床への影響、Take Home Message、グラレコ用プロンプトを作る。
5. グラレコ画像を作る。
6. 画像を `images/YYYY/MM/PMID_xxxxxxxx.png` に保存する。
7. 画像を公開URL化する。
8. Notion DBにページを作る。
9. 画像URLを `Graphic URL` とページカバー、または `Graphic Image` に登録する。
10. 医師が原文と画像を確認し、`Human Checked` をオンにする。

## Notion登録時の最小項目

最初は以下だけ入ればギャラリー運用できます。

- `Title`
- `PMID`
- `PubMed URL`
- `Journal`
- `Year`
- `Topic`
- `Read Status`
- `Impact`
- `Practice Change`
- `Summary JP`
- `Take Home Message`
- `Graphic URL`
- `Graphic Image` またはページカバー

## ステータス運用

- `未読`: 追加しただけで、まだ本文確認していない
- `要確認`: AI要約や画像はあるが、医師確認が必要
- `完了`: 原文確認と画像確認が完了
- `保留`: 関連性が低い、または判断待ち

## AI生成物の確認ポイント

AI要約とグラレコ画像は、以下を必ず確認します。

- 対象患者・対象集団
- 研究デザイン
- 介入または曝露
- 比較対象
- 主要アウトカム
- N数、割合、p値、信頼区間などの数値
- 結論が原文より強くなっていないか
- 診療変更の必要性が過大評価されていないか

## グラレコの推奨構成

- 横長16:9
- 白背景
- 青、緑、グレーを基調
- 3から4カラム
- 左: 対象・研究デザイン
- 中央: 介入/曝露、比較、評価項目
- 右: 主要結果
- 下段: Take Home Message

## 自動化する順番

1. PMIDからPubMed情報を取得
2. 日本語要約とグラレコ用プロンプトを生成
3. 手動指定した画像URLをNotionへ登録
4. 画像ファイルをGitHub Pagesで公開
5. ページカバー画像をNotion APIで設定
6. 画像生成も自動化

最初は3まで動けば、Notionで添付画像のような論文ギャラリーとして使えます。
