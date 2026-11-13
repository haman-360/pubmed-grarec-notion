"""Offline integration tests for selected downloads and the OneDrive workflow."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from paper_pipeline.manual import Workspace
from paper_pipeline.intake import PENDING
from paper_pipeline.pdf_download import download_selected, result_path, public_url
from paper_pipeline.pdf_finder import PdfFinder, PdfResult
from paper_pipeline.pubmed import ArticleMetadata
from grarec_app import Application

PMID = '90000001'


def paper_pdf(article):
    writer = PdfWriter(); page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(('BT /F1 10 Tf 20 750 Td (' + article.title + ') Tj 0 -20 Td (doi: ' + article.doi + ') Tj ET').encode())
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO(); writer.write(output); return output.getvalue()


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ws = Workspace.create(self.root / 'shared', 'a' * 32, 'Test Mac')
        self.ws.add(PMID)
        self.article = ArticleMetadata(pmid=PMID, title='Synthetic pediatric study 90000001', doi='10.1234/test90000001', publisher_url='https://doi.org/10.1234/test90000001')
        self.raw = paper_pdf(self.article)
        self.client = Mock(); self.client.fetch_metadata.return_value = [self.article]
        self.finder = PdfFinder()
        self.finder.download_pdf = Mock(side_effect=self.download)

    def selection(self, pmid=PMID):
        return dict(pmid=pmid, heads=[self.ws.current(pmid)['id']])

    def download(self, article, destination):
        destination.write_bytes(self.raw)
        return PdfResult(True, source='Synthetic OA', url='https://example.test/paper.pdf')

    def run_download(self, items=None, **kwargs):
        with patch('requests.Session.request', side_effect=AssertionError('network forbidden')), \
             patch('paper_pipeline.pdf_download.polite_pause'):
            return download_selected(self.ws, items or [self.selection()], client=self.client, finder=self.finder, **kwargs)

    def test_download_updates_same_workspace_and_prepares_web_json_without_ai(self):
        result = self.run_download()
        self.assertEqual(result['items'][0]['status'], 'downloaded')
        snapshot = self.ws.snapshot()
        self.assertEqual(snapshot['counts']['json_pending'], 1)
        file = result['items'][0]['file']
        self.assertEqual(self.ws.read_asset(file), self.raw)
        self.assertIn(self.article.title, self.ws.current(PMID)['data']['title'])
        self.assertTrue(any(f['kind'] == 'prompt' for f in self.ws.current(PMID)['data']['files']))
        self.assertFalse(self.ws.current(PMID)['data']['done'])
        self.assertEqual(json.loads(result_path(self.ws).read_text()), result)

    def test_existing_pdf_is_never_downloaded_or_replaced(self):
        self.ws.import_file(PMID, 'pdf', 'manual.pdf', self.raw)
        before = self.ws.current(PMID)
        result = self.run_download()
        self.assertEqual(result['items'][0]['status'], 'skipped')
        self.assertEqual(before, self.ws.current(PMID))
        self.client.fetch_metadata.assert_not_called(); self.finder.download_pdf.assert_not_called()

    def test_old_selection_and_pending_manual_pdf_skip_network(self):
        selection = self.selection()
        self.ws.update(PMID, selection['heads'], 'newer')
        self.assertEqual(self.run_download([selection])['items'][0]['status'], 'skipped')
        pending = self.ws.path(PENDING + '/PMID_' + PMID + '/manual.pdf')
        pending.parent.mkdir(parents=True); pending.write_bytes(self.raw)
        self.assertIn('手動保存', self.run_download()['items'][0]['reason'])
        self.client.fetch_metadata.assert_not_called()

    def test_no_result_keeps_pdf_waiting_and_manual_links_survive_restart(self):
        self.finder.download_pdf.return_value = PdfResult(False, reason='Publisher returned HTML instead of PDF')
        self.finder.download_pdf.side_effect = None
        result = self.run_download()
        self.assertEqual(result['items'][0]['status'], 'not_found')
        self.assertTrue(result['items'][0]['manual_urls'])
        self.assertEqual(self.ws.snapshot()['counts']['missing_pdf'], 1)
        app = Application(self.root / 'repo', self.ws.root)
        self.assertIn('手動確認先', app.state['papers'][0]['note'])
        # Same device settings are necessary for per-device result display.
        app.settings['device_id'] = self.ws.device_id; app.refresh()
        self.assertEqual(app.state['pdf_result']['id'], result['id'])

    def test_wrong_article_is_saved_as_candidate_without_advancing(self):
        self.raw = paper_pdf(ArticleMetadata(pmid='90000002', title='Different synthetic article', doi='10.1234/other'))
        result = self.run_download()
        self.assertEqual(result['items'][0]['status'], 'candidate')
        self.assertEqual(self.ws.snapshot()['counts']['missing_pdf'], 1)
        self.assertEqual(self.ws.read_asset(result['items'][0]['file']), self.raw)
        self.assertFalse(any(f['kind'] == 'pdf' for f in self.ws.current(PMID)['data']['files']))

    def test_broken_download_does_not_advance(self):
        self.raw = b'<html>login required</html>'
        result = self.run_download()
        self.assertNotEqual(result['items'][0]['status'], 'downloaded')
        self.assertEqual(self.ws.snapshot()['counts']['missing_pdf'], 1)

    def test_concurrent_manual_update_preserved_and_download_retained(self):
        base = self.ws.current(PMID)
        def changed(article, destination):
            self.ws.update(PMID, [base['id']], 'other Mac updated this paper')
            return self.download(article, destination)
        self.finder.download_pdf.side_effect = changed
        result = self.run_download()
        self.assertEqual(self.ws.current(PMID)['data']['note'], 'other Mac updated this paper')
        self.assertEqual(result['items'][0]['status'], 'candidate')
        self.assertEqual(self.ws.read_asset(result['items'][0]['file']), self.raw)

    def test_unselected_papers_untouched_and_failure_does_not_stop_remaining(self):
        self.ws.add('90000002'); self.ws.add('90000003')
        before = self.ws.current('90000003')
        self.client.fetch_metadata.side_effect = [requests.Timeout('secret-api-key'), [ArticleMetadata(pmid='90000002', title=self.article.title, doi=self.article.doi)]]
        result = self.run_download([self.selection(), self.selection('90000002')])
        self.assertEqual([e['status'] for e in result['items']], ['error', 'downloaded'])
        self.assertEqual(self.ws.current('90000003'), before)
        self.assertNotIn('secret-api-key', json.dumps(result))

    def test_gui_action_calls_pdf_engine_and_displays_results(self):
        app = Application(self.root / 'repo', self.ws.root)
        with patch('paper_pipeline.pdf_download.PubMedClient', return_value=self.client), \
             patch('paper_pipeline.pdf_download.PdfFinder', return_value=self.finder), \
             patch('requests.Session.request', side_effect=AssertionError('no network')):
            message = app.run('fetch-pdfs', {'items': [self.selection()]})
        app.refresh()
        self.assertIn('1/1', message)
        self.assertEqual(app.state['counts']['json_pending'], 1)
        self.assertEqual(app.state['pdf_result']['items'][0]['status'], 'downloaded')
        self.assertIsNone(app.pdf_progress)
        run = app.state['pdf_result']['id']
        with patch('grarec_app.subprocess.run') as opener:
            url = app.state['pdf_result']['items'][0]['manual_urls'][0]
            app.run('open-pdf-result', dict(run=run, pmid=PMID, url=url))
            opener.assert_called_once_with(['open', url], check=True)
            with self.assertRaises(ValueError):
                app.run('open-pdf-result', dict(run=run, pmid=PMID, url='https://unrelated.test'))
            with self.assertRaises(ValueError):
                app.run('open-pdf-result', dict(run='stale', pmid=PMID))

    def test_invalid_selection_does_not_fetch_or_create_papers(self):
        for value in (None, [], [{'pmid':'../escape', 'heads':['x']}], [{'pmid':PMID}], [self.selection()]*51):
            with self.assertRaises(ValueError):
                download_selected(self.ws, value, client=self.client, finder=self.finder)
        self.client.fetch_metadata.assert_not_called()

    def test_display_urls_remove_query_credentials_and_forbid_other_schemes(self):
        self.assertEqual(public_url('https://example.test/a?api_key=secret#part'), 'https://example.test/a')
        self.assertEqual(public_url('javascript:alert(1)'), '')


if __name__ == '__main__':
    unittest.main()
