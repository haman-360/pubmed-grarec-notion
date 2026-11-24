"""Exercise offline chapter handoffs against synthetic, source-numbered PDFs."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from paper_pipeline.chapter_plan import chapter_plan, source_window
from paper_pipeline.chapter_run import dump, prepare, resume
from paper_pipeline.chapter_review import validate_result
from paper_pipeline.reader_data import paper_from_json
from paper_pipeline.store import digest

PMID = '90000001'
TITLE = 'Synthetic guideline for testing the reader'


def fixture_pdf(path, count=64, outline=True):
    writer = PdfWriter()
    writer.add_metadata({'/Title': TITLE})
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    resources = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    headings = {1:'Introduction', 4:'2. Evidence', 5:'2.1 Subgroup', 6:'3. Discussion'}
    for number in range(1, count+1):
        page = writer.add_blank_page(width=400, height=300)
        page[NameObject('/Resources')] = resources
        stream = DecodedStreamObject()
        text = f'Fixture page {number}: 15% in the sample.'
        first = headings.get(number, '15 patients completed the example')
        stream.set_data(f'BT /F1 12 Tf 20 250 Td ({first}) Tj 0 -24 Td ({text}) Tj ET'.encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    if outline:
        parent = None
        for number, title in headings.items():
            if number <= count:
                node = writer.add_outline_item(title, number-1, parent=parent if number==5 else None)
                if number == 4:
                    parent = node
    writer.write(path)


def response(folder, job):
    result = json.loads((folder/job['id']/'response-template.json').read_text())
    n = job['start']
    result.update(title_ja='試験用の章 ' + job['id'], summary_ja=f'原本{n}ページからのテスト用説明。',
                  sources=[dict(page=n, quote=f'Fixture page {n}: 15% in the sample.')],
                  findings=[dict(kind='cited_study', role='result', statement_ja='合成資料に記載された動作確認用の値は15%。',
                                 highlight=True, source_numbers=[1], measurements=[dict(label='試験値', reported='15%')],
                                 conditions=['合成資料のみ'], exceptions=['臨床に適用しない'])])
    return result


class ChapterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.pdf = self.root/'test.pdf'
        self.options = dict(pmid=PMID, title=None, article_type='guideline')
        guard = patch('requests.sessions.Session.request', side_effect=AssertionError('must stay offline'))
        guard.start()
        self.addCleanup(guard.stop)

    def prepare(self, **changes):
        return prepare(self.pdf, self.root/'exports', **(self.options | changes))

    def test_long_guideline_partial_failure_resume_and_immutable_reader(self):
        fixture_pdf(self.pdf)
        source_sha = digest(self.pdf.read_bytes())
        folder = self.prepare()
        plan = json.loads((folder/'manifest.json').read_text())
        self.assertEqual(plan['split_method'], 'outline')
        jobs = plan['jobs']
        self.assertGreater(len(jobs), 6)
        covered = {p for j in jobs for p in range(j['start'], j['end']+1)}
        self.assertEqual(covered, set(range(1,65)))
        child = next(j for j in jobs if j['title']=='2.1 Subgroup')
        self.assertEqual(child['parent'], jobs[1]['id'])
        for job in jobs:
            packet = PdfReader(folder/job['id']/'source.pdf')
            self.assertLessEqual(len(packet.pages), 10)
            self.assertIn(f"Fixture page {job['start']}:", packet.pages[0].extract_text())
        for job in jobs[:-1]:
            (folder/job['id']/'response.json').write_text(dump(response(folder, job)))
        bad = folder/jobs[1]['id']/'response.json'
        bad.write_text('{')
        partial = resume(folder)
        self.assertIn(jobs[1]['id'], partial['errors'])
        self.assertEqual(partial['pending'], [jobs[-1]['id']])
        self.assertEqual(partial['accepted'], len(jobs)-2)
        self.assertFalse((folder/'editions').exists())
        checkpoint = folder/jobs[0]['id']/'accepted.json'
        stamp = checkpoint.stat().st_mtime_ns
        (folder/jobs[0]['id']/'response.json').unlink()
        self.assertEqual(folder, self.prepare())
        for job in (jobs[1],jobs[-1]):
            (folder/job['id']/'response.json').write_text(dump(response(folder, job)))
        report = resume(folder)
        master = json.loads(Path(report['master']).read_text())
        html = Path(report['reader']).read_text()
        self.assertEqual(master['metadata']['pdf_sha256'], source_sha)
        self.assertFalse(master['quality_control']['human_checked'])
        self.assertIsNone(master['overview']['practice_change'])
        self.assertEqual(len(master['chapters']), len(jobs))
        self.assertEqual(len(master['evidence']), len(jobs))
        self.assertEqual(master['evidence'][0]['conditions'], ['合成資料のみ'])
        self.assertIn('pp.1–3', html)
        self.assertIn('Fixture page 1: 15%', html)
        self.assertEqual(paper_from_json(Path(report['master']).read_bytes()).title, TITLE)
        self.assertEqual(report, resume(folder))
        self.assertEqual(stamp, checkpoint.stat().st_mtime_ns)
        self.assertEqual(source_sha, digest(self.pdf.read_bytes()))
        # A corrected result gets a new edition; the earlier edition stays readable.
        corrected = response(folder, jobs[1])
        corrected['title_ja'] = '訂正版'
        bad.write_text(dump(corrected))
        revised = resume(folder, revise=[jobs[1]['id']])
        self.assertNotEqual(revised['reader'], report['reader'])
        self.assertEqual(Path(report['reader']).read_text(), html)

    def test_bad_binding_range_quote_and_numeric_suffix_rejected(self):
        fixture_pdf(self.pdf, 6)
        folder = self.prepare()
        jobs = json.loads((folder/'manifest.json').read_text())['jobs']
        job = jobs[0]
        source = folder/job['id']/'response.json'
        for mutate in [lambda r:r.update(job_key='wrong'), lambda r:r.update(chapter_id=jobs[1]['id']),
                       lambda r:r['sources'][0].update(page=6), lambda r:r['sources'][0].update(page=2),
                       lambda r:r['sources'][0].update(quote='absent'),
                       lambda r:r['findings'][0]['measurements'][0].update(reported='5%'),
                       lambda r:r['findings'][0].update(source_numbers=[2])]:
            result = response(folder, job)
            mutate(result)
            source.write_text(dump(result))
            self.assertIn(job['id'], resume(folder)['errors'])
            self.assertFalse((source.parent/'accepted.json').exists())
        result = response(folder, job)
        result['sources'][0].update(visual_only=True)
        source.write_text(dump(result))
        self.assertEqual(resume(folder)['accepted'], 0)
        self.assertEqual(resume(folder, confirm_visual=[job['id']])['accepted'], 1)

    def test_manual_shared_page_text_fallback_density_and_source_replacement(self):
        fixture_pdf(self.pdf, 8, outline=False)
        r = PdfReader(self.pdf)
        mode, jobs, _ = chapter_plan(r, max_pages=8)
        self.assertEqual(mode, 'text_headings')
        self.assertIn('2.1 Subgroup', [j['title'] for j in jobs])
        self.assertFalse(any('patients' in j['title'] for j in jobs))
        headings = [dict(title='A',page=2), dict(title='B',page=2,level=2), dict(title='C',page=4)]
        mode, jobs, _ = chapter_plan(r, headings=headings, max_chars=1)
        self.assertEqual(mode,'manual')
        self.assertTrue(all(j['start']==j['end'] for j in jobs))
        self.assertEqual({j['start'] for j in jobs},set(range(1,9)))
        self.assertEqual(next(j for j in jobs if j['title']=='A')['end'],2)
        with self.assertRaises(ValueError):
            chapter_plan(r, headings=list(reversed(headings)))
        folder = self.prepare()
        other = self.prepare(headings=headings)
        self.assertNotEqual(folder, other)
        fixture_pdf(self.pdf, 7)
        self.assertNotEqual(self.prepare(), folder)

    def test_page_shared_between_two_headings_has_distinct_quote_windows(self):
        texts = ['continued paragraph\nMethods 12\nMethod A.\nResults 14\nResult B.']
        job = dict(id='ch0001', start=1, end=1, start_heading='Methods', next_heading='Results')
        window = source_window(texts, job)
        self.assertEqual(window, ['Methods 12\nMethod A.\n'])
        from paper_pipeline.chapter_review import packet_key
        result = dict(chapter_id=job['id'], job_key=packet_key('run',job), title_ja='方法', summary_ja='要旨',
                      method='manual_local', findings=[], sources=[dict(page=1,quote='Result B.')])
        with self.assertRaisesRegex(ValueError, '照合'):
            validate_result(result, 'run', job, window, False)
        result['sources'][0]['quote'] = 'Method A.'
        validate_result(result, 'run', job, window, False)

    def test_restore_missing_packet_assets_and_reject_changed_original(self):
        fixture_pdf(self.pdf, 4)
        old = self.prepare()
        job = json.loads((old/'manifest.json').read_text())['jobs'][0]
        returned = dump(response(old, job))
        packet = old/job['id']
        (packet/'response.json').write_text(returned)
        (packet/'source.pdf').unlink()
        self.assertIn(job['id'], resume(old)['errors'])
        self.assertEqual(self.prepare(), old)
        self.assertEqual((packet/'response.json').read_text(), returned)
        self.assertEqual(resume(old)['accepted'], 1)
        fixture_pdf(self.pdf, 6)
        new = self.prepare()
        (new/job['id']/'response.json').write_text(returned)
        self.assertIn(job['id'], resume(new)['errors'])
        self.assertFalse((new/job['id']/'accepted.json').exists())

    def test_duplicate_sources_merge_but_different_conditions_survive(self):
        fixture_pdf(self.pdf, 1)
        folder = self.prepare(headings=[dict(title='A',page=1), dict(title='B',page=1)])
        jobs = json.loads((folder/'manifest.json').read_text())['jobs']
        for job in jobs:
            result = response(folder, job)
            if job == jobs[1]:
                result['findings'].append(dict(result['findings'][0],conditions=['別の条件']))
            (folder/job['id']/'response.json').write_text(dump(result))
        master = json.loads(Path(resume(folder)['master']).read_text())
        self.assertEqual(len([s for s in master['source_references'] if not s['id'].startswith('range-')]), 1)
        self.assertEqual(len(master['evidence']), 2)
        self.assertEqual(len(master['chapters'][1]['blocks']), 2)
