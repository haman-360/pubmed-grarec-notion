"""Offline acceptance tests. All paper IDs, PDFs and API responses are synthetic."""
import base64
import copy
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from pypdf import PdfWriter
from PIL import Image
from paper_pipeline.store import Store, encode, now, atomic_write
from paper_pipeline.sources import Sources, inspect_pdf, matches_pdf
from paper_pipeline.batches import API, Batches, config, schema
from paper_pipeline.notion import Notion, rich, plain
from paper_pipeline import cli


def sample_summary(paper):
    def value(spec):
        if spec['type'] == 'string':
            return spec.get('enum',['説明'])[0]
        if spec['type'] == 'array':
            return []
        if spec['type'] == 'object':
            return {k:value(v) for k,v in spec['properties'].items()}
        return 0
    result = value(schema())
    result.update({k:paper[k] or '' for k in ('pmid','doi','title')})
    result.update(journal='',year='',study_type='Original Article RCT',main_results=['群A 10%、群B 20%'],
                  result_evidence=[{'result_index':0,'location':'p.2 Table 1','supporting_text':'10% vs 20%'}])
    return result


def png(size=(2048,1152)):
    output = io.BytesIO()
    Image.new('RGB',size,'white').save(output,format='PNG')
    return output.getvalue()


class FakeOpenAI:
    def __init__(self):
        self.files, self.batches = {}, {}
        self.posts = 0
        self.timeout_after_create = False
        self.timeout_before_create = False

    def upload(self,path,purpose):
        identifier = f'file-{len(self.files)}'
        self.files[identifier] = Path(path).read_bytes()
        return identifier

    def create(self,file_id,stage,attempt_id):
        self.posts += 1
        if self.timeout_before_create:
            raise TimeoutError('unresolved')
        batch = {'id':f'batch-{len(self.batches)}','status':'in_progress','input_file_id':file_id,
                 'metadata':{'attempt_id':attempt_id},'stage':stage}
        self.batches[batch['id']] = batch
        if self.timeout_after_create:
            raise TimeoutError('accepted but response lost')
        return batch

    def retrieve(self,identifier):
        return self.batches[identifier]

    def find(self,attempt):
        return [b for b in self.batches.values() if b['metadata']['attempt_id']==attempt['id']]

    def download(self,identifier):
        return self.files[identifier]

    def complete(self,attempt,body,status=200):
        batch = self.batches[attempt['batch_id']]
        identifier = 'out-'+batch['id']
        self.files[identifier] = encode({'custom_id':attempt['id'],'response':{'status_code':status,'body':body}}).encode()
        batch.update(status='completed',output_file_id=identifier)


