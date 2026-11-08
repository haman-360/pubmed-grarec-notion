"""Exercise real Requests preparation/transport boundary without opening sockets."""
import hashlib
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from requests.adapters import BaseAdapter

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from paper_pipeline.batches import API


class Capture(BaseAdapter):
    def __init__(self, root, mode='ok'):
        self.root,self.mode,self.calls=root,mode,[]

    def send(self,request,**kwargs):
        self.calls.append((request,kwargs))
        # Evidence must exist before the request reaches any transport.
        directory=next(self.root.iterdir())
        assert (directory/'request.body.json').read_bytes()==request.body
        if self.mode=='timeout':
            raise requests.Timeout('private error containing secret-token')
        response=requests.Response()
        response.status_code=302 if self.mode=='redirect' else 200
        if self.mode=='redirect':
            response.headers['Location']='https://example.invalid/must-not-follow'
        response.headers.update({'x-request-id':'server-test-id','openai-processing-ms':'15',
            'openai-version':'test-version','Set-Cookie':'private-response-cookie'})
        response._content=b'{"usage":{"input_tokens":8},"data":[{"generation_id":"test-gen","b64_json":"aW1hZ2U="}]}'
        response.request=request
        response.url=request.url
        if self.mode=='mismatch':
            response.request=request.copy()
            response.request.body=b'{"prompt":"wrong"}'
        return response

    def close(self): pass


class TransportAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)/'audit'
        self.payload={'model':'gpt-image-2.5-sunburst','prompt':'医学グラレコ PMID 42835691\n'*1000,
            'size':'2048x1152','quality':'high','n':1,'output_format':'png'}

    def tearDown(self): self.temp.cleanup()

    def api(self,mode):
        session=requests.Session()
        session.headers['Cookie']='private-request-cookie'
        adapter=Capture(self.root,mode)
        session.mount('https://',adapter)
        return API('test-secret-token',session=session,audit_dir=self.root),adapter

    def test_exact_prepared_body_headers_and_linkage_are_preserved(self):
        api,adapter=self.api('ok')
        with patch.object(api.session,'send',wraps=api.session.send) as send:
            response=api.call('POST','/images/generations',json=self.payload,timeout=600,
                audit_context={'attempt_id':'attempt','revision_id':'revision','ignored':'secret-context'})
            self.assertFalse(send.call_args.kwargs['allow_redirects'])
        sent,kwargs=adapter.calls[0]
        self.assertEqual(json.loads(sent.body),self.payload)
        self.assertEqual(len(adapter.calls),1)
        directory=self.root/response.paper_pipeline_audit_id
        request=json.loads((directory/'request.metadata.json').read_text())
        self.assertEqual(sent.headers['X-Client-Request-Id'],request['client_request_id'])
        metadata=json.loads((directory/'response.metadata.json').read_text())
        self.assertTrue(metadata['response_request_body_matches'])
        self.assertEqual(metadata['headers']['x-request-id'],'server-test-id')
        self.assertEqual(metadata['generation_ids'],['test-gen'])
        self.assertEqual(metadata['images'][0]['sha256'],hashlib.sha256(b'image').hexdigest())
        text=''.join(p.read_text() for p in directory.iterdir())
        for secret in ('test-secret-token','private-request-cookie','private-response-cookie','secret-context'):
            self.assertNotIn(secret,text)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode),0o700)
        for path in directory.iterdir(): self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)

    def test_redirect_is_recorded_never_followed_or_retried(self):
        api,adapter=self.api('redirect')
        with self.assertRaisesRegex(RuntimeError,'リダイレクト'):
            api.call('POST','/images/generations',json=self.payload)
        self.assertEqual(len(adapter.calls),1)
        directory=next(self.root.iterdir())
        self.assertEqual(json.loads((directory/'response.metadata.json').read_text())['http_status'],302)

    def test_body_mismatch_preserves_response_and_stops(self):
        api,adapter=self.api('mismatch')
        with self.assertRaisesRegex(RuntimeError,'本文不一致'):
            api.call('POST','/images/generations',json=self.payload)
        directory=next(self.root.iterdir())
        self.assertTrue((directory/'response.body.json').exists())
        self.assertFalse(json.loads((directory/'response.metadata.json').read_text())['response_request_body_matches'])
        self.assertEqual(len(adapter.calls),1)

    def test_timeout_preserves_id_and_never_retries_or_logs_private_message(self):
        api,adapter=self.api('timeout')
        with self.assertRaises(requests.Timeout):
            api.call('POST','/images/generations',json=self.payload)
        directory=next(self.root.iterdir())
        data=json.loads((directory/'transport_error.json').read_text())
        self.assertEqual(data['type'],'Timeout')
        self.assertEqual(len(adapter.calls),1)
        self.assertNotIn('secret-token',str(data))

    def test_empty_prompt_stops_before_transport(self):
        api,adapter=self.api('ok')
        with self.assertRaises(ValueError): api.call('POST','/images/generations',json={'prompt':''})
        self.assertFalse(adapter.calls)


if __name__=='__main__': unittest.main()
