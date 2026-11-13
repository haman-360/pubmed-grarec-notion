"""Offline tests of selected shared PNG registration and interrupted Notion writes."""
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pypdf import PdfWriter
from paper_pipeline import notion_sync as ns
from paper_pipeline.graphics import adapt_legacy
from paper_pipeline.manual import Workspace
from paper_pipeline.notion import rich
from paper_pipeline.store import Store, encode
from grarec_app import Application, Handler
from test_paper_pipeline import FakeNotion as BaseFakeNotion, sample_summary, png

PMID = '90000001'


class FakeNotion(BaseFakeNotion):
    def handle(self, method, path, **kwargs):
        result = super().handle(method, path, **kwargs)
        if path == '/data_sources/ds/query':
            archived = kwargs.get('json',{}).get('is_archived',False)
            result['results'] = [p for p in result['results'] if bool(p.get('is_archived',False)) == archived]
        return result


class NotionSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.repo=self.root/'repo';self.repo.mkdir()
        self.ws=Workspace.create(self.root/'shared','a'*32,'Test Mac');self.ws.add(PMID)
        writer=PdfWriter();writer.add_blank_page(width=300,height=300)
        output=io.BytesIO();writer.write(output);self.pdf=output.getvalue()
        self.master=adapt_legacy(sample_summary(dict(pmid=PMID,title='Synthetic paper',doi='')))
        self.ws.import_file(PMID,'pdf','source.pdf',self.pdf)
        self.ws.import_file(PMID,'json','master.json',encode(self.master).encode())
        self.ws.import_file(PMID,'png','main.png',png())
        self.api=FakeNotion()
        self.config=patch.object(ns,'credentials',return_value=dict(NOTION_TOKEN='synthetic-token',NOTION_DATABASE_ID='db',NOTION_DATA_SOURCE_ID=''))
        self.config.start();self.addCleanup(self.config.stop)
        self.network=patch('requests.Session.request',side_effect=AssertionError('real network forbidden'))
        self.network.start();self.addCleanup(self.network.stop)

    def rows(self,table):
        store=Store(ns.local_root(self.repo,self.ws))
        try:return store.rows('SELECT * FROM '+table)
        finally:store.close()

    def prepare(self):
        ns.prepare(self.repo,self.ws,[dict(pmid=PMID,heads=[self.ws.current(PMID)['id']])],self.api)
        return ns.status(self.repo,self.ws)['preview']

    def confirmation(self,preview):
        return dict(preview_id=preview['id'],confirmed=True,reviewer='Test Reviewer',note='原文・JSON・全PNGを照合',
                    reviewed=[image['review_id'] for item in preview['items'] for image in item['images']])

    def send(self):
        return ns.submit(self.repo,self.ws,self.confirmation(self.prepare()),self.api)

    def mutations(self):
        return [x for x in self.api.calls if x[0]!='GET' and not x[1].endswith('/query')]

    def existing_page(self,pmid=PMID,automation='',**fields):
        page=dict(id='page-existing',parent={'data_source_id':'ds'},properties={'Name':{'title':rich('Existing')},
                  'PMID':{'rich_text':rich(pmid)},'Automation ID':{'rich_text':rich(automation)},'Human Checked':{'checkbox':True}},**fields)
        self.api.pages[page['id']]=page;self.api.blocks[page['id']]=[]
        return page

    def test_preview_is_read_only_and_all_png_checks_required(self):
        preview=self.prepare();self.assertFalse(self.mutations())
        self.assertEqual(preview['target']['data_source_id'],'ds')
        data=self.confirmation(preview);data['reviewed']=[]
        with self.assertRaisesRegex(ValueError,'全PNG'):ns.submit(self.repo,self.ws,data,self.api)
        data=self.confirmation(preview);data['confirmed']=False
        with self.assertRaises(ValueError):ns.submit(self.repo,self.ws,data,self.api)
        self.assertFalse(self.mutations())
        self.assertEqual(self.ws.snapshot()['counts']['review_pending'],1)

    def test_registration_updates_same_workspace_and_no_openai(self):
        self.ws.add('90000002');untouched=self.ws.current('90000002')
        self.assertIn('1/1',self.send())
        current=self.ws.current(PMID)
        self.assertTrue(current['data']['done']);self.assertEqual(self.ws.snapshot()['counts']['completed'],1)
        self.assertEqual(current['data']['notion_registration']['reviewer'],'Test Reviewer')
        self.assertEqual(self.ws.current('90000002'),untouched)
        self.assertEqual(len(self.api.pages),1);self.assertEqual(len(self.api.uploads),3)
        self.assertFalse(next(iter(self.api.pages.values()))['properties']['Human Checked']['checkbox'])
        self.assertEqual(ns.status(self.repo,self.ws)['jobs'][0]['status'],'completed')
        self.assertEqual(self.rows('attempts'),[])

    def test_repeat_click_and_completed_paper_do_not_duplicate(self):
        preview=self.prepare();confirmation=self.confirmation(preview)
        ns.submit(self.repo,self.ws,confirmation,self.api);before=len(self.mutations())
        with self.assertRaises(ValueError):ns.submit(self.repo,self.ws,confirmation,self.api)
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(len(self.mutations()),before)

    def test_existing_page_and_legacy_automation_identity_are_preserved(self):
        page=self.existing_page(automation='legacy-automation-id')
        original=copy.deepcopy(page['properties'])
        self.assertIn('1/1',self.send())
        self.assertEqual(len(self.api.pages),1)
        self.assertEqual(self.rows('papers')[0]['id'],'legacy-automation-id')
        self.assertEqual(page['properties'],original)

    def test_multiple_existing_matches_block_before_writes(self):
        page=self.existing_page()
        other=copy.deepcopy(page);other['id']='page-second';self.api.pages[other['id']]=other
        with self.assertRaisesRegex(ValueError,'複数'):self.prepare()
        self.assertFalse(self.mutations())

    def test_archived_existing_page_is_not_duplicated(self):
        self.existing_page(is_archived=True)
        with self.assertRaisesRegex(ValueError,'アーカイブ'):self.prepare()
        self.assertFalse(self.mutations())

    def test_new_page_does_not_claim_clinical_approval_or_use_external_image_url(self):
        self.master.update(human_checked=True,graphic_url='https://example.invalid/untrusted.png')
        self.ws.import_file(PMID,'json','changed.json',encode(self.master).encode())
        self.ws.import_file(PMID,'png','main.png',png())
        self.api.properties['Graphic Image']={'type':'files'}
        self.api.properties['Graphic URL']={'type':'url'}
        self.send();page=next(iter(self.api.pages.values()))
        self.assertFalse(page['properties']['Human Checked']['checkbox'])
        self.assertNotIn('Graphic URL',page['properties'])
        self.assertEqual(page['properties']['Graphic Image']['files'][0]['type'],'file_upload')

    def test_secret_in_network_error_is_redacted_and_write_is_not_retried(self):
        import requests
        with patch('requests.Session.request',side_effect=requests.Timeout('secret-token-in-error')) as request:
            with self.assertRaises(RuntimeError) as error:
                ns.SafeNotionAPI('secret-token-in-error').call('POST','/pages',json={})
        self.assertNotIn('secret-token',str(error.exception))
        self.assertEqual(request.call_count,1)

    def test_all_topics_must_be_reviewed_before_any_upload(self):
        second=copy.deepcopy(self.master['topics'][0]);second.update(topic_id='second',topic_title='Second topic')
        self.master['topics'].append(second)
        self.master['visualization_strategy'].update(mode='topics',independent_question_count=2)
        self.ws.import_file(PMID,'json','two.json',encode(self.master).encode())
        self.ws.import_file(PMID,'png','main.png',png())
        self.ws.import_file(PMID,'png','second.png',png((1200,800)))
        preview=self.prepare();data=self.confirmation(preview);data['reviewed'].pop()
        with self.assertRaisesRegex(ValueError,'全PNG'):ns.submit(self.repo,self.ws,data,self.api)
        self.assertFalse(self.mutations())
        ns.submit(self.repo,self.ws,self.confirmation(preview),self.api)
        self.assertEqual(len(self.api.pages),1);self.assertEqual(len(self.api.uploads),6)
        self.assertTrue(self.ws.current(PMID)['data']['done'])

    def test_preview_endpoint_requires_token_current_head_and_known_png(self):
        app=Application(self.repo,self.ws.root)
        event=self.ws.current(PMID);image=next(f for f in event['data']['files'] if f['kind']=='png')
        handler=Handler.__new__(Handler);handler.server=SimpleNamespace(app=app,server_port=12345)
        handler.path='/api/png?pmid='+PMID+'&head='+event['id']+'&sha='+image['sha']
        handler.headers={'Host':'127.0.0.1:12345','X-App-Token':app.token};handler.reply=Mock()
        handler.do_GET();self.assertEqual(handler.reply.call_args.args,(200,self.ws.read_asset(image),'image/png'))
        handler.headers['X-App-Token']='wrong';handler.do_GET();self.assertEqual(handler.reply.call_args.args[0],403)
        handler.headers['X-App-Token']=app.token
        self.ws.update(PMID,[event['id']],'Changed after preview')
        handler.do_GET();self.assertEqual(handler.reply.call_args.args[0],409)

    def test_conflicting_pmid_found_by_doi_does_not_get_overwritten(self):
        self.master['doi']='10.1234/test'
        self.ws.import_file(PMID,'json','new.json',encode(self.master).encode());self.ws.import_file(PMID,'png','main.png',png())
        page=self.existing_page(pmid='90000002');page['properties']['DOI']={'rich_text':rich(self.master['doi'])}
        with self.assertRaisesRegex(ValueError,'矛盾'):self.prepare()
        self.assertFalse(self.mutations())

    def test_missing_schema_blocks_without_creating_properties(self):
        self.api.properties.pop('Automation ID')
        with self.assertRaisesRegex(ValueError,'自動変更'):self.prepare()
        self.assertFalse(self.mutations())

    def test_all_attachments_preflight_before_writes(self):
        self.api.file_limit=10
        with self.assertRaisesRegex(ValueError,'添付上限'):self.prepare()
        self.assertFalse(self.mutations())

    def test_changed_png_after_review_requires_new_confirmation(self):
        preview=self.prepare();before=len(self.mutations())
        self.ws.import_file(PMID,'png','main.png',png((1200,800)))
        with self.assertRaises(ValueError):ns.submit(self.repo,self.ws,self.confirmation(preview),self.api)
        self.assertEqual(len(self.mutations()),before)

    def test_unorganized_inputs_prevent_registration(self):
        path=self.ws.path('00_取り込み待ち/PMID_'+PMID+'/main.png');path.parent.mkdir(parents=True)
        path.write_bytes(png((1200,800)))
        with self.assertRaisesRegex(ValueError,'未整理'):self.prepare()
        self.assertFalse(self.api.calls)

    def test_missing_or_tampered_file_prevents_remote_calls(self):
        image=next(f for f in self.ws.current(PMID)['data']['files'] if f['kind']=='png')
        self.ws.path(image['path']).write_bytes(b'broken')
        with self.assertRaises(ValueError):self.prepare()
        self.assertFalse(self.api.calls)

    def test_page_creation_response_lost_reconciles_without_new_page(self):
        self.api.fail_after=('POST','/pages');self.assertIn('0/1',self.send())
        self.assertEqual(len(self.api.pages),1)
        self.assertFalse(self.ws.current(PMID)['data']['done'])
        self.assertIn('1/1',self.send())
        self.assertEqual(len(self.api.pages),1);self.assertEqual(len(self.api.uploads),3)

    def test_ambiguous_create_without_visible_page_never_reposts(self):
        self.api.fail_before=('POST','/pages');self.send();self.send()
        self.assertEqual(len([x for x in self.api.calls if x[:2]==('POST','/pages')]),1)
        self.assertFalse(self.ws.current(PMID)['data']['done'])

    def test_attachment_response_lost_reuses_upload_and_block(self):
        original=self.api.call
        fired=[False]
        def lost(method,path,**kwargs):
            result=original(method,path,**kwargs)
            children=kwargs.get('json',{}).get('children',[])
            if not fired[0] and any(b.get('type')=='image' for b in children):
                fired[0]=True;raise TimeoutError('accepted then disconnected')
            return result
        self.api.call=lost
        self.send();self.assertFalse(self.ws.current(PMID)['data']['done'])
        count=len(self.api.uploads);self.assertIn('1/1',self.send())
        self.assertEqual(len(self.api.uploads),count)
        images=[b for blocks in self.api.blocks.values() for b in blocks if b['type']=='image']
        self.assertEqual(len(images),1)

    def test_new_source_is_blocked_while_old_page_creation_is_ambiguous(self):
        self.api.fail_before=('POST','/pages');self.send()
        self.ws.import_file(PMID,'png','main.png',png((1200,800)))
        with self.assertRaisesRegex(ValueError,'旧版'):self.prepare()
        self.assertEqual(len([x for x in self.api.calls if x[:2]==('POST','/pages')]),1)

    def test_shared_changes_during_registration_do_not_mark_new_version_done(self):
        original=self.api.call;changed=[False]
        def change(method,path,**kwargs):
            result=original(method,path,**kwargs)
            if path=='/pages' and method=='POST' and not changed[0]:
                changed[0]=True;self.ws.import_file(PMID,'png','main.png',png((1200,800)))
            return result
        self.api.call=change;self.send()
        self.assertFalse(self.ws.current(PMID)['data']['done'])
        self.assertEqual(ns.status(self.repo,self.ws)['jobs'][0]['status'],'registered_stale')
        self.assertEqual(len(self.api.pages),1)

    def test_crash_after_remote_success_before_shared_save_can_resume(self):
        with patch.object(self.ws,'record',side_effect=OSError('disk unavailable')):
            self.send()
        self.assertFalse(self.ws.current(PMID)['data']['done'])
        before=len(self.mutations());self.assertIn('1/1',self.send())
        self.assertEqual(len(self.mutations()),before)
        self.assertEqual(len(self.api.pages),1)

    def test_remote_text_changed_after_interrupted_save_is_not_marked_done(self):
        with patch.object(self.ws,'record',side_effect=OSError('disk unavailable')):
            self.send()
        row=self.rows('graphics')[0];state=json.loads(row['notion'])
        block=self.api.blocks[state['section_id']][0]
        block['paragraph']['rich_text']=rich('Manually edited in Notion')
        self.send()
        self.assertFalse(self.ws.current(PMID)['data']['done'])
        self.assertEqual(block['paragraph']['rich_text'],rich('Manually edited in Notion'))

    def test_different_database_and_other_mac_cannot_resume(self):
        preview=self.prepare()
        other=Workspace(self.ws.root,'b'*32,'Other Mac')
        with self.assertRaisesRegex(ValueError,'担当Mac'):
            ns.prepare(self.repo,other,[dict(pmid=PMID,heads=[other.current(PMID)['id']])],self.api)
        with patch.object(ns.SharedNotion,'setup',lambda client:setattr(client,'data_source_id','different')):
            with self.assertRaisesRegex(ValueError,'登録先'):
                ns.submit(self.repo,self.ws,self.confirmation(preview),self.api)
        self.assertFalse(self.mutations())

    def test_pinned_page_archived_or_moved_does_not_recreate(self):
        self.api.fail_after=('POST','/pages');self.send()
        page=next(iter(self.api.pages.values()));page['in_trash']=True
        with self.assertRaisesRegex(ValueError,'ゴミ箱'):self.prepare()
        self.assertEqual(len(self.api.pages),1)

    def test_manual_registration_remains_keyless(self):
        event=self.ws.current(PMID)
        with patch.object(ns,'credentials',return_value=dict(NOTION_TOKEN='',NOTION_DATABASE_ID='',NOTION_DATA_SOURCE_ID='')):
            app=Application(self.repo,self.ws.root)
            app.run('record',dict(pmid=PMID,heads=[event['id']],note='手動登録を確認',done=True))
        self.assertTrue(self.ws.current(PMID)['data']['done']);self.assertFalse(self.api.calls)

    def test_gui_actions_share_registration_engine(self):
        app=Application(self.repo,self.ws.root);app.settings['device_id']=self.ws.device_id
        with patch.object(ns,'SafeNotionAPI',return_value=self.api):
            app.run('notion-prepare',dict(items=[dict(pmid=PMID,heads=[self.ws.current(PMID)['id']])]))
            app.refresh();preview=app.state['notion_sync']['preview']
            app.run('notion-send',self.confirmation(preview));app.refresh()
        self.assertEqual(app.state['counts']['completed'],1)
        self.assertEqual(app.state['notion_sync']['jobs'][0]['status'],'completed')


if __name__=='__main__':unittest.main()
