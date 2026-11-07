from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EFETCH_BATCH_SIZE = 200


@dataclass
class ArticleMetadata:
    pmid: str
    title: str = ""
    journal: str = ""
    year: str = ""
    doi: str = ""
    pmcid: str = ""
    pii: str = ""
    publisher_url: str = ""
    volume: str = ""
    free_full_text_url: str = ""

    def as_csv_row(self) -> dict[str, str]:
        return {
            "PMID": self.pmid,
            "title": self.title,
            "journal": self.journal,
            "year": self.year,
            "DOI": self.doi,
            "PMCID": self.pmcid,
            "PII": self.pii,
            "publisher_url": self.publisher_url,
            "volume": self.volume,
            "free_full_text_url": self.free_full_text_url,
        }


class PubMedClient:
    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.api_key = os.getenv("NCBI_API_KEY", "")
        self.email = os.getenv("NCBI_EMAIL", "") or os.getenv("UNPAYWALL_EMAIL", "")
        retry = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def fetch_metadata(self, pmids: list[str]) -> list[ArticleMetadata]:
        if not pmids:
            return []

        records: list[ArticleMetadata] = []
        for start in range(0, len(pmids), EFETCH_BATCH_SIZE):
            batch = pmids[start : start + EFETCH_BATCH_SIZE]
            params = {
                "db": "pubmed",
                "id": ",".join(batch),
                "retmode": "xml",
                "tool": "pubmed_pdf_fetcher",
            }
            if self.email:
                params["email"] = self.email
            if self.api_key:
                params["api_key"] = self.api_key

            if len(batch) > 100:
                response = self.session.post(
                    f"{EUTILS_BASE}/efetch.fcgi", data=params, timeout=self.timeout
                )
            else:
                response = self.session.get(
                    f"{EUTILS_BASE}/efetch.fcgi", params=params, timeout=self.timeout
                )
            response.raise_for_status()

            root = ET.fromstring(response.content)
            batch_records = [self._parse_article(article) for article in root.findall(".//PubmedArticle")]
            free_links = self._fetch_free_full_text_links(batch)
            for record in batch_records:
                record.free_full_text_url = free_links.get(record.pmid, "")
            records.extend(batch_records)
            if start + EFETCH_BATCH_SIZE < len(pmids):
                time.sleep(0.34)
        found = {record.pmid for record in records}

        for pmid in pmids:
            if pmid not in found:
                records.append(ArticleMetadata(pmid=pmid))

        order = {pmid: index for index, pmid in enumerate(pmids)}
        return sorted(records, key=lambda record: order.get(record.pmid, len(order)))

    def _fetch_free_full_text_links(self, pmids: list[str]) -> dict[str, str]:
        params = {
            "dbfrom": "pubmed",
            "id": ",".join(pmids),
            "cmd": "llinks",
            "retmode": "xml",
            "tool": "pubmed_pdf_fetcher",
        }
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key

        try:
            response = self.session.get(f"{EUTILS_BASE}/elink.fcgi", params=params, timeout=self.timeout)
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except (requests.RequestException, ET.ParseError):
            return {}

        links: dict[str, str] = {}
        for id_url_set in root.findall(".//IdUrlSet"):
            pmid = text(id_url_set.find("Id"))
            for obj_url in id_url_set.findall("ObjUrl"):
                category = text(obj_url.find("Category")).lower()
                attributes = {
                    text(attribute).lower() for attribute in obj_url.findall("Attribute")
                }
                url = text(obj_url.find("Url"))
                if category == "full text sources" and "free resource" in attributes and url:
                    links[pmid] = url
                    break
        return links

    def _parse_article(self, article: ET.Element) -> ArticleMetadata:
        pmid = text(article.find(".//MedlineCitation/PMID"))
        title = flatten_text(article.find(".//ArticleTitle"))
        journal = text(article.find(".//Journal/Title")) or text(article.find(".//Journal/ISOAbbreviation"))
        year = extract_year(article)
        volume = text(article.find(".//Journal/JournalIssue/Volume"))
        doi = extract_article_id(article, "doi")
        pmcid = normalize_pmcid(extract_article_id(article, "pmc"))
        pii = extract_article_id(article, "pii")
        publisher_url = f"https://doi.org/{doi}" if doi else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

        return ArticleMetadata(
            pmid=pmid,
            title=title,
            journal=journal,
            year=year,
            doi=doi,
            pmcid=pmcid,
            pii=pii,
            publisher_url=publisher_url,
            volume=volume,
        )


def text(element: ET.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return element.text.strip()


def flatten_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def extract_article_id(article: ET.Element, id_type: str) -> str:
    for article_id in article.findall(".//PubmedData/ArticleIdList/ArticleId"):
        if article_id.attrib.get("IdType", "").lower() == id_type:
            return text(article_id)
    return ""


def normalize_pmcid(value: str) -> str:
    if not value:
        return ""
    return value if value.upper().startswith("PMC") else f"PMC{value}"


def extract_year(article: ET.Element) -> str:
    paths = [
        ".//Journal/JournalIssue/PubDate/Year",
        ".//ArticleDate/Year",
        ".//DateCompleted/Year",
        ".//DateRevised/Year",
    ]
    for path in paths:
        value = text(article.find(path))
        if value:
            return value

    medline_date = text(article.find(".//Journal/JournalIssue/PubDate/MedlineDate"))
    for token in medline_date.split():
        if token[:4].isdigit():
            return token[:4]
    return ""


def polite_pause() -> None:
    time.sleep(0.34)
