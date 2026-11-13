"""Common intake, replay safety, and CLI/UI behavior with synthetic files only."""
import copy
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pypdf import PdfWriter
from paper_pipeline.manual import Workspace
from paper_pipeline.intake import PENDING, ARCHIVE, ensure_folders, inventory, organize, parse_pmids
from paper_pipeline.graphics import adapt_legacy
from paper_pipeline.store import encode
from test_paper_pipeline import sample_summary, png
from grarec_app import Application
import organize_intake

PMID = '90000001'


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ws = Workspace.create(self.root / 'shared', 'a' * 32, 'test Mac')
        ensure_folders(self.ws)
        self.inbox = self.ws.path(PENDING)
        self.master = adapt_legacy(sample_summary(dict(pmid=PMID, title='Synthetic paper', doi='')))
        writer = PdfWriter(); writer.add_blank_page(width=300, height=300)
        stream = io.BytesIO(); writer.write(stream); self.pdf = stream.getvalue()

    def write(self, name, raw):
        path = self.inbox / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw if isinstance(raw, bytes) else raw.encode())
        os.utime(path, (time.time() - 10, time.time() - 10))
        return path

    def bundle(self, prefix=''):
        self.write(prefix + 'paper.pdf', self.pdf)
        self.write(prefix + 'master.json', encode(self.master))
        self.write(prefix + 'main.png', png())
        self.write(prefix + 'メモ.txt', f'PMID {PMID}はPDFをダウンロードし、JSONとPNGを作りました')

    def stage(self):
        return self.ws.snapshot()['papers'][0]['category']

    def test_archive_cleanup_requires_retention_and_verified_copy(self):
        from paper_pipeline.intake import cleanup_archive
        self.bundle(); organize(self.ws)
        archive = self.ws.path(ARCHIVE)
        paths = [p for p in archive.rglob('*') if p.is_file()]
        self.assertEqual(len(paths), 4)
        self.assertEqual(cleanup_archive(self.ws), 0)
        # A missing stored PDF must keep its archived original.
        pdf = next(f for f in self.ws.current(PMID)['data']['files'] if f['kind'] == 'pdf')
        self.ws.path(pdf['path']).unlink()
        self.assertEqual(cleanup_archive(self.ws, time.time() + 31 * 86400), 3)
        remaining = [p for p in archive.rglob('*') if p.is_file()]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].suffix, '.pdf')
        self.assertEqual(remaining[0].read_bytes(), self.pdf)

    def test_archive_cleanup_keeps_changed_copy_and_unstored_list(self):
        from paper_pipeline.intake import cleanup_archive
        self.bundle(); organize(self.ws)
        file = next(f for f in self.ws.current(PMID)['data']['files'] if f['kind'] == 'json')
        self.ws.path(file['path']).write_text('{}')
        self.write('希望PMID.txt', PMID)
        organize(self.ws)
        self.assertEqual(cleanup_archive(self.ws, time.time() + 31 * 86400), 3)
        names = [p.name for p in self.ws.path(ARCHIVE).rglob('*') if p.is_file()]
        self.assertEqual(set(names), {'master.json', '希望PMID.txt'})

    def test_flat_bundle_skips_intermediate_stages_and_preserves_all_inputs(self):
        self.bundle()
        with patch('requests.Session.request', side_effect=AssertionError('no network')):
            result = organize(self.ws)
        self.assertEqual(result['errors'], [])
        self.assertEqual((result['registered'], result['archived']), (1, 4))
        self.assertEqual(self.stage(), 'review_pending')
        self.assertEqual(inventory(self.ws)['pending_files'], 0)
        archived = [p for p in self.ws.path(ARCHIVE).rglob('*') if p.is_file()]
        self.assertEqual(len(archived), 4)
        self.assertIn('PNGを作りました', self.ws.current(PMID)['data']['note'])
        self.assertFalse(self.ws.current(PMID)['data']['done'])

    def test_folder_bundle_and_replay_preserve_completion(self):
        self.bundle(f'PMID_{PMID}/')
        self.assertFalse(organize(self.ws)['errors'])
        event = self.ws.current(PMID)
        self.ws.update(PMID, [event['id']], '確認し登録した', done=True)
        before = self.ws.current(PMID)['id']
        self.bundle(f'PMID_{PMID}/')
        result = organize(self.ws)
        self.assertEqual((result['imported'], result['skipped']), (0, 4))
        self.assertEqual(self.ws.current(PMID)['id'], before)
        self.assertEqual(self.stage(), 'completed')

    def test_pmid_list_dedup_and_notes_do_not_claim_file_completion(self):
        self.write('希望PMID.txt', f'# 希望\n{PMID}\nPMID: {PMID}\nhttps://pubmed.ncbi.nlm.nih.gov/90000002/\n')
        self.write(f'PMID_{PMID}/メモ.txt', 'PNGまで作りました')
        result = organize(self.ws)
        self.assertEqual(result['registered'], 2)
        self.assertEqual(self.ws.snapshot()['counts']['missing_pdf'], 2)
        self.assertEqual(parse_pmids('2026年11月\nDOI: 10.1234/5678\nPMID 90000001'), [PMID])

    def test_mismatched_json_unknown_files_and_ambiguous_png_are_retained(self):
        self.write(f'PMID_{PMID}/wrong.json', encode(dict(self.master, pmid='90000002')))
        self.write('random.png', png())
        self.write('partial.part', b'partial')
        result = organize(self.ws)
        self.assertEqual(result['archived'], 0)
        self.assertEqual(len(result['errors']), 3)
        self.assertEqual(inventory(self.ws)['pending_files'], 3)
        self.assertEqual(self.ws.snapshot()['counts']['total'], 0)

    def test_multiple_pdf_candidates_do_not_change_paper(self):
        self.write(f'PMID_{PMID}/a.pdf', self.pdf)
        self.write(f'PMID_{PMID}/b.pdf', self.pdf + b'\n')
        result = organize(self.ws)
        self.assertEqual(result['registered'], 0)
        self.assertIn('候補が複数', result['errors'][0])
        self.assertEqual(result['pending_files'], 2)

    def test_prefixed_png_maps_to_topic(self):
        self.write(f'{PMID}_paper.pdf', self.pdf)
        self.write('master.json', encode(self.master))
        self.write(f'PMID_{PMID}_main.png', png())
        self.assertFalse(organize(self.ws)['errors'])
        self.assertEqual(self.stage(), 'review_pending')

    def test_multiple_papers_named_json_go_directly_in_flat_inbox(self):
        for pmid, name in ((PMID, f'PMID_{PMID}.json'), ('90000002', '90000002_master.json')):
            self.ws.add(pmid)
            self.ws.import_file(pmid, 'pdf', 'source.pdf', self.pdf)
            self.write(name, encode(dict(self.master, pmid=pmid)))
        result = organize(self.ws)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['archived'], 2)
        self.assertEqual(self.ws.snapshot()['counts']['web_pending'], 2)
        self.assertFalse(any(self.inbox.iterdir()))

    def test_request_json_is_not_imported_as_completed_data(self):
        self.bundle(); organize(self.ws)
        request = self.ws.request_file(PMID, [self.ws.current(PMID)['id']], 'main')
        self.write(request['name'], self.ws.read_asset(request))
        before = self.ws.current(PMID)['id']
        result = organize(self.ws)
        self.assertEqual(result['archived'], 0)
        self.assertIn('これはPNG作成依頼', result['errors'][0])
        self.assertEqual(self.ws.current(PMID)['id'], before)

    def test_gui_exports_request_and_reveals_file_in_finder(self):
        self.bundle(); organize(self.ws)
        app = Application(self.root / 'repo', self.ws.root)
        with patch('grarec_app.subprocess.run') as run:
            message = app.run('export-image-request', dict(pmid=PMID,
                heads=[self.ws.current(PMID)['id']], topic='main'))
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ['open', '-R'])
        self.assertEqual(json.loads(Path(args[2]).read_text())['pmid'], PMID)
        self.assertIn('JSONファイル1個', message)

    def test_missing_pdf_blocks_json_and_png_and_preserves_inputs(self):
        self.write('master.json', encode(self.master))
        self.write('main.png', png())
        result = organize(self.ws)
        self.assertEqual(result['archived'], 0)
        self.assertEqual(result['pending_files'], 2)
        self.assertEqual(self.stage(), 'missing_pdf')

    def test_old_files_do_not_revert_replaced_versions(self):
        self.bundle(); organize(self.ws)
        original = copy.deepcopy(self.master)
        self.master['one_line_summary'] = 'revised'
        self.write('master.json', encode(self.master)); organize(self.ws)
        before = self.ws.current(PMID)['id']
        self.master = original; self.bundle()
        result = organize(self.ws)
        self.assertFalse(result['errors'])
        self.assertEqual(self.ws.current(PMID)['id'], before)
        self.assertEqual(self.stage(), 'web_pending')

    def test_unstable_and_symlink_inputs_stay_put(self):
        fresh = self.write(f'PMID_{PMID}/paper.pdf', self.pdf)
        os.utime(fresh, None)
        target = self.root / 'outside.json'; target.write_text(encode(self.master))
        (self.inbox / 'linked.json').symlink_to(target)
        result = organize(self.ws)
        self.assertEqual(result['archived'], 0)
        self.assertTrue(fresh.exists()); self.assertTrue(target.exists())
        self.assertEqual(len(result['errors']), 2)

    def test_new_png_with_historical_json_is_not_bound_to_current_json(self):
        self.bundle(); organize(self.ws)
        original = encode(self.master)
        self.master['one_line_summary'] = 'revised'
        self.write('master.json', encode(self.master)); organize(self.ws)
        before = self.ws.current(PMID)['id']
        self.write('master.json', original)
        self.write('main.png', png() + b'new input')
        result = organize(self.ws)
        self.assertIn('古いPDF・JSON', result['errors'][0])
        self.assertEqual(self.ws.current(PMID)['id'], before)
        self.assertEqual(result['archived'], 0)

    def test_invalid_replacement_stops_downstream_import(self):
        self.bundle(); organize(self.ws)
        before = self.ws.current(PMID)['id']
        self.write(f'PMID_{PMID}/replacement.pdf', b'not a PDF')
        self.master['one_line_summary'] = 'new json'
        self.write('master.json', encode(self.master))
        result = organize(self.ws)
        self.assertEqual(self.ws.current(PMID)['id'], before)
        self.assertEqual(result['pending_files'], 2)

    def test_malformed_json_cannot_attach_new_png_to_previous_json(self):
        self.bundle(); organize(self.ws)
        before = self.ws.current(PMID)['id']
        self.write(f'PMID_{PMID}/master.json', '{')
        self.write(f'PMID_{PMID}/main.png', png() + b'new')
        result = organize(self.ws)
        self.assertEqual(result['archived'], 0)
        self.assertEqual(self.ws.current(PMID)['id'], before)

    def test_draft_is_retained_and_png_stays_pending(self):
        self.master['topics'][0]['source_sections'] = ['missing-section']
        self.bundle()
        result = organize(self.ws)
        self.assertEqual(self.stage(), 'attention')
        self.assertTrue((self.inbox / 'main.png').exists())
        self.assertEqual(result['pending_files'], 1)
        self.assertTrue(any(f['kind'] == 'draft' for f in self.ws.current(PMID)['data']['files']))

    def test_gui_only_organizes_on_explicit_action_and_cli_result_is_visible(self):
        self.bundle()
        app = Application(self.root / 'repo', self.ws.root)
        self.assertEqual(app.state['counts']['total'], 0)
        self.assertEqual(app.state['intake']['pending_files'], 4)
        app.run('organize', {}); app.refresh()
        self.assertEqual(app.state['counts']['review_pending'], 1)
        self.assertEqual(app.state['intake']['pending_files'], 0)
        self.assertEqual(app.state['intake_result']['archived'], 4)
        self.assertTrue(app.state['observed_at'])

    def test_cli_exports_shared_status_without_ai_or_notion(self):
        self.bundle()
        settings = dict(shared_root=str(self.ws.root), device_id='a' * 32, device_name='test Mac')
        with patch.object(organize_intake, 'local_settings', return_value=settings), \
             patch.object(organize_intake, 'StatusReporter') as reporter, \
             patch('requests.Session.request', side_effect=AssertionError('no network')):
            self.assertEqual(organize_intake.main([]), 0)
            reporter.return_value.submit.assert_called_once()
        text = self.ws.path('状況/' + 'a' * 32 + '/状況.txt').read_text()
        self.assertIn('登録総数: 1本', text)
        self.assertIn('取り込み待ち: 0ファイル', text)


if __name__ == '__main__':
    unittest.main()
