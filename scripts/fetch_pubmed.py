from __future__ import annotations

import html
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional


NCBI_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


@dataclass
class PubMedArticle:
    pmid: str
    title: str
    abstract: str
    journal: str
    year: Optional[int]
    published_date: Optional[str]
    doi: str
    pubmed_url: str


def fetch_pubmed_article(pmid: str, email: str | None = None, tool: str = "pubmed-grarec-notion") -> PubMedArticle:
    params = {
        "db": "pubmed",
        "id": pmid,
        "retmode": "xml",
        "tool": tool,
    }
    if email:
        params["email"] = email

    url = f"{NCBI_EFETCH_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = response.read()

    return parse_pubmed_xml(payload, pmid)


def parse_pubmed_xml(payload: bytes, requested_pmid: str) -> PubMedArticle:
    root = ET.fromstring(payload)
    article_node = root.find(".//PubmedArticle")
    if article_node is None:
        raise ValueError(f"PubMed article not found for PMID {requested_pmid}")

    pmid = _text(article_node.find(".//MedlineCitation/PMID")) or requested_pmid
    article = article_node.find(".//MedlineCitation/Article")
    if article is None:
        raise ValueError(f"Article metadata not found for PMID {requested_pmid}")

    title = _join_text(article.find("ArticleTitle"))
    abstract_parts = []
    for node in article.findall(".//Abstract/AbstractText"):
        label = node.attrib.get("Label")
        text = _join_text(node)
        if not text:
            continue
        abstract_parts.append(f"{label}: {text}" if label else text)
    abstract = "\n".join(abstract_parts)

    journal = _text(article.find(".//Journal/Title")) or _text(article.find(".//Journal/ISOAbbreviation"))
    published_date = _published_date(article)
    year = _year_from_date(published_date) or _article_year(article)
    doi = _doi(article_node)

    return PubMedArticle(
        pmid=pmid,
        title=html.unescape(title),
        abstract=html.unescape(abstract),
        journal=journal,
        year=year,
        published_date=published_date,
        doi=doi,
        pubmed_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    )


def _text(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _join_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def _published_date(article: ET.Element) -> Optional[str]:
    date_node = article.find(".//Journal/JournalIssue/PubDate")
    if date_node is None:
        date_node = article.find(".//ArticleDate")
    if date_node is None:
        return None

    year = _text(date_node.find("Year"))
    month = _normalize_month(_text(date_node.find("Month")))
    day = _text(date_node.find("Day")) or "01"
    if not year:
        return None
    if not month:
        month = "01"
    return f"{year}-{month}-{day.zfill(2)}"


def _normalize_month(value: str) -> str:
    if not value:
        return ""
    if value.isdigit():
        return value.zfill(2)
    months = {
        "jan": "01",
        "feb": "02",
        "mar": "03",
        "apr": "04",
        "may": "05",
        "jun": "06",
        "jul": "07",
        "aug": "08",
        "sep": "09",
        "oct": "10",
        "nov": "11",
        "dec": "12",
    }
    return months.get(value[:3].lower(), "")


def _year_from_date(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return int(value[:4])
    except ValueError:
        return None


def _article_year(article: ET.Element) -> Optional[int]:
    for path in [".//ArticleDate/Year", ".//Journal/JournalIssue/PubDate/Year"]:
        value = _text(article.find(path))
        if value.isdigit():
            return int(value)
    return None


def _doi(article_node: ET.Element) -> str:
    for node in article_node.findall(".//ArticleIdList/ArticleId"):
        if node.attrib.get("IdType") == "doi":
            return _text(node)
    for node in article_node.findall(".//ELocationID"):
        if node.attrib.get("EIdType") == "doi":
            return _text(node)
    return ""
