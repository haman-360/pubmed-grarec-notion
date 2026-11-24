"""Contract tests for resumable specialist reading, synthesis, and legacy PNG export."""
import copy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from paper_pipeline.chapter_run import dump, prepare, resume
from paper_pipeline.chapter_review import build_master, validate_result
from paper_pipeline.graphics import validate_master
from paper_pipeline.reader_data import paper_from_json
from paper_pipeline.teaching import FIELD_KEYS, PACKET
from test_chapter_review import fixture_pdf, response


class TeachingTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pdf = Path(tmp.name) / 'original.pdf'
        fixture_pdf(self.pdf, 1)
        self.folder = prepare(self.pdf, Path(tmp.name)/'out', pmid='90000001', title='Fixture',
                              article_type='narrative_review', learning=True)
        self.plan = json.loads((self.folder/'manifest.json').read_bytes())
        self.job = self.plan['jobs'][0]
        self.result = response(self.folder, self.job)
        self.result['findings'][0].update(study=dict(reference='Fixture 1',design='観察',population='試験集団',
                                                    exposure='試験曝露',outcome='試験結果'),result_key='fixture')
        self.reply = self.folder/self.job['id']/'response.json'
        self.reply.write_text(dump(self.result))

    def synthesis(self):
        report = resume(self.folder)
        self.assertTrue(report['pending'])
        dest = Path(report['synthesis'])
        ctx = json.loads((dest/'context.json').read_bytes())
        overview = ctx['master']['overview']
        overview.update(one_line_summary='試験要旨', topic=['教材'])
        fields = {k:dict(status='not_assessed',reason='試験では未評価',evidence_ids=[]) for k in FIELD_KEYS}
        for k in ('one_line_summary','topic','abstract'):
            fields[k] = dict(status='recorded',evidence_ids=['e1'])
        data = dict(basis=dest.name,fields=fields,overview=overview,abstract_parts=[
            dict(page=1,fragments=['Fixture page 1:', '15% in the sample.'],translation_ja='試験用。')],
            clinical_interpretation=[],visualizations=[],units=[dict(title='学習単元',chapter_ids=[self.job['id']])])
        return dest,data

    def test_synthesis_hash_status_and_offline_export(self):
        dest, data = self.synthesis()
        reply = dest/'response.json'
        reply.write_text(dump(dict(data,basis='stale')))
        with self.assertRaisesRegex(ValueError,'版'):
            resume(self.folder)
        broken = copy.deepcopy(data)
        broken['fields']['limitations'].update(status='recorded',evidence_ids=['e1'])
        reply.write_text(dump(broken))
        with self.assertRaisesRegex(ValueError,'limitations'):
            resume(self.folder)
        broken = copy.deepcopy(data)
        broken['abstract_parts'][0]['fragments'].reverse()
        reply.write_text(dump(broken))
        with self.assertRaisesRegex(ValueError,'逐語'):
            resume(self.folder)
        reply.write_text(dump(data))
        with self.assertRaisesRegex(ValueError,'SHA-256'):
            resume(self.folder,pdf=b'other file')
        report = resume(self.folder,pdf=self.pdf.read_bytes())
        edition = Path(report['reader']).parent
        self.assertEqual(report['assessment'],'評価待ち')
        self.assertEqual(report,resume(self.folder,pdf=self.pdf.read_bytes()))
        check = json.loads((edition/'master.json').read_bytes())
        self.assertEqual(check['abstract']['parts'][0]['original'],'Fixture page 1: 15% in the sample.')
        self.assertEqual(paper_from_json((edition/'master.json').read_bytes()).teaching_master,check)
        self.assertIn('Evidence View',(edition/'reader.html').read_text())
        validate_master(json.loads((edition/'png-master.json').read_bytes()))
        self.assertTrue(list(edition.glob('png-request-*.json')))
        assessment = Path(report['quality_template'])
        ratings = json.loads(assessment.read_bytes())
        ratings['quality']['医学的正確性'] = dict(status='要修正',reviewer='試験者',note='原文支持を再確認')
        assessment.with_name('response.json').write_text(dump(ratings))
        self.assertEqual(resume(self.folder)['assessment'],'評価待ち')
        revised = resume(self.folder,pdf=self.pdf.read_bytes())
        self.assertEqual(revised['assessment'],'要修正')
        self.assertTrue((edition/'master.json').exists())
        self.result['summary_ja'] = '改訂'
        self.reply.write_text(dump(self.result))
        self.assertNotEqual(resume(self.folder,revise=[self.job['id']])['synthesis'],str(dest))

    def test_linked_sources_cannot_merge_different_outcomes(self):
        other = copy.deepcopy(self.result)
        other['sources'][0]['quote'] = '15% in the sample.'
        plan = dict(self.plan,jobs=[self.job,dict(self.job,id='ch0002')])
        m = build_master(plan,[self.result,other],self.folder.name,{})
        self.assertEqual(len(m['evidence']),1)
        self.assertEqual(len(m['evidence'][0]['measurements'][0]['source_refs']),2)
        other['findings'][0]['study']['outcome'] = '異なる評価項目'
        with self.assertRaisesRegex(ValueError,'解析'):
            build_master(plan,[self.result,other],self.folder.name,{})

    def test_ratio_matches_its_interval_and_metric(self):
        quote = 'aHR=1.25, 95% CI=0.77–2.04'
        self.result['sources'] = [dict(page=1,quote=quote)]
        measure = dict(label='aHR',reported=quote,estimate='1.25',ci_lower='0.77',ci_upper='2.04',ci_level='95%')
        self.result['findings'][0]['measurements']=[measure]
        def validate():
            validate_result(self.result,self.folder.name,self.job,[quote],False,PACKET)
        validate()
        for key,val in [('ci_lower','0.7'),('ci_upper','2.0'),('estimate','0.77'),('label','aOR')]:
            original = measure[key]
            measure[key] = val
            with self.assertRaises(ValueError):
                validate()
            measure[key] = original
