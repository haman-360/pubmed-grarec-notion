"""JSON-only GUI integration with fake Batch transport and disposable workspaces."""
import copy
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pypdf import PdfWriter
from paper_pipeline import json_batch as jb
from paper_pipeline.graphics import adapt_legacy
from paper_pipeline.manual import Workspace
from paper_pipeline.store import Store, encode, digest
from grarec_app import Application
from test_paper_pipeline import FakeOpenAI, sample_summary

PMID = '90000001'


class FakeAPI(FakeOpenAI):
    def check_models(self, include_images=False):
        assert not include_images


class JsonBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.repo = self.root / 'repo'; self.repo.mkdir()
        self.ws = Workspace.create(self.root / 'shared', 'a' * 32, 'Test Mac')
        self.ws.add(PMID)
        writer = PdfWriter(); writer.add_blank_page(width=300, height=300)
        buf = io.BytesIO(); writer.write(buf); self.raw = buf.getvalue()
        self.ws.import_file(PMID, 'pdf', 'source.pdf', self.raw)
        self.master = adapt_legacy(sample_summary(dict(pmid=PMID, title='Synthetic paper', doi='')))
        self.master['source_hash'] = digest(self.raw)
        self.api = FakeAPI()
        self.network = patch('requests.Session.request', side_effect=AssertionError('network forbidden'))
        self.network.start(); self.addCleanup(self.network.stop)
        self.budget()

    def budget(self, **changes):
        data = dict(confirmed=True, remaining=10, other_usage=0,
                    end=(datetime.now(timezone.utc)+timedelta(days=30)).date().isoformat())
        data.update(changes)
        return jb.set_budget(self.repo, self.ws, data)

    def rows(self, table):
        store = Store(jb.local_root(self.repo, self.ws))
        try:
            return store.rows('SELECT * FROM ' + table)
        finally:
            store.close()

    def prepare(self):
        jb.prepare(self.repo, self.ws, [dict(pmid=PMID, heads=[self.ws.current(PMID)['id']])])
        return jb.status(self.repo, self.ws)['preview']

    def send(self):
        preview = self.prepare()
        return jb.submit(self.repo, self.ws, dict(confirmed=True, preview_id=preview['id']), self.api)

    def complete(self, master=None, **changes):
        body = dict(status='completed', usage=dict(input_tokens=1000, output_tokens=500),
                    output=[dict(type='message', content=[dict(type='output_text', text=encode(master or self.master))])])
        body.update(changes)
        self.api.complete(self.rows('attempts')[0], body)

    def test_full_batch_to_shared_json_and_manual_png_handoff(self):
        self.send()
        request = json.loads(next(raw for key, raw in self.api.files.items() if raw.startswith(b'{"body"')))
        self.assertEqual(request['url'], '/v1/responses')
        self.assertEqual(request['body']['text']['format']['schema']['properties']['source_hash']['enum'], [digest(self.raw)])
        self.assertEqual(request['body']['max_output_tokens'], 32000)
        self.complete(); jb.collect(self.repo, self.ws, self.api)
        self.assertEqual(self.ws.snapshot()['counts']['web_pending'], 1)
        event = self.ws.current(PMID)
        self.assertFalse(event['data']['done'])
        self.assertFalse(any(f['kind'] == 'png' for f in event['data']['files']))
        saved = next(f for f in event['data']['files'] if f['kind'] == 'json')
        self.assertFalse(json.loads(self.ws.read_asset(saved))['human_checked'])
        report = jb.status(self.repo, self.ws)
        self.assertEqual(report['jobs'][0]['status'], 'saved')
        self.assertTrue(self.ws.path(report['jobs'][0]['result']).is_file())
        self.assertEqual(self.rows('attempts')[0]['actual'], .0035)
        jb.collect(self.repo, self.ws, self.api)
        self.assertEqual(self.ws.current(PMID)['id'], event['id'])
        self.assertEqual(self.api.posts, 1)

    def test_prepare_and_double_click_never_send_unselected_or_duplicate(self):
        self.ws.add('90000002')
        preview = self.prepare(); self.assertEqual(self.api.posts, 0)
        data = dict(confirmed=True, preview_id=preview['id'])
        jb.submit(self.repo, self.ws, data, self.api)
        with self.assertRaises(ValueError): jb.submit(self.repo, self.ws, data, self.api)
        with self.assertRaisesRegex(ValueError, '送信履歴'): self.prepare()
        self.assertEqual(self.api.posts, 1)
        self.assertEqual(self.ws.category(self.ws.current('90000002')['data']), 'missing_pdf')

    def test_budget_refusal_does_not_upload_and_can_reprepare(self):
        self.budget(remaining=0)
        self.send()
        self.assertFalse(self.api.files); self.assertEqual(self.api.posts, 0)
        self.assertEqual(jb.status(self.repo, self.ws)['jobs'][0]['status'], 'budget_wait')
        self.budget(); self.send(); self.assertEqual(self.api.posts, 1)

    def test_budget_update_preserves_period_and_validates_expiry_and_confirmation(self):
        store = Store(jb.local_root(self.repo, self.ws)); start = store.setting('budget')['start']; store.close()
        self.budget(remaining=15)
        store = Store(jb.local_root(self.repo, self.ws)); self.assertEqual(start, store.setting('budget')['start']); store.close()
        for change in (dict(confirmed=False), dict(remaining=float('nan')), dict(other_usage=20), dict(end='2020-01-01')):
            with self.assertRaises(ValueError): self.budget(**change)

    def test_unknown_submission_reconciles_after_restart_without_resending(self):
        self.api.timeout_after_create = True; self.send()
        self.assertEqual(self.rows('attempts')[0]['status'], 'unknown')
        jb.collect(self.repo, self.ws, self.api)
        self.assertEqual(self.rows('attempts')[0]['status'], 'submitted')
        self.complete(); jb.collect(self.repo, self.ws, self.api)
        self.assertEqual(self.api.posts, 1)
        self.assertEqual(self.ws.snapshot()['counts']['web_pending'], 1)

    def test_unknown_with_no_match_keeps_reservation(self):
        self.api.timeout_before_create = True; self.send()
        jb.collect(self.repo, self.ws, self.api)
        attempt = self.rows('attempts')[0]
        self.assertEqual(attempt['status'], 'unknown'); self.assertIsNone(attempt['actual'])
        self.assertGreater(attempt['reserve'], 0)
        with self.assertRaises(ValueError): self.prepare()

    def test_changed_record_before_send_prevents_network(self):
        preview = self.prepare(); event = self.ws.current(PMID)
        self.ws.update(PMID, [event['id']], 'manually changed')
        with self.assertRaises(ValueError):
            jb.submit(self.repo, self.ws, dict(confirmed=True, preview_id=preview['id']), self.api)
        self.assertFalse(self.api.files)

    def test_replacement_pdf_can_be_selected_as_new_source_without_resending_old_job(self):
        self.send()
        self.ws.import_file(PMID, 'pdf', 'replacement.pdf', self.raw+b'\n')
        self.send()
        self.assertEqual(self.api.posts, 2)
        jobs = jb.status(self.repo, self.ws)['jobs']
        self.assertEqual(len({j['source_hash'] for j in jobs}), 2)

    def test_manual_json_during_batch_is_preserved_and_api_candidate_saved(self):
        self.send(); manual = copy.deepcopy(self.master); manual['one_line_summary'] = 'Manual version'
        self.ws.import_file(PMID, 'json', 'manual.json', encode(manual).encode())
        before = self.ws.current(PMID)
        self.complete(); jb.collect(self.repo, self.ws, self.api)
        self.assertEqual(self.ws.current(PMID), before)
        self.assertEqual(jb.status(self.repo, self.ws)['jobs'][0]['status'], 'candidate')

    def test_changed_pdf_and_unorganized_inputs_prevent_adoption(self):
        for mode in ('pdf', 'pending'):
            with self.subTest(mode=mode):
                # Separate test case workspaces using a fresh instance.
                case = JsonBatchTests(); case.setUp()
                try:
                    case.send()
                    if mode == 'pdf': case.ws.import_file(PMID, 'pdf', 'replacement.pdf', case.raw+b'\n')
                    else:
                        path = case.ws.path('00_取り込み待ち/PMID_'+PMID+'/master.json')
                        path.parent.mkdir(parents=True); path.write_text(encode(case.master))
                    before = case.ws.current(PMID)
                    case.complete(); jb.collect(case.repo, case.ws, case.api)
                    self.assertEqual(case.ws.current(PMID), before)
                    self.assertEqual(jb.status(case.repo, case.ws)['jobs'][0]['status'], 'candidate')
                finally: case.doCleanups()

    def test_invalid_structure_is_retained_and_manual_repair_advances(self):
        self.send(); bad = copy.deepcopy(self.master); bad['topics'][0]['source_sections'] = ['unknown']
        self.complete(bad); jb.collect(self.repo, self.ws, self.api)
        self.assertEqual(self.ws.snapshot()['counts']['attention'], 1)
        self.assertEqual(jb.status(self.repo, self.ws)['jobs'][0]['status'], 'structure_review_required')
        self.ws.import_file(PMID, 'json', 'fixed.json', encode(self.master).encode())
        self.assertEqual(self.ws.snapshot()['counts']['web_pending'], 1)

    def test_incomplete_and_wrong_source_are_not_imported(self):
        self.send(); self.complete(status='incomplete', incomplete_details={'reason':'max_output_tokens'})
        jb.collect(self.repo, self.ws, self.api)
        self.assertEqual(self.ws.snapshot()['counts']['json_pending'], 1)
        self.assertEqual(self.rows('attempts')[0]['status'], 'review_required')
        self.assertTrue(list(jb.local_root(self.repo, self.ws).glob('batches/*output*.jsonl')))

    def test_invalid_schema_or_wrong_pmid_hash_never_advances(self):
        for field, value in (('pmid', '90000002'), ('source_hash', 'wrong'), ('topics', 'invalid')):
            with self.subTest(field=field):
                case = JsonBatchTests(); case.setUp()
                try:
                    case.send(); bad = dict(case.master, **{field:value}); case.complete(bad)
                    jb.collect(case.repo, case.ws, case.api)
                    self.assertEqual(case.ws.snapshot()['counts']['json_pending'], 1)
                    self.assertEqual(case.rows('attempts')[0]['status'], 'review_required')
                finally: case.doCleanups()

    def test_pre_batch_upload_failure_allows_one_explicit_retry(self):
        with patch.object(self.api, 'upload', side_effect=RuntimeError('upload failed')):
            self.send()
        self.assertEqual(self.rows('attempts')[0]['status'], 'rejected')
        self.assertEqual(self.rows('attempts')[0]['actual'], 0)
        self.send(); self.assertEqual(self.api.posts, 1)

    def test_long_pdf_counted_only_after_confirmation_and_never_exceeds_budget(self):
        writer = PdfWriter()
        for _ in range(20): writer.add_blank_page(width=300, height=300)
        buf = io.BytesIO(); writer.write(buf)
        self.ws.import_file(PMID, 'pdf', 'long.pdf', buf.getvalue())
        preview = self.prepare()
        self.assertTrue(preview['items'][0]['needs_count'])
        self.assertEqual(preview['total'], 4)
        self.assertFalse(self.api.files)
        with patch.object(self.api, 'count_input', return_value=40000, create=True) as counter:
            jb.submit(self.repo, self.ws, dict(confirmed=True, preview_id=preview['id']), self.api)
            counter.assert_called_once()
        self.assertEqual(self.api.posts, 1)
        self.assertLess(self.rows('attempts')[0]['reserve'], 4)

    def test_actual_long_context_requires_review_and_stops_generation(self):
        writer = PdfWriter()
        for _ in range(20): writer.add_blank_page(width=300, height=300)
        buf = io.BytesIO(); writer.write(buf)
        self.ws.import_file(PMID, 'pdf', 'long.pdf', buf.getvalue())
        with patch.object(self.api, 'count_input', return_value=300000, create=True):
            self.send()
        self.assertEqual(self.api.posts, 0)
        self.assertFalse(self.rows('attempts'))

    def test_duplicate_result_and_unknown_usage_keep_cost_reservation(self):
        self.send(); self.complete(usage=None)
        out = next(k for k in self.api.files if k.startswith('out-'))
        self.api.files[out] += b'\n'+self.api.files[out]
        jb.collect(self.repo, self.ws, self.api)
        self.assertIsNone(self.rows('attempts')[0]['actual'])
        self.assertEqual(self.rows('attempts')[0]['status'], 'review_required')
        self.assertEqual(self.ws.snapshot()['counts']['json_pending'], 1)

    def test_unknown_usage_with_valid_json_saves_but_retains_reserve(self):
        self.send(); self.complete(usage=None)
        jb.collect(self.repo, self.ws, self.api)
        self.assertEqual(self.ws.snapshot()['counts']['web_pending'], 1)
        self.assertIsNone(self.rows('attempts')[0]['actual'])

    def test_no_consent_no_key_stale_preview_never_sends(self):
        preview = self.prepare()
        with self.assertRaises(ValueError):
            jb.submit(self.repo, self.ws, dict(confirmed=False, preview_id=preview['id']), self.api)
        with self.assertRaises(ValueError):
            jb.submit(self.repo, self.ws, dict(confirmed=True, preview_id='old'), self.api)
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'OPENAI_API_KEY'):
                jb.submit(self.repo, self.ws, dict(confirmed=True, preview_id=preview['id']))
        self.assertFalse(self.api.files)

    def test_missing_duplicate_terminal_result_keeps_reservation(self):
        self.send(); attempt = self.rows('attempts')[0]
        self.api.batches[attempt['batch_id']].update(status='expired')
        jb.collect(self.repo, self.ws, self.api)
        self.assertEqual(self.rows('attempts')[0]['status'], 'review_required')
        self.assertIsNone(self.rows('attempts')[0]['actual'])
        self.assertEqual(self.ws.snapshot()['counts']['json_pending'], 1)

    def test_other_mac_and_missing_ledger_cannot_submit(self):
        other = Workspace(self.ws.root, 'b'*32, 'Other Mac')
        self.assertFalse(jb.status(self.repo, other)['is_owner'])
        with self.assertRaisesRegex(ValueError, 'API担当Mac'):
            jb.collect(self.repo, other, self.api)
        with self.assertRaisesRegex(ValueError, '元の台帳'):
            jb.collect(self.root/'empty_repo', self.ws, self.api)

    def test_key_only_local_and_api_failure_does_not_echo_credentials(self):
        (self.repo/'.env').write_text('OPENAI_API_KEY="secret-test-key"\nNOTION_TOKEN=private-notion')
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(jb.api_key(self.repo), 'secret-test-key')
            state = jb.status(self.repo, self.ws)
            self.assertTrue(state['key_ready']); self.assertNotIn('secret-test-key', encode(state))
        with patch('requests.Session.request', side_effect=__import__('requests').Timeout('secret-test-key')):
            with patch('paper_pipeline.batches.time.sleep'):
                with self.assertRaisesRegex(RuntimeError, '^OpenAIとの通信に失敗しました') as error:
                    jb.JsonAPI('secret-test-key').check_models(include_images=False)
                self.assertNotIn('secret-test-key', str(error.exception))

    def test_crash_after_import_before_local_commit_is_idempotent(self):
        self.send(); self.complete()
        store = Store(jb.local_root(self.repo, self.ws))
        jb.JsonBatches(store, self.api).collect()
        revision = store.rows('SELECT * FROM revisions')[0]
        raw = (store.root/revision['summary']).read_bytes()
        self.ws.import_file(PMID, 'json', 'PMID_'+PMID+'_master.json', raw)
        before = self.ws.current(PMID); store.close()
        jb.collect(self.repo, self.ws, self.api)
        self.assertEqual(self.ws.current(PMID), before)
        self.assertEqual(jb.status(self.repo, self.ws)['jobs'][0]['status'], 'saved')

    def test_gui_actions_use_same_engine_and_never_generate_png_or_notion(self):
        app = Application(self.repo, self.ws.root); app.settings['device_id'] = self.ws.device_id
        with patch('paper_pipeline.json_batch.JsonAPI', return_value=self.api):
            app.run('json-prepare', {'items':[dict(pmid=PMID, heads=[self.ws.current(PMID)['id']])]})
            app.refresh(); preview = app.state['json_batch']['preview']
            app.run('json-send', dict(confirmed=True, preview_id=preview['id']))
            self.complete(); app.run('json-collect', {})
        app.refresh()
        self.assertEqual(app.state['counts']['web_pending'], 1)
        self.assertEqual(app.state['json_batch']['jobs'][0]['status'], 'saved')
        self.assertEqual([b['stage'] for b in self.api.batches.values()], ['analysis'])


if __name__ == '__main__': unittest.main()
