from __future__ import annotations

from dataclasses import asdict
import re
from typing import Any

from fetch_pubmed import PubMedArticle


def generate_summary(article: PubMedArticle, topic: list[str] | None = None) -> dict[str, Any]:
    topics = topic or infer_topics(article)
    return {
        "title": article.title,
        "pmid": article.pmid,
        "journal": article.journal,
        "year": article.year,
        "published_date": article.published_date,
        "doi": article.doi,
        "pubmed_url": article.pubmed_url,
        "topic": topics,
        "study_type": infer_study_type(article),
        "read_status": "要確認",
        "impact": "Medium",
        "practice_change": "Unclear",
        "why_important": "PubMedから取得したabstractをもとに、臨床的意義を医師が確認してください。",
        "clinical_impact": "現時点ではAIによる下読み段階です。診療変更の要否は原文確認後に判断してください。",
        "practice_change_reason": "自動判定は未実装です。Human Checked前に原文で確認してください。",
        "take_home_message": build_take_home_message(article),
        "summary_jp": build_placeholder_summary(article),
        "source": asdict(article),
    }


def generate_graphic_prompt(summary: dict[str, Any]) -> str:
    topics = ", ".join(_prompt_list(summary.get("topic")))
    study_type = str(summary.get("study_type") or summary.get("study_design") or "")
    layout_guidance = graphic_layout_guidance(study_type)
    return f"""以下の論文情報をもとに、日本語の医師向けグラフィカルレコード画像を1枚作成してください。

目的：医師が30秒で内容を把握できること。

デザイン条件：
- 用途: scientific-educational
- Asset type: Japanese medical graphical abstract
- 横長16:9、1600x900
- 白背景、tealを介入/主題、coralを比較/注意、navyを本文、light grayを区切り線に使う
- クリアな医学誌風インフォグラフィック。装飾よりも、密度があり読みやすい誌面構成を優先
- 日本語テキストは画像内に最初から正確に描画し、後から追加しない
- 文字はサムネイルでも読める大きさ。タイトルは大きく、本文は短く
- アイコン、矢印、簡潔な患者/研究/臓器/検査/モデルの図を使う
- 数字は提供されたものだけ使用し、推測で追加しない

構成：
1. 最上段：大きな日本語タイトル
2. タイトル下：短いサブタイトルまたは比較軸
3. 中央：研究デザインに合わせた主ビジュアル
4. 下段：3つの結果カード
5. 右下：短い結論バッジ
6. 小さく：PMID、Journal、Year

画像内に入れるテキスト枠：
- Title: Take Home Messageを日本語で短く
- Subtitle: Topicまたは比較軸を日本語で短く
- Left label: 対象・介入・曝露・入力のいずれか
- Left small text: N数または重要な条件。なければ「原文確認」
- Right label: 比較・参照基準・アウトカム・臨床的意味のいずれか
- Right small text: 主要指標。なければ「数値は原文確認」
- Bottom card 1: 主要結果
- Bottom card 2: 臨床への影響
- Bottom card 3: 注意点・限界
- Conclusion badge: Take Home Messageをさらに短く

研究デザイン別の中央ビジュアル：
{layout_guidance}

制約：
- 余計な英単語、ランダム文字、透かし、ロゴを入れない
- 日本語の誤字・文字化けを避ける
- 略語はTXI、WLI、AI、CT、MRIなど不可避なものだけ英字可
- 結論を原文より強くしない
- レイアウトは装飾的ではなく、医学誌・学会抄録風にする

論文情報：
タイトル：{summary.get("title", "")}
PMID：{summary.get("pmid", "")}
Journal：{summary.get("journal", "")}
Year：{summary.get("year", "")}
Topic：{topics}
研究デザイン：{study_type}
Abstract：
{_source_abstract(summary)}

日本語要約：
{summary.get("summary_jp", "")}

臨床への影響：
{summary.get("clinical_impact", "")}

Take Home Message：
{summary.get("take_home_message") or summary.get("one_line_summary", "")}
"""


