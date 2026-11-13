"""Selected, human-reviewed shared PNGs -> resumable Notion registration."""
import copy
import io
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

import requests
from PIL import Image

from .graphics import active_graphics, graphic_data, persist_file, review_image, validate_master
from .intake import PENDING, workspace_lock
from .notion import Notion, NotionAPI, content_blocks, plain
from .pdf_download import validate_selection
from .store import Store, atomic_write, digest, doi_key, encode, now, uid

SHARED = 'NOTION'


def credentials(repo):
    keys = ('NOTION_TOKEN', 'NOTION_DATABASE_ID', 'NOTION_DATA_SOURCE_ID')
    values = {k: os.environ.get(k, '').strip() for k in keys}
    path = Path(repo) / '.env'
    if path.is_file():
        for line in path.read_text().splitlines():
            match = re.fullmatch(r'\s*(?:export\s+)?(NOTION_TOKEN|NOTION_DATABASE_ID|NOTION_DATA_SOURCE_ID)\s*=\s*(.*?)\s*', line)
            if match and not values[match[1]]:
                values[match[1]] = match[2].strip('"\'')
    return values


def local_root(repo, workspace):
    return Path(repo) / 'output/notion_sync' / digest(str(workspace.root).encode())[:24]


def read(workspace, name, default=None):
    path = workspace.path(SHARED + '/' + name + '.json')
    return json.loads(path.read_text()) if path.is_file() else default


def status(repo, workspace):
    owner = read(workspace, 'owner', {})
    cfg = credentials(repo)
    return dict(read(workspace, 'status', {}), owner=owner.get('device_name', ''),
                can_operate=not owner or owner.get('device_id') == workspace.device_id,
                key_ready=bool(cfg['NOTION_TOKEN']), database_id=cfg['NOTION_DATABASE_ID'])


@contextmanager
def ledger(repo, workspace, claim=False):
    root = local_root(repo, workspace)
    owner = read(workspace, 'owner', {})
    if owner and (owner.get('device_id') != workspace.device_id or not (root / 'ledger.sqlite3').is_file()):
        raise ValueError('Notion担当Mac（' + owner.get('device_name', '') + '）の元の台帳を使ってください。重複防止のため新規台帳では再開しません')
    if not owner and not claim:
        raise ValueError('先に対象を選び、登録内容を確認してください')
    store = Store(root)
    try:
        with store.lock():
            identity = store.setting('identity')
            if not identity:
                identity = uid(); store.set_setting('identity', identity)
            if not owner:
                owner = dict(device_id=workspace.device_id, device_name=workspace.device_name, ledger_id=identity)
                with workspace_lock(workspace):
                    path = workspace.path(SHARED + '/owner.json'); path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open('x') as out:
                        out.write(encode(owner))
            if owner.get('ledger_id') != identity or len(list(workspace.path(SHARED).glob('owner*.json'))) != 1:
                raise ValueError('Notion担当設定・台帳が競合しています。送信せず元の記録を確認してください')
            try:
                yield store
            finally:
                publish(workspace, store)
    finally:
        store.close()


def publish(workspace, store):
    jobs = []
    for rev in store.rows('SELECT * FROM revisions ORDER BY created_at DESC'):
        binding = store.setting('binding:' + rev['id'], {})
        paper = store.one('papers', rev['paper_id'])
        outcome = store.setting('outcome:' + rev['id'], {})
        jobs.append(dict(id=rev['id'], pmid=paper['pmid'], at=rev['created_at'],
                         fingerprint=binding.get('fingerprint'), **outcome,
                         page_id=paper.get('page_id'), topics=[dict(topic=r['topic_id'], status=r['status'])
                         for r in active_graphics(store, rev['id'])]))
    atomic_write(workspace.path(SHARED + '/status.json'), encode(dict(at=now(), jobs=jobs,
                 preview=store.setting('preview'), target=store.setting('target'))))


