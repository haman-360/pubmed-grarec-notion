"""Offline export and regression coverage for heterogeneous paper JSON."""
import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pypdf import PdfWriter
from generate_reader import main
from grarec_app import Application
from paper_pipeline import reading_guide
from paper_pipeline.graphics import adapt_legacy
from paper_pipeline.image_review import CHECKS
from paper_pipeline.manual import Workspace
from paper_pipeline.pubmed import ArticleMetadata
from paper_pipeline.reader import render, save_reader
from paper_pipeline.reader_data import paper_from_json
from test_paper_pipeline import png, sample_summary

PMID = '90000001'


def encoded(data):
    return json.dumps(data, ensure_ascii=False).encode()


def learning_example():
    overview = sample_summary(dict(pmid=PMID, title='Fixture', doi=''))
    for key in ('pmid', 'title', 'journal', 'year', 'doi', 'result_evidence'):
        overview.pop(key)
    overview.update(result_evidence_ids=[['e1']], source=None)
    measurements = [dict(id=key, label='指標', reported=value + ' 単位', estimate=value, unit='単位',
                         source_refs=['s1']) for key, value in [('m1', '1.0'), ('m2', '2.0')]]
    return dict(schema_version='learning-v2', metadata=dict(pmid=PMID, title='Fixture', journal=None,
                    year=None, doi=None, pdf_sha256='a'*64), article_type='rct', classification_reason='原著',
                abstract=dict(source='pubmed', status='recorded', parts=[
                    dict(id='a1', label='目的', language='en', original='Original abstract', translation_ja='原文に対応する訳')]),
                overview=overview, clinical_interpretation=[], chapters=[dict(id='c1', parent_id=None,
                    section_title_original='Results', title_ja='第1章', summary_ja='章の概要', source_refs=['s1'],
                    blocks=[dict(role='finding', origin='paper', text_ja='根拠に対応する文章', evidence_ids=['e1'])],
                    visualization_ids=['v1'])], evidence=[dict(id='e1', kind='study_result', statement_ja='章の結果',
                    measurements=measurements, source_refs=['s1'])], source_references=[dict(id='s1', source='original_pdf',
                    pdf_page_start=2, table='Table I', quote='short quotation')], visualizations=[dict(id='v1',
                    type='bar_chart', title_ja='比較図', evidence_ids=['e1'], axis=dict(scale='linear', measure='指標', unit='単位'),
                    items=[dict(id=k, label=k, evidence_ids=['e1'], measurement_ids=[m]) for k,m in [('g1','m1'),('g2','m2')]])],
                png_plan=[dict(id='overview', role='overview', title='図', message='概要', evidence_ids=['e1'], required_caveats=[])],
                quality_control=dict(human_checked=False, issues=[], review_event_id=None),
                generation_metadata=dict(method='manual_web', schema_revision='1', prompt_version=None, run_id='manual'))


class ReaderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        no_network = patch('requests.sessions.Session.request', side_effect=AssertionError('offline only'))
        no_network.start()
        self.addCleanup(no_network.stop)

    def test_legacy_cli_fenced_json_and_inlined_png(self):
        data = dict(PMID=PMID, title='Result < & >', topic='小児',
                    figure_table_summary={'table_1':['1.0', '2.0']}, main_results='5% vs 8%',
                    safety=['記録'], source='元記録', practice_change=None,
                    tomorrow_action='<script>window.injection=true</script>')
        source = self.root / 'old.json'
        source.write_bytes(b'\xef\xbb\xbf```json\n' + encoded(data) + b'\n```')
        image = self.root / f'PMID_{PMID}_grarec.png'
        image.write_bytes(png((10,10)))
        inputs = [p.read_bytes() for p in (source, image)]
        args = [str(source), '--png', str(image), '--output-dir', str(self.root/'out')]
        with patch('builtins.print') as output:
            main(args)
            path = Path(output.call_args.args[0])
            content, mtime = path.read_text(), path.stat().st_mtime_ns
            main(args)
            self.assertEqual(path.stat().st_mtime_ns, mtime)
        for expected in ('data:image/png;base64,', '5% vs 8%', 'table_1', '未評価', '元記録', '&lt;script&gt;'):
            self.assertIn(expected, content)
        self.assertEqual(content.count('<script>'), 1)
        self.assertEqual(inputs, [p.read_bytes() for p in (source, image)])
        wrong = self.root/'PMID_123_other.png'
        wrong.write_bytes(inputs[1])
        with self.assertRaises(SystemExit):
            main([str(source), '--png', str(wrong)])
        different = dict(data, title='新版')
        export = save_reader(self.root/'out', paper_from_json(encoded(different)))
        self.assertNotEqual(export, path)
        self.assertEqual(path.read_text(), content)

    def test_declines_unknown_versions_images_and_missing_identity(self):
        for data in [dict(schema_version='other'), dict(pmid=PMID, title='graphic', topic_id='main',
                          one_line_summary='text'), dict(pmid='../tmp', title='Data', main_results=[])]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                paper_from_json(encoded(data))

    def test_learning_chapter_abstract_chart_and_references(self):
        data = learning_example()
        html = render(paper_from_json(encoded(data))).decode()
        for expected in ('原文に対応する訳', 'Original abstract', '第1章', 'short quotation', 'Table I',
                         '<meter ', '2.0 単位', '根拠に対応する文章'):
            self.assertIn(expected, html)
        data['evidence'][0]['measurements'][1]['unit'] = '別の単位'
        html = render(paper_from_json(encoded(data))).decode()
        self.assertIn('別の単位', html)
        self.assertNotIn('<meter ', html)
        for mutate in [lambda d: d['metadata'].pop('title'), lambda d: d['chapters'][0].update(parent_id='c1'),
                       lambda d: d['overview'].update(result_evidence_ids=[['missing']]),
                       lambda d: d['chapters'][0].update(source_refs=['absent'])]:
            invalid = copy.deepcopy(data)
            mutate(invalid)
            with self.assertRaises(ValueError):
                paper_from_json(encoded(invalid))

    def test_app_uses_cached_abstract_and_only_approved_png_without_scanning(self):
        ws = Workspace.create(self.root/'shared', 'a'*32, 'test')
        ws.add(PMID)
        pdf = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.write(pdf)
        ws.import_file(PMID, 'pdf', 'paper.pdf', pdf.getvalue())
        master = adapt_legacy(sample_summary(dict(pmid=PMID, title='Title', doi='')))
        ws.import_file(PMID, 'json', 'master.json', encoded(master))
        reading_guide.save_metadata(ws, ArticleMetadata(PMID, title='Title', abstract='Cached abstract'))
        image = png((10,10))
        with patch('paper_pipeline.manual.inspect_png', return_value=dict(status='review_required', reason='fixture')):
            ws.import_file(PMID, 'png', 'main.png', image)
        app = Application(self.root/'repo', ws.root)

        def export():
            head = ws.current(PMID)['id']
            before = {p: hashlib.sha256(p.read_bytes()).digest() for p in ws.root.rglob('*') if p.is_file()}
            with patch.object(app, 'refresh', side_effect=AssertionError('Reader must not scan or sync')), \
                 patch('grarec_app.subprocess.run') as opened:
                app.start('open-reader', dict(pmid=PMID, heads=[head])).join(10)
                opened.assert_called_once()
                result = Path(opened.call_args.args[0][1]).read_text()
            self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).digest() for p in ws.root.rglob('*') if p.is_file()})
            return result

        before = export()
        self.assertIn('Cached abstract', before)
        self.assertIn('p.2 Table 1', before)
        self.assertNotIn('src="data:image/', before)
        head = ws.current(PMID)['id']
        ws.review_png(PMID, [head], 'main', hashlib.sha256(image).hexdigest(), PMID, master['practice_change'], list(CHECKS))
        self.assertIn('src="data:image/png', export())
        with self.assertRaises(ValueError):
            app.run('open-reader', dict(pmid=PMID, heads=[head]))

    def test_broken_png_still_allows_reading(self):
        paper = paper_from_json(encoded(dict(title='Title', pmid=PMID, main_results='Result')))
        html = render(paper, [('Bad PNG', b'invalid')]).decode()
        self.assertIn('PNGを読み込めません', html)
        self.assertIn('Result', html)
