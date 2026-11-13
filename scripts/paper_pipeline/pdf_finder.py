"""PDF discovery reused from haman-360/pubmed-pdf-fetcher at 92831f7.

Kept in this repository so a second checkout is not required on another Mac.
Uses the already shared ArticleMetadata model in .pubmed; no AI APIs.
"""
from __future__ import annotations

import os
from html.parser import HTMLParser
from dataclasses import dataclass
from pathlib import Path
import tarfile
from urllib.parse import quote, urljoin
import xml.etree.ElementTree as ET

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .pubmed import ArticleMetadata


PDF_CONTENT_TYPES = {
    "application/pdf",
    "application/x-pdf",
    "application/octet-stream",
}


@dataclass
class PdfResult:
    success: bool
    source: str = ""
    url: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ManualCheckCandidate:
    url: str
    priority: str
    status: str
    evidence: str


class PdfFinder:
    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "pubmed-pdf-downloader/0.1 (mailto:local-user@example.com)",
                "Accept": "application/pdf,application/json,text/html;q=0.8,*/*;q=0.5",
            }
        )
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.unpaywall_email = os.getenv("UNPAYWALL_EMAIL", "")

    def download_pdf(self, metadata: ArticleMetadata, destination: Path) -> PdfResult:
        reasons: list[str] = []

        if metadata.pmcid:
            result = self._try_pmc(metadata.pmcid, destination)
            if result.success:
                return result
            if result.reason:
                reasons.append(f"PMC: {result.reason}")

        if metadata.doi:
            result = self._try_unpaywall(metadata.doi, destination)
            if result.success:
                return result
            if result.reason:
                reasons.append(f"Unpaywall: {result.reason}")

        result = self._try_europe_pmc(metadata, destination)
        if result.success:
            return result
        if result.reason:
            reasons.append(f"Europe PMC: {result.reason}")

        if metadata.doi or metadata.pii:
            result = self._try_publisher_pdf_candidates(metadata, destination)
            if result.success:
                return result
            if result.reason:
                reasons.append(f"Publisher: {result.reason}")

        if not metadata.pmcid and not metadata.doi:
            return PdfResult(success=False, reason="No PMCID or DOI available")
        return PdfResult(success=False, reason="; ".join(reasons) or "No legally available OA PDF found")

    def _try_pmc(self, pmcid: str, destination: Path) -> PdfResult:
        oa_pdf_url, oa_package_url = self._pmc_oa_urls(pmcid)
        if oa_pdf_url:
            result = self._download_if_pdf(oa_pdf_url, destination, "PMC")
            if result.success:
                return result

        if oa_package_url:
            result = self._download_pmc_oa_package(oa_package_url, destination)
            if result.success:
                return result

        article_pdf_url = self._pmc_article_page_pdf_url(pmcid)
        if article_pdf_url:
            result = self._download_if_pdf(article_pdf_url, destination, "PMC")
            if result.success:
                return result

        candidates = [
            f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/",
            f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/",
            f"https://europepmc.org/backend/ptpmcrender.fcgi?accid={pmcid}&blobtype=pdf",
        ]

        last_reason = ""
        for url in candidates:
            result = self._download_if_pdf(url, destination, "PMC")
            if result.success:
                return result
            last_reason = result.reason

        return PdfResult(success=False, source="PMC", reason=last_reason or "PMC PDF not found")

    def _pmc_oa_urls(self, pmcid: str) -> tuple[str, str]:
        url = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"
        try:
            response = self.session.get(url, params={"id": pmcid}, timeout=self.timeout)
            if response.status_code == 404:
                return "", ""
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except (requests.RequestException, ET.ParseError):
            return "", ""

        pdf_url = ""
        package_url = ""
        for link in root.findall(".//link"):
            link_format = link.attrib.get("format", "").lower()
            href = link.attrib.get("href", "")
            if link_format == "pdf" and href:
                pdf_url = href
            elif link_format in {"tgz", "tar.gz"} and href:
                package_url = https_url_for_ftp(href)
        return pdf_url, package_url

    def _pmc_oa_pdf_url(self, pmcid: str) -> str:
        """Return the direct PDF URL when the OA API supplies one."""
        pdf_url, _ = self._pmc_oa_urls(pmcid)
        return pdf_url

    def _download_pmc_oa_package(self, url: str, destination: Path) -> PdfResult:
        archive_path = destination.with_suffix(destination.suffix + ".oa-package.part")
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            archive_path.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
            with self.session.get(url, timeout=self.timeout, stream=True, allow_redirects=True) as response:
                if response.status_code in {401, 403}:
                    return PdfResult(False, source="PMC OA package", url=url, reason="OA package requires authentication")
                if response.status_code == 404:
                    return PdfResult(False, source="PMC OA package", url=url, reason="OA package not found")
                response.raise_for_status()
                with archive_path.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            handle.write(chunk)

            with tarfile.open(archive_path, mode="r:gz") as archive:
                pdf_members = [
                    member
                    for member in archive.getmembers()
                    if member.isfile() and member.name.lower().endswith(".pdf")
                ]
                if not pdf_members:
                    return PdfResult(False, source="PMC OA package", url=url, reason="No PDF in OA package")

                member = max(pdf_members, key=lambda candidate: candidate.size)
                extracted = archive.extractfile(member)
                if extracted is None:
                    return PdfResult(False, source="PMC OA package", url=url, reason="Could not read PDF in OA package")
                with extracted, temporary.open("wb") as handle:
                    first_chunk = extracted.read(8192)
                    if not first_chunk.startswith(b"%PDF-"):
                        return PdfResult(False, source="PMC OA package", url=url, reason="OA package entry is not a PDF")
                    handle.write(first_chunk)
                    while chunk := extracted.read(8192):
                        handle.write(chunk)

            temporary.replace(destination)
            return PdfResult(True, source="PMC OA package", url=url)
        except (requests.RequestException, tarfile.TarError) as exc:
            return PdfResult(False, source="PMC OA package", url=url, reason=f"OA package download failed: {exc}")
        except OSError as exc:
            return PdfResult(False, source="PMC OA package", url=url, reason=f"Could not save PDF: {exc}")
        finally:
            archive_path.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)

    def _pmc_article_page_pdf_url(self, pmcid: str) -> str:
        url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException:
            return ""

        parser = PmcPdfLinkParser(url)
        parser.feed(response.text)
        return parser.pdf_url

    def _try_unpaywall(self, doi: str, destination: Path) -> PdfResult:
        if not self.unpaywall_email:
            return PdfResult(success=False, source="Unpaywall", reason="UNPAYWALL_EMAIL is not set")

        url = f"https://api.unpaywall.org/v2/{quote(doi, safe='')}"
        try:
            response = self.session.get(
                url,
                params={"email": self.unpaywall_email},
                timeout=self.timeout,
            )
            if response.status_code == 404:
                return PdfResult(success=False, source="Unpaywall", reason="DOI not found in Unpaywall")
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            return PdfResult(success=False, source="Unpaywall", reason=f"API request failed: {exc}")

        pdf_urls = self._unpaywall_pdf_urls(data)
        if not pdf_urls:
            return PdfResult(success=False, source="Unpaywall", reason="No OA PDF URL in Unpaywall")

        last_result = PdfResult(success=False, source="Unpaywall", reason="No downloadable OA PDF found")
        for pdf_url in pdf_urls:
            last_result = self._download_if_pdf(pdf_url, destination, "Unpaywall")
            if last_result.success:
                return last_result
        return last_result

    def _try_publisher_pdf_candidates(self, metadata: ArticleMetadata, destination: Path) -> PdfResult:
        candidates = self._publisher_pdf_candidates(metadata)
        last_reason = ""
        for url in candidates:
            result = self._download_if_pdf(url, destination, "Publisher")
            if result.success:
                return result
            last_reason = result.reason

        landing_pdf_url = self._publisher_landing_pdf_url(metadata.publisher_url)
        if landing_pdf_url and landing_pdf_url not in candidates:
            result = self._download_if_pdf(landing_pdf_url, destination, "Publisher metadata")
            if result.success:
                return result
            last_reason = result.reason

        return PdfResult(
            success=False,
            source="Publisher",
            reason=last_reason or "No public PDF link found on the publisher page",
        )

    def _publisher_pdf_candidates(self, metadata: ArticleMetadata) -> list[str]:
        normalized = metadata.doi.strip()
        lower = normalized.lower()
        encoded = quote(normalized, safe="/")
        pii = normalize_pii(metadata.pii)
        candidates: list[str] = []

        if lower.startswith("10.1111/"):
            candidates.append(f"https://onlinelibrary.wiley.com/doi/epdf/{encoded}")

        if lower.startswith("10.1024/"):
            candidates.append(f"https://econtent.hogrefe.com/doi/pdf/{encoded}?download=true")

        if lower.startswith("10.1136/bmj-") and metadata.volume:
            article_id = quote(normalized.split("/", 1)[1], safe="-")
            volume = quote(metadata.volume, safe="")
            candidates.append(f"https://www.bmj.com/content/bmj/{volume}/{article_id}.full.pdf")

        if lower.startswith("10.1053/") and pii:
            compact_pii = compact_pii_for_url(pii)
            candidates.extend(
                [
                    f"https://www.gastrojournal.org/article/{pii}/pdf",
                    f"https://www.sciencedirect.com/science/article/pii/{compact_pii}/pdfft",
                    f"https://www.sciencedirect.com/science/article/pii/{compact_pii}/pdf",
                ]
            )

        return candidates

    def manual_pdf_candidates(self, doi: str) -> list[str]:
        return self._publisher_pdf_candidates(ArticleMetadata(pmid="", doi=doi))

    def manual_pdf_candidates_for_article(self, metadata: ArticleMetadata) -> list[str]:
        return [candidate.url for candidate in self.manual_check_candidates_for_article(metadata)]

    def manual_check_candidates_for_article(
        self, metadata: ArticleMetadata
    ) -> list[ManualCheckCandidate]:
        candidates: list[ManualCheckCandidate] = []
        free_evidence = "PubMed LinkOutで無料全文（free resource）と確認"

        for url in self._publisher_pdf_candidates(metadata):
            if metadata.free_full_text_url:
                candidates.append(
                    ManualCheckCandidate(
                        url=url,
                        priority="1_high",
                        status="手動ダウンロードできる可能性が高い",
                        evidence=f"{free_evidence}。出版社PDF直リンク候補あり",
                    )
                )
            else:
                candidates.append(
                    ManualCheckCandidate(
                        url=url,
                        priority="2_medium",
                        status="ブラウザで手動確認を推奨",
                        evidence="出版社の既知PDF直リンク候補あり",
                    )
                )

        if metadata.pmcid:
            candidates.append(
                ManualCheckCandidate(
                    url=f"https://pmc.ncbi.nlm.nih.gov/articles/{metadata.pmcid}/",
                    priority="1_high",
                    status="手動ダウンロードできる可能性が高い",
                    evidence="PMCIDがあり、PMCで全文公開",
                )
            )

        if metadata.free_full_text_url:
            candidates.append(
                ManualCheckCandidate(
                    url=metadata.free_full_text_url,
                    priority="1_high",
                    status="手動ダウンロードできる可能性が高い",
                    evidence=free_evidence,
                )
            )

        if metadata.publisher_url:
            candidates.append(
                ManualCheckCandidate(
                    url=metadata.publisher_url,
                    priority="3_low" if not metadata.free_full_text_url else "1_high",
                    status=(
                        "手動ダウンロードできる可能性が高い"
                        if metadata.free_full_text_url
                        else "出版社ページで無料公開か確認"
                    ),
                    evidence=free_evidence if metadata.free_full_text_url else "出版社ページあり",
                )
            )

        unique: list[ManualCheckCandidate] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate.url not in seen:
                unique.append(candidate)
                seen.add(candidate.url)
        return sorted(unique, key=lambda candidate: candidate.priority)

    def _publisher_landing_pdf_url(self, landing_url: str) -> str:
        if not landing_url:
            return ""
        try:
            response = self.session.get(
                landing_url,
                timeout=self.timeout,
                headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"},
            )
            response.raise_for_status()
        except requests.RequestException:
            return ""

        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type not in {"text/html", "application/xhtml+xml"}:
            return ""
        parser = PublisherPdfLinkParser(response.url)
        parser.feed(response.text)
        return parser.pdf_url

    def _try_europe_pmc(self, metadata: ArticleMetadata, destination: Path) -> PdfResult:
        queries: list[str] = []
        if metadata.doi:
            queries.append(f'DOI:"{metadata.doi}"')
        if metadata.pmid:
            queries.append(f"EXT_ID:{metadata.pmid} AND SRC:MED")

        for query in queries:
            pdf_url = self._europe_pmc_pdf_url(query)
            if not pdf_url:
                continue
            result = self._download_if_pdf(pdf_url, destination, "Europe PMC")
            if result.success:
                return result
            if result.reason == "PDF requires authentication":
                return result

        return PdfResult(success=False, source="Europe PMC", reason="No OA PDF URL in Europe PMC")

    def _europe_pmc_pdf_url(self, query: str) -> str:
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        try:
            response = self.session.get(
                url,
                params={"query": query, "format": "json", "resultType": "core", "pageSize": 1},
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError):
            return ""

        results = data.get("resultList", {}).get("result", [])
        if not results:
            return ""

        full_text_urls = results[0].get("fullTextUrlList", {}).get("fullTextUrl", [])
        for item in full_text_urls:
            url_value = item.get("url", "")
            style = item.get("documentStyle", "").lower()
            availability = item.get("availability", "").lower()
            if url_value and style == "pdf" and "free" in availability:
                return url_value

        for item in full_text_urls:
            url_value = item.get("url", "")
            style = item.get("documentStyle", "").lower()
            if url_value and (style == "pdf" or url_value.lower().split("?")[0].endswith(".pdf")):
                return url_value

        return ""

    def _unpaywall_pdf_urls(self, data: dict) -> list[str]:
        urls: list[str] = []
        best = data.get("best_oa_location") or {}
        if best.get("url_for_pdf"):
            urls.append(best["url_for_pdf"])

        for location in data.get("oa_locations") or []:
            if location.get("url_for_pdf"):
                urls.append(location["url_for_pdf"])

        return list(dict.fromkeys(urls))

    @staticmethod
    def is_pdf_file(path: Path) -> bool:
        try:
            with path.open("rb") as handle:
                return handle.read(5) == b"%PDF-"
        except OSError:
            return False

    def _download_if_pdf(self, url: str, destination: Path, source: str) -> PdfResult:
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            temporary.unlink(missing_ok=True)
            with self.session.get(url, timeout=self.timeout, stream=True, allow_redirects=True) as response:
                if response.status_code in {401, 403}:
                    return PdfResult(False, source=source, url=url, reason="PDF requires authentication")
                if response.status_code == 404:
                    return PdfResult(False, source=source, url=url, reason="PDF URL not found")
                response.raise_for_status()

                content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                chunks = response.iter_content(chunk_size=8192)
                first_chunk = next(chunks, b"")

                if not first_chunk.startswith(b"%PDF-"):
                    reason = f"URL did not return a PDF ({content_type or 'unknown content type'})"
                    if source == "Publisher" and content_type == "text/html":
                        reason = "Publisher returned HTML instead of PDF; manual browser check, CAPTCHA, or login may be required"
                    return PdfResult(False, source=source, url=url, reason=reason)

                with temporary.open("wb") as handle:
                    handle.write(first_chunk)
                    for chunk in chunks:
                        if chunk:
                            handle.write(chunk)

            temporary.replace(destination)
            return PdfResult(True, source=source, url=url)
        except requests.RequestException as exc:
            return PdfResult(False, source=source, url=url, reason=f"Download failed: {exc}")
        except OSError as exc:
            return PdfResult(False, source=source, url=url, reason=f"Could not save PDF: {exc}")
        finally:
            temporary.unlink(missing_ok=True)


class PmcPdfLinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.pdf_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.pdf_url:
            return

        attr_map = {key.lower(): value or "" for key, value in attrs}
        href = attr_map.get("href", "")
        if not href:
            return

        lower_href = href.lower()
        classes = attr_map.get("class", "").lower()
        aria_label = attr_map.get("aria-label", "").lower()

        if (
            lower_href.endswith(".pdf")
            or "/pdf/" in lower_href
            or "pdf" in classes
            or "pdf" in aria_label
        ):
            self.pdf_url = urljoin(self.base_url, href)


class PublisherPdfLinkParser(HTMLParser):
    """Find standard scholarly HTML metadata that explicitly points to a PDF."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.pdf_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.pdf_url:
            return
        attr_map = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            field_name = (attr_map.get("name") or attr_map.get("property") or "").lower()
            if field_name in {"citation_pdf_url", "wkhealth_pdf_url"} and attr_map.get("content"):
                self.pdf_url = urljoin(self.base_url, attr_map["content"])
        elif tag.lower() == "link":
            rel = attr_map.get("rel", "").lower().split()
            media_type = attr_map.get("type", "").lower()
            if "alternate" in rel and media_type == "application/pdf" and attr_map.get("href"):
                self.pdf_url = urljoin(self.base_url, attr_map["href"])


def normalize_pii(value: str) -> str:
    return value.strip().strip("()")


def compact_pii_for_url(value: str) -> str:
    return "".join(char for char in value if char.isalnum())


def https_url_for_ftp(url: str) -> str:
    if url.lower().startswith("ftp://"):
        return "https://" + url[6:]
    return url
