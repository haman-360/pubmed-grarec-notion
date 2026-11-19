"""Human decisions survive imports, restarts and concurrent OneDrive records."""
import copy
import io
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pypdf import PdfWriter
from grarec_app import Application
from paper_pipeline.manual import Workspace, next_month
from paper_pipeline.manual_status import StatusReporter
from paper_pipeline.pdf_download import download_selected, eligible
from paper_pipeline.json_batch import eligible as json_eligible
from paper_pipeline.notion_sync import bundle
from paper_pipeline.paid_pdf import cooldown

PMID = '90000001'


class PaperManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ws = Workspace.create(self.root / 'shared', 'a' * 32, 'Test Mac')
        self.ws.add(PMID, 'Synthetic paper')
        self.clock = patch('paper_pipeline.manual.today', return_value=date(2026, 11, 16))
        self.clock.start(); self.addCleanup(self.clock.stop)
        writer = PdfWriter(); writer.add_blank_page(width=300, height=300)
        stream = io.BytesIO(); writer.write(stream); self.pdf = stream.getvalue()

    def heads(self):
        return [self.ws.current(PMID)['id']]

    def manage(self, action, **values):
        return self.ws.manage(PMID, self.heads(), action, **values)

    def row(self):
        return self.ws.snapshot()['papers'][0]

    def test_calendar_month_default_handles_year_end_and_leap_days(self):
        for source, expected in [('2026-11-16', '2026-12-16'), ('2026-01-31', '2026-02-28'),
                                 ('2028-01-31', '2028-02-29'), ('2026-12-31', '2027-01-31')]:
            self.assertEqual(next_month(date.fromisoformat(source)).isoformat(), expected)
        self.manage('defer', result='有料のみ')
        self.assertEqual(self.row()['pdf_recheck']['next_on'], '2026-12-16')
        self.assertEqual(self.ws.snapshot()['default_recheck_on'], '2026-12-16')

    def test_wait_expires_on_due_date_without_writing_or_starting_work(self):
        self.manage('defer', result='アクセスできない', note='出版社で確認')
        before = {p: p.read_bytes() for p in self.ws.root.rglob('*') if p.is_file()}
        with patch('requests.Session.request', side_effect=AssertionError('no network')):
            with patch('paper_pipeline.manual.today', return_value=date(2026, 12, 15)):
                self.assertEqual(self.row()['category'], 'deferred')
                with self.assertRaises(ValueError): eligible(self.ws, PMID, self.heads())
            with patch('paper_pipeline.manual.today', return_value=date(2026, 12, 16)):
                row = self.row()
                self.assertEqual(row['category'], 'missing_pdf'); self.assertTrue(row['recheck_due'])
                self.assertEqual(eligible(self.ws, PMID, self.heads())['pmid'], PMID)
        self.assertEqual(before, {p: p.read_bytes() for p in self.ws.root.rglob('*') if p.is_file()})

    def test_reschedule_and_resume_preserve_check_and_ordinary_note(self):
        self.ws.update(PMID, self.heads(), '既存の作業メモ')
        self.manage('defer', checked_on='2026-11-15', result='PDFが見つからない', note='PubMedで確認')
        self.manage('reschedule', next_on='2027-02-01')
        row = self.row()
        self.assertEqual(row['pdf_recheck']['checked_on'], '2026-11-15')
        self.assertEqual(row['pdf_recheck']['note'], 'PubMedで確認')
        self.assertEqual(row['note'], '既存の作業メモ')
        self.manage('resume')
        self.assertEqual(self.row()['category'], 'missing_pdf'); self.assertFalse(self.row()['recheck_due'])
        self.assertEqual(len(self.row()['management_history']), 3)

    def test_archive_restore_keeps_files_completion_and_note(self):
        self.ws.import_file(PMID, 'pdf', 'source.pdf', self.pdf)
        original = self.ws.current(PMID)
        archived = self.manage('archive', reason='長文のため', note='入力上限を超過')
        self.assertEqual(self.row()['category'], 'archived')
        self.assertEqual(self.row()['archive']['reason'], '長文のため')
        self.assertEqual(archived['data']['files'], original['data']['files'])
        for file in original['data']['files']: self.assertTrue(self.ws.read_asset(file))
        self.ws.add(PMID)
        self.assertEqual(self.ws.current(PMID), archived)
        for check in (json_eligible, bundle):
            with self.assertRaises(ValueError): check(self.ws, PMID, self.heads())
        self.manage('restore')
        self.assertEqual(self.ws.current(PMID)['data'], original['data'])
        self.assertEqual(self.row()['category'], 'json_pending')
        self.assertEqual(self.ws.events(PMID)[original['id']], original)

    def test_archive_retains_prior_wait_and_restores_it(self):
        self.manage('defer', result='有料のみ')
        self.manage('archive')
        self.assertEqual(self.row()['category'], 'archived')
        self.manage('restore')
        self.assertEqual(self.row()['category'], 'deferred')
        other = Workspace(self.ws.root, 'b' * 32, 'Other Mac')
        self.assertEqual(other.snapshot()['papers'][0]['pdf_recheck'], self.row()['pdf_recheck'])

    def test_pdf_import_ends_wait_without_erasing_check_history(self):
        self.manage('defer', result='有料のみ')
        self.ws.import_file(PMID, 'pdf', 'source.pdf', self.pdf)
        row = self.row()
        self.assertEqual(row['category'], 'json_pending')
        self.assertFalse(row['pdf_recheck']['active'])
        self.assertEqual(row['management_history'][0]['result'], '有料のみ')
        with self.assertRaises(ValueError): self.manage('defer', result='有料のみ')

    def test_archive_skips_inbox_scan_and_rejects_explicit_import(self):
        self.manage('archive', reason='古い')
        before = self.ws.current(PMID)
        (self.ws.incoming(PMID) / 'PDF/new.pdf').write_bytes(self.pdf)
        self.assertEqual(self.ws.scan(), (0, []))
        with self.assertRaisesRegex(ValueError, 'アーカイブ'):
            self.ws.import_file(PMID, 'pdf', 'new.pdf', self.pdf)
        self.assertEqual(self.ws.current(PMID), before)

    def test_invalid_dates_results_and_missing_or_stale_heads_do_not_write(self):
        before = self.ws.current(PMID)
        for values in [dict(next_on='bad'), dict(next_on='2026-02-30'), dict(next_on='2026-11-16'),
                       dict(checked_on='2026-11-17'), dict(checked_on='20261115'), dict(result='unknown')]:
            with self.assertRaises(ValueError): self.manage('defer', **{'result': '有料のみ', **values})
        for heads in (None, [], ['stale']):
            with self.assertRaises(ValueError): self.ws.manage(PMID, heads, 'archive')
        self.assertEqual(self.ws.current(PMID), before)

    def test_conflicting_decisions_are_retained_and_require_resolution(self):
        original = self.ws.current(PMID)
        self.manage('archive', reason='古い')
        other = copy.deepcopy(original['data'])
        other['pdf_recheck'] = dict(active=True, checked_on='2026-11-16', next_on='2026-12-16', result='有料のみ', note='')
        self.ws.record(PMID, other, [original['id']])
        self.assertEqual(self.row()['category'], 'attention')
        self.assertEqual(len(self.row()['conflicts']), 2)
        with self.assertRaises(ValueError): self.ws.manage(PMID, [e['id'] for e in self.ws.heads(PMID)], 'restore')

    def test_downloads_do_not_contact_network_for_archived_or_deferred(self):
        for action in ('defer', 'archive'):
            self.manage(action, result='有料のみ')
            client, finder = Mock(), Mock()
            result = download_selected(self.ws, [dict(pmid=PMID, heads=self.heads())], client=client, finder=finder)
            self.assertEqual(result['items'][0]['status'], 'skipped')
            client.fetch_metadata.assert_not_called(); finder.download_pdf.assert_not_called()

    def test_manual_wait_does_not_change_paid_search_cooldown(self):
        path = self.ws.path('API_PDF/PMID_' + PMID + '.json'); path.parent.mkdir()
        path.write_text(json.dumps(dict(at='2026-11-10T00:00:00+00:00', status='completed')))
        current = datetime(2026, 11, 16, tzinfo=timezone.utc)
        before = cooldown(self.ws, PMID, current)
        self.assertTrue(before['blocked'])
        self.manage('defer', result='有料のみ'); self.manage('resume')
        self.assertEqual(cooldown(self.ws, PMID, current), before)

    def test_app_reports_duplicate_archive_and_exports_dates_and_reason(self):
        app = Application(self.root / 'repo', self.ws.root)
        app.run('manage-paper', dict(pmid=PMID, heads=self.heads(), operation='defer', result='有料のみ'))
        app.run('manage-paper', dict(pmid=PMID, heads=self.heads(), operation='archive', reason='古い'))
        self.assertIn('アーカイブ中', app.run('add', dict(pmids=PMID)))
        app.refresh()
        self.assertEqual(app.state['counts']['archived'], 1)
        exported = self.ws.path('状況/' + app.settings['device_id'] + '/状況.txt').read_text()
        self.assertIn('2026-12-16', exported); self.assertIn('古い', exported)

    def test_mobile_contract_remains_valid_and_contains_no_decision_details(self):
        self.manage('archive', note='private reason')
        self.ws.add('90000002')
        self.ws.manage('90000002', [self.ws.current('90000002')['id']], 'defer', result='有料のみ')
        config = self.root / '.remote_status/config.json'; config.parent.mkdir(); config.write_text('{}')
        reporter = StatusReporter(self.root); reporter.submit(self.ws.snapshot())
        counts = reporter.pending['counts']
        self.assertEqual(counts['total'], 1); self.assertEqual(counts['missing_pdf'], 1)
        self.assertEqual(sum(v for k,v in counts.items() if k!='total'), counts['total'])
        self.assertNotIn('private reason', json.dumps(reporter.pending))
        self.assertNotIn('90000001', json.dumps(reporter.pending))

    def test_completed_paper_can_be_archived_and_restored_without_reapproval(self):
        from paper_pipeline.graphics import adapt_legacy
        from test_paper_pipeline import sample_summary, png
        self.ws.import_file(PMID, 'pdf', 'source.pdf', self.pdf)
        master = adapt_legacy(sample_summary(dict(pmid=PMID, title='Synthetic paper', doi='')))
        self.ws.import_file(PMID, 'json', 'master.json', json.dumps(master).encode())
        self.ws.import_file(PMID, 'png', 'main.png', png())
        self.ws.update(PMID, self.heads(), '確認・登録済み', done=True)
        before = self.ws.current(PMID)['data']
        self.manage('archive'); self.manage('restore')
        self.assertEqual(self.row()['category'], 'completed')
        self.assertEqual(self.ws.current(PMID)['data'], before)