def graphic_layout_guidance(study_type: str) -> str:
    normalized = study_type.lower()
    if "random" in normalized or normalized == "rct":
        return (
            "- RCT: 左に多施設ランダム化試験と患者数、中央に介入群と比較群の並列パネル、"
            "下段に数値アウトカムカード、右下に結論バッジ"
        )
    if "総説" in study_type or "専門家" in study_type or "expert" in normalized:
        return (
            "- Review: 背景、病態/機序、臨床的意味、今後の課題を4ブロックで整理"
        )
    if "systematic" in normalized or "meta" in normalized:
        return (
            "- Systematic review/meta-analysis: 検索フロー、採用研究数、pooled result風カード、"
            "異質性/確実性カードを配置"
        )
    if "diagnostic" in normalized or "ai" in normalized or "model" in normalized:
        return (
            "- Diagnostic/AI model: 入力データ、index test/model、参照基準、外部検証、"
            "性能指標を左から右へ流す"
        )
    if "observ" in normalized or "cohort" in normalized or "case-control" in normalized:
        return (
            "- Observational study: コホート/曝露/比較/アウトカムのタイムラインと、"
            "調整済み結果カードを配置"
        )
    if "review" in normalized:
        return (
            "- Review: 背景、病態/機序、臨床的意味、今後の課題を4ブロックで整理"
        )
    return (
        "- Original/non-RCT study: 対象集団、方法/介入または曝露、主要アウトカム、"
        "臨床的意味を流れ図として整理"
    )


def _prompt_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def _source_abstract(summary: dict[str, Any]) -> str:
    source = summary.get("source")
    if isinstance(source, dict):
        return str(source.get("abstract") or "")
    return ""


def infer_topics(article: PubMedArticle) -> list[str]:
    text = f"{article.title} {article.abstract}".lower()
    topics = []
    if any(term in text for term in ["podocyte", "slit diaphragm", "nephrotic", "glomer"]):
        topics.extend(["腎臓", "糸球体"])
    if any(re.search(pattern, text) for pattern in [r"\bai\b", r"\bartificial intelligence\b", r"\bcadx\b", r"\bdeep learning\b"]):
        topics.append("AI/CADx")
    if any(term in text for term in ["children", "pediatric", "paediatric"]):
        topics.append("小児")
    return topics or ["未分類"]


def infer_study_type(article: PubMedArticle) -> str:
    text = f"{article.title} {article.abstract}".lower()
    if "systematic review" in text or "meta-analysis" in text:
        return "Systematic Review"
    if "randomized" in text or "randomised" in text:
        return "RCT"
    if "review" in text:
        return "Review"
    return "Original Article"


def build_take_home_message(article: PubMedArticle) -> str:
    text = f"{article.title} {article.abstract}".lower()
    if "anti-nephrin" in text and "podocyte" in text:
        return "抗ネフリン抗体は、後天性ポドサイト疾患の診断・予後・治療選択に影響しうる注目標的として整理されている。"
    if article.abstract:
        first_sentence = article.abstract.split(". ")[0].strip()
        if first_sentence:
            return f"要確認: {_trim_sentence(first_sentence, 180)}"
    return "要確認: abstractまたは本文を確認してTake Home Messageを記載してください。"


def build_placeholder_summary(article: PubMedArticle) -> str:
    text = f"{article.title} {article.abstract}".lower()
    if "anti-nephrin" in text and "podocyte" in text:
        return (
            "AI下読み用の暫定要約です。\n"
            "- この総説は、ポドサイトのスリット膜構造と抗ネフリン抗体に関する新しい知見を整理している。\n"
            "- 対象となる疾患として、小児のステロイド感受性ネフローゼ症候群、微小変化型ネフローゼ症候群、一部の原発性FSGSが挙げられている。\n"
            "- スリット膜はネフリン、Neph1、関連タンパクからなる多層の構造として理解されつつあり、抗ネフリン抗体がその構造とポドサイト骨格を障害し、足突起消失や蛋白尿につながる可能性が示されている。\n"
            "- 今後の課題として、標的エピトープ、ネフリン内在化の機序、広く使える抗ネフリン抗体測定系の確立が挙げられる。\n"
            "- 注意: 診断や治療方針への反映は、検査法の妥当性と臨床研究の位置づけを原文で確認してから判断してください。"
        )
    abstract = article.abstract.strip()
    if not abstract:
        return "PubMed abstractを取得できませんでした。PDFまたは出版社ページから本文を確認してください。"
    return (
        "AI下読み用の暫定要約です。\n"
        f"- 論文: {article.title}\n"
        f"- 雑誌/年: {article.journal} / {article.year or ''}\n"
        f"- Abstract要旨: {abstract[:900]}{'...' if len(abstract) > 900 else ''}\n"
        "- 注意: 研究対象、主要評価項目、数値、結論は原文で確認してください。"
    )


def _trim_sentence(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    trimmed = value[:limit].rsplit(" ", 1)[0]
    return f"{trimmed}..."
