import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from grarec_app import Application
from paper_pipeline.manual import SCHEMA, Workspace
from paper_pipeline.manual_status import StatusReporter


class ManualStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.config = self.repo / '.remote_status/config.json'
        self.config.parent.mkdir()
        self.config.write_text(json.dumps(dict(url='https://test.invalid', source_id='synthetic',
                                               service_token='private', reporter_token='private')))
        self.workspace = Workspace.create(self.repo / 'shared', 'a' * 32, '自宅 Mac')
        self.workspace.add('90000001')

    def test_reports_exact_desktop_categories_including_scan_errors_without_paper_data(self):
        (self.workspace.incoming('90000001') / 'PDF/invalid.pdf').write_bytes(b'invalid')
        with patch('paper_pipeline.remote_status.Client') as client:
            app = Application(self.repo, self.workspace.root)
            before = {p: p.read_bytes() for p in self.workspace.root.rglob('*') if p.is_file()}
            self.assertEqual(app.state['counts']['attention'], 1)
            app.reporter.publish()
            args = client.return_value.call.call_args.args
            self.assertEqual(args[0], '/api/report')
            payload = args[1]
            self.assertEqual(payload['counts'], app.state['counts'])
            self.assertEqual(payload['workflow'], SCHEMA)
            self.assertEqual(payload['device_name'], app.state['device'])
            self.assertEqual(set(payload), {'workflow', 'source_id', 'device_name', 'observed_at',
                                            'busy', 'counts', 'last_event'})
            self.assertNotIn('90000001', json.dumps(payload))
            self.assertEqual(before, {p: p.read_bytes() for p in self.workspace.root.rglob('*') if p.is_file()})
            self.assertIsNone(app.reporter.pending)
            self.assertTrue(app.reporter.status()['last_shared_at'])

    def test_network_failure_keeps_observation_and_latest_change_replaces_retry(self):
        reporter = StatusReporter(self.repo)
        snapshot = self.workspace.snapshot()
        reporter.submit(snapshot)
        first = copy.deepcopy(reporter.pending)
        with patch('paper_pipeline.remote_status.Client') as client:
            client.return_value.call.side_effect = RuntimeError('private token must not be logged')
            reporter.publish()
            self.assertEqual(reporter.pending, first)
            self.assertNotIn('private token', reporter.status()['message'])
            snapshot['counts'].update(missing_pdf=0, attention=1)
            reporter.submit(snapshot)
            client.return_value.call.side_effect = None
            reporter.publish()
            self.assertEqual(client.return_value.call.call_args.args[1]['counts']['attention'], 1)
            self.assertIsNone(reporter.pending)

    def test_new_observation_during_upload_is_not_lost(self):
        reporter = StatusReporter(self.repo)
        reporter.submit(self.workspace.snapshot())
        def change(*args):
            self.workspace.add('90000002')
            reporter.submit(self.workspace.snapshot())
        with patch('paper_pipeline.remote_status.Client') as client:
            client.return_value.call.side_effect = change
            reporter.publish()
            self.assertEqual(reporter.pending['counts']['total'], 2)
            client.return_value.call.side_effect = None
            reporter.publish()
            self.assertEqual(client.return_value.call.call_args.args[1]['counts']['total'], 2)

    def test_missing_configuration_remains_offline_and_invalid_workspace_is_not_reported(self):
        self.config.unlink()
        app = Application(self.repo, self.workspace.root)
        with patch('requests.Session.request') as request:
            app.reporter.publish()
            request.assert_not_called()
        self.assertFalse(app.reporter.status()['configured'])
        self.config.write_text('{}')
        app = Application(self.repo, self.repo / 'missing-workspace')
        self.assertIsNone(app.reporter.pending)
