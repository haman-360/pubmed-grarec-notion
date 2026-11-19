import hashlib
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from grarec_app import Application
from paper_pipeline import ledger_inbox
from paper_pipeline.manual import Workspace, local_settings


class LedgerInboxTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        settings = local_settings(self.repo)
        self.workspace = Workspace.create(self.repo / 'shared', settings['device_id'], 'test')
        self.code = ledger_inbox.pairing_code(self.repo, self.workspace)

    def test_startup_registers_only_new_papers_and_acknowledges_after_save(self):
        ws = self.workspace
        ws.add('12')
        event = ws.current('12')
        event['data']['archive'] = {'reason': 'test'}
        ws.record('12', event['data'], [event['id']])
        before = ws.events('12')
        def exchange(key, received):
            self.assertEqual(hashlib.sha256(key.encode()).hexdigest(), self.code)
            if not received:
                return ['11', '12']
            self.assertEqual(received, ['11', '12'])
            self.assertTrue(ws.events('11'))
            return []
        with patch.object(ledger_inbox, 'exchange', side_effect=exchange):
            app = Application(self.repo, ws.root)
            deadline = time.monotonic() + 10
            while app.busy and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertIn('新規1件', app.message)
            self.assertEqual(app.state['counts']['missing_pdf'], 1)
            self.assertEqual(ws.events('12'), before)

    def test_interrupted_ack_replays_without_creating_a_second_record(self):
        with patch.object(ledger_inbox, 'exchange', side_effect=[['31'], OSError('offline')]):
            with self.assertRaises(OSError):
                ledger_inbox.receive(self.repo, self.workspace)
        before = self.workspace.events('31')
        with patch.object(ledger_inbox, 'exchange', side_effect=[['31'], []]):
            self.assertIn('登録済み1件', ledger_inbox.receive(self.repo, self.workspace))
        self.assertEqual(self.workspace.events('31'), before)

    def test_invalid_page_is_not_partially_registered_or_acknowledged(self):
        with patch.object(ledger_inbox, 'exchange', return_value=['31', 'invalid']) as call:
            with self.assertRaises(ValueError):
                ledger_inbox.receive(self.repo, self.workspace)
            self.assertEqual(call.call_count, 1)
        self.assertFalse(self.workspace.events('31'))
