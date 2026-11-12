"""Keyless workflow: immutable file versions and append-only records in OneDrive.

Each Mac owns its local settings and exported view. No SQLite database, symlinks,
credentials or network client is shared. Concurrent records remain separate heads.
"""
from __future__ import annotations

import copy
import html
import io
import json
import re
import socket
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from PIL import Image
from .store import atomic_write, digest, encode, now

SCHEMA = 'manual-onedrive-v1'
STAGES = {'missing_pdf': 'PDF取得待ち', 'json_pending': 'JSON作成待ち',
          'web_pending': 'PNG作成待ち', 'review_pending': 'PNG確認・手動登録待ち',
          'completed': '手動登録完了', 'attention': '要確認・競合'}
KINDS = {'pdf': '.pdf', 'json': '.json', 'png': '.png'}


def pmid_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,8}', value):
        raise ValueError('PMIDは1〜9桁の数字で指定してください')
    return value


def local_settings(repo):
    path = Path(repo) / 'output/manual_settings.json'
    if path.exists():
        return json.loads(path.read_text())
    settings = {'device_id': uuid.uuid4().hex, 'device_name': socket.gethostname(), 'shared_root': ''}
    atomic_write(path, encode(settings))
    return settings


def save_settings(repo, settings):
    atomic_write(Path(repo) / 'output/manual_settings.json', encode(settings))


def suggested_root():
    roots = sorted((Path.home() / 'Library/CloudStorage').glob('OneDrive-*'))
    return str(roots[0] / 'PubMed_GraRec') if len(roots) == 1 else ''