class SafeNotionAPI(NotionAPI):
    def call(self, method, path, **kwargs):
        # Notion's average request rate is shared by reads and writes. Keep this
        # sequential desktop operation below three calls per second.
        delay = .35 - (time.monotonic() - getattr(self, '_last_call', 0))
        if delay > 0:
            time.sleep(delay)
        self._last_call = time.monotonic()
        try:
            return super().call(method, path, **kwargs)
        except requests.RequestException as error:
            raise RuntimeError('Notionとの通信に失敗しました（' + type(error).__name__ + '）。同じ対象で登録内容を再確認してください') from None


def prop_text(page, name):
    prop = page.get('properties', {}).get(name, {})
    return ''.join(x.get('plain_text', x.get('text', {}).get('content', '')) for x in prop.get('rich_text', []))


class SharedNotion(Notion):
    def setup(self):
        super().setup()
        self._archive_checked = set()
        if self.properties.get('PMID', {}).get('type') != 'rich_text':
            raise ValueError('重複照合のためPMID（rich_text）が必要です。DB構造は自動変更しません')

    def find_page(self, paper):
        if paper['id'] not in self._archive_checked:
            for name, value in (('PMID',paper['pmid']), ('Automation ID',paper['id'])):
                if self.all_results('/data_sources/'+self.data_source_id+'/query', 'POST',
                    {'is_archived':True, 'filter':{'property':name,'rich_text':{'equals':value}}}):
                    raise ValueError('同じ論文のNotionページがアーカイブされています。新規作成せず既存ページを確認してください')
            doi_kind = self.properties.get('DOI',{}).get('type')
            if paper.get('doi') and doi_kind in ('rich_text','url'):
                for page in self.all_results('/data_sources/'+self.data_source_id+'/query','POST',
                    {'is_archived':True,'filter':{'property':'DOI',doi_kind:{'is_not_empty':True}}}):
                    value = page.get('properties',{}).get('DOI',{}).get('url') or prop_text(page,'DOI')
                    if doi_key(value) == paper['doi']:
                        raise ValueError('同じDOIのNotionページがアーカイブされています。新規作成しません')
            self._archive_checked.add(paper['id'])
        # A pinned page must not be silently replaced after archive/move/trash.
        if paper.get('page_id'):
            page = self.api.call('GET', '/pages/' + paper['page_id'])
            self.check_page(page, paper)
        page = super().find_page(paper)
        if page:
            self.check_page(page, paper)
        return page

    def check_page(self, page, paper):
        if page.get('archived') or page.get('is_archived') or page.get('in_trash'):
            raise ValueError('既存Notionページがアーカイブ・ゴミ箱にあります。再作成しません')
        if page.get('parent', {}).get('data_source_id') != self.data_source_id:
            raise ValueError('既存Notionページの登録先が一致しません')
        pmid = prop_text(page, 'PMID').strip()
        doi_prop = page.get('properties', {}).get('DOI', {})
        doi = doi_key(doi_prop.get('url') or prop_text(page, 'DOI'))
        if (pmid and pmid != paper['pmid']) or (doi and paper['doi'] and doi != paper['doi']):
            raise ValueError('既存NotionページのPMID・DOIが矛盾しています。新規作成・上書きはしません')


def client_for(repo, store, api=None):
    cfg = credentials(repo)
    client = SharedNotion(store, api or SafeNotionAPI(cfg['NOTION_TOKEN']),
                          cfg['NOTION_DATABASE_ID'], cfg['NOTION_DATA_SOURCE_ID'] or None)
    client.setup()
    target = dict(database_id=client.database_id, data_source_id=client.data_source_id)
    old = store.setting('target')
    if old and old != target:
        raise ValueError('Notion登録先が前回と異なります。元の設定で再開してください')
    store.set_setting('target', target)
    return client


def pending_input(workspace, pmid):
    seen = {f['sha'] for e in workspace.events(pmid).values() for f in e['data']['files']}
    for folder in (workspace.path(PENDING), workspace.incoming(pmid)):
        for path in folder.rglob('*'):
            if path.is_file() and path.suffix.lower() in ('.pdf', '.json', '.png') and digest(path.read_bytes()) not in seen:
                raise ValueError('未整理のPDF・JSON・PNGがあります。先に取り込み・整理をしてください')


