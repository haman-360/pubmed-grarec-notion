"""Receive the explicitly queued PMIDs on the paired Mac."""
import hashlib
from http.client import HTTPSConnection, HTTPException
import json
import os
import re
import secrets
import tempfile
from pathlib import Path

from .manual import pmid_value

HOST = 'pmid-ledger-test.tempter-carrel-5e.workers.dev'


def key_path(repo, workspace):
    # A newly selected workspace requires its own explicit pairing.
    root_hash = hashlib.sha256(str(workspace.root).encode()).hexdigest()
    return Path(repo) / 'output' / ('ledger-receiver-' + root_hash + '.key')


def receiver_key(path, create=False):
    if create and not path.exists():
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
            temp.write(secrets.token_hex(32).encode())
        try:
            os.link(temp.name, path)
        except FileExistsError:
            pass
        finally:
            os.unlink(temp.name)
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError('このMacの接続コードを設定してください。')
    key = path.read_text()
    if not re.fullmatch('[0-9a-f]{64}', key):
        raise ValueError('受信Macの接続設定を確認してください。')
    return key


def pairing_code(repo, workspace):
    key = receiver_key(key_path(repo, workspace), create=True)
    return hashlib.sha256(key.encode()).hexdigest()


def exchange(key, received):
    connection = HTTPSConnection(HOST, timeout=12)
    try:
        connection.request('POST', '/api/grarecInbox', json.dumps({'received': received}),
                           {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
        response = connection.getresponse()
        if response.status == 403:
            raise ValueError('研究台帳の「グラレコの受信Macを設定」で、このMacの接続コードを登録してください。')
        if response.status != 200:
            raise ValueError('研究台帳の受信を完了できませんでした。台帳の反映・接続を確認してください。')
        data = json.loads(response.read(16384))
        return data['value']['pmids']
    except (HTTPException, OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError('研究台帳から受信できませんでした。接続後にもう一度受信してください。') from error
    finally:
        connection.close()


def receive(repo, workspace):
    key = receiver_key(key_path(repo, workspace))
    received = []
    seen = set()
    added = 0
    while True:
        # The preceding page is acknowledged only after every local record was saved.
        page = exchange(key, received)
        if not isinstance(page, list) or len(page) > 100:
            raise ValueError('研究台帳の受信内容を確認できませんでした。')
        received = [pmid_value(value) for value in page]
        if not received:
            return f'研究台帳から新規{added}件をPDF取得待ちに登録しました（登録済み{len(seen) - added}件）。'
        if len(set(received)) != len(received) or seen.intersection(received):
            raise ValueError('研究台帳の受信確認が完了していません。次回起動時に再確認します。')
        for pmid in received:
            if workspace.add(pmid):
                added += 1
        seen.update(received)
