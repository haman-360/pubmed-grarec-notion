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
        self.store.set_setting('budget',{'suspended_reason':'hold','image_suspended_reason':'image hold','analysis_suspended_reason':'analysis hold'})
        self.dash.run('budget',dict(start='2026-11-01',end='2026-12-01T00:00:00+09:00',remaining=10,confirmed=True),self.store)
        self.assertEqual(self.store.setting('budget')['suspended_reason'],'hold')
        self.assertEqual(self.store.setting('budget')['image_suspended_reason'],'image hold')
        self.assertEqual(self.store.setting('budget')['analysis_suspended_reason'],'analysis hold')
    def test_json_button_ignores_only_image_hold(self):
        self.store.set_setting('budget',{'image_suspended_reason':'image hold'})
        state=self.dash.snapshot(self.store)
        self.assertFalse(state['stop'])
        self.assertEqual(state['image_stop'],'image hold')
        self.store.update('revisions',self.f.revision['id'],summary=None,status='analysis_ready')
        item=self.dash.snapshot(self.store)['papers'][0]['json_target']
        with patch.dict('os.environ',{'OPENAI_API_KEY':'synthetic'}), patch('paper_pipeline.dashboard.API'), patch('paper_pipeline.dashboard.Batches') as runner:
            self.dash.run('json',{'items':[item]},self.store)
            runner.return_value.prepare_revisions.assert_called_once_with(paper_ids=[self.f.paper['id']])
            runner.return_value.submit.assert_called_once_with(1,revision_ids=[self.f.revision['id']])
            runner.return_value.collect.assert_not_called()
            self.store.set_setting('budget',{'analysis_suspended_reason':'analysis hold'})
            runner.reset_mock()
            with self.assertRaisesRegex(ValueError,'停止中'):
                self.dash.run('json',{'items':[item]},self.store)
            runner.return_value.submit.assert_not_called()

    def test_counts_partition_all_papers_and_target_excludes_completed(self):
        self.store.paper(pmid='90000002')
        held=self.store.paper(pmid='90000003')
        self.store.update('papers',held['id'],disposition='held')
        snapshot=self.dash.snapshot(self.store)
        keys=('missing_pdf','json_pending','json_running','web_pending','completed','attention')
        self.assertEqual(sum(snapshot['counts'][k] for k in keys),snapshot['counts']['total'])
        self.assertEqual(snapshot['counts']['web_pending'],1)
        self.assertEqual(snapshot['counts']['missing_pdf'],1)
        self.assertEqual(snapshot['counts']['attention'],1)
        self.store.update('revisions',self.f.revision['id'],status='completed')
        snapshot=self.dash.snapshot(self.store)
        self.assertEqual(snapshot['counts']['completed'],1)
        self.assertFalse(any(p['json_target'] for p in snapshot['papers']))

    def test_stale_or_ambiguous_json_selection_fails_before_api(self):
        self.store.update('revisions',self.f.revision['id'],summary=None,status='analysis_ready')
        item=self.dash.snapshot(self.store)['papers'][0]['json_target']
        with patch('paper_pipeline.dashboard.API') as api:
            for items in ([],[item]*2,[item]*6):
                with self.assertRaises(ValueError):self.dash.run('json',{'items':items},self.store)
            self.store.update('revisions',self.f.revision['id'],status='analysis_submitted')
            with self.assertRaisesRegex(ValueError,'変わりました'):
                self.dash.run('json',{'items':[item]},self.store)
            self.assertEqual(self.dash.snapshot(self.store)['counts']['json_running'],1)
            api.assert_not_called()

    def test_unknown_attempt_and_changed_source_excluded(self):
        r=self.f.revision
        self.store.update('revisions',r['id'],summary=None,status='analysis_ready')
        item=self.dash.snapshot(self.store)['papers'][0]['json_target']
        self.store.update('sources',self.f.source['id'],status='revision_review')
        self.assertIsNone(self.dash.snapshot(self.store)['papers'][0]['json_target'])
        with patch('paper_pipeline.dashboard.API') as api, self.assertRaises(ValueError):
            self.dash.run('json',{'items':[item]},self.store)
        api.assert_not_called()
        self.store.update('sources',self.f.source['id'],status='confirmed')
        with self.store.db:
            self.store.db.execute("INSERT INTO attempts(id,revision_id,stage,status,reserve,period,created_at) VALUES('pending',?,'analysis','unknown',1,'test','test')",(r['id'],))
        self.assertIsNone(self.dash.snapshot(self.store)['papers'][0]['json_target'])

    def test_search_then_preview_never_creates_json(self):
        with patch('paper_pipeline.dashboard.Sources'),patch('paper_pipeline.dashboard.API') as api:
            self.dash.run('auto',{},self.store)
            api.assert_not_called()

    def copy_item(self):
        p=self.dash.snapshot(self.store)['papers'][0]
        t=p['web_topics'][0]
        return dict(pmid=p['pmid'],graphic_id=t['id'],revision=t['revision'],topic=t['topic'],json_hash=t['json_hash'])

    def test_copy_prompt_matches_prepared_file_and_preserves_approval(self):
        item=self.copy_item()
        expected=(self.f.bundle/'ready_for_web/00_main_prompt.md').read_bytes()
        before=list(self.store.db.iterdump())
        with patch('paper_pipeline.dashboard.subprocess.run') as copy:
            result=self.dash.run('copy-prompt',item,self.store)
        self.assertEqual(copy.call_args.args[0],['/usr/bin/pbcopy'])
        self.assertEqual(copy.call_args.kwargs['input'],expected)
        self.assertIn('コピーしました：PMID 90000001',result)
        self.assertEqual(list(self.store.db.iterdump()),before)

    def test_copy_rejects_stale_topic_and_modified_prompt_before_clipboard(self):
        item=self.copy_item()
        with patch('paper_pipeline.dashboard.subprocess.run') as copy:
            for key in ('graphic_id','revision','topic','json_hash','pmid'):
                with self.assertRaises(ValueError):
                    self.dash.run('copy-prompt',dict(item,**{key:'stale'}),self.store)
            (self.f.bundle/'ready_for_web/00_main_prompt.md').write_text('wrong paper')
            with self.assertRaisesRegex(ValueError,'一致しません'):
                self.dash.run('copy-prompt',item,self.store)
            copy.assert_not_called()

    def test_copy_missing_file_and_clipboard_failure_report_error(self):
        item=self.copy_item()
        with patch('paper_pipeline.dashboard.subprocess.run',side_effect=OSError('unavailable')):
            with self.assertRaisesRegex(ValueError,'コピーできません'):
                self.dash.run('copy-prompt',item,self.store)
        (self.f.bundle/'ready_for_web/00_main_prompt.md').unlink()
        with patch('paper_pipeline.dashboard.subprocess.run') as copy, self.assertRaisesRegex(ValueError,'Web用準備'):
            self.dash.run('copy-prompt',item,self.store)
        copy.assert_not_called()

    def test_api_requires_token_and_loopback_host(self):
        from paper_pipeline.manual import Workspace
        from paper_pipeline.manual_migration import migrate
        shared=Workspace.create(self.f.repo/'shared', 'a'*32, 'Test Mac')
        migrate(shared,self.store.root)
        app=Application(self.f.repo,shared.root)
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