def bundle(workspace, pmid, heads):
    event = workspace.current(pmid, heads)
    if workspace.category(event['data']) != 'review_pending':
        raise ValueError('全テーマのPNGが揃った、未登録の論文を選んでください')
    pending_input(workspace, pmid)
    files = [f for f in event['data']['files'] if f['kind'] in ('pdf', 'json', 'graphic', 'png')]
    raw = {f['path']: workspace.read_asset(f) for f in files}
    pdfs = [f for f in files if f['kind'] == 'pdf']; masters = [f for f in files if f['kind'] == 'json']
    if len(pdfs) != 1 or len(masters) != 1:
        raise ValueError('原本PDFとMaster JSONを1組にしてください')
    master = json.loads(raw[masters[0]['path']]); validate_master(master)
    if master['pmid'] != pmid or master.get('source_hash') not in (None, '', pdfs[0]['sha']):
        raise ValueError('Master JSONとPMID・原本PDFが一致しません')
    topics = copy.deepcopy(master['topics'])
    if master['visualization_strategy']['include_overview']:
        topics.insert(0, dict(topic_id='overview', topic_title='Overview', central_message=master['overview']['central_message'],
            clinical_question='論文全体の俯瞰', source_sections=[s['section_id'] for s in master['document_structure']],
            result_indices=list(range(len(master['main_results']))), recommendation_indices=list(range(len(master['recommendations']))),
            practice_change=master['practice_change'], practice_change_reason=master['practice_change_reason']))
    pairs = []
    for topic in topics:
        graphics = [f for f in files if f['kind'] == 'graphic' and f['topic'] == topic['topic_id']]
        images = [f for f in files if f['kind'] == 'png' and f['topic'] == topic['topic_id']]
        if len(graphics) != 1 or len(images) != 1:
            raise ValueError('テーマのJSON・PNGを1組ずつ用意してください')
        graphic, image = graphics[0], images[0]
        data = json.loads(raw[graphic['path']])
        if graphic['basis'] != masters[0]['sha'] or image['basis'] != graphic['sha'] or data != graphic_data(master, topic, data.get('revision_id')):
            raise ValueError('Master・テーマJSON・PNGの対応が変わっています。確認し直してください')
        with Image.open(io.BytesIO(raw[image['path']])) as im:
            if im.format != 'PNG': raise ValueError('PNG形式を確認してください')
            im.verify()
        pairs.append(dict(topic=topic['topic_id'], title=topic['topic_title'], graphic=graphic, image=image))
    if len(files) != 2 + 2*len(pairs):
        raise ValueError('テーマに対応しない余分な成果物があります')
    fingerprint = digest(sorted((f['kind'], f.get('topic',''), f['sha'], f.get('basis','')) for f in files))
    return dict(event=event, files=files, raw=raw, pdf=pdfs[0], master_file=masters[0], master=master, pairs=pairs, fingerprint=fingerprint)


