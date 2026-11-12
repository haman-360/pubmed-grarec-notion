import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from paper_pipeline.remote_status import Outbox, Client, deliver, read_snapshot, valid_endpoint
from paper_pipeline.store import now
import test_workspace_library as fixtures

class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'outbox.sqlite3'
        self.o = Outbox(self.path); self.addCleanup(self.o.close)
        self.o.record([{'id':'old','at':None}])
        self.client = Mock(); self.client.push.return_value = True
        self.subs = [{'id':'phone','subscription':{'endpoint':'https://web.push.apple.com/test'}}]
    def event(self):
        self.o.record([{'id':'old','at':None},{'id':'new','at':now()}])
    def test_initial_baseline_never_notifies_old_results(self):
        deliver(self.o,self.client,self.subs)
        self.assertIsNone(self.o.latest()); self.client.push.assert_not_called()
    def test_new_results_once_and_retry_survives_restart(self):
        self.event(); self.client.push.side_effect=RuntimeError('offline')
        with self.assertRaises(RuntimeError): deliver(self.o,self.client,self.subs)
        second=Outbox(self.path); self.addCleanup(second.close)
        self.client.push.side_effect=None
        deliver(second,self.client,self.subs); deliver(second,self.client,self.subs)
        self.assertEqual(self.client.push.call_count,2)
        self.assertEqual(second.latest()['count'],1)
        self.o.record([{'id':'old','at':None},{'id':'new','at':now()}])
        self.assertEqual(self.o.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],1)
    def test_one_unreachable_device_does_not_block_the_other(self):
        self.event()
        self.subs.append({'id':'ipad','subscription':{'endpoint':'https://web.push.apple.com/ipad'}})
        self.client.push.side_effect=[RuntimeError('offline'),True]
        with self.assertRaises(RuntimeError): deliver(self.o,self.client,self.subs)
        self.assertEqual(self.client.push.call_count,2)
        self.client.push.side_effect=None
        deliver(self.o,self.client,self.subs)
        self.assertEqual(self.client.push.call_count,3)
    def test_no_subscriptions_does_not_send_historical_event_to_new_device(self):
        self.event(); deliver(self.o,self.client,[]); deliver(self.o,self.client,self.subs)
        self.client.push.assert_not_called()
    def test_expired_subscription_removed_without_repeating_delivery(self):
        self.event(); self.client.push.return_value=False
        deliver(self.o,self.client,self.subs); deliver(self.o,self.client,self.subs)
        self.client.call.assert_called_once_with('/api/unsubscribe',{'endpoint':'https://web.push.apple.com/test'})
        self.assertEqual(self.client.push.call_count,1)
    def test_test_ack_failure_does_not_resend_test(self):
        self.subs[0]['test_requested_at']=now()
        self.client.call.side_effect=RuntimeError('offline')
        with self.assertRaises(RuntimeError): deliver(self.o,self.client,self.subs)
        self.client.call.side_effect=None; deliver(self.o,self.client,self.subs)
        self.assertEqual(self.client.push.call_count,1)
    def test_site_credentials_never_follow_redirects(self):
        client=Client({'url':'https://test.invalid','service_token':'service','reporter_token':'writer'})
        client.session=Mock(); client.session.request.return_value.status_code=302
        with self.assertRaises(RuntimeError): client.call('/api/report',{})
        self.assertFalse(client.session.request.call_args.kwargs['allow_redirects'])
    def test_push_endpoint_allowlist(self):
        self.assertTrue(valid_endpoint('https://web.push.apple.com/x'))
        for url in ('https://127.0.0.1/x','https://web.push.apple.com.evil.invalid/x','http://web.push.apple.com/x','https://u@web.push.apple.com/x'):
            self.assertFalse(valid_endpoint(url))

class SnapshotTests(unittest.TestCase):
    def test_reuses_real_categories_without_database_or_file_changes(self):
        fixture=fixtures.LibraryTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        before=list(fixture.store.db.iterdump())
        files=set(fixture.repo.rglob('*'))
        counts,completed,busy=read_snapshot(fixture.repo,fixture.store.root)
        self.assertEqual(counts['total'],1); self.assertEqual(counts['web_pending'],1)
        self.assertEqual(list(fixture.store.db.iterdump()),before)
        self.assertEqual(set(fixture.repo.rglob('*')),files)
        self.assertEqual(completed,[])

class PushEncryptionTests(unittest.TestCase):
    def test_real_library_encrypts_payload_and_preserves_event_id(self):
        try:
            import http_ece
            import pywebpush
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
        except ImportError:
            self.skipTest('Install requirements-status.txt to check Web Push encryption')
        import setup_status_sync
        with tempfile.TemporaryDirectory() as tmp, patch.object(setup_status_sync,'FOLDER',Path(tmp)):
            cfg=setup_status_sync.prepare('https://test.invalid','synthetic','test')
            private=ec.generate_private_key(ec.SECP256R1())
            public=private.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
            auth=b'a'*16
            b64=lambda b:base64.urlsafe_b64encode(b).rstrip(b'=').decode()
            subscription={'endpoint':'https://web.push.apple.com/test','keys':{'p256dh':b64(public),'auth':b64(auth)}}
            response=Mock(status_code=201)
            with patch('requests.Session.request',return_value=response) as request:
                self.assertTrue(Client(cfg).push(subscription,{'id':'event','count':2,'at':now()}))
            call=request.call_args.kwargs
            decoded=http_ece.decrypt(call['data'],private_key=private,auth_secret=auth,version='aes128gcm')
            self.assertEqual(json.loads(decoded)['count'],2)
            self.assertEqual(json.loads(decoded)['id'],'event')
            self.assertFalse(call['allow_redirects'])
            self.assertNotIn('OAI-Sites-Authorization',call['headers'])

class AppSyncTests(unittest.TestCase):
    def test_app_has_no_old_ledger_sync_or_collection(self):
        from grarec_app import Application
        self.assertFalse(hasattr(Application, 'sync_status_once'))
        self.assertFalse(hasattr(Application, 'sync_status'))
        self.assertFalse(hasattr(Application, 'poll'))
