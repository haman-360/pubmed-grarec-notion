"""Manual OneDrive desktop UI with aggregate reporting to the private status site."""
import json
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from paper_pipeline.manual import Workspace, STAGES, local_settings, save_settings, suggested_root, pmid_value
from paper_pipeline.manual_status import StatusReporter
from paper_pipeline.intake import (PENDING, ARCHIVE, ensure_folders, inventory, organize,
                                   parse_pmids, result_message, workspace_lock)
from paper_pipeline.store import now

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
        self.message = '共通の取り込み待ちに保存し、「取り込み・整理」でまとめて進められます。'
        self.intake_result = None
        self.pdf_progress = None
        self.scan_errors = []
        self.state = {}
        self.reporter = StatusReporter(repo)
        self.refresh(scan=True)

    def workspace(self):
        if not self.settings['shared_root']:
            raise ValueError('OneDriveの共通フォルダを設定してください')
        return Workspace(self.settings['shared_root'], self.settings['device_id'], self.settings['device_name'])

    def refresh(self, scan=False):
        if self.settings['shared_root']:
            try:
                workspace = self.workspace()
            except (ValueError, OSError):
                return self._refresh(scan)
            with workspace_lock(workspace):
                return self._refresh(scan)
        return self._refresh(scan)

    def _refresh(self, scan=False):
        base = dict(configured=bool(self.settings['shared_root']), suggestion=suggested_root(),
                    device=self.settings['device_name'], root=self.settings['shared_root'])
        try:
            workspace = self.workspace()
            ensure_folders(workspace)
            if scan:
                _, self.scan_errors = workspace.scan()
            state = workspace.snapshot()
            state['intake'] = inventory(workspace)
            result_path = workspace.path('状況/' + workspace.device_id + '/取り込み結果.json')
            if result_path.is_file():
                self.intake_result = json.loads(result_path.read_text())
            from paper_pipeline.pdf_download import result_path as pdf_result_path
            pdf_path = pdf_result_path(workspace)
            state['pdf_result'] = json.loads(pdf_path.read_text()) if pdf_path.is_file() else None
            from paper_pipeline.json_batch import status as json_status
            state['json_batch'] = json_status(self.repo, workspace)
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
            self.state.update(observed_at=now(), intake_result=self.intake_result)
            self.reporter.submit(self.state)
        except Exception as error:
            self.state = dict(base, papers=[], counts=dict(total=0, **{k: 0 for k in STAGES}), errors=[str(error)])

    def run(self, action, data):
        if action not in ('configure', 'fetch-pdfs', 'json-budget', 'json-prepare', 'json-send', 'json-collect') and self.settings['shared_root']:
            with workspace_lock(self.workspace()):
                return self._run(action, data)
        return self._run(action, data)

    def _run(self, action, data):
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
        if action in ('json-budget', 'json-prepare', 'json-send', 'json-collect'):
            from paper_pipeline import json_batch
            if action == 'json-budget':
                return json_batch.set_budget(self.repo, workspace, data)
            if action == 'json-prepare':
                return json_batch.prepare(self.repo, workspace, data.get('items'))
            if action == 'json-send':
                return json_batch.submit(self.repo, workspace, data)
            return json_batch.collect(self.repo, workspace)
        if action == 'json-results-folder':
            folder = workspace.path('API_JSON')
            if not folder.is_dir():
                raise ValueError('API処理の記録はまだありません')
            subprocess.run(['open', str(folder)], check=True)
            return 'API処理の状況と回収候補の保存先を開きました。'
        if action == 'fetch-pdfs':
            from paper_pipeline.pdf_download import download_selected, result_message as pdf_result_message
            def progress(value):
                self.pdf_progress = value
                self.refresh()
            try:
                result = download_selected(workspace, data.get('items'), progress)
                return pdf_result_message(result)
            finally:
                self.pdf_progress = None
        if action == 'open-pdf-result':
            from paper_pipeline.pdf_download import result_path
            result = json.loads(result_path(workspace).read_text())
            if result['id'] != data.get('run'):
                raise ValueError('取得結果が更新されています。画面を更新してください')
            entry = next((e for e in result['items'] if e['pmid'] == data.get('pmid')), None)
            if entry and data.get('url'):
                from paper_pipeline.pdf_download import public_url
                url = data['url']
                if url not in entry['manual_urls'] or public_url(url) != url:
                    raise ValueError('取得結果の手動確認先を選んでください')
                subprocess.run(['open', url], check=True)
                return '手動確認先をブラウザーで開きました。'
            if not entry or not entry.get('file'):
                raise ValueError('保管されたPDFがありません')
            workspace.read_asset(entry['file'])
            subprocess.run(['open', str(workspace.path(entry['file']['path']))], check=True)
            return '保存したPDFを開きました。候補の場合は原本を照合してください。'
        if action == 'organize':
            self.intake_result = organize(workspace)
            return result_message(self.intake_result)
        if action in ('intake-folder', 'archive-folder'):
            ensure_folders(workspace)
            folder = workspace.path(PENDING if action == 'intake-folder' else ARCHIVE)
            subprocess.run(['open', str(folder)], check=True)
            return '取り込み待ちを開きました。' if action == 'intake-folder' else '取り込み済みを開きました。'
        if action == 'refresh':
            return '保存フォルダと進捗記録を確認しました。OneDriveのアップロード状況はOneDriveアプリで確認してください。'
        if action == 'add':
            ids = parse_pmids(data.get('pmids', ''))
            if not ids:
                raise ValueError('PMIDを入力してください')
            for pmid in ids:
                pmid_value(pmid)
            for pmid in ids:
                workspace.add(pmid)
            return f'{len(ids)}件のPMIDを登録しました。取り込み待ちのPMID_番号フォルダへファイルを保存できます。'
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
        raise ValueError('この旧操作は廃止されました。JSON Batchは専用の作成・回収ボタンを使ってください。')

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
                return self.reply(200, json.dumps(dict(app.state, busy=app.busy, message=app.message,
                                                       pdf_progress=app.pdf_progress,
                                                       sharing=app.reporter.status()), ensure_ascii=False))
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
    threading.Thread(target=app.reporter.watch, daemon=True).start()
    print(f'http://127.0.0.1:{server.server_port}/?token={app.token}', flush=True)
    server.serve_forever()