def prepare(repo, workspace, items, api=None):
    selected = validate_selection(workspace, items)
    if len(selected) > 5:
        raise ValueError('Notion登録は1回最大5本です')
    # Validate all local content before the first remote lookup.
    with workspace_lock(workspace):
        bundles = [(item, bundle(workspace, item['pmid'], item['heads'])) for item in selected]
    with ledger(repo, workspace, claim=True) as store:
        store.set_setting('preview', None)
        client = client_for(repo, store, api)
        prepared = []
        for item, content in bundles:
            pmid = item['pmid']; master = content['master']; data = content['event']['data']
            for file in content['files']:
                if file['kind'] != 'pdf' and len(content['raw'][file['path']]) > min(client.file_limit, 20*1024*1024):
                    raise ValueError('Notion添付上限を超えるJSON/PNGがあります。自動縮小・公開URL化はしません')
            existing = store.rows('SELECT * FROM papers WHERE pmid=?', (pmid,))
            candidate = existing[0] if existing else dict(id=data.get('legacy_id') or 'onedrive-pmid-'+pmid,
                         pmid=pmid, doi=doi_key(master.get('doi')), title=master['title'], page_id=data.get('notion_page'))
            page = client.find_page(candidate)
            automation_id = prop_text(page, 'Automation ID') if page else ''
            if existing and automation_id and automation_id != candidate['id']:
                raise ValueError('NotionのAutomation IDが登録時と異なります。送信せず確認してください')
            if not existing:
                paper = store.paper(pmid=pmid, doi=master.get('doi',''), title=master['title'])
                # Reuse the verified existing page identity, including pre-OneDrive jobs.
                store.update('papers', paper['id'], id=automation_id or candidate['id'], page_id=page['id'] if page else None)
                paper = store.resolve(pmid)
            else:
                paper = candidate
                if doi_key(master.get('doi')) != (paper['doi'] or ''):
                    raise ValueError('以前の登録とDOIが異なります。論文の対応を確認してください')
            source = store.source(workspace.path(content['pdf']['path']), paper['id'])
            # The shared workflow already selected and validated this source.
            store.update('papers', paper['id'], blocker='')
            cfg = dict(mode='manual_web', bundle_hash=content['fingerprint'], target=store.setting('target'))
            rev = store.revision(paper, source, cfg)
            for old in store.rows('SELECT g.notion FROM graphics g JOIN revisions r ON r.id=g.revision_id WHERE r.paper_id=? AND r.id!=?', (paper['id'],rev['id'])):
                state = json.loads(old['notion'])
                if state.get('page_intent') and not state.get('page_id'):
                    raise ValueError('旧版のページ作成結果が不明です。新しい版を送る前に元の登録結果を確認してください')
            folder = Path('artifacts') / rev['id']
            persist_file(store.root / folder / 'master.json', content['raw'][content['master_file']['path']])
            store.update('revisions', rev['id'], summary=str(folder/'master.json'))
            images = []
            for position, pair in enumerate(content['pairs']):
                topic = pair['topic']; graphic = pair['graphic']; image = pair['image']
                jp, ip = folder/'graphics'/(topic+'.json'), folder/'images'/(topic+'.png')
                persist_file(store.root/jp, content['raw'][graphic['path']]); persist_file(store.root/ip, content['raw'][image['path']])
                with store.db:
                    store.db.execute('''INSERT OR IGNORE INTO graphics
                        (id,revision_id,topic_id,position,json_path,json_hash,master_hash,image_path,image_hash,status,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (digest([rev['id'],topic]),rev['id'],topic,position,str(jp),graphic['sha'],
                        content['master_file']['sha'],str(ip),image['sha'],'IMAGE_REVIEW_REQUIRED',now()))
                images.append(dict(topic=topic,title=pair['title'],file=image,review_id=pmid+':'+topic+':'+image['sha']))
            binding = dict(pmid=pmid, heads=item['heads'], fingerprint=content['fingerprint'])
            store.set_setting('binding:'+rev['id'], binding)
            if not store.setting('outcome:'+rev['id']):
                store.set_setting('outcome:'+rev['id'], dict(status='prepared',message='全PNGを確認して登録してください'))
            prepared.append(dict(id=rev['id'], **binding, title=master['title'], images=images,
                                 page_id=page['id'] if page else None))
        store.set_setting('preview', dict(id=uid(), target=store.setting('target'), target_name=client.database_title, items=prepared))
    return '登録先と既存ページを照合しました。PNGを確認し、登録ボタンを押してください（まだアップロードしていません）。'


def verify_remote(client, store, revision):
    paper = store.one('papers', revision['paper_id'])
    page = client.find_page(paper)
    if not page or page['id'] != paper['page_id']:
        raise ValueError('登録したNotionページを再確認できません')
    sections = {b['id'] for b in client.children(page['id'])}
    for row in active_graphics(store, revision['id']):
        state = json.loads(row['notion'])
        if row['status'] != 'COMPLETED' or not state.get('verified') or state.get('section_id') not in sections:
            raise ValueError('全テーマの登録完了を確認できません')
        children = client.children(state['section_id'])
        expected = content_blocks(json.loads((store.root/row['json_path']).read_text()))
        if len(children) < len(expected) or any(a['type']!=b['type'] or plain(a)!=plain(b) for a,b in zip(children,expected)):
            raise ValueError('登録したNotion本文が変更されています。現在の内容を確認してください')
        marker_id = revision['id']+':'+row['topic_id']+':'+row['json_hash'][:12]+':'+row['image_hash'][:12]
        for kind in ('json','master','image'):
            block_type = 'image' if kind=='image' else 'file'
            if not any(b['id'] == state.get(kind+'_block') and b['type'] == block_type and
                ''.join(t.get('plain_text', t.get('text',{}).get('content','')) for t in b[block_type].get('caption',[])) == kind+':'+marker_id for b in children):
                raise ValueError('登録したNotion添付を再確認できません')
    return page['id']


def submit(repo, workspace, data, api=None):
    reviewer, note = str(data.get('reviewer','')).strip(), str(data.get('note','')).strip()
    if data.get('confirmed') is not True or not reviewer or not note:
        raise ValueError('PNGの確認者・確認内容とNotionへの登録確認が必要です')
    with ledger(repo, workspace) as store:
        preview = store.setting('preview')
        if not preview or preview['id'] != data.get('preview_id'):
            raise ValueError('対象を選び直して、登録内容を確認してください')
        expected = {image['review_id'] for item in preview['items'] for image in item['images']}
        reviewed = data.get('reviewed')
        if not isinstance(reviewed,list) or not all(isinstance(x,str) for x in reviewed) or set(reviewed) != expected:
            raise ValueError('選択した論文の全PNGを確認してください')
        with workspace_lock(workspace):
            for item in preview['items']:
                if bundle(workspace,item['pmid'],item['heads'])['fingerprint'] != item['fingerprint']:
                    raise ValueError('確認後に成果物が変わりました。確認し直してください')
        client = client_for(repo, store, api)
        completed = 0
        try:
            for item in preview['items']:
                rev = store.one('revisions',item['id']); outcome_key = 'outcome:'+rev['id']
                try:
                    with workspace_lock(workspace):
                        bundle(workspace,item['pmid'],item['heads'])
                    for image in item['images']:
                        review_image(store, rev, image['topic'], True, reviewer, note)
                    store.set_setting(outcome_key,dict(status='sending',message='登録・照合中'))
                    client.sync(store.one('revisions',rev['id']))
                    page_id = verify_remote(client, store, rev)
                    with workspace_lock(workspace):
                        try:
                            content = bundle(workspace,item['pmid'],item['heads'])
                        except ValueError:
                            store.set_setting(outcome_key,dict(status='registered_stale',message='Notion登録済みですが共有記録が変わったため工程を更新していません。現在のファイルと登録先を確認してください'))
                            continue
                        current = content['event']; record = copy.deepcopy(current['data'])
                        record.update(done=True,notion_page=page_id,
                            note='PNG確認・Notion登録済み：'+reviewer+' ／ '+note+' ／ https://www.notion.so/'+page_id.replace('-',''),
                            notion_registration=dict(revision_id=rev['id'],fingerprint=item['fingerprint'],page_id=page_id,
                                                     reviewer=reviewer,note=note,at=now()))
                        workspace.record(item['pmid'],record,[current['id']])
                    store.set_setting(outcome_key,dict(status='completed',message='全テーマの本文・JSON・PNGを確認し、登録完了を記録しました'))
                    completed += 1
                except Exception as error:
                    message = str(error)[:500] if isinstance(error,(ValueError,RuntimeError)) else '通信・保存の完了を確認できません。同じ対象を再選択して照合・再開してください（'+type(error).__name__+'）'
                    store.set_setting(outcome_key,dict(status='needs_review',message=message))
                publish(workspace, store)
        finally:
            store.set_setting('preview',None)
    return f'Notion登録：{completed}/{len(preview["items"])}本を完了しました。未完了分は結果を確認し、同じ対象から照合・再開できます。'
