"""Private evidence for a single Images request. Never logs credentials/cookies."""
import base64
import hashlib
import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests

from .store import encode, now


def private_write(path, data):
    data = data if isinstance(data,bytes) else data.encode('utf-8')
    descriptor = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'wb') as handle:
        handle.write(data)


def body_bytes(value):
    if isinstance(value,bytes):
        return value
    if isinstance(value,str):
        return value.encode('utf-8')
    raise ValueError('監査対象はバイト列または文字列のJSON本文に限定します')


def safe_url(value):
    parts = urlsplit(value or '')
    # Query strings/userinfo may contain credentials; hashes/body equality remain exact.
    host = parts.hostname or ''
    if parts.port:
        host += ':'+str(parts.port)
    return urlunsplit((parts.scheme,host,parts.path,'',''))


def send_image(session, url, payload, root, timeout, context=None):
    if url != 'https://api.openai.com/v1/images/generations':
        raise ValueError('想定外の画像API送信先です')
    if not isinstance(payload,dict) or not isinstance(payload.get('prompt'),str) or not payload['prompt']:
        raise ValueError('画像プロンプトが空です')
    request_id = str(uuid.uuid4())
    root = Path(root)
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    directory = root/request_id
    directory.mkdir(mode=0o700)
    prepared = session.prepare_request(requests.Request('POST',url,json=payload,
        headers={'X-Client-Request-Id':request_id}))
    if prepared.url != url or prepared.method != 'POST':
        raise ValueError('準備された要求の送信先・メソッドが変わりました')
    if prepared.headers.get('X-Client-Request-Id') != request_id:
        raise ValueError('準備された要求IDが変わりました')
    raw = body_bytes(prepared.body)
    decoded = json.loads(raw)
    if decoded != payload:
        raise ValueError('送信直前に画像要求が変わりました')
    metadata = {'client_request_id':request_id,'prepared_at':now(),'method':prepared.method,'url':prepared.url,
        'content_type':prepared.headers.get('Content-Type'),'content_length':prepared.headers.get('Content-Length'),
        'body_bytes':len(raw),'body_sha256':hashlib.sha256(raw).hexdigest(),
        'prompt_characters':len(decoded['prompt']),'prompt_sha256':hashlib.sha256(decoded['prompt'].encode()).hexdigest(),
        'model':decoded.get('model'),'size':decoded.get('size'),'quality':decoded.get('quality'),
        'context':{k:str(v) for k,v in (context or {}).items() if k in ('attempt_id','revision_id')}}
    private_write(directory/'request.body.json',raw)
    private_write(directory/'request.metadata.json',encode(metadata))
    settings = session.merge_environment_settings(prepared.url,{},False,None,None)
    settings.update(timeout=timeout,allow_redirects=False)
    try:
        response = session.send(prepared,**settings)
    except Exception as error:
        # URLs/exception messages may include credentials; retain only the class.
        private_write(directory/'transport_error.json',encode({'at':now(),'type':type(error).__name__,
            'client_request_id':request_id,'result':'unknown; do not automatically resend'}))
        raise
    received_at = now()
    response_body = response.content
    private_write(directory/'response.body.json',response_body)
    sent = response.request
    sent_raw = body_bytes(sent.body) if sent is not None else b''
    matched = sent is not None and sent.method == prepared.method and sent.url == prepared.url and sent_raw == raw
    headers = {name:response.headers.get(name) for name in ('x-request-id','openai-processing-ms','openai-version',
        'date','content-type','content-length')}
    result = {'received_at':received_at,'client_request_id':request_id,'http_status':response.status_code,
        'final_url':safe_url(response.url),'redirect_history':[{'status':r.status_code,'url':safe_url(r.url)} for r in response.history],
        'headers':headers,'response_bytes':len(response_body),'response_sha256':hashlib.sha256(response_body).hexdigest(),
        'response_request_body_matches':matched,'response_request_body_sha256':hashlib.sha256(sent_raw).hexdigest()}
    try:
        data = json.loads(response_body)
        if isinstance(data,dict):
            result['usage'] = data.get('usage')
            result['generation_ids'] = [item.get('generation_id') for item in data.get('data',[]) if isinstance(item,dict)]
            result['images'] = []
            for item in data.get('data',[]):
                if isinstance(item,dict) and isinstance(item.get('b64_json'),str):
                    try:
                        image = base64.b64decode(item['b64_json'],validate=True)
                        result['images'].append({'sha256':hashlib.sha256(image).hexdigest(),'bytes':len(image)})
                    except ValueError:
                        result['images'].append({'error':'invalid_base64'})
    except (ValueError,TypeError):
        result['json_parse_failed'] = True
    private_write(directory/'response.metadata.json',encode(result))
    response.paper_pipeline_audit_id = request_id
    if 300 <= response.status_code < 400 or response.history or response.url != url:
        raise RuntimeError('画像APIのリダイレクト/送信先変更を検出。再送せず監査記録を確認してください')
    if not matched:
        raise RuntimeError('画像APIの送信本文不一致。再送せず監査記録を確認してください')
    return response
