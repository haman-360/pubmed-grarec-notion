import http.client
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
import test_workspace_library as fixtures
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from paper_pipeline.dashboard import Dashboard
from paper_pipeline.store import encode
from grarec_app import Application,Handler,ThreadingHTTPServer

class DashboardTests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.LibraryTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.f=fixture; self.store=fixture.store
        (fixture.repo/'input/pdfs').mkdir(parents=True)
        self.dash=Dashboard(fixture.repo,self.store.root)
    def candidate(self):
        path=self.f.png('candidate.png','white')
        (self.dash.inbox/'PMID_90000001_main.png').write_bytes(path.read_bytes())
        return self.dash.candidates(self.store)[0]
    def test_snapshot_counts_latest_only_and_inbox_mapping(self):
        self.candidate(); s=self.dash.snapshot(self.store)
        self.assertEqual(s['counts']['total'],1)
        self.assertEqual(s['counts']['json_ready'],1)
        self.assertEqual(s['counts']['inbox_pending'],1)
        self.assertEqual(s['candidates'][0]['topic'],'main')
    def test_add_validates_all_ids_before_writing_and_deduplicates(self):
        with self.assertRaises(ValueError): self.dash.run('add',{'pmids':'90000002\nbad'},self.store)
        self.assertEqual(len(self.store.rows('SELECT * FROM papers')),1)
        self.dash.run('add',{'pmids':'90000002\n90000002'},self.store)
        self.assertEqual(len(self.store.rows('SELECT * FROM papers')),2)
    def test_search_skips_already_attempted_and_held_records(self):
        old=self.store.paper(pmid='90000002'); new=self.store.paper(pmid='90000003')
        with self.store.db: self.store.db.execute('INSERT INTO searches(paper_id,checked_at,result) VALUES(?,?,?)',(old['id'],'today','{}'))
        with patch('paper_pipeline.dashboard.Sources') as cls:
            self.dash.run('search',{},self.store)
            self.assertEqual([c.args[0]['id'] for c in cls.return_value.discover.call_args_list],[new['id']])
    def test_unconfirmed_or_changed_png_cannot_be_sent(self):
        item=self.candidate(); data={'items':[item],'confirmed':False,'reviewer':'Tester','note':'Synthetic check'}
        with patch('paper_pipeline.dashboard.Notion') as client:
            with self.assertRaises(ValueError): self.dash.run('upload',data,self.store)
            data['confirmed']=True
            (self.dash.inbox/item['name']).write_bytes(b'changed')
            with self.assertRaises(ValueError): self.dash.run('upload',data,self.store)
            client.assert_not_called()
    def test_bulk_import_approval_sync_and_already_synced_skip(self):
        item=self.candidate(); data={'items':[item],'confirmed':True,'reviewer':'Tester','note':'Synthetic check'}
        def sync(r):
            g=self.store.rows('SELECT * FROM graphics WHERE revision_id=? AND active=1',(r['id'],))[0]
            self.assertEqual(g['status'],'IMAGE_APPROVED')
            self.store.update('graphics',g['id'],status='COMPLETED')
        with patch('paper_pipeline.dashboard.Notion') as client, patch('paper_pipeline.dashboard.NotionAPI'):
            client.return_value.sync.side_effect=sync
            self.dash.run('upload',data,self.store)
            self.assertTrue(self.dash.candidates(self.store)[0]['synced'])
            with self.assertRaises(ValueError): self.dash.run('upload',data,self.store)
            self.assertEqual(client.return_value.sync.call_count,1)
    def test_budget_update_preserves_quality_hold(self):
        self.store.set_setting('budget',{'suspended_reason':'hold'})
        self.dash.run('budget',dict(start='2026-11-01',end='2026-12-01T00:00:00+09:00',remaining=10,confirmed=True),self.store)
        self.assertEqual(self.store.setting('budget')['suspended_reason'],'hold')
    def test_api_requires_token_and_loopback_host(self):
        app=Application(self.f.repo,self.store.root)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.app=app
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def get(headers):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port)
            c.request('GET','/api/state',headers=headers);r=c.getresponse();body=r.read();c.close();return r.status,body
        self.assertEqual(get({})[0],403)
        self.assertEqual(get({'X-App-Token':app.token,'Host':'attacker.example'})[0],403)
        status,body=get({'X-App-Token':app.token})
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['counts']['total'],1)
