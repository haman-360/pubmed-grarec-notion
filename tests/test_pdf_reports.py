import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from paper_pipeline.manual import Workspace
from paper_pipeline.pdf_reports import difficulty, search_rows, export_csv
from paper_pipeline.store import atomic_write, encode

class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ws=Workspace.create(Path(self.tmp.name)/'shared','a'*32,'test')
    def save(self, name, method, at, entry, device='a'*32):
        atomic_write(self.ws.path('状況/'+device+'/PDF取得履歴/'+name+'.json'),encode(dict(id=name,method=method,at=at,finished_at=at,items=[entry])))
    def test_levels_keep_evidence_and_do_not_assume_paywall(self):
        self.assertEqual(difficulty(dict(status='not_found',manual_candidates=[dict(priority='1_high',evidence='PMC公開')])) ,('low','PMC公開'))
        self.assertEqual(difficulty(dict(status='not_found',observations=[dict(kind='browser',evidence='HTTP 403')])) ,('medium','HTTP 403'))
        level, reason=difficulty(dict(status='not_found',manual_urls=['https://pubmed.ncbi.nlm.nih.gov/123/']))
        self.assertEqual(level,'high');self.assertIn('断定',reason)
        self.assertEqual(difficulty(dict(status='error'))[0],'unknown')
        self.assertEqual(difficulty(dict(status='candidate'))[0],'low')
        self.assertEqual(difficulty(dict(status='not_found',observations=[dict(kind='purchase',evidence='purchase access')]))[0],'high')
    def test_latest_per_method_across_devices_and_csv(self):
        entry=dict(pmid='123',title='=bad,title',status='not_found',manual_urls=['https://example.org/a'],manual_candidates=[])
        self.save('old','free','2026-01-01T00:00:00+00:00',entry)
        self.save('paid','paid','2026-01-02T00:00:00+00:00',entry,device='b'*32)
        self.save('new','free','2026-01-03T00:00:00+00:00',dict(entry,status='downloaded'))
        rows=search_rows(self.ws)
        self.assertEqual(len(rows),2)
        self.assertEqual({r['method']:r['status'] for r in rows},{'free':'downloaded','paid':'not_found'})
        folder=export_csv(self.ws)
        for method in ('無料','有料'):
            path=folder/(method+'_PDF検索結果.csv')
            self.assertTrue(path.read_bytes().startswith(b'\xef\xbb\xbf'))
            output=list(csv.reader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
            self.assertEqual(len(output),2);self.assertEqual(output[1][1],"'=bad,title")
    def test_api_start_does_not_replace_finished_download_result(self):
        entry=dict(pmid='123',title='title',status='downloaded',manual_urls=[])
        self.save('paid','paid','2026-01-02T00:00:00+00:00',entry)
        atomic_write(self.ws.path('API_PDF/PMID_123.json'),encode(dict(pmid='123',at='2026-01-01T00:00:00+00:00',status='completed',urls=[])))
        self.assertEqual(search_rows(self.ws)[0]['status'],'downloaded')
    def test_unfinished_api_is_visible_and_no_request_is_made(self):
        atomic_write(self.ws.path('API_PDF/PMID_123.json'),encode(dict(pmid='123',at='2026-01-01T00:00:00+00:00',status='unknown',urls=[])))
        rows=search_rows(self.ws)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['difficulty'],'unknown')