class FakeNotion:
    def __init__(self):
        self.properties = {name:{'type':kind} for name,kind in (
            ('Name','title'),('PMID','rich_text'),('DOI','rich_text'),('Human Checked','checkbox'),('Automation ID','rich_text'))}
        self.pages, self.blocks, self.uploads = {}, {}, {}
        self.calls = []
        self.fail_after = None
        self.fail_before = None
        self.file_limit = 5*1024*1024
        self.sequence = 0

    def identifier(self,prefix):
        self.sequence += 1
        return f'{prefix}-{self.sequence}'

    def call(self,method,path,**kwargs):
        self.calls.append((method,path,copy.deepcopy(kwargs.get('json'))))
        if self.fail_before == (method,path):
            self.fail_before = None
            raise TimeoutError('result unknown')
        result = self.handle(method,path,**kwargs)
        if self.fail_after == (method,path):
            self.fail_after = None
            raise TimeoutError('accepted but response lost')
        return copy.deepcopy(result)

    def handle(self,method,path,**kwargs):
        payload = kwargs.get('json',{})
        if path == '/databases/db':
            return {'data_sources':[{'id':'ds'}]}
        if path == '/data_sources/ds':
            if method=='PATCH':
                for key,spec in payload['properties'].items():
                    self.properties[key] = {'type':next(iter(spec))}
            return {'properties':self.properties}
        if path == '/users/me':
            return {'bot':{'workspace_limits':{'max_file_upload_size_in_bytes':self.file_limit}}}
        if path == '/data_sources/ds/query':
            query = payload['filter']
            kind = next(k for k in query if k!='property')
            found = []
            for page in self.pages.values():
                prop = page['properties'].get(query['property'],{})
                v = prop.get(kind)
                if isinstance(v,list):
                    v = ''.join(x.get('plain_text',x.get('text',{}).get('content','')) for x in v)
                test = query[kind]
                if (test.get('is_not_empty') and v) or ('equals' in test and v==test['equals']):
                    found.append(page)
            return {'results':found,'has_more':False}
        if path == '/pages':
            identifier = self.identifier('page')
            page = dict(payload,id=identifier)
            self.pages[identifier] = page
            self.blocks[identifier] = []
            return page
        if path.startswith('/pages/'):
            page = self.pages[path.split('/')[2]]
            if method=='PATCH':
                page['properties'].update(payload.get('properties',{}))
                if 'cover' in payload:
                    page['cover'] = payload['cover']
            return page
        if path.startswith('/blocks/'):
            identifier = path.split('/')[2]
            if method=='GET':
                return {'results':self.blocks.get(identifier,[]),'has_more':False}
            result = []
            for block in payload['children']:
                b = dict(copy.deepcopy(block),id=self.identifier('block'))
                self.blocks.setdefault(identifier,[]).append(b)
                self.blocks[b['id']] = []
                result.append(b)
            return {'results':result}
        if path == '/file_uploads':
            identifier = self.identifier('upload')
            self.uploads[identifier] = dict(payload,id=identifier,status='pending')
            return self.uploads[identifier]
        if path.startswith('/file_uploads/'):
            item = self.uploads[path.split('/')[2]]
            if path.endswith('/send'):
                item['status'] = 'uploaded'
            return item
        raise AssertionError((method,path))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root/'state')
        self.api = FakeOpenAI()
        self.runner = Batches(self.store,self.api,allow_image_api=True)
        self.budget()

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def budget(self,**changes):
        current = datetime.now(timezone.utc)
        setting = {'start':(current-timedelta(days=1)).isoformat(),'end':(current+timedelta(days=29)).isoformat(),
                   'checked_at':current.isoformat(),'remaining':100,'other_usage':20,
                   'production':False,'no_auto_recharge':True,'purchased_checked':True}
        setting.update(changes)
        self.store.set_setting('budget',setting)

    def test_explicit_revision_selection_never_submits_other_papers(self):
        a=self.paper('90000001');b=self.paper('90000002')
        ra=self.revision(a);rb=self.revision(b)
        self.assertEqual(self.runner.submit(5,revision_ids=[rb['id']]),1)
        attempts=self.store.rows('SELECT * FROM attempts')
        self.assertEqual([v['revision_id'] for v in attempts],[rb['id']])
        self.assertEqual(self.store.one('revisions',ra['id'])['status'],'analysis_ready')
        self.assertEqual(self.runner.submit(5,revision_ids=[]),0)

    def test_prepare_revisions_scopes_new_work(self):
        a=self.paper('90000001',pdf=False);b=self.paper('90000002',pdf=False)
        for i,p in enumerate((a,b)):
            writer=PdfWriter();writer.add_blank_page(width=100+i,height=100)
            path=self.root/(p['pmid']+'.pdf');writer.write(path);self.store.source(path,p['id'])
        self.runner.prepare_revisions(paper_ids=[b['id']])
        self.assertEqual([r['paper_id'] for r in self.store.rows('SELECT * FROM revisions')],[b['id']])

    def test_stage_holds_preserve_budget_checks(self):
        paper = self.paper()
        revision = self.revision(paper)
        self.budget(image_suspended_reason='image hold')
        attempt = self.store.reserve(revision, 'analysis', 0.1)
        self.assertEqual(attempt['stage'], 'analysis')
        with self.assertRaisesRegex(ValueError, '停止中'):
            self.store.reserve(revision, 'image', 0.1)
        self.budget(image_suspended_reason='image hold',
                    checked_at=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat())
        with self.assertRaisesRegex(ValueError, '24時間'):
            self.store.reserve(revision, 'analysis', 0.1)
        for key in ('suspended_reason', 'analysis_suspended_reason'):
            self.budget(**{key: 'hold'})
            with self.assertRaisesRegex(ValueError, '停止中'):
                self.store.reserve(revision, 'analysis', 0.1)

    def paper(self,pmid='90000001',pdf=True):
        p = self.store.paper(pmid=pmid,title='Synthetic pediatric study '+pmid,doi='10.1234/test'+pmid)
        if pdf:
            writer = PdfWriter()
            writer.add_blank_page(width=100,height=100)
            writer.add_metadata({'/Title':p['title']})
            path = self.root/(pmid+'.pdf')
            writer.write(path)
            self.store.source(path,p['id'])
            self.runner.prepare_revisions()
            revision = self.revision(p)
            cfg = json.loads(revision['config'])
            cfg.update(review_version='fulltext-v3',image_generation_mode='api')
            self.store.update('revisions',revision['id'],config=encode(cfg))
        return self.store.one('papers',p['id'])

    def revision(self,paper):
        return self.store.rows('SELECT * FROM revisions WHERE paper_id=? ORDER BY created_at DESC',(paper['id'],))[0]

    def analysis_body(self,paper,**changes):
        summary = sample_summary(paper)
        summary.update(changes)
        return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':encode(summary)}]}],
                'usage':{'input_tokens':30000,'output_tokens':16000}}

    def ready_notion(self,paper=None):
        paper = paper or self.paper()
        revision = self.revision(paper)
        path = f"artifacts/{revision['id']}/review.json"
        image = f"artifacts/{revision['id']}/grarec.png"
        atomic_write(self.store.root/path,encode(sample_summary(paper)))
        atomic_write(self.store.root/image,png())
        self.store.update('revisions',revision['id'],summary=path,image=image,status='notion_ready')
        from paper_pipeline.graphics import prepare_web, import_image, review_image
        r = self.revision(paper)
        prepare_web(self.store,r)
        import_image(self.store,r,'main',self.store.root/image)
        review_image(self.store,r,'main',True,'Synthetic reviewer','Synthetic facts checked')
        return self.revision(paper)

    def graphic(self, revision):
        from paper_pipeline.graphics import active_graphics
        return active_graphics(self.store,revision['id'])[0]

    def test_additive_queue_25_or_27_and_dedup(self):
        txt = self.root/'pmids.txt'
        txt.write_text('\n'.join(str(90000000+i) for i in range(10)))
        self.store.import_pmids(txt)
        self.store.import_pmids(txt)
        for p in self.store.pending_search()[:3]:
            self.store.update('papers',p['id'],access='available')
        txt.write_text('\n'.join(str(90000100+i) for i in range(20)))
        self.store.import_pmids(txt)
        self.assertEqual(len(self.store.pending_search()),27)
        for p in self.store.pending_search()[:2]:
            self.store.update('papers',p['id'],access='available')
        self.assertEqual(len(self.store.pending_search()),25)
        self.assertEqual(len(self.store.rows('SELECT * FROM papers')),30)

    def test_internal_id_survives_late_identifiers_and_conflicts_stop(self):
        p = self.store.paper(doi='https://doi.org/10.1234/ABC',title='Title')
        linked = self.store.paper(pmid='90000001',doi='10.1234/abc')
        self.assertEqual(linked['id'],p['id'])
        other = self.store.paper(pmid='90000002')
        with self.assertRaises(ValueError):
            self.store.paper(pmid=other['pmid'],doi=linked['doi'])
        blank = self.store.paper(title='Unindexed title')
        cli.dispatch(self.store,cli.parser().parse_args(['link',blank['id'],'--pmid','90000003']))
        self.assertEqual(self.store.resolve('90000003')['id'],blank['id'])

    def test_source_versions_preserve_original_and_require_regeneration(self):
        p = self.paper()
        original = self.root/(p['pmid']+'.pdf')
        before = original.read_bytes()
        self.store.source(original,p['id'])
        other = self.root/'revision.pdf'
        writer = PdfWriter(); writer.add_blank_page(width=200,height=200); writer.write(other)
        s = self.store.source(other,p['id'])
        self.assertEqual(s['status'],'revision_review')
        self.assertEqual(original.read_bytes(),before)
        self.runner.prepare_revisions()
        self.assertEqual(len(self.store.rows('SELECT * FROM revisions')),1)
        q = self.paper('90000002',pdf=False)
        with self.assertRaises(ValueError):
            self.store.source(original,q['id'])

    def test_reference_doi_and_filename_do_not_identify(self):
        p = self.paper(pdf=False)
        info = {'first':p['title']+'\nReferences\n'+p['doi'],'title':''}
        self.assertFalse(matches_pdf(info,p))
        info['first'] = p['title']+'\ndoi: '+p['doi']
        self.assertTrue(matches_pdf(info,p))
        info['first'] = 'Unrelated study '+p['doi']
        self.assertFalse(matches_pdf(info,p))

    def test_invalid_and_encrypted_pdfs_rejected(self):
        for name,content in [('html.pdf',b'<html>Forbidden</html>'),('broken.pdf',b'%PDF-1.4\ntruncated')]:
            path = self.root/name; path.write_bytes(content)
            with self.assertRaises(Exception):
                inspect_pdf(path)
        writer = PdfWriter(); writer.add_blank_page(width=100,height=100); writer.encrypt('password')
        path = self.root/'encrypted.pdf'; writer.write(path)
        with self.assertRaisesRegex(ValueError,'暗号化'):
            inspect_pdf(path)

    def test_partial_source_outage_remains_error_not_paywalled(self):
        p = self.paper(pdf=False)
        s = Sources(self.store)
        with patch.object(s,'metadata',return_value=p), patch.object(s,'pmc_candidates',side_effect=RuntimeError('offline')), \
             patch.object(s,'unpaywall_candidates',return_value=[]), patch.object(s,'europe_candidates',return_value=[]), \
             patch.object(s,'publisher_candidates',return_value=[]):
            result = s.discover(p)
        self.assertEqual(result['status'],'search_error')
        self.assertIn('error',[x['status'] for x in result['attempts']])

    def test_mismatched_download_is_identity_review(self):
        p = self.paper(pdf=False)
        q = self.paper('90000002')
        s = Sources(self.store)
        candidate = {'url':'https://example.test/free.pdf','source':'Unpaywall','priority':'high'}
        with patch.object(s,'metadata',return_value=p), patch.object(s,'pmc_candidates',return_value=[]), \
             patch.object(s,'unpaywall_candidates',return_value=[candidate]), patch.object(s,'europe_candidates',return_value=[]), \
             patch.object(s,'publisher_candidates',return_value=[]), patch.object(s,'download',return_value=self.root/(q['pmid']+'.pdf')):
            result = s.discover(p)
        self.assertEqual(result['status'],'manual_candidate')
        self.assertNotEqual(self.store.one('papers',p['id'])['access'],'available')

    def test_two_batches_complete_and_restart_does_not_resubmit(self):
        p = self.paper()
        self.assertEqual(self.runner.submit(),1)
        a = self.store.rows('SELECT * FROM attempts')[0]
        request = json.loads(self.api.files[a['input_file_id']])
        self.assertEqual(request['url'],'/v1/responses')
        self.assertEqual(request['body']['input'][0]['content'][1]['detail'],'high')
        self.api.complete(a,self.analysis_body(p))
        self.runner.collect()
        self.assertEqual(self.revision(p)['status'],'image_ready')
        self.runner.submit()
        a = self.store.rows("SELECT * FROM attempts WHERE stage='image'")[0]
        request = json.loads(self.api.files[a['input_file_id']])
        self.assertEqual(request['url'],'/v1/images/generations')
        self.assertEqual(request['body']['size'],'2048x1152')
        body = {'data':[{'b64_json':base64.b64encode(png()).decode()}],
                'usage':{'input_tokens':5000,'output_tokens':10000,'input_tokens_details':{'text_tokens':5000,'image_tokens':0}}}
        self.api.complete(a,body)
        self.store.close(); self.store = Store(self.root/'state'); self.runner = Batches(self.store,self.api,allow_image_api=True)
        self.runner.collect(); self.runner.submit(); self.runner.collect()
        self.assertEqual(self.api.posts,2)
        self.assertEqual(self.revision(p)['status'],'IMAGE_REVIEW_REQUIRED')
        self.assertAlmostEqual(sum(a['actual'] for a in self.store.rows('SELECT * FROM attempts')),0.2725)
        from paper_pipeline.graphics import review_image
        review_image(self.store,self.revision(p),'main',True,'Synthetic reviewer','Checked')
        n = FakeNotion(); Notion(self.store,n,'db').sync(self.revision(p))
        self.assertEqual(self.revision(p)['status'],'completed')
        self.assertEqual(len(n.pages),1)

    def test_unknown_submission_reconciles_without_reposting(self):
        p = self.paper(); self.api.timeout_after_create = True
        self.runner.submit()
        self.assertEqual(self.store.rows('SELECT * FROM attempts')[0]['status'],'unknown')
        self.runner.collect(); self.runner.submit()
        self.assertEqual(self.api.posts,1)
        self.assertEqual(self.store.rows('SELECT * FROM attempts')[0]['status'],'submitted')

    def test_unresolvable_submission_keeps_reserve(self):
        self.paper(); self.api.timeout_before_create = True
        self.runner.submit(); self.runner.collect(); self.runner.submit()
        a = self.store.rows('SELECT * FROM attempts')[0]
        self.assertEqual(a['status'],'unknown'); self.assertIsNone(a['actual'])
        self.assertEqual(self.api.posts,1)

    def test_partial_batch_success_does_not_repeat_successful_paper(self):
        p = self.paper(); q = self.paper('90000002')
        self.runner.submit()
        for a in self.store.rows('SELECT * FROM attempts'):
            paper = self.store.one('papers',self.store.one('revisions',a['revision_id'])['paper_id'])
            self.api.complete(a,self.analysis_body(paper) if paper['id']==p['id'] else {},200 if paper['id']==p['id'] else 500)
        self.runner.collect(); self.runner.submit()
        self.assertEqual(len(self.store.rows("SELECT * FROM attempts WHERE revision_id=? AND stage='analysis'",(self.revision(p)['id'],))),1)
        self.assertEqual(len(self.store.rows("SELECT * FROM attempts WHERE revision_id=? AND stage='analysis'",(self.revision(q)['id'],))),2)

    def test_failure_retry_limited_to_one(self):
        p = self.paper()
        for _ in range(2):
            self.runner.submit()
            a = self.store.rows('SELECT * FROM attempts ORDER BY created_at DESC')[0]
            self.api.complete(a,{},500); self.runner.collect()
        self.runner.submit()
        self.assertEqual(self.api.posts,2)
        self.assertEqual(self.revision(p)['status'],'review_required')

    def test_expired_without_result_is_not_zero_cost(self):
        p = self.paper(); self.runner.submit()
        a = self.store.rows('SELECT * FROM attempts')[0]
        self.api.batches[a['batch_id']]['status'] = 'expired'
        self.runner.collect(); self.runner.submit()
        a = self.store.one('attempts',a['id'])
        self.assertIsNone(a['actual']); self.assertEqual(a['status'],'review_required')
        self.assertEqual(self.api.posts,1)

    def test_invalid_identity_json_and_truncated_response_do_not_make_images(self):
        for scenario in ('identity','truncated','json','evidence'):
            p = self.paper('9000000'+str(len(self.store.rows('SELECT id FROM papers'))+1))
            self.runner.submit()
            a = self.store.rows('SELECT * FROM attempts WHERE revision_id=?',(self.revision(p)['id'],))[0]
            body = self.analysis_body(p)
            if scenario=='identity': body = self.analysis_body(p,pmid='wrong')
            if scenario=='truncated': body['status'] = 'incomplete'
            if scenario=='json': body['output'][0]['content'][0]['text'] = '{bad'
            if scenario=='evidence': body = self.analysis_body(p,result_evidence=[])
            self.api.complete(a,body); self.runner.collect()
            self.assertEqual(self.revision(p)['status'],'review_required')
            self.assertIsNone(self.revision(p)['summary'])

    def test_invalid_image_preserves_summary_and_unknown_usage_reserve(self):
        p = self.paper(); r = self.ready_notion(p)
        self.store.update('revisions',r['id'],image=None,status='image_ready')
        self.runner.submit()
        a = self.store.rows('SELECT * FROM attempts')[0]
        self.api.complete(a,{'data':[{'b64_json':base64.b64encode(png((100,100))).decode()}]})
        self.runner.collect()
        self.assertEqual(self.revision(p)['status'],'review_required')
        self.assertTrue((self.store.root/r['summary']).exists())
        self.assertIsNone(self.store.one('attempts',a['id'])['actual'])

    def test_budget_fails_closed_stale_expiring_nan_and_limit(self):
        p = self.paper(); r = self.revision(p)
        for change in ({'checked_at':(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()},
                       {'end':(datetime.now(timezone.utc)+timedelta(hours=48)).isoformat()},
                       {'remaining':float('nan')},{'remaining':20.01}):
            self.budget(**change)
            with self.assertRaises(ValueError): self.store.reserve(r,'analysis',0.1)
        self.budget()
        self.store.reserve(r,'analysis',3)
        with self.assertRaises(ValueError): self.store.reserve(r,'image',1.1)

    def test_outstanding_reserve_carries_across_billing_period(self):
        p = self.paper(); r = self.revision(p)
        a = self.store.reserve(r,'analysis',2)
        self.store.update('attempts',a['id'],period='old-period')
        self.budget(remaining=22.5)
        with self.assertRaises(ValueError): self.store.reserve(r,'image',1)
        self.assertEqual(self.store.one('attempts',a['id'])['reserve'],2)

    def test_cumulative_five_paper_limit_removed_with_money_cap_retained(self):
        for i in range(6): self.paper(str(90000000+i))
        self.runner.submit(limit=10)
        self.assertEqual(self.api.posts,6)
        self.assertEqual(len(self.store.rows("SELECT * FROM revisions WHERE status='budget_wait'")),0)
        r=self.store.rows('SELECT * FROM revisions')[0]
        with self.assertRaisesRegex(ValueError, '期間予算'):
            self.store.reserve(r,'analysis',20)

    def test_hold_and_release_affect_search_and_submit(self):
        p = self.paper()
        cli.dispatch(self.store,cli.parser().parse_args(['hold',p['id']]))
        self.assertEqual(self.runner.submit(),0)
        cli.dispatch(self.store,cli.parser().parse_args(['release',p['id']]))
        self.assertEqual(self.runner.submit(),1)

    def test_double_start_lock(self):
        second = Store(self.root/'state')
        try:
            with self.store.lock():
                with self.assertRaises(ValueError):
                    with second.lock(): pass
        finally: second.close()

    def test_backup_restores_ledger_and_artifacts(self):
        self.ready_notion()
        self.store.backup(self.root/'backup')
        restored = Store(self.root/'backup')
        try:
            self.assertEqual(restored.rows('SELECT * FROM papers'),self.store.rows('SELECT * FROM papers'))
            r = restored.rows('SELECT * FROM revisions')[0]
            self.assertTrue((restored.root/r['summary']).exists())
            self.assertEqual(restored.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        finally: restored.close()

    def test_notion_checked_page_and_notes_are_unchanged(self):
        r = self.ready_notion(); p = self.store.one('papers',r['paper_id'])
        api = FakeNotion()
        page = api.call('POST','/pages',json={'parent':{'data_source_id':'ds'},'properties':{
            'PMID':{'rich_text':rich(p['pmid'])},'Human Checked':{'checkbox':True},'Automation ID':{'rich_text':rich(p['id'])},
            'My Note':{'rich_text':rich('手書きメモ')}},'cover':{'type':'external','external':{'url':'https://example.test/old.png'}}})
        api.blocks[page['id']] = [{'id':'old','type':'paragraph','paragraph':{'rich_text':rich('確認済み本文')}}]
        before = copy.deepcopy(api.pages[page['id']])
        client = Notion(self.store,api,'db'); client.sync(r); client.sync(self.store.one('revisions',r['id']))
        self.assertEqual(api.pages[page['id']],before)
        self.assertEqual(len(api.blocks[page['id']]),2)
        self.assertEqual(plain(api.blocks[page['id']][0]),'確認済み本文')
        self.assertEqual(len(api.uploads),3)

    def test_notion_duplicate_pages_stop_without_write(self):
        r = self.ready_notion(); p = self.store.one('papers',r['paper_id']); api = FakeNotion()
        for _ in range(2):
            api.call('POST','/pages',json={'parent':{'data_source_id':'ds'},'properties':{'PMID':{'rich_text':rich(p['pmid'])}}})
        writes = len(api.calls)
        with self.assertRaisesRegex(ValueError,'複数'):
            Notion(self.store,api,'db').sync(r)
        self.assertFalse(any(m in ('PATCH',) or (m=='POST' and path=='/pages') for m,path,_ in api.calls[writes:]))

    def test_notion_timeout_after_page_create_is_reconciled(self):
        r = self.ready_notion(); api = FakeNotion(); client = Notion(self.store,api,'db')
        api.fail_after = ('POST','/pages')
        with self.assertRaises(TimeoutError): client.sync(r)
        client.sync(self.store.one('revisions',r['id']))
        self.assertEqual(len(api.pages),1)
        self.assertEqual(self.store.one('revisions',r['id'])['status'],'completed')

    def test_notion_timeout_before_create_never_blindly_repeats(self):
        r = self.ready_notion(); api = FakeNotion(); client = Notion(self.store,api,'db')
        api.fail_before = ('POST','/pages')
        with self.assertRaises(TimeoutError): client.sync(r)
        with self.assertRaisesRegex(ValueError,'結果が不明'): client.sync(self.store.one('revisions',r['id']))
        self.assertEqual(len(api.pages),0)
        self.assertEqual(sum(m=='POST' and p=='/pages' for m,p,_ in api.calls),1)

    def test_edited_generated_section_is_retained_and_copy_appended(self):
        r = self.ready_notion(); api = FakeNotion(); client = Notion(self.store,api,'db'); client.sync(r)
        r = self.store.one('revisions',r['id']); state = json.loads(self.graphic(r)['notion'])
        api.blocks[state['section_id']][0]['paragraph']['rich_text'] = rich('利用者による編集')
        self.store.update('graphics',self.graphic(r)['id'],status='IMAGE_APPROVED')
        client.sync(r)
        new = json.loads(self.graphic(r)['notion'])
        self.assertNotEqual(new['section_id'],state['section_id'])
        self.assertEqual(plain(api.blocks[state['section_id']][0]),'利用者による編集')
        self.assertEqual(len(api.blocks[state['page_id']]),2)

    def test_notion_upload_limit_and_expired_upload(self):
        r = self.ready_notion(); api = FakeNotion(); api.file_limit = 1
        client = Notion(self.store,api,'db')
        with self.assertRaisesRegex(ValueError,'上限'): client.sync(r)
        self.assertEqual(len(api.uploads),0)
        client.file_limit = 5*1024*1024
        r = self.store.one('revisions',r['id']); state = json.loads(self.graphic(r)['notion'])
        upload = api.call('POST','/file_uploads',json={}); api.uploads[upload['id']]['status'] = 'expired'
        state['json_upload'] = {'id':upload['id'],'send_intent':True}
        self.store.update('graphics',self.graphic(r)['id'],notion=encode(state))
        client.sync(self.store.one('revisions',r['id']))
        self.assertEqual(len(api.uploads),4)

    def test_migration_is_read_only_idempotent_and_does_not_recharge(self):
        repo = self.root/'oldrepo'; path = repo/'output/jobs/PMID_90000001.json'; path.parent.mkdir(parents=True)
        path.write_text(encode({'pmid':'90000001','article':{'title':'Legacy title'},'batch_id':'old-batch'}))
        batch = repo/'output/batches/old-batch.json'; batch.parent.mkdir()
        batch.write_text(encode({'batch_id':'old-batch','pmids':['90000001']}))
        before = path.read_bytes()
        with patch.object(cli,'REPO',repo): cli.migrate(self.store); cli.migrate(self.store)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(len(self.store.rows('SELECT * FROM papers')),1)
        self.assertEqual(len(cli.legacy_pending(self.store)),1)
        self.runner.prepare_revisions(); self.assertEqual(self.runner.submit(),0)

    def test_prepare_offline_never_calls_paid_notion_or_git(self):
        repo = self.root/'empty'; repo.mkdir(); txt = repo/'pmids.txt'; txt.write_text('90000001\n')
        args = cli.parser().parse_args(['prepare','--offline','--pmids-file',str(txt),'--pdf-dir',str(repo)])
        with patch.object(cli,'REPO',repo), patch.object(cli,'API',side_effect=AssertionError('paid API')), \
             patch.object(cli,'NotionAPI',side_effect=AssertionError('Notion')), patch('subprocess.run',side_effect=AssertionError('git')):
            cli.dispatch(self.store,args)
        self.assertEqual(len(self.store.pending_search()),1)


    def test_migrates_page_mapping_and_recovers_missing_batch_manifest(self):
        repo = self.root/'old'; output = repo/'output'; jobs = output/'jobs'; jobs.mkdir(parents=True)
        (jobs/'job.json').write_text(encode({'pmid':'90000001','status':'batch_submitted','batch_id':'orphan'}))
        (output/'notion_pages.json').write_text(encode([{'pmid':'90000001','notion_page_id':'existing-page'}]))
        with patch.object(cli,'REPO',repo): cli.migrate(self.store)
        self.assertEqual(self.store.resolve('90000001')['page_id'],'existing-page')
        self.assertEqual(cli.legacy_pending(self.store)[0][1]['batch_id'],'orphan')

    def test_manual_confirmation_of_revised_unbound_pdf_requires_review(self):
        p = self.paper()
        path = self.root/'other.pdf'; writer = PdfWriter(); writer.add_blank_page(width=300,height=300); writer.write(path)
        s = self.store.source(path,status='identity_review')
        bound = self.store.source(path,p['id'])
        self.assertEqual(s['id'],bound['id'])
        self.assertEqual(bound['status'],'revision_review')
        self.assertEqual(self.store.one('papers',p['id'])['blocker'],'revision_review')

    def test_image_regeneration_reuses_json_and_supersedes_waiting_version(self):
        p = self.paper(); old = self.ready_notion(p)
        cli.dispatch(self.store,cli.parser().parse_args(['regenerate',p['id'],'--stage','image']))
        new = self.revision(p)
        self.assertNotEqual(new['id'],old['id'])
        self.assertEqual(new['status'],'image_ready')
        self.assertEqual(self.store.one('revisions',old['id'])['status'],'superseded')
        self.runner.submit()
        self.assertEqual([a['stage'] for a in self.store.rows('SELECT * FROM attempts')],[])
        self.assertEqual(self.revision(p)['status'],'READY_FOR_WEB')

    def test_notion_text_timeout_and_attachment_timeout_reconcile(self):
        r = self.ready_notion(); api = FakeNotion(); client = Notion(self.store,api,'db')
        original = api.call
        stopped = [False]
        def call(method,path,**kwargs):
            result = original(method,path,**kwargs)
            children = kwargs.get('json',{}).get('children',[])
            if not stopped[0] and children and children[0]['type']=='paragraph':
                stopped[0] = True
                raise TimeoutError('text accepted')
            return result
        api.call = call
        with self.assertRaises(TimeoutError): client.sync(r)
        stopped[0] = False
        def call(method,path,**kwargs):
            result = original(method,path,**kwargs)
            children = kwargs.get('json',{}).get('children',[])
            if not stopped[0] and children and children[0]['type']=='image':
                stopped[0] = True
                raise TimeoutError('image accepted')
            return result
        api.call = call
        with self.assertRaises(TimeoutError): client.sync(self.store.one('revisions',r['id']))
        api.call = original
        client.sync(self.store.one('revisions',r['id']))
        state = json.loads(self.graphic(r)['notion'])
        self.assertEqual(sum(b['type']=='image' for b in api.blocks[state['section_id']]),1)
        self.assertEqual(len(api.uploads),3)

    def test_resuming_does_not_repeat_pdf_search_or_publish_git(self):
        self.paper()
        args = cli.parser().parse_args(['auto','--resume-only','--yes'])
        with patch.dict(os.environ,{'NOTION_TOKEN':'test-only','NOTION_DATABASE_ID':'db'}), \
             patch.object(cli,'prepare',side_effect=AssertionError('repeat discovery')), \
             patch.object(cli,'API') as api, patch.object(cli.Batches,'submit',return_value=0), \
             patch('subprocess.run',side_effect=AssertionError('git publication')):
            cli.dispatch(self.store,args)
        api.return_value.check_models.assert_called_once()

    def test_source_identifier_disagreement_is_not_an_official_match(self):
        from paper_pipeline.sources import identifiers_match
        self.assertFalse(identifiers_match({'pmid':'1','doi':'10.1234/a'},{'pmid':'2','doi':'10.1234/a'}))
        self.assertTrue(identifiers_match({'pmid':'1','doi':'10.1234/a'},{'pmid':'1'}))
        self.assertFalse(identifiers_match({'pmid':'1','doi':''},{'pmid':'2','doi':''}))

    def test_notion_doi_url_matches_existing_page(self):
        r = self.ready_notion(); p = self.store.one('papers',r['paper_id']); api = FakeNotion()
        page = api.call('POST','/pages',json={'parent':{'data_source_id':'ds'},'properties':{
            'DOI':{'rich_text':rich('https://doi.org/'+p['doi'].upper())},'Human Checked':{'checkbox':True}}})
        Notion(self.store,api,'db').sync(r)
        self.assertEqual(len(api.pages),1)
        self.assertEqual(self.store.one('papers',p['id'])['page_id'],page['id'])

    def test_source_error_log_does_not_contain_api_key(self):
        import requests
        from paper_pipeline.sources import error_reason
        reason = error_reason(requests.HTTPError('GET /efetch?api_key=private-example'))
        self.assertNotIn('private-example',reason)


    def test_direct_uses_standard_prices_and_never_creates_batch(self):
        from paper_pipeline.direct import Direct
        p = self.paper()
        api = Mock()
        api.upload.return_value = 'pdf-file'
        api.count_input.return_value = 30000
        api.call.side_effect = [Mock(status_code=200,json=Mock(return_value=self.analysis_body(p))),
            Mock(status_code=200,json=Mock(return_value={'data':[{'b64_json':base64.b64encode(png()).decode()}],
                'usage':{'input_tokens':5000,'output_tokens':10000,'input_tokens_details':{'text_tokens':5000,'image_tokens':0}}}))]
        direct = Direct(self.store,api,allow_image_api=True)
        direct.run([p['id']]); direct.run([p['id']])
        self.assertEqual([c.args[:2] for c in api.call.call_args_list],[('POST','/responses'),('POST','/images/generations')])
        api.create.assert_not_called()
        self.assertEqual(self.revision(p)['status'],'IMAGE_REVIEW_REQUIRED')
        self.assertAlmostEqual(sum(a['actual'] for a in self.store.rows('SELECT * FROM attempts')),0.545)
        self.runner.collect(); self.runner.submit()
        self.assertEqual(self.api.posts,0)

    def test_direct_unknown_holds_cost_and_never_enters_batch_collection(self):
        from paper_pipeline.direct import Direct
        p = self.paper(); api = Mock(); api.call.side_effect = TimeoutError(); api.count_input.return_value = 30000
        api.upload.return_value = 'pdf-file'
        direct = Direct(self.store,api); direct.run([p['id']]); direct.run([p['id']])
        a = self.store.rows('SELECT * FROM attempts')[0]
        self.assertEqual(a['status'],'direct_unknown'); self.assertIsNone(a['actual'])
        self.runner.collect(); self.runner.submit()
        self.assertEqual(api.call.call_count,1)
        with self.assertRaises(ValueError):
            cli.dispatch(self.store,cli.parser().parse_args(['regenerate',p['id']]))

    def test_direct_rejects_five_targets_and_old_batch_work(self):
        from paper_pipeline.direct import Direct
        papers = [self.paper(str(90000000+i)) for i in range(5)]
        api = Mock()
        with self.assertRaises(ValueError): Direct(self.store,api).run([p['id'] for p in papers])
        self.runner.submit(limit=1)
        with self.assertRaises(ValueError): Direct(self.store,api).run([papers[0]['id']])
        api.call.assert_not_called()

    def test_prompt_v2_retains_medical_rules_and_frozen_old_revision(self):
        from paper_pipeline.batches import PROMPT
        text = PROMPT.read_text()
        self.assertIn('画面の約60%',text)
        self.assertIn('## 10. 医学的正確性',text)
        self.assertNotIn('青・緑・グレーを基本色',text)
        p = self.paper(); r = self.ready_notion(p)
        cfg = json.loads(r['config']); cfg['image_prompt_text'] = 'archived';
        from paper_pipeline.store import digest
        cfg['image_prompt_hash'] = digest(b'archived')
        self.store.update('revisions',r['id'],config=encode(cfg))
        self.assertTrue(self.runner.image_prompt(self.revision(p)).startswith('archived'))

    def test_pmid_txt_accepts_header_but_rejects_invalid_data(self):
        txt = self.root/'with-header.txt'
        txt.write_text('PMID\n\n90000001\n90000002\n')
        self.store.import_pmids(txt)
        self.assertEqual(len(self.store.pending_search()),2)
        txt.write_text('90000001\nPMID\n')
        with self.assertRaises(ValueError): self.store.import_pmids(txt)

    def test_wrapped_doi_is_matched_only_with_corresponding_title(self):
        from paper_pipeline.sources import pdf_dois
        text = 'Own synthetic pediatric study\nDOI of correction: 10.1016/j.test.2024.999999\nhttps://doi.org/10.\n1016/j.test.2024.\n101136'
        self.assertEqual(pdf_dois(text),['10.1016/j.test.2024.999999','10.1016/j.test.2024.101136'])
        info = {'first':text,'title':'Own synthetic pediatric study'}
        self.assertTrue(matches_pdf(info,{'title':'Own synthetic pediatric study','doi':'10.1016/j.test.2024.101136'}))
        self.assertFalse(matches_pdf(info,{'title':'Unrelated correction title','doi':'10.1016/j.test.2024.999999'}))

    def test_token_count_preserves_file_schema_and_excludes_generation_controls(self):
        api = API('test-only',session=Mock())
        api.call = Mock(return_value=Mock(json=Mock(return_value={'input_tokens':50000})))
        body = {'model':'model','input':[{'type':'input_file','file_id':'file'}],
                'text':{'format':{'type':'json_schema'}},'reasoning':{'effort':'medium'},'store':False,'max_output_tokens':16000}
        self.assertEqual(api.count_input(body),50000)
        payload=api.call.call_args.kwargs['json']
        self.assertEqual(payload['input'],body['input'])
        self.assertEqual(payload['text'],body['text'])
        self.assertNotIn('store',payload); self.assertNotIn('max_output_tokens',payload)

    def test_provider_count_can_admit_long_pdf_without_truncation(self):
        p=self.paper();r=self.revision(p);cfg=json.loads(r['config'])
        cfg.update(mode='direct',counted_input_tokens=50000)
        self.store.update('revisions',r['id'],config=encode(cfg))
        with patch('paper_pipeline.batches.inspect_pdf',return_value={'text_bytes':148163,'pages':53}):
            self.assertLess(self.runner.estimate(self.revision(p),'analysis'),4)

    def test_quality_hold_stops_new_paid_requests_and_preserves_balance(self):
        p=self.paper();b=self.store.setting('budget');b['suspended_reason']='Image unrelated to paper';self.store.set_setting('budget',b)
        with self.assertRaisesRegex(ValueError,'停止中'):
            self.store.reserve(self.revision(p),'image',1)
        self.assertEqual(self.store.setting('budget')['remaining'],100)
        self.assertFalse(self.store.rows('SELECT id FROM attempts'))


if __name__ == '__main__':
    unittest.main()