class Workspace:
    def __init__(self, root, device_id, device_name):
        self.root = Path(root).expanduser().resolve()
        self.device_id, self.device_name = device_id, device_name
        if not re.fullmatch(r'[a-f0-9]{32}', device_id):
            raise ValueError('端末設定が不正です')
        marker = self.root / 'workspace.json'
        if not marker.is_file() or json.loads(marker.read_text()).get('schema') != SCHEMA:
            raise ValueError('共有フォルダが未設定、未同期、または別形式です。OneDriveの同期を確認してください')

    @classmethod
    def create(cls, root, device_id, device_name):
        root = Path(root).expanduser().resolve()
        if not (root / 'workspace.json').exists():
            if root.exists() and any(root.iterdir()):
                raise ValueError('初回は空の専用フォルダを指定してください')
            root.mkdir(parents=True, exist_ok=True)
            atomic_write(root / 'workspace.json', encode({'schema': SCHEMA}))
        return cls(root, device_id, device_name)

    def path(self, relative):
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError('共有フォルダ内の相対パスが必要です')
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('共有フォルダ外のファイルは参照できません')
        return path

    def folder(self, pmid):
        return self.path('papers/PMID_' + pmid_value(pmid))

    def incoming(self, pmid):
        folder = self.folder(pmid) / '受け渡し'
        for kind in KINDS:
            (folder / kind.upper()).mkdir(parents=True, exist_ok=True)
        return folder

    def events(self, pmid):
        events = {}
        for path in sorted((self.folder(pmid) / 'records').glob('*.json')):
            try:
                event = json.loads(path.read_text())
                if (event['schema'] != SCHEMA or event['pmid'] != pmid or
                        not re.fullmatch(r'[a-f0-9]{32}', event['id']) or
                        not isinstance(event['parents'], list) or
                        not all(isinstance(p, str) for p in event['parents']) or
                        not isinstance(event['data']['files'], list)):
                    raise ValueError()
                datetime.fromisoformat(event['at'])
                for file in event['data']['files']:
                    self.path(file['path'])
                if event['id'] in events and events[event['id']] != event:
                    raise ValueError()
                events[event['id']] = event
            except (ValueError, KeyError, TypeError) as error:
                raise ValueError('進捗記録が不正または同期途中です: ' + path.name) from error
        missing = {p for e in events.values() for p in e['parents']} - events.keys()
        if missing:
            raise ValueError('前の進捗記録が未同期です。OneDriveの同期完了を待ってください')
        return events

    def heads(self, pmid):
        events = self.events(pmid)
        parents = {p for e in events.values() for p in e['parents']}
        heads = [e for identifier, e in events.items() if identifier not in parents]
        if events and not heads:
            raise ValueError('進捗記録に循環があります')
        return sorted(heads, key=lambda e: (e['at'], e['id']))

    def current(self, pmid, expected=None):
        heads = self.heads(pmid)
        if len(heads) != 1:
            raise ValueError('端末間の更新が競合しています。採用する記録を確認してください')
        if expected is not None and expected != [heads[0]['id']]:
            raise ValueError('別の端末で更新されています。一覧を更新してください')
        return heads[0]

    def record(self, pmid, data, parents):
        # UUID files, rather than replacement of one shared database, preserve forks.
        event = dict(schema=SCHEMA, id=uuid.uuid4().hex, pmid=pmid_value(pmid),
                     at=now(), device=self.device_name, device_id=self.device_id,
                     parents=parents, data=copy.deepcopy(data))
        atomic_write(self.path(f"papers/PMID_{pmid}/records/{event['id']}.json"), encode(event))
        self.incoming(pmid)
        return event

    def add(self, pmid, title=''):
        pmid_value(pmid)
        if self.heads(pmid):
            return
        self.record(pmid, {'title': title, 'files': [], 'note': '', 'done': False}, [])

    def asset(self, pmid, kind, name, raw, topic='', basis=''):
        original_name = Path(name).name
        name = re.sub(r'[^\w.\-]', '_', original_name)[:100] or 'file'
        sha = digest(raw)
        relative = f'papers/PMID_{pmid}/files/{sha}/{name}'
        path = self.path(relative)
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError('保存済みの版が変更されています: ' + name)
        else:
            atomic_write(path, raw)
        return dict(kind=kind, name=original_name, path=relative, sha=sha, topic=topic, basis=basis)

    def read_asset(self, file):
        path = self.path(file['path'])
        if not path.is_file():
            raise ValueError('ファイルが未同期です: ' + file['name'])
        raw = path.read_bytes()
        if digest(raw) != file['sha']:
            raise ValueError('保存済みの版が変更されています: ' + file['name'])
        return raw

    def import_file(self, pmid, kind, name, raw, expected=None):
        from .graphics import validate_master, graphic_data
        if kind not in KINDS or Path(name).suffix.lower() != KINDS[kind]:
            raise ValueError('PDF・JSON・PNGを対応するフォルダに保存してください')
        event = self.current(pmid, expected)
        data = copy.deepcopy(event['data'])
        files = data['files']
        if any(f['sha'] == digest(raw) and f['kind'] == kind and f['name'] == name for f in files):
            return event
        if kind == 'pdf':
            from pypdf import PdfReader
            if not raw.startswith(b'%PDF-') or not PdfReader(io.BytesIO(raw)).pages:
                raise ValueError('本文PDFを確認してください')
            # New source invalidates all downstream selections, but old events retain them.
            files = [self.asset(pmid, kind, name, raw)]
        elif kind == 'json':
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError('JSONは8MB以下で保存してください')
            master = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(master, dict) or str(master.get('pmid', '')) != pmid:
                raise ValueError('JSON内のpmidが保存先と一致しません')
            validate_master(master)
            pdfs = [f for f in files if f['kind'] == 'pdf']
            if not pdfs:
                raise ValueError('先に本文PDFを登録してください')
            if master.get('source_hash') not in (None, '', pdfs[0]['sha']):
                raise ValueError('JSONのsource_hashが本文PDFと一致しません')
            files = pdfs + [self.asset(pmid, kind, name, raw, basis=pdfs[0]['sha'])]
            data['title'] = master.get('title') or data.get('title', '')
            topics = copy.deepcopy(master['topics'])
            if master['visualization_strategy']['include_overview']:
                topics.insert(0, dict(topic_id='overview', topic_title='Overview',
                    central_message=master['overview']['central_message'], clinical_question='論文全体の俯瞰',
                    source_sections=[s['section_id'] for s in master['document_structure']],
                    result_indices=list(range(len(master['main_results']))),
                    recommendation_indices=list(range(len(master['recommendations']))),
                    practice_change=master['practice_change'], practice_change_reason=master['practice_change_reason']))
            for topic in topics:
                content = graphic_data(master, topic, digest(raw))
                files.append(self.asset(pmid, 'graphic', topic['topic_id'] + '.json',
                                        encode(content).encode(), topic['topic_id'], digest(raw)))
            data['note'] = 'JSONの形式確認済み。医学的な内容は原文との照合が必要です。'
        else:
            topic = Path(name).stem
            graphics = [f for f in files if f['kind'] == 'graphic' and f['topic'] == topic]
            if len(graphics) != 1:
                raise ValueError('PNG名をテーマID.pngにしてください。一覧のテーマIDを確認できます')
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != 'PNG':
                    raise ValueError('PNG形式ではありません')
                image.verify()
            files = [f for f in files if not (f['kind'] == 'png' and f['topic'] == topic)]
            files.append(self.asset(pmid, kind, name, raw, topic, graphics[0]['sha']))
        data.update(files=files, done=False, done_note='')
        if kind in ('pdf', 'json'):
            self.prepare_prompts(pmid, data)
        return self.record(pmid, data, [event['id']])

    def prepare_prompts(self, pmid, data):
        from .graphics import web_prompt_text
        files = data['files']
        if not any(f['kind'] == 'pdf' for f in files):
            return
        files.append(self.asset(pmid, 'prompt', 'JSON作成依頼.txt', self.json_prompt(pmid, data).encode()))
        instruction = (Path(__file__).resolve().parents[2] / 'prompts/grarec-v2.md').read_text()
        for file in list(files):
            if file['kind'] == 'graphic':
                text = web_prompt_text({'pmid': pmid, 'title': data.get('title', '')},
                    {'id': file['basis']}, file['topic'], file['sha'], instruction, self.read_asset(file).decode())
                files.append(self.asset(pmid, 'prompt', file['topic'] + '_PNG作成依頼.txt',
                                        text.encode(), file['topic'], file['sha']))

    def scan(self):
        errors, changed = [], 0
        for folder in sorted((self.root / 'papers').glob('PMID_*')):
            pmid = folder.name[5:]
            try:
                pmid_value(pmid)
                if not self.heads(pmid):
                    self.add(pmid)
                self.current(pmid)
                # Old inbox versions stay in place. Never re-adopt them on the next scan.
                seen = {(f['kind'], f.get('topic', '') if f['kind'] == 'png' else '', f['sha']) for e in self.events(pmid).values()
                        for f in e['data']['files']}
                seen.update(tuple(item) for e in self.events(pmid).values() for item in e['data'].get('seen_inputs', []))
                for kind, suffix in KINDS.items():
                    paths = sorted((self.incoming(pmid) / kind.upper()).glob('*' + suffix))
                    fresh = []
                    for path in paths:
                        raw = path.read_bytes()
                        if (kind, path.stem if kind == 'png' else '', digest(raw)) not in seen:
                            fresh.append((path, raw))
                    if kind != 'png' and len(fresh) > 1:
                        raise ValueError(kind.upper() + 'の新しい候補が複数あります。採用する1ファイルだけを受け渡しフォルダに残してください')
                    for path, raw in fresh:
                        self.import_file(pmid, kind, path.name, raw)
                        changed += 1
            except Exception as error:
                # Keep incomplete downloads/invalid input in the inbox for a later retry.
                errors.append(f'PMID {pmid}: {str(error)[:250]}')
        return changed, errors

    def category(self, data):
        files = data['files']
        graphics = {f['topic']: f for f in files if f['kind'] == 'graphic'}
        pngs = {f['topic']: f for f in files if f['kind'] == 'png'}
        if not any(f['kind'] == 'pdf' for f in files):
            return 'missing_pdf'
        if not any(f['kind'] == 'json' for f in files):
            return 'json_pending'
        if not graphics:
            return 'attention'
        if any(t not in pngs or pngs[t].get('basis') != g['sha'] for t, g in graphics.items()):
            return 'web_pending'
        return 'completed' if data.get('done') else 'review_pending'

    def update(self, pmid, expected, note, done=False, chosen=None):
        heads = self.heads(pmid)
        if sorted(expected) != sorted(e['id'] for e in heads):
            raise ValueError('別の端末で更新されています。一覧を更新してください')
        event = next((e for e in heads if e['id'] == chosen), None) if chosen else self.current(pmid, expected)
        if event is None or not note.strip():
            raise ValueError('採用する記録と確認内容を指定してください')
        data = copy.deepcopy(event['data'])
        if done:
            if self.category(data) not in ('review_pending', 'completed'):
                raise ValueError('全テーマのPNGを保存してから完了を記録してください')
            for file in data['files']:
                self.read_asset(file)
        data.update(done=done, note=note.strip())
        return self.record(pmid, data, [e['id'] for e in heads])

    def snapshot(self):
        rows, errors = [], []
        for folder in sorted((self.root / 'papers').glob('PMID_*')):
            pmid = folder.name[5:]
            try:
                heads = self.heads(pmid_value(pmid))
                if not heads:
                    continue
                event = heads[-1]
                data = copy.deepcopy(event['data'])
                category = self.category(data) if len(heads) == 1 else 'attention'
                missing = [f['name'] for f in data['files'] if not self.path(f['path']).is_file()]
                if missing:
                    category = 'attention'
                rows.append(dict(pmid=pmid, **data, category=category, at=event['at'], device=event['device'],
                    heads=[e['id'] for e in heads], conflicts=heads if len(heads) > 1 else [],
                    missing=missing, folder=str(self.folder(pmid))))
            except (ValueError, OSError, KeyError, TypeError) as error:
                errors.append(f'PMID {pmid}: {error}')
                rows.append(dict(pmid=pmid, title='', files=[], note=str(error), category='attention',
                                 at='', device='', heads=[], conflicts=[], missing=[], folder=str(folder)))
        counts = {key: sum(p['category'] == key for p in rows) for key in STAGES}
        counts['total'] = len(rows)
        return dict(papers=rows, counts=counts, errors=errors, root=str(self.root), device=self.device_name)

    def export_view(self, snapshot):
        """Per-device views never overwrite another Mac's observations."""
        folder = self.path('状況/' + self.device_id)
        previous = folder / 'snapshot.json'
        content = encode(snapshot)
        if previous.is_file() and previous.read_text() == content:
            return
        atomic_write(previous, content)
        at = now()
        lines = [f'確認端末: {self.device_name}', f'この端末での記録日時: {at}',
                 'OneDriveへのアップロード完了を表す時刻ではありません。',
                 'Mac停止中は最後に同期された記録です。ファイル本体は papers/ を開いてください。', '']
        table = []
        for row in snapshot['papers']:
            lines.extend([f"PMID {row['pmid']}  {row['title']}", STAGES[row['category']],
                          f"更新: {row['at']} / {row['device']}", row['note']])
            links = []
            for file in row['files']:
                lines.append('  ' + file['kind'] + ': ' + file['path'])
                links.append('<a href="../../' + quote(file['path']) + '">' + html.escape(file['name']) + '</a>')
            lines.append('')
            table.append('<tr><td>' + html.escape(row['pmid']) + '</td><td>' + html.escape(row['title']) +
                         '</td><td>' + STAGES[row['category']] + '</td><td>' + html.escape(row['at'] + ' / ' + row['device']) +
                         '</td><td>' + '<br>'.join(links) + '</td></tr>')
        lines.extend(snapshot['errors'])
        atomic_write(folder / '状況.txt', '\n'.join(lines))
        atomic_write(folder / '状況.html', '<!doctype html><html lang="ja"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>PubMed 進捗</title>'
            '<style>body{font:15px system-ui;padding:20px;background:#f3f5f8}td,th{padding:12px;border-bottom:1px solid #ccc;text-align:left}table{background:white;width:100%}a{overflow-wrap:anywhere}</style>'
            '<h1>PubMed 進捗</h1><p>' + html.escape(self.device_name + ' / ' + at) +
            '</p><p>最後に記録された状況。OneDriveの同期完了を確認して端末を切り替えてください。</p>'
            '<div style="overflow:auto"><table><tr><th>PMID</th><th>論文</th><th>工程</th><th>更新</th><th>ファイル</th></tr>' + ''.join(table) + '</table></div></html>')

    def json_prompt(self, pmid, data=None):
        from .batches import review_prompt
        from .graphics import master_schema
        if data is None:
            data = self.current(pmid)['data']
        pdf = next((f for f in data['files'] if f['kind'] == 'pdf'), None)
        if not pdf:
            raise ValueError('先に本文PDFを登録してください')
        self.read_asset(pdf)
        schema = master_schema()
        schema['properties']['source_hash'] = {'type': 'string', 'const': pdf['sha']}
        schema['required'].append('source_hash')
        paper = dict(pmid=pmid, title=data.get('title', ''), doi='', metadata='{}')
        return (review_prompt(paper, 'fulltext-v4') + '\n書誌情報が空欄なら添付PDFから確認してください。'
                '\n以下のSchemaに従い、JSONをmaster.jsonとして作成してください。PDFが別論文なら停止してください。\n' +
                json.dumps(schema, ensure_ascii=False, indent=2))

    def image_prompt(self, pmid, topic, instruction):
        from .graphics import web_prompt_text
        event = self.current(pmid)
        graphic = next((f for f in event['data']['files'] if f['kind'] == 'graphic' and f['topic'] == topic), None)
        if not graphic:
            raise ValueError('画像用JSONがありません')
        return web_prompt_text({'pmid': pmid, 'title': event['data']['title']}, {'id': event['id']},
                               topic, graphic['sha'], instruction, self.read_asset(graphic).decode())
