"""Reading guides and scoped PDF exports with synthetic source material."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from pypdf import PdfReader, PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from grarec_app import Application
from paper_pipeline import reading_guide
from paper_pipeline.manual import Workspace
from paper_pipeline.pubmed import ArticleMetadata, PubMedClient

PMID = '90000001'


class ReadingGuideTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.workspace = Workspace.create(self.root / 'shared', 'a' * 32, 'Test Mac')

    def add_pdf(self):
        self.workspace.add(PMID)
        writer = PdfWriter()
        for page in range(1, 16):
            writer.add_blank_page(width=page * 10, height=200)
        writer.add_outline_item('Methods', 1)
        writer.add_outline_item('Results', 5)
        raw = io.BytesIO()
        writer.write(raw)
        return self.workspace.import_file(PMID, 'pdf', 'source.pdf', raw.getvalue())

    def test_registration_fetches_pubmed_type_and_keeps_abstract_out_of_list(self):
        client = PubMedClient()
        self.addCleanup(client.session.close)
        xml = b'''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>90000001</PMID>
        <Article><ArticleTitle>Synthetic systematic review</ArticleTitle>
        <Abstract><AbstractText Label="RESULTS">An <i>abstract</i> result.</AbstractText></Abstract>
        <PublicationTypeList><PublicationType>Review</PublicationType>
        <PublicationType>Systematic Review</PublicationType></PublicationTypeList>
        </Article></MedlineCitation></PubmedArticle></PubmedArticleSet>'''
        app = Application(self.root / 'repo', self.workspace.root)
        with patch('paper_pipeline.reading_guide.PubMedClient', return_value=client), \
                patch.object(client.session, 'get', return_value=Mock(content=xml)) as get:
            self.assertIn('PubMed書誌を1件', app.run('add', {'pmids': PMID}))
            get.assert_called_once()
            self.assertEqual(get.call_args.kwargs['params']['id'], PMID)
        app.refresh()
        paper = app.state['papers'][0]
        self.assertEqual(paper['publication_type'], 'システマティックレビュー')
        self.assertNotIn('abstract', paper)
        context = reading_guide.guide(self.workspace, PMID, paper['heads'])
        self.assertEqual(context['abstract'], 'RESULTS: An abstract result.')
        with patch('grarec_app.subprocess.run') as run:
            app.run('copy-reading-guide', {'pmid': PMID, 'heads': paper['heads']})
        self.assertIn('1分程度', run.call_args.kwargs['input'].decode())
        self.assertEqual(reading_guide.publication_label(['Journal Article']), '種別未確認')

    def test_explicit_review_title_is_marked_as_inferred_until_pubmed_has_the_type(self):
        title = 'Treatment outcomes in children: A Systematic Review.'
        value = reading_guide.publication_info({}, title)
        self.assertEqual(value['publication_type'], 'システマティックレビュー')
        self.assertTrue(value['publication_type_inferred'])
        value = reading_guide.publication_info({'publication_types': ['Review']}, 'Kidney disease-A Narrative Review.')
        self.assertEqual(value['publication_type'], 'ナラティブレビュー')
        self.assertTrue(value['publication_type_inferred'])
        value = reading_guide.publication_info({'publication_types': ['Systematic Review', 'Meta-Analysis']}, title)
        self.assertEqual(value['publication_type'], 'システマティックレビュー／メタ解析')
        self.assertFalse(value['publication_type_inferred'])
        value = reading_guide.publication_info({}, 'Teaching how to read a systematic review in clinical training')
        self.assertEqual(value['publication_type'], '種別未確認')

    def test_batch_metadata_refresh_updates_existing_pdf_candidate_without_revising_it(self):
        head = self.add_pdf()['id']
        awaiting_pdf = '90000002'
        self.workspace.add(awaiting_pdf)
        app = Application(self.root / 'repo', self.workspace.root)
        client = Mock()
        client.fetch_metadata.return_value = [ArticleMetadata(PMID, title='Synthetic review', publication_types=('Review',))]
        with patch('paper_pipeline.reading_guide.PubMedClient', return_value=client):
            message = app.run('json-metadata', {'pmids': [PMID, awaiting_pdf, PMID]})
        self.assertIn('1/1件', message)
        client.fetch_metadata.assert_called_once_with([PMID], include_links=False)
        app.refresh()
        candidate = next(p for p in app.state['papers'] if p['pmid'] == PMID)
        self.assertEqual(candidate['publication_types'], ['Review'])
        self.assertEqual(candidate['heads'], [head])

    def test_unavailable_pubmed_preserves_registration(self):
        app = Application(self.root / 'repo', self.workspace.root)
        client = Mock()
        client.fetch_metadata.side_effect = requests.Timeout('private connection details')
        with patch('paper_pipeline.reading_guide.PubMedClient', return_value=client):
            message = app.run('add', {'pmids': PMID})
        self.assertIn('0/1件', message)
        self.assertNotIn('private', message)
        self.assertEqual(self.workspace.snapshot()['counts']['missing_pdf'], 1)

    def test_guide_and_excerpt_use_original_pages_and_preserve_workflow(self):
        head = self.add_pdf()['id']
        reading_guide.save_metadata(self.workspace, ArticleMetadata(PMID, title='Synthetic review',
                                                                    publication_types=('Review',)))
        context = reading_guide.guide(self.workspace, PMID, [head])
        self.assertEqual(context['sections'], [{'title': 'Methods', 'page': 2}, {'title': 'Results', 'page': 6}])
        self.assertEqual(context['pages'], 15)
        self.assertIn('参照した範囲', reading_guide.guide_prompt(context))
        folder = reading_guide.export_excerpt(self.root / 'repo', self.workspace, PMID, [head],
                                               '6-7, 2, 6', '対象と主要結果')
        reader = PdfReader(next(folder.glob('*.pdf')))
        self.assertEqual([page.mediabox.width for page in reader.pages], [20, 60, 70])
        instructions, data = (folder / '部分図解の依頼.txt').read_text().split('\n\n', 1)
        self.assertIn('部分図解', instructions)
        self.assertEqual(json.loads(data)['original_pdf_pages'], [2, 6, 7])
        text_folder = reading_guide.export_excerpt(self.root / 'repo', self.workspace, PMID, [head],
                                                    '6-7, 2', '対象と主要結果', output_format='text')
        self.assertNotEqual(folder, text_folder)
        text_request = (text_folder / '読書ガイドの依頼.txt').read_text()
        self.assertIn('短い日本語の読書ガイド', text_request)
        self.assertNotIn('.png', text_request)
        self.assertEqual(self.workspace.current(PMID)['id'], head)
        self.assertEqual(self.workspace.snapshot()['counts']['json_pending'], 1)
        for pages in ('1-13', '1-10,12-15', '16', '3-2', '1,,,3'):
            with self.subTest(pages=pages), self.assertRaises(ValueError):
                reading_guide.export_excerpt(self.root / 'repo', self.workspace, PMID, [head], pages, '対象')
        with self.assertRaisesRegex(ValueError, '別の端末で更新'):
            reading_guide.export_excerpt(self.root / 'repo', self.workspace, PMID, ['old'], '2', '対象')
