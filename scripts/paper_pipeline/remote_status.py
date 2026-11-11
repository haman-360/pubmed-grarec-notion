"""Read-only ledger reporting and durable, retryable Web Push delivery.

Runs separately from collection: network failures never fail or repeat paid jobs.
Only aggregate counts and saved-result events leave this Mac.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests
from .dashboard import Dashboard
from .store import Store, now
from .workspace import select_state_dir

COUNT_KEYS = ('total', 'missing_pdf', 'json_pending', 'json_running', 'web_pending', 'completed', 'attention')


def read_snapshot(repo, root):
    """Use the existing categorization with a consistent, read-only SQLite snapshot."""
    store = Store.__new__(Store)
    store.root = Path(root).resolve()
    store.db = sqlite3.connect((store.root / 'ledger.sqlite3').as_uri() + '?mode=ro', uri=True)
    store.db.row_factory = sqlite3.Row
    try:
        store.db.execute('BEGIN')
        # Avoid Dashboard.__init__, which creates an inbox directory.
        dash = Dashboard.__new__(Dashboard)
        dash.repo, dash.root, dash.inbox = Path(repo), store.root, Path(repo) / 'input/web_images'
        snapshot = dash.snapshot(store)
        completed = []
        for row in store.rows("SELECT a.id,a.details,r.summary FROM attempts a JOIN revisions r ON r.id=a.revision_id WHERE a.stage='analysis' AND a.status='completed' AND r.summary IS NOT NULL"):
            if (store.root / row['summary']).is_file():
                completed.append({'id': row['id'], 'at': json.loads(row['details']).get('saved_at')})
        busy = False
        lock_path = store.root / 'run.lock'
        if lock_path.exists():
            with lock_path.open('r') as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle, fcntl.LOCK_UN)
                except BlockingIOError:
                    busy = True
        return {k: snapshot['counts'][k] for k in COUNT_KEYS}, completed, busy
    finally:
        store.close()


class Outbox:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, at TEXT NOT NULL, count INTEGER NOT NULL,
            targets TEXT, delivered TEXT NOT NULL DEFAULT '[]', done INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS tests (id TEXT PRIMARY KEY);
        ''')

    def close(self):
        self.db.close()

    def record(self, completed):
        with self.db:
            initialized = self.db.execute("SELECT 1 FROM meta WHERE key='initialized'").fetchone()
            seen = {r[0] for r in self.db.execute('SELECT id FROM seen')}
            fresh = [r for r in completed if r['id'] not in seen]
            # The first run establishes a baseline; never notify historical imports.
            if initialized and fresh:
                identifier = hashlib.sha256('|'.join(sorted(r['id'] for r in fresh)).encode()).hexdigest()
                at = max((r['at'] for r in fresh if r.get('at')), default=now())
                self.db.execute('INSERT OR IGNORE INTO events(id,at,count) VALUES(?,?,?)', (identifier, at, len(fresh)))
            self.db.executemany('INSERT OR IGNORE INTO seen(id) VALUES(?)', [(r['id'],) for r in fresh])
            self.db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('initialized','1')")

    def latest(self):
        row = self.db.execute('SELECT id,at,count FROM events ORDER BY rowid DESC LIMIT 1').fetchone()
        return dict(row) if row else None


class Client:
    def __init__(self, config):
        self.config = config
        url = urlsplit(config['url'])
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
            raise ValueError('状況共有URLはHTTPSのサイトのトップを指定してください')
        self.url = config['url'].rstrip('/')
        self.session = requests.Session()
        self.headers = {'OAI-Sites-Authorization': 'Bearer ' + config['service_token'], 'X-Reporter-Token': config['reporter_token']}

    def call(self, path, data=None):
        r = self.session.request('GET' if data is None else 'POST', self.url + path,
                                 headers=self.headers, json=data, timeout=(5, 15), allow_redirects=False)
        if not 200 <= r.status_code < 300:
            raise RuntimeError('状況共有の通信に失敗しました（HTTP %d）' % r.status_code)
        return r.json()

    def push(self, subscription, event):
        from pywebpush import webpush, WebPushException
        if not valid_endpoint(subscription.get('endpoint', '')):
            raise ValueError('通知先が標準Web Pushの送信先ではありません')
        # Do not attach Site credentials to push services or follow endpoint redirects.
        class PushSession(requests.Session):
            def request(self, *args, **kwargs):
                kwargs['allow_redirects'] = False
                return super().request(*args, **kwargs)
        try:
            with PushSession() as session:
                result = webpush(subscription_info=subscription, data=json.dumps(event),
                                 vapid_private_key=self.config['vapid_private_key'],
                                 vapid_claims={'sub': self.url}, ttl=86400, timeout=15,
                                 requests_session=session)
            if not 200 <= result.status_code < 300:
                raise RuntimeError('通知サービスが受け付けませんでした')
            return True
        except WebPushException as error:
            if error.response is not None and error.response.status_code in (404, 410):
                return False
            # Never expose subscription endpoints, keys, or response bodies in logs.
            raise RuntimeError('通知サービスへの送信に失敗しました') from None


def valid_endpoint(endpoint):
    try:
        u = urlsplit(endpoint)
        h = u.hostname or ''
        return u.scheme == 'https' and not u.port and not u.username and not u.password and not u.fragment and (
            h == 'web.push.apple.com' or h.endswith('.push.apple.com') or h == 'fcm.googleapis.com' or
            h == 'updates.push.services.mozilla.com' or h.endswith('.notify.windows.com'))
    except ValueError:
        return False


def deliver(outbox, client, subscriptions):
    subscriptions = {s['id']: s for s in subscriptions}
    failed = False
    pending = list(outbox.db.execute('SELECT * FROM events WHERE done=0 ORDER BY rowid'))
    for event in pending:
        targets = json.loads(event['targets']) if event['targets'] is not None else list(subscriptions)
        delivered = set(json.loads(event['delivered']))
        with outbox.db:
            outbox.db.execute('UPDATE events SET targets=? WHERE id=?', (json.dumps(targets), event['id']))
        # Stop retrying old notifications after seven days; their status remains in the page.
        expired = (datetime.now(timezone.utc) - datetime.fromisoformat(event['at'])).total_seconds() > 7 * 86400
        for identifier in targets:
            if identifier in delivered:
                continue
            sub = subscriptions.get(identifier)
            if sub and not expired:
                try:
                    if not client.push(sub['subscription'], {k: event[k] for k in ('id', 'at', 'count')}):
                        client.call('/api/unsubscribe', {'endpoint': sub['subscription']['endpoint']})
                except Exception:
                    failed = True
                    continue
            delivered.add(identifier)
            with outbox.db:
                outbox.db.execute('UPDATE events SET delivered=? WHERE id=?', (json.dumps(sorted(delivered)), event['id']))
        with outbox.db:
            outbox.db.execute('UPDATE events SET done=? WHERE id=?', (int(set(targets) <= delivered), event['id']))
    for identifier, sub in subscriptions.items():
        at = sub.get('test_requested_at')
        if not at:
            continue
        test_id = hashlib.sha256((identifier + at).encode()).hexdigest()
        if not outbox.db.execute('SELECT 1 FROM tests WHERE id=?', (test_id,)).fetchone():
            if not client.push(sub['subscription'], {'id': test_id, 'test': True, 'at': at}):
                client.call('/api/unsubscribe', {'endpoint': sub['subscription']['endpoint']})
            with outbox.db:
                outbox.db.execute('INSERT OR IGNORE INTO tests(id) VALUES(?)', (test_id,))
        client.call('/api/test-ack', {'id': identifier, 'at': at})
    if failed:
        raise RuntimeError('一部の端末への通知を次回再試行します')


def sync_once(repo, config_path=None):
    repo = Path(repo).resolve()
    folder = repo / '.remote_status'
    config_path = Path(config_path) if config_path else folder / 'config.json'
    config = json.loads(config_path.read_text())
    folder.mkdir(mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    with (folder / 'sync.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        root = select_state_dir(repo, config.get('state_dir'))
        counts, completed, busy = read_snapshot(repo, root)
        with closing(Outbox(folder / 'outbox.sqlite3')) as outbox:
            # Keep events through offline periods and process restarts.
            outbox.record(completed)
            client = Client(config)
            client.call('/api/report', {'source_id': config['source_id'], 'device_name': config['device_name'],
                                       'observed_at': now(), 'busy': busy, 'counts': counts, 'last_event': outbox.latest()})
            subscriptions = client.call('/api/subscriptions')['subscriptions']
            deliver(outbox, client, subscriptions)
    return True
