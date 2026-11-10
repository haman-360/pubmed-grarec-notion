"""Offline hybrid acceptance tests. IDs identify cases, clinical content is synthetic."""
import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from paper_pipeline import cli
from paper_pipeline.batches import Batches, config, schema, review_prompt
from paper_pipeline.direct import Direct
from paper_pipeline.graphics import (active_graphics, adapt_legacy, check_graphic, import_image,
    master_schema, prepare_web, review_image, validate_master, read_web_prompt)
from paper_pipeline.notion import Notion
from paper_pipeline.store import Store, atomic_write, encode, digest
from test_paper_pipeline import FakeNotion, FakeOpenAI, sample_summary, png
from pypdf import PdfWriter


class HybridTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root/'state')
        # Every test here fails if a real HTTP request is attempted.
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('real network forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def paper(self, pmid='41064908', pages=1, kind='original_article', topics=1):
        paper = self.store.paper(pmid=pmid, title='Synthetic paper '+pmid, doi='10.1234/test'+pmid)
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=100, height=100)
        path = self.root/(pmid+'.pdf')
        writer.write(path)
        source = self.store.source(path, paper['id'])
        revision = self.store.revision(paper, source, config())
        data = adapt_legacy(sample_summary(paper))
        data.pop('structure_review_required')
        data.update(paper_type=kind, classification_reason='Synthetic classification fixture',
                    document_structure=[{'section_id':'s1','title':'Clinical question','location':'p.1'}],
                    clinical_questions=['Synthetic question'],
                    main_results=[f'Question {i}: 10% vs 20%, FAS, CI 1–2' for i in range(topics)],
                    result_evidence=[{'result_index':i,'location':'p.1 Table 1','supporting_text':f'Question {i} 10% vs 20%'} for i in range(topics)])
        if kind != 'original_article':
            data['pico'] = None
            data['practice_change'] = None
        template = data['topics'][0]
        data['topics'] = [dict(template, topic_id='main' if topics == 1 else f'topic_{i}',
                               topic_title=f'Question {i}', central_message=f'Message {i}',
                               result_indices=[i], source_sections=['s1']) for i in range(topics)]
        data['visualization_strategy'].update(mode='single' if topics == 1 else 'topics',
                                             independent_question_count=topics, reason='Independent questions, not pages')
        data['source_hash'] = source['hash']
        relative = f"artifacts/{revision['id']}/review.json"
        atomic_write(self.store.root/relative, encode(data))
        self.store.update('revisions', revision['id'], summary=relative, status='image_ready')
        return self.store.one('revisions', revision['id']), data

    def rows(self, revision):
        return active_graphics(self.store, revision['id'])

    def image(self, color='white', size=(1600,900)):
        from PIL import Image
        path = self.root/(color+'.png')
        Image.new('RGB',size,color).save(path)
        return path

    def approve(self, revision, topic='main', color='white'):
        import_image(self.store,revision,topic,self.image(color))
        review_image(self.store,revision,topic,True,'Synthetic clinician','Synthetic paper/JSON/numbers checked')

    def test_each_topic_prompt_contains_only_its_own_graphic_json(self):
        revision,data=self.paper(kind='narrative_review',topics=2)
        folder=prepare_web(self.store,revision)
        before=list(self.store.db.iterdump())
        for g in self.rows(revision):
            text=read_web_prompt(self.store,revision,g)
            path=self.store.root/g['json_path']
            self.assertEqual(text,(folder/'ready_for_web'/(path.stem+'_prompt.md')).read_text())
            copied=json.loads(text.split('```json\n',1)[1].rsplit('\n```',1)[0])
            self.assertEqual(copied['topic_id'],g['topic_id'])
            self.assertEqual(len(copied['main_results']),1)
            self.assertEqual(copied['main_results'][0],data['main_results'][g['position']])
        self.assertEqual(list(self.store.db.iterdump()),before)

    def test_case_a_original_one_graphic_preserves_pico_results_and_evidence(self):
        revision, data = self.paper(pages=11)
        original = (self.store.root/revision['summary']).read_bytes()
        folder = prepare_web(self.store,revision)
        row = self.rows(revision)[0]
        graphic = check_graphic(self.store,row)
        self.assertEqual(graphic['paper_type'],'original_article')
        self.assertEqual(graphic['pico'],data['pico'])
        self.assertEqual(graphic['main_results'],data['main_results'])
        self.assertEqual(graphic['limitations'],data['limitations'])
        self.assertEqual(len(list((folder/'ready_for_web').glob('*.md'))),1)
        prompt = next((folder/'ready_for_web').glob('*.md')).read_text()
        self.assertIn(encode(graphic),prompt)
        self.assertIn('41064908',prompt)
        self.assertIn(row['json_hash'],prompt)
        self.assertEqual((self.store.root/revision['summary']).read_bytes(),original)
        self.assertEqual(self.store.one('revisions',revision['id'])['status'],'READY_FOR_WEB')

    def test_case_b_consensus_multiple_graphics_with_optional_overview(self):
        revision, data = self.paper('42835691',pages=53,kind='guideline_consensus',topics=3)
        data['visualization_strategy']['include_overview'] = True
        data['recommendations'] = [{'kind':'expert_consensus','text':'Synthetic conditional statement',
            'population':'selected population','conditions':'condition must hold','exceptions':'exception applies',
            'agreement':'80% expert agreement','evidence_strength':'not graded',
            'evidence_references':[{'location':'p.2 statement 4','supporting_text':'Synthetic quotation'}]}]
        data['topics'][1]['recommendation_indices'] = [0]
        prepare_web(self.store,revision,data)
        rows = self.rows(revision)
        self.assertEqual(len(rows),4)
        self.assertEqual(rows[0]['topic_id'],'overview')
        graphic = check_graphic(self.store,rows[2])
        self.assertIsNone(graphic['pico'])
        self.assertEqual(graphic['recommendations'],data['recommendations'])
        self.assertIsNone(check_graphic(self.store,rows[0])['practice_change'])

    def test_case_c_long_single_question_review_stays_single(self):
        revision,_ = self.paper(pages=60,kind='systematic_review')
        prepare_web(self.store,revision)
        self.assertEqual(len(self.rows(revision)),1)

    def test_case_d_short_independent_questions_split(self):
        revision,_ = self.paper(pages=2,kind='narrative_review',topics=6)
        prepare_web(self.store,revision)
        self.assertEqual(len(self.rows(revision)),6)

    def test_case_e_wrong_paper_image_rejected_never_sent(self):
        revision,_ = self.paper()
        prepare_web(self.store,revision)
        import_image(self.store,revision,'main',self.image())
        review_image(self.store,revision,'main',False,'Reviewer','Image depicts another paper')
        api = FakeNotion()
        with self.assertRaisesRegex(ValueError,'承認'):
            Notion(self.store,api,'db').sync(self.store.one('revisions',revision['id']))
        self.assertEqual(api.calls,[])
        self.assertEqual(self.rows(revision)[0]['status'],'IMAGE_REJECTED')

    def test_manual_batch_result_creates_handoff_without_image_api(self):
        revision,data = self.paper()
        api = FakeOpenAI()
        runner = Batches(self.store,api)
        self.store.db.execute("INSERT INTO attempts(id,revision_id,stage,status,reserve,period,created_at) VALUES(?,?,'analysis','submitted',1,'test','test')",('a',revision['id']))
        self.store.db.commit()
        body = {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':encode({k:data[k] for k in master_schema()['properties']})}]}],
                'usage':{'input_tokens':10,'output_tokens':10}}
        runner.accept(self.store.one('attempts','a'),{'response':{'status_code':200,'body':body}})
        self.assertEqual(self.store.one('revisions',revision['id'])['status'],'READY_FOR_WEB')
        runner.submit()
        self.assertEqual(api.posts,0)
        self.assertEqual(len(self.store.rows('SELECT * FROM attempts')),1)

    def test_manual_direct_only_calls_analysis_and_restarts_without_api(self):
        from datetime import datetime, timedelta, timezone
        revision,data = self.paper()
        self.store.update('revisions',revision['id'],summary=None,status='analysis_ready')
        current = datetime.now(timezone.utc)
        self.store.set_setting('budget',{'start':(current-timedelta(days=1)).isoformat(),'end':(current+timedelta(days=20)).isoformat(),
            'checked_at':current.isoformat(),'remaining':100,'other_usage':20,'production':False,'no_auto_recharge':True,'purchased_checked':True,'image_suspended_reason':'Image quality hold'})
        api = Mock()
        api.upload.return_value = 'synthetic-pdf'
        api.count_input.return_value = 100
        payload = {k:data[k] for k in master_schema()['properties']}
        api.call.return_value = Mock(status_code=200,json=Mock(return_value={'status':'completed',
            'output':[{'type':'message','content':[{'type':'output_text','text':encode(payload)}]}],
            'usage':{'input_tokens':100,'output_tokens':100}}))
        Direct(self.store,api).run(['41064908'])
        Direct(self.store,api).run(['41064908'])
        self.assertEqual([c.args[:2] for c in api.call.call_args_list],[('POST','/responses')])
        self.assertEqual(self.store.one('revisions',revision['id'])['status'],'READY_FOR_WEB')
        self.assertEqual(self.store.setting('budget')['image_suspended_reason'],'Image quality hold')

    def test_explicit_analysis_resume_retains_quality_hold_and_never_generates_image(self):
        from datetime import datetime,timedelta,timezone
        revision,data=self.paper()
        self.store.update('revisions',revision['id'],summary=None,status='analysis_ready')
        current=datetime.now(timezone.utc)
        budget={'start':(current-timedelta(days=1)).isoformat(),'end':(current+timedelta(days=20)).isoformat(),
            'checked_at':current.isoformat(),'remaining':100,'other_usage':20,'production':False,
            'no_auto_recharge':True,'purchased_checked':True,'suspended_reason':'Unrelated image'}
        self.store.set_setting('budget',budget)
        api=Mock(); api.upload.return_value='synthetic-pdf'; api.count_input.return_value=100
        payload={k:data[k] for k in master_schema()['properties']}
        api.call.return_value=Mock(status_code=200,json=Mock(return_value={'status':'completed',
            'output':[{'type':'message','content':[{'type':'output_text','text':encode(payload)}]}],
            'usage':{'input_tokens':100,'output_tokens':100}}))
        with self.assertRaisesRegex(ValueError,'停止'):
            Direct(self.store,api).run(['41064908'])
        Direct(self.store,api).run(['41064908'],resume_analysis_only=True)
        self.assertEqual([c.args[:2] for c in api.call.call_args_list],[('POST','/responses')])
        self.assertEqual(self.store.setting('budget'),budget)
        attempt=self.store.rows('SELECT * FROM attempts')[0]
        self.assertTrue(json.loads(attempt['details'])['resume_analysis_only'])
        with self.assertRaisesRegex(ValueError,'精読のみ'):
            self.store.reserve(revision,'image',1,resume_analysis_only=True)
        with self.assertRaisesRegex(ValueError,'停止中'):
            self.store.reserve(revision,'analysis',1)
        stale=dict(budget,checked_at=(current-timedelta(days=2)).isoformat())
        self.store.set_setting('budget',stale)
        with self.assertRaisesRegex(ValueError,'24時間'):
            self.store.reserve(revision,'analysis',1,resume_analysis_only=True)

    def test_scoped_resume_rejects_notion_images_and_multiple_targets_before_network(self):
        for options in (['--notion'],['--allow-image-api'],['--paper','42835691']):
            args=cli.parser().parse_args(['direct','--paper','41064908','--yes','--resume-analysis-only']+options)
            with patch.object(cli,'API',side_effect=AssertionError('network')), self.assertRaisesRegex(ValueError,'精読のみ'):
                cli.dispatch(self.store,args)
        with self.assertRaisesRegex(ValueError,'併用'):
            Direct(self.store,Mock(),allow_image_api=True).run(['41064908'],resume_analysis_only=True)

    def test_scoped_image_resume_generates_once_retains_hold_and_requires_review(self):
        import base64
        from datetime import datetime,timedelta,timezone
        revision,_=self.paper()
        cfg=json.loads(revision['config']); cfg['image_generation_mode']='api'
        self.store.update('revisions',revision['id'],config=encode(cfg))
        current=datetime.now(timezone.utc)
        budget={'start':(current-timedelta(days=1)).isoformat(),'end':(current+timedelta(days=20)).isoformat(),
            'checked_at':current.isoformat(),'remaining':100,'other_usage':20,'production':False,
            'no_auto_recharge':True,'purchased_checked':True,'suspended_reason':'Unrelated image'}
        self.store.set_setting('budget',budget)
        api=Mock()
        api.call.return_value=Mock(status_code=200,json=Mock(return_value={
            'data':[{'b64_json':base64.b64encode(png()).decode()}],
            'usage':{'input_tokens':100,'output_tokens':100,'input_tokens_details':{'text_tokens':100,'image_tokens':0}}}))
        runner=Direct(self.store,api,allow_image_api=True)
        with self.assertRaisesRegex(ValueError,'停止'):
            runner.run(['41064908'])
        runner.run(['41064908'],resume_image_only=True)
        runner.run(['41064908'],resume_image_only=True)
        self.assertEqual([c.args[:2] for c in api.call.call_args_list],[('POST','/images/generations')])
        api.upload.assert_not_called(); api.count_input.assert_not_called()
        self.assertEqual(self.store.setting('budget'),budget)
        revision=self.store.one('revisions',revision['id'])
        self.assertEqual(revision['status'],'IMAGE_REVIEW_REQUIRED')
        self.assertEqual(self.rows(revision)[0]['status'],'IMAGE_REVIEW_REQUIRED')
        self.assertTrue(json.loads(self.store.rows('SELECT * FROM attempts')[0]['details'])['resume_image_only'])
        with self.assertRaises(ValueError):
            Notion(self.store,Mock(),'db').sync(revision)
        with self.assertRaisesRegex(ValueError,'画像のみ'):
            self.store.reserve(revision,'analysis',1,resume_image_only=True)
        with self.assertRaisesRegex(ValueError,'停止中'):
            self.store.reserve(revision,'image',1)
        self.store.set_setting('budget',dict(budget,checked_at=(current-timedelta(days=2)).isoformat()))
        with self.assertRaisesRegex(ValueError,'24時間'):
            self.store.reserve(revision,'image',1,resume_image_only=True)

    def test_scoped_image_resume_rejects_wrong_scope_before_network(self):
        base=['direct','--paper','41064908','--yes','--resume-image-only']
        for options in ([],['--allow-image-api','--notion'],['--allow-image-api','--resume-analysis-only'],
                        ['--allow-image-api','--paper','42835691']):
            args=cli.parser().parse_args(base+options)
            with patch.object(cli,'API',side_effect=AssertionError('network')), self.assertRaisesRegex(ValueError,'画像限定'):
                cli.dispatch(self.store,args)
        revision,_=self.paper()
        api=Mock()
        with self.assertRaisesRegex(ValueError,'api版'):
            Direct(self.store,api,allow_image_api=True).run(['41064908'],resume_image_only=True)
        cfg=json.loads(revision['config']); cfg['image_generation_mode']='api'
        self.store.update('revisions',revision['id'],config=encode(cfg),summary=None)
        with self.assertRaisesRegex(ValueError,'api版'):
            Direct(self.store,api,allow_image_api=True).run(['41064908'],resume_image_only=True)
        api.call.assert_not_called()

    def test_old_api_config_does_not_bypass_default_gate(self):
        revision,_ = self.paper()
        cfg = json.loads(revision['config']); cfg['image_generation_mode'] = 'api'
        self.store.update('revisions',revision['id'],config=encode(cfg))
        revision = self.store.one('revisions',revision['id'])
        api = Mock()
        with self.assertRaisesRegex(ValueError,'明示'):
            Batches(self.store,api).request_body(revision,'image')
        Batches(self.store,api).submit()
        api.create.assert_not_called()
        self.assertEqual(self.store.one('revisions',revision['id'])['status'],'READY_FOR_WEB')

    def test_explicit_api_mode_rejects_multi_image_before_reserving(self):
        revision,_ = self.paper(topics=2)
        cfg = json.loads(revision['config']); cfg['image_generation_mode']='api'
        self.store.update('revisions',revision['id'],config=encode(cfg))
        with self.assertRaisesRegex(ValueError,'複数画像'):
            Batches(self.store,Mock(),allow_image_api=True).estimate(self.store.one('revisions',revision['id']),'image')
        self.assertEqual(self.store.rows('SELECT * FROM attempts'),[])

    def test_validation_rejects_missing_claims_bad_ids_and_missing_evidence(self):
        _,data = self.paper(topics=2)
        for alter in (
            lambda d:d['topics'].pop(),
            lambda d:d['topics'][0].update(topic_id='../escape'),
            lambda d:d['topics'][0].update(result_indices=[99]),
            lambda d:d['topics'][0].update(source_sections=['missing']),
            lambda d:d['result_evidence'].clear(),
            lambda d:d.update(pico=None),
        ):
            broken=copy.deepcopy(data); alter(broken)
            with self.assertRaises(ValueError): validate_master(broken)

    def test_plan_changes_only_affected_graphic_and_retains_old_artifacts(self):
        revision,data = self.paper(topics=2)
        original_folder = prepare_web(self.store,revision)
        self.approve(revision,'topic_0')
        self.approve(revision,'topic_1','blue')
        old = self.rows(revision)
        plan = {k:copy.deepcopy(data[k]) for k in ('topics','visualization_strategy')}
        plan['topics'][1]['central_message']='Revised focus'
        folder = prepare_web(self.store,revision,plan=plan)
        new = self.rows(revision)
        self.assertNotEqual(original_folder,folder)
        self.assertEqual(new[0]['id'],old[0]['id'])
        self.assertEqual(new[0]['status'],'IMAGE_APPROVED')
        self.assertNotEqual(new[1]['id'],old[1]['id'])
        self.assertEqual(new[1]['status'],'READY_FOR_WEB')
        self.assertTrue((self.store.root/old[1]['image_path']).exists())
        prepare_web(self.store,revision)
        self.assertEqual([r['id'] for r in self.rows(revision)],[r['id'] for r in new])

    def test_manual_merge_uses_all_indices(self):
        revision,data=self.paper(topics=2)
        prepare_web(self.store,revision)
        plan={k:copy.deepcopy(data[k]) for k in ('topics','visualization_strategy')}
        plan['topics']= [dict(plan['topics'][0],topic_id='merged',result_indices=[0,1])]
        plan['visualization_strategy'].update(mode='single',reason='User reviewed combined clinical question')
        prepare_web(self.store,revision,plan=plan)
        self.assertEqual(check_graphic(self.store,self.rows(revision)[0])['main_results'],data['main_results'])

    def test_png_format_ratio_duplicates_and_overwrite(self):
        revision,_=self.paper(topics=2)
        prepare_web(self.store,revision)
        with self.assertRaisesRegex(ValueError,'16:9'):
            import_image(self.store,revision,'topic_0',self.image(size=(100,100)))
        from PIL import Image
        disguised=self.root/'not_png.png'; Image.new('RGB',(1600,900)).save(disguised,format='JPEG')
        with self.assertRaisesRegex(ValueError,'PNG'):
            import_image(self.store,revision,'topic_0',disguised)
        self.approve(revision,'topic_0')
        old=self.rows(revision)[0]
        with self.assertRaisesRegex(ValueError,'別のGraphic'):
            import_image(self.store,revision,'topic_1',self.image())
        import_image(self.store,revision,'topic_0',self.image())
        self.assertEqual(self.rows(revision)[0]['status'],'IMAGE_APPROVED')
        import_image(self.store,revision,'topic_0',self.image('blue'))
        self.assertEqual(self.rows(revision)[0]['status'],'IMAGE_REVIEW_REQUIRED')
        self.assertTrue((self.store.root/old['image_path']).exists())

    def test_web_png_pixel_rounding_preserves_original_and_requires_review(self):
        revision,_=self.paper()
        prepare_web(self.store,revision)
        for size in ((1672,941),(1600,901),(1600,899)):
            with self.subTest(size=size):
                path=self.image(size=size)
                raw=path.read_bytes()
                row=import_image(self.store,revision,'main',path)
                self.assertEqual((self.store.root/row['image_path']).read_bytes(),raw)
                self.assertEqual(path.read_bytes(),raw)
                self.assertEqual(row['status'],'IMAGE_REVIEW_REQUIRED')
        for size in ((1672,942),(1600,902),(1600,1200)):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError,'16:9'):
                import_image(self.store,revision,'main',self.image(size=size))

    def test_three_two_png_keeps_bytes_history_and_remains_unapproved(self):
        revision,_=self.paper()
        prepare_web(self.store,revision)
        self.approve(revision)
        previous=self.rows(revision)[0]
        for size in ((1536,1024),(1536,1023),(1536,1025)):
            with self.subTest(size=size):
                path=self.image(size=size); raw=path.read_bytes()
                row=import_image(self.store,revision,'main',path)
                self.assertEqual((self.store.root/row['image_path']).read_bytes(),raw)
                self.assertEqual(path.read_bytes(),raw)
                self.assertEqual(row['status'],'IMAGE_REVIEW_REQUIRED')
                self.assertEqual(json.loads(row['approval']),{})
                self.assertEqual(json.loads(row['notion']),{})
                self.assertTrue((self.store.root/previous['image_path']).exists())
                api=FakeNotion()
                with self.assertRaises(ValueError):
                    Notion(self.store,api,'db').sync(self.store.one('revisions',revision['id']))
                self.assertEqual(api.calls,[])
        self.assertTrue(any(h.get('image_hash')==previous['image_hash'] for h in json.loads(row['history'])))
        for size in ((1536,1022),(1536,1026),(1024,1536),(1200,1200)):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError,'3:2'):
                import_image(self.store,revision,'main',self.image(size=size))

    def test_gallery_image_fills_empty_property_and_preserves_existing_image(self):
        from paper_pipeline.notion import rich
        for existing in ([],[{'name':'User image','type':'external','external':{'url':'https://example.test/user.png'}}]):
            with self.subTest(existing=bool(existing)):
                revision,_=self.paper(pmid='41064908' if not existing else '39678704',pages=1 if not existing else 2)
                prepare_web(self.store,revision); self.approve(revision,color='white' if not existing else 'blue')
                paper=self.store.one('papers',revision['paper_id'])
                api=FakeNotion(); api.properties['Graphic Image']={'type':'files'}
                page=api.call('POST','/pages',json={'parent':{'data_source_id':'ds'},'properties':{
                    'PMID':{'rich_text':rich(paper['pmid'])},'Automation ID':{'rich_text':rich(paper['id'])},
                    'Human Checked':{'checkbox':False},'Graphic Image':{'files':copy.deepcopy(existing)}}})
                client=Notion(self.store,api,'db'); client.sync(revision)
                saved=api.pages[page['id']]['properties']
                self.assertFalse(saved['Human Checked']['checkbox'])
                if existing:
                    self.assertEqual(saved['Graphic Image']['files'],existing)
                else:
                    state=json.loads(self.rows(revision)[0]['notion'])
                    self.assertEqual(saved['Graphic Image']['files'][0]['file_upload']['id'],state['image_upload']['id'])
                    self.assertEqual(len(api.uploads),3)
                calls=len(api.calls); client.sync(self.store.one('revisions',revision['id']))
                self.assertEqual(len(api.calls),calls)

    def test_modified_image_json_or_master_blocks_all_notion_calls(self):
        revision,_=self.paper()
        prepare_web(self.store,revision); self.approve(revision)
        row=self.rows(revision)[0]
        for path in (self.store.root/row['image_path'], self.store.root/row['json_path'],
                     (self.store.root/row['json_path']).parent.parent/'master.json'):
            raw=path.read_bytes(); path.write_bytes(raw+b' ')
            api=FakeNotion()
            with self.assertRaisesRegex(ValueError,'変更'):
                Notion(self.store,api,'db').sync(self.store.one('revisions',revision['id']))
            self.assertEqual(api.calls,[])
            path.write_bytes(raw)

    def test_partial_notion_completion_one_page_and_no_duplicate_upload(self):
        revision,_=self.paper(topics=2)
        prepare_web(self.store,revision); self.approve(revision,'topic_0')
        api=FakeNotion(); client=Notion(self.store,api,'db')
        client.sync(self.store.one('revisions',revision['id']))
        self.assertEqual([r['status'] for r in self.rows(revision)],['COMPLETED','READY_FOR_WEB'])
        first_uploads=set(api.uploads)
        self.approve(revision,'topic_1','blue')
        client.sync(self.store.one('revisions',revision['id']))
        self.assertEqual(len(api.pages),1)
        self.assertEqual(len(api.uploads),6)
        self.assertTrue(first_uploads <= set(api.uploads))
        client.sync(self.store.one('revisions',revision['id']))
        self.assertEqual(len(api.uploads),6)
        self.assertEqual(self.store.one('revisions',revision['id'])['status'],'completed')

    def test_notion_schema_missing_does_not_mutate_database(self):
        revision,_=self.paper()
        prepare_web(self.store,revision); self.approve(revision)
        api=FakeNotion(); api.properties.pop('Automation ID')
        with self.assertRaisesRegex(ValueError,'自動変更'):
            Notion(self.store,api,'db').sync(revision)
        self.assertFalse(any(method != 'GET' for method,_,_ in api.calls))

    def test_legacy_consensus_waits_for_explicit_structure_plan(self):
        revision,data=self.paper('42835691',kind='guideline_consensus',topics=2)
        legacy=sample_summary(self.store.one('papers',revision['paper_id']))
        legacy.update(study_type='Consensus Delphi',study_design='Consensus')
        folder=prepare_web(self.store,revision,legacy)
        self.assertEqual(self.rows(revision),[])
        self.assertEqual(self.store.one('revisions',revision['id'])['status'],'structure_review_required')
        master=json.loads((folder/'master.json').read_text())
        plan=json.loads((folder/'plan.json').read_text())
        plan['document_structure']=[{'section_id':'legacy','title':'Confirmed retained result','location':'p.2'}]
        prepare_web(self.store,revision,master,plan)
        self.assertEqual(len(self.rows(revision)),1)
        self.assertEqual(check_graphic(self.store,self.rows(revision)[0])['main_results'],legacy['main_results'])

    def test_legacy_original_does_not_require_reanalysis(self):
        revision,_=self.paper()
        legacy=sample_summary(self.store.one('papers',revision['paper_id']))
        prepare_web(self.store,revision,legacy)
        self.assertEqual(len(self.rows(revision)),1)
        self.assertEqual(self.store.rows('SELECT * FROM attempts'),[])

    def test_additive_migration_backs_up_database_and_keeps_rows(self):
        self.store.close()
        path=self.root/'legacy'; path.mkdir()
        db=sqlite3.connect(path/'ledger.sqlite3')
        db.execute('CREATE TABLE revisions (id TEXT PRIMARY KEY, paper_id TEXT, source_id TEXT, signature TEXT,status TEXT,config TEXT,summary TEXT,image TEXT,error TEXT,notion TEXT,created_at TEXT)')
        db.execute("INSERT INTO revisions(id,status) VALUES ('old','quality_wait')")
        db.commit(); db.close()
        self.store=Store(path)
        self.assertEqual(self.store.one('revisions','old')['status'],'quality_wait')
        backups=list((path/'schema_backups').glob('*.sqlite3'))
        self.assertEqual(len(backups),1)
        with sqlite3.connect(backups[0]) as backup:
            self.assertEqual(backup.execute('SELECT status FROM revisions').fetchone()[0],'quality_wait')
        self.store.close(); self.store=Store(path)
        self.assertEqual(len(list((path/'schema_backups').glob('*.sqlite3'))),1)

    def test_cli_handoff_export_import_review_are_offline(self):
        revision,_=self.paper()
        destination=self.root/'export'
        commands=[['prepare-web','--paper','41064908','--export',str(destination)],
                  ['import-image','--paper','41064908','--topic','main','--source',str(self.image())],
                  ['review-image','--paper','41064908','--topic','main','--reject','--reviewer','Tester','--note','Wrong content']]
        with patch.object(cli,'API',side_effect=AssertionError('OpenAI')),patch.object(cli,'NotionAPI',side_effect=AssertionError('Notion')):
            for args in commands: cli.dispatch(self.store,cli.parser().parse_args(args))
        self.assertTrue((destination/'master.json').exists())
        self.assertEqual(self.rows(revision)[0]['status'],'IMAGE_REJECTED')

    def test_long_batch_counts_full_pdf_without_truncation(self):
        from datetime import datetime, timedelta, timezone
        revision,_=self.paper(pages=53,kind='guideline_consensus')
        self.store.update('revisions',revision['id'],summary=None,status='analysis_ready')
        current=datetime.now(timezone.utc)
        self.store.set_setting('budget',{'start':(current-timedelta(days=1)).isoformat(),'end':(current+timedelta(days=20)).isoformat(),
            'checked_at':current.isoformat(),'remaining':100,'other_usage':20,'production':False,'no_auto_recharge':True,'purchased_checked':True})
        api=FakeOpenAI()
        api.count_input=Mock(return_value=134837)
        runner=Batches(self.store,api)
        self.assertEqual(runner.submit(),1)
        body=api.count_input.call_args.args[0]
        file_id=body['input'][0]['content'][1]['file_id']
        source=self.store.one('sources',revision['source_id'])
        self.assertEqual(api.files[file_id],(self.store.root/source['path']).read_bytes())
        self.assertEqual(json.loads(self.store.one('revisions',revision['id'])['config'])['counted_input_tokens'],134837)
        runner.submit()
        self.assertEqual(api.count_input.call_count,1)
        self.assertEqual(api.posts,1)

    def test_old_handwritten_original_missing_new_fields_is_retained(self):
        revision,_=self.paper()
        legacy=sample_summary(self.store.one('papers',revision['paper_id']))
        legacy['study_design']='Original Article'
        for key in ('study_type','clinical_impact','summary_jp','evidence_notes','result_evidence'):
            legacy.pop(key)
        original=copy.deepcopy(legacy)
        prepare_web(self.store,revision,legacy)
        graphic=check_graphic(self.store,self.rows(revision)[0])
        self.assertEqual(graphic['main_results'],legacy['main_results'])
        self.assertEqual(legacy,original)
        self.assertIn('未確認',graphic['evidence_references'][0]['location'])

    def test_backup_and_restart_preserve_approval(self):
        revision,_=self.paper()
        prepare_web(self.store,revision); self.approve(revision)
        self.store.backup(self.root/'backup')
        self.store.close(); self.store=Store(self.root/'backup')
        row=self.rows(revision)[0]
        check_graphic(self.store,row,approved=True)
        before=row['id']
        prepare_web(self.store,self.store.one('revisions',revision['id']))
        self.assertEqual(self.rows(revision)[0]['id'],before)
        self.assertEqual(self.rows(revision)[0]['status'],'IMAGE_APPROVED')

    def test_unapproved_legacy_notion_ready_is_blocked(self):
        revision,_=self.paper()
        self.store.update('revisions',revision['id'],status='notion_ready')
        api=FakeNotion()
        with self.assertRaisesRegex(ValueError,'承認'):
            Notion(self.store,api,'db').sync(self.store.one('revisions',revision['id']))
        self.assertEqual(api.calls,[])

    def test_prompts_schema_and_default_do_not_force_review_pico(self):
        revision,_=self.paper()
        self.assertEqual(config()['image_generation_mode'],'manual_web')
        self.assertEqual(schema('fulltext-v4'),master_schema())
        prompt=review_prompt(self.store.one('papers',revision['paper_id']),'fulltext-v4')
        self.assertIn('Delphi',prompt)
        self.assertNotIn('非該当PICO項目は「非該当」',prompt)
        api=Mock()
        from paper_pipeline.batches import API
        instance=API('synthetic-key',session=api)
        api.request.return_value=Mock(status_code=200)
        instance.check_models()
        self.assertEqual(api.request.call_count,1)


if __name__=='__main__':
    unittest.main()
