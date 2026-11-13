from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import requests

from paper_pipeline.pdf_finder import PdfFinder, PublisherPdfLinkParser
from paper_pipeline.pubmed import ArticleMetadata


class FakeResponse:
    def __init__(self, chunks: list[bytes], content_type: str = "application/pdf") -> None:
        self.status_code = 200
        self.headers = {"Content-Type": content_type}
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        yield from self._chunks


class FailingResponse(FakeResponse):
    def iter_content(self, chunk_size: int):
        yield b"%PDF-1.7 partial"
        raise requests.ConnectionError("connection lost")


class PdfFinderTests(unittest.TestCase):
    def test_publisher_parser_finds_standard_pdf_metadata(self) -> None:
        parser = PublisherPdfLinkParser("https://journal.example.test/article/1")
        parser.feed('<meta name="citation_pdf_url" content="/article/1.pdf">')

        self.assertEqual(parser.pdf_url, "https://journal.example.test/article/1.pdf")

    def test_publisher_parser_ignores_meta_without_name_or_property(self) -> None:
        parser = PublisherPdfLinkParser("https://journal.example.test/article/1")
        parser.feed(
            '<meta charset="utf-8"><meta name>'
            '<meta name="citation_pdf_url" content="/article/1.pdf">'
        )

        self.assertEqual(parser.pdf_url, "https://journal.example.test/article/1.pdf")

    def test_unpaywall_urls_are_deduplicated_in_priority_order(self) -> None:
        finder = PdfFinder()
        data = {
            "best_oa_location": {"url_for_pdf": "https://example.test/best.pdf"},
            "oa_locations": [
                {"url_for_pdf": "https://example.test/best.pdf"},
                {"url_for_pdf": "https://example.test/backup.pdf"},
            ],
        }

        self.assertEqual(
            finder._unpaywall_pdf_urls(data),
            ["https://example.test/best.pdf", "https://example.test/backup.pdf"],
        )

    def test_hogrefe_download_url_is_a_publisher_candidate(self) -> None:
        finder = PdfFinder()

        self.assertEqual(
            finder.manual_pdf_candidates("10.1024/1422-4917/a001082"),
            [
                "https://econtent.hogrefe.com/doi/pdf/"
                "10.1024/1422-4917/a001082?download=true"
            ],
        )

    def test_bmj_download_url_is_a_publisher_candidate(self) -> None:
        finder = PdfFinder()

        self.assertEqual(
            finder._publisher_pdf_candidates(
                ArticleMetadata(
                    pmid="42526949",
                    doi="10.1136/bmj-2026-100163",
                    volume="394",
                )
            ),
            ["https://www.bmj.com/content/bmj/394/bmj-2026-100163.full.pdf"],
        )

    def test_free_linkout_makes_manual_candidate_high_priority(self) -> None:
        finder = PdfFinder()

        candidates = finder.manual_check_candidates_for_article(
            ArticleMetadata(
                pmid="42526949",
                doi="10.1136/bmj-2026-100163",
                volume="394",
                publisher_url="https://doi.org/10.1136/bmj-2026-100163",
                free_full_text_url="https://www.bmj.com/free-article",
            )
        )

        self.assertTrue(candidates)
        self.assertTrue(all(candidate.priority == "1_high" for candidate in candidates))
        self.assertIn("free resource", candidates[0].evidence)

    def test_download_is_saved_atomically(self) -> None:
        finder = PdfFinder()
        finder.session.get = Mock(return_value=FakeResponse([b"%PDF-1.7\n", b"body"]))
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "article.pdf"
            result = finder._download_if_pdf("https://example.test/article.pdf", destination, "test")

            self.assertTrue(result.success)
            self.assertTrue(finder.is_pdf_file(destination))
            self.assertFalse(destination.with_suffix(".pdf.part").exists())

    def test_interrupted_download_leaves_no_partial_pdf(self) -> None:
        finder = PdfFinder()
        finder.session.get = Mock(return_value=FailingResponse([]))
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "article.pdf"
            result = finder._download_if_pdf("https://example.test/article.pdf", destination, "test")

            self.assertFalse(result.success)
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_suffix(".pdf.part").exists())

    def test_html_with_pdf_content_type_is_not_accepted(self) -> None:
        finder = PdfFinder()
        finder.session.get = Mock(return_value=FakeResponse([b'<html>login</html>']))
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'article.pdf'
            self.assertFalse(finder._download_if_pdf('https://example.test/a', destination, 'test').success)
            self.assertFalse(destination.exists())

    def test_pdf_is_extracted_from_pmc_oa_package(self) -> None:
        pdf_content = b"%PDF-1.7\narticle body"
        package = io.BytesIO()
        with tarfile.open(fileobj=package, mode="w:gz") as archive:
            member = tarfile.TarInfo("PMC123/article.pdf")
            member.size = len(pdf_content)
            archive.addfile(member, io.BytesIO(pdf_content))

        finder = PdfFinder()
        finder.session.get = Mock(return_value=FakeResponse([package.getvalue()], "application/gzip"))
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "article.pdf"
            result = finder._download_pmc_oa_package(
                "https://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/test.tar.gz",
                destination,
            )

            self.assertTrue(result.success)
            self.assertEqual(result.source, "PMC OA package")
            self.assertEqual(destination.read_bytes(), pdf_content)
            self.assertFalse(destination.with_suffix(".pdf.oa-package.part").exists())

    def test_pmc_oa_ftp_package_is_converted_to_https(self) -> None:
        response = Mock(
            status_code=200,
            content=(
                b'<OA><records><record><link format="tgz" '
                b'href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/article.tar.gz" />'
                b'</record></records></OA>'
            ),
        )
        finder = PdfFinder()
        finder.session.get = Mock(return_value=response)

        self.assertEqual(
            finder._pmc_oa_urls("PMC123"),
            ("", "https://ftp.ncbi.nlm.nih.gov/pub/pmc/article.tar.gz"),
        )

    def test_manual_candidates_include_pmc_article_page(self) -> None:
        finder = PdfFinder()

        self.assertEqual(
            finder.manual_pdf_candidates_for_article(
                ArticleMetadata(pmid="123", pmcid="PMC123")
            ),
            ["https://pmc.ncbi.nlm.nih.gov/articles/PMC123/"],
        )


if __name__ == "__main__":
    unittest.main()
