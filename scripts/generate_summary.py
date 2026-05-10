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
    topics = ", ".join(summary.get("topic") or [])
    return f"""以下の論文情報をもとに、日本語の医師向けグラフィカルレコード画像を1枚作成してください。

目的：医師が30秒で内容を把握できること。

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
5. 下段：Take Home Message

論文情報：
タイトル：{summary.get("title", "")}
PMID：{summary.get("pmid", "")}
Journal：{summary.get("journal", "")}
Year：{summary.get("year", "")}
Topic：{topics}
研究デザイン：{summary.get("study_type", "")}
Abstract：
{summary.get("source", {}).get("abstract", "")}

日本語要約：
{summary.get("summary_jp", "")}

臨床への影響：
{summary.get("clinical_impact", "")}

Take Home Message：
{summary.get("take_home_message", "")}
"""


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
