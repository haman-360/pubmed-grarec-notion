import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from paper_pipeline.manual import Workspace
from paper_pipeline.paid_pdf import PaidPdfFinder, check_url
from paper_pipeline.pubmed import ArticleMetadata
from paper_pipeline.pdf_download import download_selected
from test_pdf_download import paper_pdf
from grarec_app import Application

class PaidTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ws = Workspace.create(self.root/'shared', 'a'*32, 'test')
        self.ws.add('90000001')
        self.article = ArticleMetadata(pmid='90000001', title='Synthetic pediatric study 90000001', doi='10.1234/test90000001')
        with patch('paper_pipeline.paid_pdf.api_key', return_value='test-key'):
            self.finder = PaidPdfFinder(self.root, self.ws)
        self.addCleanup(self.finder.session.close)

    def test_search_cache_and_usage(self):
        result = dict(id='resp_test',status='completed',usage={'input_tokens':100},output=[
            {'type':'web_search_call','action':{'sources':[{'url':'https://example.org/paper.pdf'}]}}])
        response = Mock(); response.json.return_value = result
        with patch('paper_pipeline.paid_pdf.requests.post', return_value=response) as post:
            self.assertEqual(self.finder.search(self.article), ['https://example.org/paper.pdf'])
            self.finder.search(self.article)
            self.assertEqual(post.call_count,1)
            self.assertEqual(post.call_args.kwargs['json']['max_tool_calls'],2)
        saved=json.loads(self.ws.path('API_PDF/PMID_90000001.json').read_text())
        self.assertEqual(saved['usage'], {'input_tokens':100})
        self.assertNotIn('test-key', json.dumps(saved))

    def test_timeout_is_not_retried(self):
        with patch('paper_pipeline.paid_pdf.requests.post', side_effect=requests.Timeout) as post:
            with self.assertRaises(ValueError): self.finder.search(self.article)
            with self.assertRaisesRegex(ValueError,'重複課金'): self.finder.search(self.article)
            self.assertEqual(post.call_count,1)

    def test_public_address_validation(self):
        for url in ['file:///tmp/a', 'http://example.org/a', 'https://user:pass@example.org/a']:
            with self.assertRaises(ValueError): check_url(url)
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError): check_url('https://example.org/a')

    def test_paid_download_uses_normal_validation_and_import(self):
        raw=paper_pdf(self.article)
        response=Mock(); response.__enter__=Mock(return_value=response); response.__exit__=Mock(return_value=False)
        response.iter_content.return_value=[raw]
        client=Mock();client.fetch_metadata.return_value=[self.article]
        with patch.object(self.finder,'search',return_value=['https://example.org/paper.pdf']), patch.object(self.finder,'public_get',return_value=response):
            result=download_selected(self.ws,[dict(pmid=self.article.pmid,heads=[self.ws.current(self.article.pmid)['id']])],client=client,finder=self.finder)
        self.assertEqual(result['items'][0]['status'],'downloaded')
        self.assertEqual(self.ws.category(self.ws.current(self.article.pmid)['data']),'json_pending')

    def test_gui_requires_paid_confirmation_before_finder_creation(self):
        app=Application(self.root/'repo', self.ws.root)
        with patch('paper_pipeline.paid_pdf.PaidPdfFinder') as finder:
            with self.assertRaisesRegex(ValueError,'検索API料金'):
                app.run('fetch-pdfs-paid',dict(items=[dict(pmid=self.article.pmid,heads=[self.ws.current(self.article.pmid)['id']])]))
            finder.assert_not_called()
