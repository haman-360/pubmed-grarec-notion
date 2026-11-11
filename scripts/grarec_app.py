"""Loopback-only backend for the native macOS window. No requests run on launch."""
import contextlib
import io
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from openai_batch import load_dotenv
from paper_pipeline.dashboard import Dashboard
from paper_pipeline.store import Store
from paper_pipeline.workspace import select_state_dir, remember_state_dir
from paper_pipeline.library import expose_library

REPO=Path(__file__).resolve().parents[1]

class Application:
    def __init__(self, repo, root):
        self.dashboard=Dashboard(repo,root); self.token=secrets.token_urlsafe(32)
        self.mutex=threading.Lock(); self.busy=False; self.message='準備できました。'; self.auto_collect=False
        self.remote_sync='共有設定なし'
        self.refresh()
    def refresh(self):
        store=Store(self.dashboard.root)
        try:
            with store.lock():
                expose_library(self.dashboard.repo,store)
                remember_state_dir(self.dashboard.repo,store.root)
                self.state=self.dashboard.snapshot(store)
                self.pending=bool(store.rows("SELECT 1 FROM attempts WHERE status='submitted' LIMIT 1"))
        finally: store.close()
    def start(self, action, data):
        with self.mutex:
            if self.busy: raise ValueError('処理中です。完了を待ってください。')
            self.busy=True; self.message='処理中… '+{'search':'未検索PMIDの検索','auto':'検索→JSON作成','json':'JSON作成','upload':'PNG登録'}.get(action,'')
        def work():
            store=None
            try:
                if action=='refresh': self.refresh(); self.message='一覧を更新しました。'
                else:
                    store=Store(self.dashboard.root)
                    with store.lock(),contextlib.redirect_stdout(io.StringIO()):
                        self.message=self.dashboard.run(action,data,store)
                    store.close(); store=None
                    self.refresh()
            except (Exception, SystemExit) as e:
                self.message='要確認: '+str(e)[:1200]
                if store: store.close(); store=None
                try: self.refresh()
                except Exception: pass
            finally:
                with self.mutex: self.busy=False
        threading.Thread(target=work,daemon=True).start()
    def poll(self):
        while True:
            time.sleep(60)
            if self.auto_collect and self.pending and not self.busy:
                try: self.start('collect',{})
                except ValueError: pass

    def sync_status_once(self):
        if not (self.dashboard.repo/'.remote_status/config.json').is_file():
            return
        from paper_pipeline.remote_status import sync_once
        try:
            if sync_once(self.dashboard.repo): self.remote_sync='状況共有：同期済み'
        except Exception:
            self.remote_sync='状況共有：接続できません（約1分後に再試行）'

    def sync_status(self):
        # A separate thread keeps network/notification retries off the collection path.
        while True:
            self.sync_status_once()
            time.sleep(60)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def reply(self,status,body,kind='application/json'):
        self.send_response(status); self.send_header('Content-Type',kind+'; charset=utf-8')
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers(); self.wfile.write(body.encode())
    def allowed(self):
        return self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}' and self.headers.get('X-App-Token')==self.server.app.token
    def do_GET(self):
        app=self.server.app
        if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}': return self.reply(403,'{}')
        if self.path=='/?token='+app.token:
            html=(REPO/'scripts/dashboard.html').read_text().replace('__TOKEN__',app.token)
            return self.reply(200,html,'text/html')
        if self.path=='/api/state' and self.allowed():
            with app.mutex:
                if not app.busy:
                    store=Store(app.dashboard.root)
                    try:
                        with store.lock(): app.state=app.dashboard.snapshot(store)
                    except ValueError: pass
                    finally: store.close()
            return self.reply(200,json.dumps(dict(app.state,busy=app.busy,message=app.message,auto_collect=app.auto_collect,remote_sync=app.remote_sync),ensure_ascii=False))
        self.reply(403,'{}')
    def do_POST(self):
        if not self.allowed() or self.path!='/api/action': return self.reply(403,'{}')
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<1 or length>100000: raise ValueError('入力が大きすぎます')
            data=json.loads(self.rfile.read(length)); action=data.pop('action')
            if action=='poll': self.server.app.auto_collect=data.get('enabled') is True
            else: self.server.app.start(action,data)
            self.reply(200,'{}')
        except (ValueError,KeyError,TypeError) as e: self.reply(400,json.dumps({'error':str(e)}))

if __name__=='__main__':
    load_dotenv(str(REPO/'.env'))
    app=Application(REPO,select_state_dir(REPO,os.getenv('PAPER_PIPELINE_HOME')))
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler); server.app=app
    threading.Thread(target=app.poll,daemon=True).start()
    threading.Thread(target=app.sync_status,daemon=True).start()
    print(f'http://127.0.0.1:{server.server_port}/?token={app.token}',flush=True)
    server.serve_forever()
