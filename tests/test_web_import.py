"""Offline Web JSON -> per-topic PNG -> existing Notion path, using synthetic data."""
import copy
import json
import unittest
from unittest.mock import patch

import test_hybrid_workflow as fixtures
from test_paper_pipeline import FakeNotion
from paper_pipeline import web_import
from paper_pipeline.batches import API, Batches, config
from paper_pipeline.dashboard import Dashboard
from paper_pipeline.graphics import active_graphics, check_graphic, master_schema
from paper_pipeline.library import sync_library
from paper_pipeline.store import encode


class WebImportTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.HybridTests(); self.f.setUp()
        self.addCleanup(self.f.doCleanups); self.addCleanup(self.f.tearDown)
        self.store = self.f.store
        self.old, data = self.f.paper(kind='systematic_review', topics=2)
        self.paper = self.store.one('papers', self.old['paper_id'])
        self.source = self.store.one('sources', self.old['source_id'])
        self.data = {k: data[k] for k in master_schema()['properties']}
        self.data['source_hash'] = self.source['hash']
        (self.f.root / 'input').mkdir()
        self.dash = Dashboard(self.f.root, self.store.root)
        sync_library(self.store)

    def save(self, data=None):
        path = web_import.inbox(self.store, self.paper) / 'master.json'
        path.write_text(encode(data or self.data))
        return path

    def import_file(self):
        candidates = self.dash.snapshot(self.store)['web_json_candidates']
        return self.dash.run('import-web-json', {'items': candidates}, self.store)

    def test_web_full_workflow_uses_no_openai_and_preserves_originals(self):
        original = (self.store.root / self.old['summary']).read_bytes()
        original_cfg = self.old['config']
        path = self.save()
        with patch.dict('os.environ', {}, clear=True), patch('paper_pipeline.dashboard.API') as api:
            with patch('paper_pipeline.dashboard.subprocess.run') as clipboard:
                self.dash.run('copy-json-request', {'pmid': self.paper['pmid']}, self.store)
            prompt = clipboard.call_args.kwargs['input'].decode()
            self.assertIn(self.source['hash'], prompt)
            self.assertIn('source.pdf', prompt)
            self.assertIn('source_hash', prompt)
            self.assertIn('1件', self.import_file())
            api.assert_not_called()
        revision = self.dash.latest(self.store, self.paper)
        self.assertNotEqual(revision['id'], self.old['id'])
        self.assertEqual(revision['status'], 'READY_FOR_WEB')
        self.assertEqual((self.store.root / self.old['summary']).read_bytes(), original)
        self.assertEqual(self.store.one('revisions', self.old['id'])['config'], original_cfg)
        self.assertEqual(json.loads(path.read_text()), self.data)
        master = json.loads((self.store.root / revision['summary']).read_text())
        self.assertFalse(master['human_checked'])
        self.assertEqual(master['creation_method'], 'chatgpt_web')
        self.assertFalse(self.store.rows('SELECT * FROM attempts'))
        self.assertIsNone(self.dash.snapshot(self.store)['papers'][0]['json_target'])
        self.assertEqual(len(active_graphics(self.store, revision['id'])), 2)
        Batches(self.store, None).submit()  # No older waiting revision enters API processing.
        for i, g in enumerate(active_graphics(self.store, revision['id'])):
            graphic = check_graphic(self.store, g)
            self.assertEqual(len(graphic['main_results']), 1)
            target = web_import.inbox(self.store, self.paper) / 'images' / (g['topic_id'] + '.png')
            target.write_bytes(self.f.image(color='white' if i==0 else 'blue').read_bytes())
        items = self.dash.candidates(self.store)
        self.assertEqual(len(items), 2)
        self.assertTrue(all(not c['error'] for c in items))
        fake = FakeNotion()
        with patch.dict('os.environ', {'NOTION_DATABASE_ID': 'db'}, clear=True), patch('paper_pipeline.dashboard.NotionAPI', return_value=fake):
            message = self.dash.run('upload', {'items': items, 'confirmed': True, 'reviewer': 'Tester', 'note': 'Synthetic check'}, self.store)
        self.assertIn('2件', message)
        self.assertEqual(self.dash.latest(self.store, self.paper)['status'], 'completed')
        self.assertTrue(all(c['synced'] for c in self.dash.candidates(self.store)))
        self.assertEqual(len(fake.pages), 1)
        self.assertEqual(len(self.store.rows('SELECT * FROM attempts')), 0)

    def test_same_json_is_idempotent_changed_json_creates_version(self):
        path = self.save(); self.import_file()
        revision = self.dash.latest(self.store, self.paper)
        count = len(self.store.rows('SELECT * FROM revisions'))
        web_import.import_master(self.store, self.paper, path.read_bytes())
        self.assertEqual(len(self.store.rows('SELECT * FROM revisions')), count)
        self.data['topics'][0]['central_message'] = 'Revised synthetic message'
        self.save(); self.import_file()
        self.assertEqual(len(self.store.rows('SELECT * FROM revisions')), count + 1)
        self.assertTrue((self.store.root / revision['summary']).is_file())
        self.assertTrue(all(g['status'] == 'READY_FOR_WEB' for g in active_graphics(self.store, self.dash.latest(self.store, self.paper)['id'])))

    def test_interrupted_local_import_retries_without_losing_old_revision(self):
        self.save()
        with patch('paper_pipeline.web_import.prepare_web', side_effect=OSError('Synthetic disk failure')):
            self.assertIn('Synthetic disk failure', self.import_file())
        revision = self.dash.latest(self.store, self.paper)
        self.assertFalse(self.dash.web_json_candidates(self.store)[0]['imported'])
        self.assertNotEqual(self.store.one('revisions', self.old['id'])['status'], 'superseded')
        self.assertIn('1件', self.import_file())
        self.assertEqual(self.dash.latest(self.store, self.paper)['id'], revision['id'])
        self.assertEqual(self.dash.latest(self.store, self.paper)['status'], 'READY_FOR_WEB')
        self.assertEqual(self.store.one('revisions', self.old['id'])['status'], 'superseded')

    def test_wrong_identity_source_or_missing_evidence_never_changes_ledger(self):
        for key, value in [('pmid', '123'), ('title', 'Other paper'), ('source_hash', 'changed'), ('result_evidence', []), ('topics', [])]:
            with self.subTest(key=key):
                data = copy.deepcopy(self.data); data[key] = value
                before = list(self.store.db.iterdump())
                with self.assertRaises(ValueError):
                    web_import.import_master(self.store, self.paper, encode(data).encode())
                self.assertEqual(list(self.store.db.iterdump()), before)
        self.save({'not': 'a master'})
        self.assertTrue(self.dash.web_json_candidates(self.store)[0]['error'])

    def test_pending_or_ambiguous_api_blocks_web_import_without_mutation(self):
        for status in ('submitted', 'unknown', 'review_required', 'direct_unknown'):
            with self.subTest(status=status):
                with self.store.db:
                    self.store.db.execute("INSERT OR REPLACE INTO attempts(id,revision_id,stage,status,reserve,period,created_at) VALUES('pending',?,'analysis',?,1,'test','test')", (self.old['id'], status))
                before = list(self.store.db.iterdump())
                with self.assertRaisesRegex(ValueError, '処理中・結果不明'):
                    web_import.import_master(self.store, self.paper, encode(self.data).encode())
                self.assertEqual(list(self.store.db.iterdump()), before)

    def test_stale_json_selection_and_duplicate_png_are_rejected(self):
        self.save(); items = self.dash.web_json_candidates(self.store)
        self.data['topics'][0]['central_message'] = 'Changed'
        self.save()
        with self.assertRaisesRegex(ValueError, '変わりました'):
            self.dash.run('import-web-json', {'items': items}, self.store)
        self.import_file()
        image = self.f.image().read_bytes()
        (web_import.inbox(self.store, self.paper) / 'images/topic_0.png').write_bytes(image)
        (self.dash.inbox / f"PMID_{self.paper['pmid']}_topic_0.png").write_bytes(image)
        self.assertTrue(all('複数' in c['error'] for c in self.dash.candidates(self.store)))
        with patch('paper_pipeline.dashboard.NotionAPI') as api, self.assertRaises(ValueError):
            self.dash.run('upload', {'items': self.dash.candidates(self.store), 'confirmed': True, 'reviewer': 'Tester', 'note': 'Check'}, self.store)
        api.assert_not_called()

    def test_batch_endpoint_and_cost_account_for_cache(self):
        api = API('synthetic')
        with patch.object(api, 'call') as call:
            api.create('file', 'analysis', 'attempt')
        self.assertEqual(call.call_args.args, ('POST', '/batches'))
        self.assertEqual(call.call_args.kwargs['json']['completion_window'], '24h')
        self.assertEqual(config()['mode'], 'batch')
        usage = {'input_tokens': 100000, 'output_tokens': 10000,
                 'input_tokens_details': {'cached_tokens': 10000, 'cache_write_tokens': 80000}}
        self.assertAlmostEqual(Batches(self.store, None).actual_cost(usage, 'analysis', config()['rates']), .1605)
        usage['input_tokens_details']['cache_write_tokens'] = 100001
        self.assertIsNone(Batches(self.store, None).actual_cost(usage, 'analysis', config()['rates']))
