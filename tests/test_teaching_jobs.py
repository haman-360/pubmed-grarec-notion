"""Exercise the desktop PMID workflow with a real split PDF and offline API transports."""
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from grarec_app import Application
from paper_pipeline import json_batch, materials, notion_sync, teaching_jobs as jobs
from paper_pipeline.manual import Workspace, save_settings
from paper_pipeline.teaching import FIELD_KEYS
from test_chapter_review import fixture_pdf, response
from test_materials import EmbeddedNotion
from test_paper_pipeline import FakeOpenAI, sample_summary
from paper_pipeline.graphics import adapt_legacy


class CountedAPI(FakeOpenAI):
    def count_input(self, body):
        return 1500


class TeachingFlow(unittest.TestCase):
    pmid = '90000001'

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        self.ws = Workspace.create(self.repo/'shared', 'a'*32, 'API Mac')
        save_settings(self.repo,dict(shared_root=str(self.ws.root),device_id=self.ws.device_id,device_name='API Mac'))
        self.pdf = self.repo/'fixture.pdf'
        fixture_pdf(self.pdf, 4)
        self.api = CountedAPI()
        self.notion = EmbeddedNotion()
        for obj,name,value in ((json_batch,'api_key','fixture'),(json_batch,'JsonAPI',self.api),
                               (notion_sync,'SafeNotionAPI',self.notion),(notion_sync,'credentials',
                                dict(NOTION_TOKEN='fixture', NOTION_DATABASE_ID='db', NOTION_DATA_SOURCE_ID=''))):
            mock = patch.object(obj,name,return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        guard = patch('requests.Session.request', side_effect=AssertionError('real network forbidden'))
        guard.start()
        self.addCleanup(guard.stop)
        self.budget()
        with notion_sync.ledger(self.repo,self.ws,claim=True):
            pass
        self.app = Application(self.repo)
        self.intent = dict(pmid=self.pmid, paid=True, notion=True)

    def budget(self, remaining=10):
        json_batch.set_budget(self.repo,self.ws,dict(remaining=remaining,other_usage=0,confirmed=True,
                              end=(datetime.now(timezone.utc)+timedelta(days=30)).date().isoformat()))

    def attach(self):
        self.ws.add(self.pmid)
        self.ws.import_file(self.pmid,'pdf','original.pdf',self.pdf.read_bytes())
        from paper_pipeline.pubmed import ArticleMetadata
        jobs.reading_guide.save_metadata(self.ws, ArticleMetadata(self.pmid,title='Synthetic paper',
                                             publication_types=('Observational Study',),year='2025'))

    def complete(self, batch, data):
        body = dict(status='completed',output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(data))])],
                    usage=dict(input_tokens=111,output_tokens=222))
        self.api.complete(dict(batch_id=batch['id'],id=batch['metadata']['attempt_id']),body)

    def run_folder(self):
        return next((json_batch.local_root(self.repo,self.ws)/'teaching'/('PMID_'+self.pmid)).iterdir())

    def test_pmid_to_pdf_wait_to_chapters_to_kit_across_restart(self):
        with patch.object(jobs.pdf_download,'download_selected',return_value={'items':[{'reason':'手動PDF待ち'}]}) as fetch:
            self.app.run('teaching-request',self.intent)
            fetch.assert_called_once()
        self.assertEqual(self.api.posts,0)
        (self.ws.incoming(self.pmid)/'PDF'/'original.pdf').write_bytes(self.pdf.read_bytes())
        from paper_pipeline.pubmed import ArticleMetadata
        jobs.reading_guide.save_metadata(self.ws,ArticleMetadata(self.pmid,title='Synthetic',publication_types=('Observational Study',)))
        self.api.timeout_after_create = True
        self.app.run('teaching-poll',{})
        run = self.run_folder()
        plan = jobs.read(run/'manifest.json')
        self.assertEqual(self.api.posts,2)
        self.app = Application(self.repo)
        self.app.run('teaching-poll',{})
        self.assertEqual(self.api.posts,2)
        self.api.timeout_after_create = False
        for packet, batch in zip(plan['jobs'], self.api.batches.values()):
            result = response(run,packet)
            result['method'] = 'api_batch'
            result['findings'][0]['study'] = dict(reference='Fixture', design='test',population='test',exposure='test',outcome='test')
            self.complete(batch,result)
        # The original JSON collection button must leave chapter responses to the teaching collector.
        json_batch.collect(self.repo,self.ws,self.api)
        self.assertFalse(list(run.glob('ch*/response.json')))
        self.app.run('teaching-poll',{})
        dest = next((run/'synthesis').iterdir())
        ctx = jobs.read(dest/'context.json')
        overview = ctx['master']['overview']
        overview.update(one_line_summary='テスト用', topic=['教材'],pico=dict(p='test',i_or_exposure='test',c='未記載',o='test'))
        fields = {k:dict(status='not_assessed',reason='試験',evidence_ids=[]) for k in FIELD_KEYS}
        for k in ('one_line_summary','topic','abstract','pico'):
            fields[k] = dict(status='recorded',evidence_ids=['e1'])
        answer = dict(basis=dest.name,fields=fields,overview=overview,abstract_parts=[
            dict(page=1,fragments=['Fixture page 1:'],translation_ja='試験')],clinical_interpretation=[],
            visualizations=[],units=[dict(title='試験',chapter_ids=[p['id'] for p in plan['jobs']])])
        self.complete(list(self.api.batches.values())[-1],answer)
        self.app = Application(self.repo)
        self.app.run('teaching-poll',{})
        kit = materials.current(self.ws,self.pmid)
        self.assertEqual(kit['page_id'],next(iter(self.notion.pages)))
        out = materials.checked_folder(self.ws,kit['version'],self.pmid)
        master = jobs.read(out/'master.json')
        self.assertEqual(master['generation_metadata']['method'],'api_batch')
        self.assertEqual(master['quality_control']['human_checked'],False)
        self.assertIn(b'<!doctype html>',(out/materials.HTML).read_bytes())
        self.assertTrue(list(out.glob('png-request-*.json')))
        self.app.run('teaching-request',self.intent)
        self.app.run('teaching-poll',{})
        self.assertEqual(self.api.posts,3)
        self.assertEqual(len(self.notion.pages),1)
        self.assertEqual(len(self.notion.uploads),2)
        with json_batch.ledger(self.repo,self.ws) as store:
            attempts=store.rows('SELECT * FROM attempts')
            self.assertEqual(len({a['revision_id'] for a in attempts}),1)
            self.assertTrue(all(a['actual']>0 for a in attempts))

        saved = adapt_legacy(sample_summary(dict(pmid=self.pmid, title='Existing summary', doi='')))
        self.ws.import_file(self.pmid, 'json', 'saved.json', json.dumps(saved).encode())
        self.app.run('teaching-request', dict(self.intent, route='existing', paid=False))
        summary = materials.current(self.ws, self.pmid, 'existing')
        self.assertEqual(summary['page_id'], kit['page_id'])
        self.assertEqual(materials.current(self.ws, self.pmid), kit)
        self.assertEqual(len(self.notion.pages), 1)
        self.assertEqual([b['type'] for b in self.notion.blocks[kit['page_id']]], ['heading_2','embed','pdf']*2)

        # A previously assembled edition also works on a Mac without an API ledger.
        cached = Workspace.create(self.repo/'cached', 'c'*32, 'Reader')
        cached.add(self.pmid)
        cached.import_file(self.pmid,'pdf','original.pdf',self.pdf.read_bytes())
        shutil.copytree(run, self.repo/'output/chapter_reviews'/('PMID_'+self.pmid)/run.name)
        jobs.request(self.repo,cached,dict(pmid=self.pmid,paid=False,notion=False))
        self.assertEqual(materials.current(cached,self.pmid)['version'],kit['version'])
        self.assertEqual(self.api.posts,3)

    def test_consent_budget_and_wrong_device(self):
        self.attach()
        other=Workspace(self.ws.root,'b'*32,'Other')
        jobs.request(self.repo,other,dict(self.intent,paid=False))
        jobs.advance(self.repo,self.ws,self.pmid,self.api)
        self.assertEqual(self.api.files,{})
        self.budget(0)
        jobs.request(self.repo,other,self.intent)
        jobs.advance(self.repo,other,self.pmid,self.api)
        self.assertEqual(self.api.files,{})
        jobs.advance(self.repo,self.ws,self.pmid,self.api)
        self.assertEqual(self.api.files,{})
        self.assertEqual(self.api.posts,0)
        self.assertTrue(jobs.progress(self.ws)[0]['held'])
        self.budget()
        self.app.run('teaching-request',self.intent)
        self.assertEqual(self.api.posts,2)

    def test_pending_settlement_keeps_teaching_poll_enabled(self):
        self.attach()
        jobs.request(self.repo, self.ws, self.intent)
        with patch.object(jobs, 'process', side_effect=jobs.PendingBudgetSettlement('予約精算待ち')):
            jobs.advance(self.repo, self.ws, self.pmid, self.api)
        row, = jobs.progress(self.ws)
        self.assertFalse(row['held'])
        self.assertIn('自動で再判定', row['message'])
        jobs.advance(self.repo, self.ws, self.pmid, self.api)
        self.assertEqual(self.api.posts, 2)

    def test_selected_requests_preserve_existing_intent_and_skip_stale_rows(self):
        self.attach()
        other = '90000002'
        stale = '90000003'
        self.ws.add(other)
        self.ws.add(stale)
        batch = dict(items=[dict(pmid=stale, heads=['old']),
                            dict(pmid=self.pmid, heads=[self.ws.current(self.pmid)['id']]),
                            dict(pmid=other, heads=[self.ws.current(other)['id']])],
                     paid=False, notion=True)
        with patch.object(jobs.pdf_download, 'download_selected',
                          return_value={'items':[{'reason':'手動PDF待ち'}]}) as fetch:
            result = self.app.run('teaching-request-selected', batch)
            self.assertIn('要確認 1本', result)
            self.assertIn(stale, result)
            fetch.assert_called_once()
            original = jobs.read(jobs.directory(self.ws, self.pmid)/'request.json')
            self.assertIs(original['notion'], True)
            self.assertIs(original['paid'], False)
            self.assertEqual(set(jobs.requests_list(self.ws)), {self.pmid, other})
            result = self.app.run('teaching-request-selected', dict(batch, paid=True, notion=False))
            self.assertIn('要確認 3本', result)
            self.assertEqual(jobs.read(jobs.directory(self.ws, self.pmid)/'request.json'), original)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(self.api.posts, 0)

    def test_existing_json_and_master_requests_can_coexist(self):
        self.attach()
        data = adapt_legacy(sample_summary(dict(pmid=self.pmid, title='Saved findings', doi='')))
        raw = json.dumps(data).encode()
        event = self.ws.import_file(self.pmid, 'json', 'saved.json', raw)
        batch = dict(items=[dict(pmid=self.pmid, heads=[event['id']])], route='existing', paid=False, notion=True)
        # Simulate a different Mac preparing the kit before the Notion owner picks it up.
        other = Workspace(self.ws.root, 'b'*32, 'Reader')
        with patch.object(jobs, 'process', side_effect=AssertionError('no paid reading')), \
                patch.object(jobs.pdf_download, 'download_selected', side_effect=AssertionError('no PDF search')):
            self.assertIn('要確認 0本', jobs.request_selected(self.repo, other, batch))
            self.assertFalse(self.notion.pages)
            self.app.run('teaching-poll', {})
            self.app.run('teaching-poll', {})
        rows = jobs.progress(self.ws)
        self.assertEqual(len(rows), 1)
        kit = rows[0]
        self.assertEqual(kit['route'], 'existing')
        self.assertTrue(kit['page_id'])
        folder = materials.checked_folder(self.ws, kit['version'], self.pmid, 'existing')
        self.assertEqual((folder/'source.json').read_bytes(), raw)
        self.assertIn('Saved findings', (folder/materials.HTML).read_text())
        self.assertIn('既存グラレコJSON版', (folder/materials.HTML).read_text())
        self.assertFalse((folder/'master.json').exists())
        self.assertEqual(materials.current(self.ws, self.pmid), {})
        self.assertEqual(self.api.posts, 0)
        self.assertEqual(len(self.notion.uploads), 2)
        self.assertEqual([b['type'] for b in self.notion.blocks[kit['page_id']]], ['heading_2','embed','pdf'])
        self.assertIn('要確認 1本', self.app.run('teaching-request-selected', dict(batch, notion=False)))
        self.assertTrue(materials.current(self.ws, self.pmid, 'existing')['notion'])
        self.app.run('teaching-request-selected', dict(batch, route='master', paid=True))
        self.assertGreater(self.api.posts, 0)
        self.assertEqual(materials.current(self.ws, self.pmid, 'existing')['version'], kit['version'])
        self.assertEqual(len(jobs.progress(self.ws)), 2)

    def test_existing_route_never_falls_back_and_supports_legacy_json_without_pdf(self):
        self.ws.add(self.pmid)
        intent = dict(pmid=self.pmid, route='existing', paid=False, notion=False)
        with self.assertRaises(ValueError):
            self.app.run('teaching-request', intent)
        self.assertEqual(jobs.requests_list(self.ws), [])
        self.assertFalse(jobs.existing_pmids(self.ws))
        event = self.ws.current(self.pmid)
        raw = json.dumps(dict(PMID=self.pmid, title='Old paper', main_results='Recorded finding')).encode()
        event['data']['files'] = [self.ws.asset(self.pmid,'json','old.json',raw)]
        self.ws.record(self.pmid, event['data'], [event['id']])
        self.app.run('teaching-request', intent)
        kit = materials.current(self.ws,self.pmid,'existing')
        self.assertFalse(kit['has_pdf'])
        self.assertFalse(self.notion.pages)
        for _ in range(2):
            self.app.run('materials-register',dict(pmid=self.pmid,route='existing',version=kit['version']))
        self.assertEqual(len(self.notion.pages), 1)
        self.assertEqual(len(self.notion.uploads), 1)
        self.assertEqual(self.api.posts, 0)
