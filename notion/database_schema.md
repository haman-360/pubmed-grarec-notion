# PubMed Watchlist - 論文DB

NotionでPubMed論文をグラレコ画像つきで管理するためのデータベース設計です。添付画像のようなギャラリー表示を前提にします。

## データベース概要

- データベース名: `PubMed Watchlist - 論文DB`
- 推奨アイコン: 本、論文、またはライブラリ系アイコン
- 説明文:
  - `PubMedウォッチリストから追加された論文を管理するリッチデータベース。AI下読み・ポリープスクリーニング・トップジャーナルAIの3トピックを横断管理。`

## プロパティ

| プロパティ名 | 型 | 必須 | 用途 |
|---|---|---:|---|
| Title | Title | yes | 論文タイトル |
| PMID | Text | yes | PubMed ID |
| PubMed URL | URL | yes | PubMedページ |
| DOI | Text | no | DOI |
| Journal | Select | yes | 雑誌名 |
| Year | Number | yes | 出版年 |
| Published Date | Date | no | 出版日 |
| Topic | Multi-select | yes | 領域・テーマ |
| Subtopic | Multi-select | no | 疾患、手技、薬剤など |
| Study Type | Select | no | RCT, Review, Guideline, Meta-analysisなど |
| Read Status | Status | yes | 未読、要確認、完了 |
| Impact | Select | no | High, Medium, Low |
| Practice Change | Select | no | Yes, No, Unclear |
| Why Important | Text | no | なぜ重要か |
| Clinical Impact | Text | no | 臨床への影響 |
| Summary JP | Text | no | 日本語要約 |
| Take Home Message | Text | no | 1行メッセージ |
| Graphic URL | URL | no | GitHub Pagesやraw画像URL |
| Graphic Image | Files & media | no | ギャラリーカード用画像 |
| PDF URL | URL | no | PDFまたは出版社ページ |
| NotebookLM | Checkbox | no | NotebookLM登録済み |
| Blog Candidate | Checkbox | no | ブログ化候補 |
| LINE Candidate | Checkbox | no | LINE配信候補 |
| Created By AI | Checkbox | no | AI処理済み |
| Human Checked | Checkbox | no | 医師確認済み |

## Select候補

### Read Status

- `未読`
- `要確認`
- `完了`
- `保留`

### Impact

- `High`
- `Medium`
- `Low`

### Practice Change

- `Yes`
- `No`
- `Unclear`

### Study Type

- `Original Article`
- `RCT`
- `Observational Study`
- `Cohort Study`
- `Case-control Study`
- `Systematic Review`
- `Meta-analysis`
- `Guideline`
- `Review`
- `Editorial`

### Topic

初期候補は運用に合わせて増やします。

- `AI/CADx`
- `拡大内視鏡`
- `EUS`
- `ポリープ`
- `大腸`
- `胃`
- `小児`
- `感染症`
- `アレルギー`
- `喘息`
- `ワクチン`
- `栄養`

## ギャラリービュー

ビュー名: `画像ギャラリー`

設定:

- Layout: Gallery
- Card preview: `Graphic Image` または `Page cover`
- Card size: MediumまたはLarge
- Fit image: on
- 表示プロパティ:
  - `Read Status`
  - `Topic`
  - `Journal`
  - `Year`
  - `Impact`
  - `Practice Change`

並び順:

1. `Year` 降順
2. `Published Date` 降順

フィルター例:

- `Human Checked` が unchecked
- `Read Status` が `完了` ではない

## 追加ビュー

### Default view

テーブル表示。全プロパティを確認・編集する管理用ビューです。

### 要確認

フィルター:

- `Read Status` が `要確認`
- または `Human Checked` が unchecked

### 高インパクト

フィルター:

- `Impact` が `High`

### 診療変更候補

フィルター:

- `Practice Change` が `Yes` または `Unclear`

### NotebookLM未登録

フィルター:

- `NotebookLM` が unchecked

## ページテンプレート

テンプレート名: `論文レビュー`

本文:

```markdown
## Take Home Message


## 日本語要約


## なぜ重要か


## 臨床への影響


## 原文確認メモ

- 対象:
- 介入/曝露:
- 比較:
- 主要アウトカム:
- 注意点:

## グラレコ確認

- 数値に誤りがない:
- 対象・介入・アウトカムに誤りがない:
- 結論が言い過ぎになっていない:
```

## カード画像の扱い

最も安定する運用は以下です。

1. グラレコ画像をGitHub repositoryの `images/YYYY/MM/PMID_xxxxxxxx.png` に保存する。
2. GitHub Pagesなどで公開URLを作る。
3. `Graphic URL` に公開URLを保存する。
4. Notion上で `Graphic Image` に同じURLを埋め込む、またはページカバーに設定する。
5. ギャラリービューのCard previewで `Graphic Image` または `Page cover` を選ぶ。

Notion APIで自動登録する場合は、ページカバーに外部URLを設定するとギャラリー表示が崩れにくくなります。
