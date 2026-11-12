"""Loopback desktop UI for the manual OneDrive workflow. No external requests."""
import json
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from paper_pipeline.manual import Workspace, STAGES, local_settings, save_settings, suggested_root, pmid_value

REPO = Path(__file__).resolve().parents[1]


class Application:
    def __init__(self, repo, root=None):
        self.repo = Path(repo)
        self.settings = local_settings(repo)
        if root is not None:
            self.settings['shared_root'] = str(root)
        self.token = secrets.token_urlsafe(32)
        self.mutex = threading.Lock()
        self.busy = False
        self.message = '手動ワークフロー。保存したファイルの状態を記録します。'
        self.scan_errors = []
        self.state = {}
        self.refresh(scan=True)

    def workspace(self):
        if not self.settings['shared_root']:
            raise ValueError('OneDriveの共通フォルダを設定してください')
        return Workspace(self.settings['shared_root'], self.settings['device_id'], self.settings['device_name'])

    def refresh(self, scan=False):
        base = dict(configured=bool(self.settings['shared_root']), suggestion=suggested_root(),
                    device=self.settings['device_name'], root=self.settings['shared_root'])
        try:
            workspace = self.workspace()
            if scan:
                _, self.scan_errors = workspace.scan()
            state = workspace.snapshot()
            state['errors'] += self.scan_errors
            for row in state['papers']:
                errors = [e for e in self.scan_errors if e.startswith('PMID ' + row['pmid'] + ':')]
                if errors and row['category'] != 'attention':
                    state['counts'][row['category']] -= 1
                    state['counts']['attention'] += 1
                    row['category'] = 'attention'
                if errors:
                    row['note'] = '\n'.join([row['note'], *errors])
            workspace.export_view(state)
            self.state = dict(base, **{k: v for k, v in state.items() if k not in base})
        except Exception as error:
            self.state = dict(base, papers=[], counts=dict(total=0, **{k: 0 for k in STAGES}), errors=[str(error)])

    def run(self, action, data):
        if action == 'configure':
            value = data.get('root', '').strip()
            if not value or not Path(value).expanduser().is_absolute():
                raise ValueError('OneDrive内の専用フォルダの絶対パスを指定してください')
            name = data.get('device', '').strip()
            if not name:
                raise ValueError('端末名を入力してください（例：自宅 MacBook Air）')
            workspace = Workspace.create(value, self.settings['device_id'], name)
            self.settings.update(shared_root=str(workspace.root), device_name=name)
            save_settings(self.repo, self.settings)
            return '共有フォルダを設定しました。OneDrive標準アプリで同期します。'
        workspace = self.workspace()
        if action == 'refresh':
            return '保存フォルダと進捗記録を確認しました。OneDriveのアップロード状況はOneDriveアプリで確認してください。'
        if action == 'add':
            ids = list(dict.fromkeys(x.strip() for x in data.get('pmids', '').splitlines() if x.strip()))
            if not ids:
                raise ValueError('PMIDを入力してください')
            for pmid in ids:
                pmid_value(pmid)
            for pmid in ids:
                workspace.add(pmid)
            return f'{len(ids)}件のPMIDを登録しました。PDFを各論文の受け渡し/PDFに保存してください。'
        if action == 'migrate':
            from paper_pipeline.manual_migration import migrate
            from paper_pipeline.workspace import select_state_dir
            result = migrate(workspace, select_state_dir(self.repo))
            return f"旧台帳から{result['copied']}件をコピー（既存{result['skipped']}件は保持）。" + '\n'.join(result['warnings'])
        pmid = data.get('pmid')
        if action == 'folder':
            folder = workspace.incoming(pmid) if pmid else workspace.root
            subprocess.run(['open', str(folder)], check=True)
            return '共有フォルダを開きました。'
        if action == 'view':
            subprocess.run(['open', str(workspace.path('状況/' + workspace.device_id))], check=True)
            return 'この端末が最後に記録した状況を開きました。'
        if action == 'open-file':
            event = workspace.current(pmid, data['heads'])
            file = next((f for f in event['data']['files'] if f['path'] == data.get('path')), None)
            if file is None:
                raise ValueError('現在のファイルを選んでください')
            workspace.read_asset(file)
            subprocess.run(['open', str(workspace.path(file['path']))], check=True)
            return 'ファイルを開きました。'
        if action in ('copy-json', 'copy-image'):
            workspace.current(pmid, data['heads'])
            text = workspace.json_prompt(pmid) if action == 'copy-json' else workspace.image_prompt(
                pmid, data['topic'], (self.repo / 'prompts/grarec-v2.md').read_text())
            subprocess.run(['/usr/bin/pbcopy'], input=text.encode(), check=True)
            return f'コピーしました：PMID {pmid}。ChatGPT Webに貼り付けてください。'
        if action == 'record':
            workspace.update(pmid, data['heads'], data.get('note', ''), data.get('done') is True, data.get('chosen'))
            return '進捗を記録しました。実ファイルのクラウド同期はOneDriveアプリで確認してください。'
        raise ValueError('この操作は廃止されました。API処理・自動回収・Notion自動送信は利用しません。')

    def start(self, action, data):
        with self.mutex:
            if self.busy:
                raise ValueError('処理中です。完了を待ってください。')
            self.busy = True
        def work():
            try:
                self.message = self.run(action, data)
                self.refresh(scan=True)
            except Exception as error:
                self.message = '要確認: ' + str(error)[:1200]
                self.refresh()
            finally:
                with self.mutex:
                    self.busy = False
        threading.Thread(target=work, daemon=True).start()

    def watch(self):
        while True:
            time.sleep(30)
            with self.mutex:
                if self.busy:
                    continue
                self.busy = True
            try:
                self.refresh(scan=True)
            finally:
                with self.mutex:
                    self.busy = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body, kind='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', kind + '; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body.encode())

    def allowed(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}' and self.headers.get('X-App-Token') == self.server.app.token

    def do_GET(self):
        app = self.server.app
        if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
            return self.reply(403, '{}')
        if self.path == '/?token=' + app.token:
            return self.reply(200, (REPO / 'scripts/dashboard.html').read_text().replace('__TOKEN__', app.token), 'text/html')
        if self.path == '/api/state' and self.allowed():
            with app.mutex:
                return self.reply(200, json.dumps(dict(app.state, busy=app.busy, message=app.message), ensure_ascii=False))
        self.reply(403, '{}')

    def do_POST(self):
        if not self.allowed() or self.path != '/api/action':
            return self.reply(403, '{}')
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 1 <= length <= 100000:
                raise ValueError('入力が大きすぎます')
            data = json.loads(self.rfile.read(length))
            action = data.pop('action')
            self.server.app.start(action, data)
            self.reply(200, '{}')
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            self.reply(400, json.dumps({'error': str(error)}))


if __name__ == '__main__':
    app = Application(REPO)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.app = app
    threading.Thread(target=app.watch, daemon=True).start()
    print(f'http://127.0.0.1:{server.server_port}/?token={app.token}', flush=True)
    server.serve_forever()
