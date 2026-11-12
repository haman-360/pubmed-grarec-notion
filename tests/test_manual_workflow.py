"""Offline multi-device tests: divergent copies emulate OneDrive's eventual sync."""
import copy
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pypdf import PdfWriter
from paper_pipeline.manual import Workspace, local_settings
from paper_pipeline.manual_migration import migrate
from paper_pipeline.graphics import adapt_legacy
from paper_pipeline.store import encode
from test_paper_pipeline import sample_summary, png
from grarec_app import Application
import test_workspace_library as fixtures

PMID = '90000001'


class ManualTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ws = Workspace.create(self.root / 'shared', 'a' * 32, '自宅 Mac')
        self.ws.add(PMID)
        writer = PdfWriter(); writer.add_blank_page(width=300, height=300)
        stream = io.BytesIO(); writer.write(stream); self.pdf = stream.getvalue()
        self.master = adapt_legacy(sample_summary(dict(pmid=PMID, title='Synthetic paper', doi='')))

    def imported(self):
        self.ws.import_file(PMID, 'pdf', 'source.pdf', self.pdf)
        self.ws.import_file(PMID, 'json', 'master.json', encode(self.master).encode())

    def category(self):
        return self.ws.snapshot()['papers'][0]['category']

    def test_end_to_end_manual_handoff_without_any_network(self):
        with patch('requests.Session.request', side_effect=AssertionError('network forbidden')):
            self.imported()
            self.assertEqual(self.category(), 'web_pending')
            self.assertIn('90000001', self.ws.json_prompt(PMID))
            self.assertIn('90000001', self.ws.image_prompt(PMID, 'main', 'synthetic design'))
            self.ws.import_file(PMID, 'png', 'main.png', png())
            self.assertEqual(self.category(), 'review_pending')
            event = self.ws.current(PMID)
            self.ws.update(PMID, [event['id']], '原文と照合し手動登録済み', done=True)
            self.assertEqual(self.category(), 'completed')
            other = Workspace(self.ws.root, 'b' * 32, '職場 Mac')
            self.assertEqual(other.snapshot()['papers'][0]['device'], '自宅 Mac')
            for file in other.current(PMID)['data']['files']:
                self.assertTrue(other.read_asset(file))

    def test_inbox_scan_is_idempotent_and_does_not_revert_to_old_files(self):
        inbox = self.ws.incoming(PMID)
        (inbox / 'PDF/a source.pdf').write_bytes(self.pdf)
        (inbox / 'JSON/first master.json').write_text(encode(self.master))
        count, errors = self.ws.scan()
        self.assertEqual((count, errors), (2, []))
        self.master['one_line_summary'] = 'revised'
        (inbox / 'JSON/second.json').write_text(encode(self.master))
        self.assertEqual(self.ws.scan(), (1, []))
        before = self.ws.current(PMID)['id']
        self.assertEqual(self.ws.scan(), (0, []))
        self.assertEqual(self.ws.current(PMID)['id'], before)

    def test_new_json_and_pdf_invalidate_png_and_completion_without_deleting_history(self):
        self.imported()
        self.ws.import_file(PMID, 'png', 'main.png', png())
        event = self.ws.current(PMID)
        done = self.ws.update(PMID, [event['id']], 'checked', done=True)
        old_files = done['data']['files']
        self.master['one_line_summary'] = 'changed'
        self.ws.import_file(PMID, 'json', 'master.json', encode(self.master).encode())
        self.assertEqual(self.category(), 'web_pending')
        self.assertTrue(all(self.ws.path(f['path']).exists() for f in old_files))
        self.ws.import_file(PMID, 'pdf', 'new.pdf', self.pdf + b'\n')
        self.assertEqual(self.category(), 'json_pending')

    def test_wrong_pmid_and_invalid_json_leave_progress_unchanged(self):
        self.ws.import_file(PMID, 'pdf', 'source.pdf', self.pdf)
        before = self.ws.current(PMID)['id']
        for raw in (b'{}', b'{', encode(dict(self.master, pmid='90000002')).encode()):
            with self.assertRaises(ValueError):
                self.ws.import_file(PMID, 'json', 'master.json', raw)
        self.assertEqual(self.ws.current(PMID)['id'], before)

    def test_generated_structure_draft_is_retained_blocked_and_repairable(self):
        self.imported()
        bad=copy.deepcopy(self.master);bad['topics'][0]['source_sections']=['missing-section']
        inbox=self.ws.incoming(PMID)/'JSON/master.json'
        inbox.write_text(encode(bad))
        self.assertEqual(self.ws.scan(),(1,[]))
        event=self.ws.current(PMID)
        self.assertIn('missing-section',event['data']['structure_wait'])
        self.assertEqual(self.category(),'attention')
        draft=next(f for f in event['data']['files'] if f['kind']=='draft')
        self.assertEqual(json.loads(self.ws.read_asset(draft)),bad)
        with self.assertRaisesRegex(ValueError,'構造確認待ち'):
            self.ws.image_prompt(PMID,'main','instruction')
        self.assertEqual(self.ws.scan(),(0,[]))
        self.master['one_line_summary']='repaired new version'
        inbox.write_text(encode(self.master))
        self.assertEqual(self.ws.scan(),(1,[]))
        self.assertNotIn('structure_wait',self.ws.current(PMID)['data'])
        self.assertEqual(self.category(),'web_pending')
        self.assertTrue(self.ws.path(draft['path']).is_file())

    def test_json_prompt_requires_registered_ids_and_complete_assignment(self):
        self.imported()
        prompt=self.ws.json_prompt(PMID)
        self.assertIn('登録済みsection_idと完全一致',prompt)
        self.assertIn('欠番なく含む',prompt)

    def test_concurrent_offline_updates_preserve_both_and_require_explicit_choice(self):
        self.imported()
        other_root = self.root / 'office'
        shutil.copytree(self.ws.root, other_root)
        other = Workspace(other_root, 'b' * 32, '職場 Mac')
        base = self.ws.current(PMID)
        home = self.ws.update(PMID, [base['id']], '自宅で作業')
        office = other.update(PMID, [base['id']], '職場で作業')
        records = other.folder(PMID) / 'records'
        shutil.copy2(records / (office['id'] + '.json'), self.ws.folder(PMID) / 'records')
        self.assertEqual(self.category(), 'attention')
        with self.assertRaises(ValueError):
            self.ws.import_file(PMID, 'png', 'main.png', png())
        resolved = self.ws.update(PMID, [home['id'], office['id']], '両方を確認し職場の版を採用', chosen=office['id'])
        self.assertEqual(set(resolved['parents']), {home['id'], office['id']})
        self.assertIn(home['id'], self.ws.events(PMID))
        self.assertEqual(len(self.ws.heads(PMID)), 1)

    def test_out_of_order_records_and_missing_assets_are_not_treated_as_complete(self):
        self.imported()
        event = self.ws.current(PMID)
        parent_path = self.ws.folder(PMID) / 'records' / (event['parents'][0] + '.json')
        raw = parent_path.read_bytes(); parent_path.unlink()
        self.assertEqual(self.category(), 'attention')
        with self.assertRaisesRegex(ValueError, '未同期'):
            self.ws.current(PMID)
        parent_path.write_bytes(raw)
        file = event['data']['files'][0]
        self.ws.path(file['path']).unlink()
        self.assertEqual(self.category(), 'attention')
        with self.assertRaisesRegex(ValueError, '未同期'):
            self.ws.json_prompt(PMID)

    def test_changed_file_and_stale_update_are_rejected(self):
        self.imported()
        event = self.ws.current(PMID)
        file = next(f for f in event['data']['files'] if f['kind'] == 'graphic')
        self.ws.path(file['path']).write_text('{}')
        with self.assertRaisesRegex(ValueError, '変更'):
            self.ws.image_prompt(PMID, 'main', 'design')
        self.ws.update(PMID, [event['id']], 'newer')
        with self.assertRaisesRegex(ValueError, '更新'):
            self.ws.update(PMID, [event['id']], 'stale')

    def test_per_device_views_preserve_each_other_and_do_not_touch_unchanged_files(self):
        self.imported()
        self.ws.export_view(self.ws.snapshot())
        path = self.ws.root / '状況' / ('a' * 32) / '状況.txt'
        before = path.stat().st_mtime_ns
        self.ws.export_view(self.ws.snapshot())
        self.assertEqual(path.stat().st_mtime_ns, before)
        other = Workspace(self.ws.root, 'b' * 32, '職場')
        other.export_view(other.snapshot())
        self.assertEqual(path.stat().st_mtime_ns, before)
        self.assertTrue((other.root / '状況' / ('b' * 32) / '状況.txt').is_file())
        self.assertFalse(any(p.is_symlink() for p in self.ws.root.rglob('*')))

    def test_ambiguous_inputs_do_not_choose_a_file_arbitrarily(self):
        folder = self.ws.incoming(PMID) / 'PDF'
        (folder / 'a.pdf').write_bytes(self.pdf)
        (folder / 'b.pdf').write_bytes(self.pdf + b'\n')
        count, errors = self.ws.scan()
        self.assertEqual(count, 0); self.assertIn('複数', errors[0])
        self.assertEqual(self.category(), 'missing_pdf')

    def test_paths_and_existing_folder_are_protected(self):
        with self.assertRaises(ValueError):
            self.ws.path('../../elsewhere')
        with self.assertRaises(ValueError):
            self.ws.add('../escape')
        occupied = self.root / 'occupied'; occupied.mkdir(); (occupied / 'keep.txt').write_text('keep')
        with self.assertRaises(ValueError):
            Workspace.create(occupied, 'c' * 32, 'other')
        self.assertEqual((occupied / 'keep.txt').read_text(), 'keep')

    def test_main_app_has_no_old_api_actions_even_with_environment_keys(self):
        app = Application(self.root / 'repo', self.ws.root)
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic', 'NOTION_TOKEN': 'synthetic'}), \
             patch('requests.Session.request', side_effect=AssertionError('network forbidden')):
            for action in ('json', 'collect', 'upload', 'poll', 'auto', 'search', 'budget', 'remote-status'):
                with self.assertRaisesRegex(ValueError, '廃止'):
                    app.run(action, {})
            app.refresh(scan=True)
            self.assertEqual(app.state['counts']['total'], 1)

    def test_invalid_inbox_is_visible_in_attention_count(self):
        self.imported()
        (self.ws.incoming(PMID) / 'JSON/master.json').write_text('{')
        app = Application(self.root / 'repo', self.ws.root)
        self.assertEqual(app.state['counts']['attention'], 1)
        self.assertEqual(app.state['counts']['web_pending'], 0)
        self.assertTrue(app.state['errors'])

    def test_topic_images_are_all_required_before_completion(self):
        second = copy.deepcopy(self.master['topics'][0])
        second.update(topic_id='second', result_indices=[])
        self.master['topics'].append(second)
        self.master['visualization_strategy']['mode'] = 'topics'
        self.imported()
        self.ws.import_file(PMID, 'png', 'main.png', png())
        self.assertEqual(self.category(), 'web_pending')
        event = self.ws.current(PMID)
        with self.assertRaisesRegex(ValueError, '全テーマ'):
            self.ws.update(PMID, [event['id']], 'checked', done=True)
        self.ws.import_file(PMID, 'png', 'second.png', png())
        self.assertEqual(self.category(), 'review_pending')

    def test_migration_is_idempotent_and_does_not_modify_source_database(self):
        fixture = fixtures.LibraryTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.store.update('revisions', fixture.revision['id'], error='Synthetic previous hold')
        before = list(fixture.store.db.iterdump())
        target = Workspace.create(self.root / 'migrated', 'c' * 32, 'migration')
        first = migrate(target, fixture.store.root)
        second = migrate(target, fixture.store.root)
        self.assertEqual((first['copied'], second['skipped']), (1, 1))
        self.assertEqual(list(fixture.store.db.iterdump()), before)
        self.assertEqual(target.snapshot()['counts']['web_pending'], 1)
        self.assertIn('Synthetic previous hold', target.current(PMID)['data']['note'])
        for file in target.current(PMID)['data']['files']:
            self.assertTrue(target.read_asset(file))
