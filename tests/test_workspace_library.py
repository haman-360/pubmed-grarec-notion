"""Offline checks for continuation, readable copies, and preservation of old records."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from paper_pipeline import cli
from paper_pipeline.batches import config
from paper_pipeline.graphics import adapt_legacy, prepare_web, import_image, review_image
from paper_pipeline.library import sync_library, expose_library, paper_folder
from paper_pipeline.store import Store, atomic_write, encode
from paper_pipeline.workspace import select_state_dir, remember_state_dir, location_file
from pypdf import PdfWriter
from PIL import Image
from test_paper_pipeline import sample_summary


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()

    def ledger(self, name, populated=True):
        store = Store(self.repo / name)
        if populated:
            store.paper(pmid='90000001', title='Synthetic record')
        store.close()
        return self.repo / name

    def test_reuses_existing_records_even_if_default_database_is_empty(self):
        default = self.ledger('.paper_pipeline', False)
        pilot = self.ledger('.paper_pipeline-pilot')
        before = (pilot / 'ledger.sqlite3').read_bytes()
        self.assertEqual(select_state_dir(self.repo), pilot)
        self.assertEqual((pilot / 'ledger.sqlite3').read_bytes(), before)
        self.assertTrue((default / 'ledger.sqlite3').exists())

    def test_remembered_location_survives_new_database_and_explicit_override(self):
        previous = self.ledger('other-store')
        remember_state_dir(self.repo, previous)
        self.ledger('.paper_pipeline')
        self.assertEqual(select_state_dir(self.repo), previous)
        self.assertEqual(select_state_dir(self.repo, '.paper_pipeline'), self.repo / '.paper_pipeline')
        self.assertEqual(select_state_dir(self.repo), previous)
        self.assertEqual(json.loads(location_file(self.repo).read_text())['state_dir'], 'other-store')

    def test_missing_previous_location_does_not_silently_create_an_empty_ledger(self):
        remember_state_dir(self.repo, self.repo / 'missing')
        with self.assertRaisesRegex(ValueError, '前回の保存先'):
            select_state_dir(self.repo)
        self.assertFalse((self.repo / '.paper_pipeline').exists())

    def test_two_unselected_databases_are_preserved_without_merging(self):
        paths = [self.ledger(name) for name in ('.paper_pipeline', '.paper_pipeline-pilot')]
        before = [(p / 'ledger.sqlite3').read_bytes() for p in paths]
        with self.assertRaisesRegex(ValueError, '複数'):
            select_state_dir(self.repo)
        self.assertEqual([(p / 'ledger.sqlite3').read_bytes() for p in paths], before)

    def test_launcher_menu_reopens_same_ledger_without_network_or_budget_changes(self):
        root = self.ledger('.paper_pipeline-pilot')
        store = Store(root)
        store.set_setting('budget', {'suspended_reason': 'Synthetic quality hold',
                                    'checked_at': '2026-01-01', 'end': '2026-01-02'})
        before = list(store.db.iterdump())
        store.close()
        out = io.StringIO()
        with patch.object(cli, 'REPO', self.repo), patch.dict(os.environ, {}, clear=True), \
             patch('builtins.input', return_value='q'), contextlib.redirect_stdout(out), \
             patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden')):
            self.assertEqual(cli.main(['menu']), 0)
            self.assertEqual(cli.main(['menu']), 0)
        self.assertEqual((self.repo / 'papers/PMID_90000001').resolve(), root / 'papers/PMID_90000001')
        self.assertEqual(select_state_dir(self.repo), root)
        self.assertFalse((self.repo / '.paper_pipeline').exists())
        store = Store(root)
        self.assertEqual(list(store.db.iterdump()), before)
        store.close()
        self.assertIn('90000001', out.getvalue())
        self.assertIn('16 論文フォルダを開く', out.getvalue())
        self.assertIn(str(self.repo / 'papers/PMID_90000001'), out.getvalue())
        self.assertNotIn(str(root / 'papers'), out.getvalue())


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.store = Store(self.repo / 'state')
        self.addCleanup(self.store.close)
        network = patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden'))
        network.start()
        self.addCleanup(network.stop)
        self.paper = self.store.paper(pmid='90000001', title='Synthetic paper')
        path = self.repo / 'source.pdf'
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(path)
        self.source = self.store.source(path, self.paper['id'])
        self.revision = self.store.revision(self.paper, self.source, config())
        summary = adapt_legacy(sample_summary(self.paper))
        relative = 'artifacts/' + self.revision['id'] + '/review.json'
        atomic_write(self.store.root / relative, encode(summary))
        self.store.update('revisions', self.revision['id'], summary=relative, status='image_ready')
        self.revision = self.store.one('revisions', self.revision['id'])
        self.bundle = prepare_web(self.store, self.revision)
        self.folder = paper_folder(self.store, self.paper)

    def png(self, name, color):
        path = self.repo / name
        Image.new('RGB', (1600, 900), color).save(path)
        return path

    def test_pdf_json_and_web_prompt_are_readable_copies_with_originals_untouched(self):
        before = {p: p.read_bytes() for d in ('pdfs', 'artifacts')
                  for p in (self.store.root / d).rglob('*') if p.is_file()}
        ledger_before = list(self.store.db.iterdump())
        sync_library(self.store)
        sync_library(self.store)
        self.assertEqual((self.folder / 'source.pdf').read_bytes(), (self.repo / 'source.pdf').read_bytes())
        self.assertEqual((self.folder / 'master.json').read_bytes(), (self.bundle / 'master.json').read_bytes())
        self.assertEqual(json.loads((self.folder / 'graphics/00_main.json').read_text())['paper_type'], 'original_article')
        self.assertEqual((self.folder / 'ready_for_web/00_main_prompt.md').read_bytes(),
                         (self.bundle / 'ready_for_web/00_main_prompt.md').read_bytes())
        self.assertNotEqual((self.folder / 'master.json').resolve(), self.bundle / 'master.json')
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(list(self.store.db.iterdump()), ledger_before)

    def test_image_approval_and_replacement_update_view_and_preserve_history(self):
        first = self.png('first.png', 'white')
        import_image(self.store, self.revision, 'main', first)
        review_image(self.store, self.revision, 'main', True, 'Synthetic reviewer', 'Synthetic check')
        sync_library(self.store)
        previous_manifest = (self.folder / 'manifest.json').resolve()
        manifest = json.loads(previous_manifest.read_text())
        self.assertEqual(manifest['graphics'][0]['status'], 'IMAGE_APPROVED')
        second = self.png('second.png', 'blue')
        import_image(self.store, self.revision, 'main', second)
        sync_library(self.store)
        self.assertEqual(json.loads((self.folder / 'manifest.json').read_text())['graphics'][0]['status'], 'IMAGE_REVIEW_REQUIRED')
        self.assertEqual(json.loads(previous_manifest.read_text())['graphics'][0]['status'], 'IMAGE_APPROVED')
        self.assertEqual({p.read_bytes() for p in (self.folder / 'images').glob('*.png')},
                         {first.read_bytes(), second.read_bytes()})

    def test_new_unfinished_revision_hides_old_ready_files_but_preserves_them(self):
        sync_library(self.store)
        old_prompt = (self.folder / 'ready_for_web/00_main_prompt.md').resolve()
        new = self.store.revision(self.paper, self.source, config(), force=True)
        sync_library(self.store)
        self.assertFalse((self.folder / 'ready_for_web').exists())
        self.assertFalse((self.folder / 'master.json').exists())
        self.assertTrue(old_prompt.is_file())
        self.assertEqual(json.loads((self.folder / 'status.json').read_text())['latest_revision'], new['id'])

    def test_edited_copy_is_kept_and_never_written_back_to_original(self):
        sync_library(self.store)
        original = (self.bundle / 'master.json').read_bytes()
        (self.folder / 'master.json').write_text('User edited copy')
        with self.assertRaisesRegex(ValueError, '編集'):
            sync_library(self.store)
        self.assertEqual((self.folder / 'master.json').read_text(), 'User edited copy')
        self.assertEqual((self.bundle / 'master.json').read_bytes(), original)

    def test_visible_folder_never_replaces_existing_user_directory(self):
        existing = self.repo / 'papers/PMID_90000001'
        existing.mkdir(parents=True)
        (existing / 'mine.txt').write_text('Keep')
        with self.assertRaisesRegex(ValueError, '既存ファイル'):
            expose_library(self.repo, self.store)
        self.assertEqual((existing / 'mine.txt').read_text(), 'Keep')

    def test_existing_folder_contents_are_preserved_and_new_papers_appear(self):
        root = self.repo / 'papers'
        root.mkdir()
        (root / '.DS_Store').write_bytes(b'Existing Finder metadata')
        expose_library(self.repo,self.store)
        self.assertTrue((root / 'PMID_90000001/source.pdf').is_file())
        self.assertEqual((root / '.DS_Store').read_bytes(), b'Existing Finder metadata')
        self.store.paper(pmid='90000002', title='Another synthetic record')
        expose_library(self.repo,self.store)
        self.assertTrue((root / 'PMID_90000002/案内.txt').is_file())


if __name__ == '__main__':
    unittest.main()
