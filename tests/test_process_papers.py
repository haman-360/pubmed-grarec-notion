from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import process_papers  # noqa: E402


class ProcessPapersTests(unittest.TestCase):
    def test_batch_cost_uses_configured_half_price_rates(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENAI_BATCH_INPUT_USD_PER_MILLION": "1",
                "OPENAI_BATCH_OUTPUT_USD_PER_MILLION": "6",
            },
        ):
            self.assertAlmostEqual(process_papers.estimate_batch_cost(30_000, 8_000), 0.078)

    def test_collect_pmids_deduplicates_file_and_pdf_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pmids = root / "pmids.txt"
            pmids.write_text("# queue\n42115808\n42115808\nbad\n", encoding="utf-8")
            pdfs = root / "pdfs"
            pdfs.mkdir()
            (pdfs / "PMID_41733080.pdf").write_bytes(b"%PDF-1.4")
            result = process_papers._collect_pmids(None, pmids, pdfs)
        self.assertEqual(result, ["42115808", "41733080"])

    def test_custom_id_round_trip(self) -> None:
        job = {"pmid": "42115808", "source_hash": "abcdef0123456789"}
        custom_id = process_papers._custom_id(job)
        self.assertEqual(custom_id, "pmid-42115808-summary-v2-abcdef01")
        self.assertEqual(process_papers._pmid_from_custom_id(custom_id), "42115808")

    def test_explicit_pmid_does_not_add_unrelated_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdfs = root / "pdfs"
            pdfs.mkdir()
            (pdfs / "PMID_41733080.pdf").write_bytes(b"%PDF-1.4")
            result = process_papers._collect_pmids(["42115808"], root / "missing.txt", pdfs)
        self.assertEqual(result, ["42115808"])

    def test_review_schema_is_strict_and_all_fields_required(self) -> None:
        schema = process_papers.review_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(schema["properties"]["pico"]["type"], "object")
        self.assertEqual(schema["properties"]["main_results"]["type"], "array")
        self.assertEqual(schema["properties"]["limitations"]["type"], "array")
        self.assertEqual(schema["properties"]["tomorrow_action"]["type"], "array")

    def test_canonical_metadata_overrides_model_output(self) -> None:
        result = {"pmid": "wrong", "title": "wrong", "one_line_summary": "結論"}
        job = {
            "article": {
                "pmid": "42115808",
                "title": "Canonical title",
                "journal": "Journal",
                "year": 2026,
                "doi": "10.1/example",
                "pubmed_url": "https://pubmed.ncbi.nlm.nih.gov/42115808/",
                "published_date": "2026-01-01",
            },
            "source_type": "user_pdf",
            "source_hash": "abc",
        }
        summary = process_papers._canonicalize_summary(result, job)
        self.assertEqual(summary["pmid"], "42115808")
        self.assertEqual(summary["title"], "Canonical title")
        self.assertEqual(summary["take_home_message"], "結論")
        self.assertEqual(summary["source_level"], "user_pdf")

    def test_has_uncollected_batches(self) -> None:
        manifests = [
            (Path("first.json"), {"batch_id": "batch-1", "collected_at": "2026-08-04"}),
            (Path("second.json"), {"batch_id": "batch-2"}),
        ]
        with patch.object(process_papers, "_batch_manifests", return_value=manifests):
            self.assertTrue(process_papers._has_uncollected_batches())

        with patch.object(process_papers, "_batch_manifests", return_value=manifests[:1]):
            self.assertFalse(process_papers._has_uncollected_batches())

    def test_automatic_workflow_prepares_submits_then_watches(self) -> None:
        args = object()
        with (
            patch.object(process_papers, "prepare_jobs") as prepare,
            patch.object(process_papers, "submit_jobs", return_value=True) as submit,
            patch.object(process_papers, "watch_batches") as watch,
        ):
            process_papers.automatic_workflow(args)
        prepare.assert_called_once_with(args)
        submit.assert_called_once_with(args)
        watch.assert_called_once_with(args)

    def test_graphic_public_url_uses_repository_relative_path(self) -> None:
        image = ROOT / "images" / "2026" / "08" / "PMID_42526949_grarec.png"
        with patch.dict(os.environ, {"GITHUB_PAGES_BASE_URL": "https://example.test/pubmed"}):
            url = process_papers.graphic_public_url(image)
        self.assertEqual(
            url,
            "https://example.test/pubmed/images/2026/08/PMID_42526949_grarec.png",
        )


if __name__ == "__main__":
    unittest.main()
