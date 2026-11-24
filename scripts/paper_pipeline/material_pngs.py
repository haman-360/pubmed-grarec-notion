"""Review one teaching PNG and append it to the already verified HTML page."""
import io
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image

from . import materials, notion_sync
from .graphics import graphic_data, image_topics, persist_file
from .image_review import fixed_facts, human_review
from .intake import workspace_lock
from .materials_notion import request_once, verify_blocks
from .notion import rich
from .store import atomic_write, digest, encode


def inbox(workspace, version, pmid=materials.PMID):
    if materials.current(workspace, pmid).get('version') != version or not version:
        raise ValueError('教材版が変わりました。画面を更新してください')
    return workspace.path(f'{materials.destination(pmid)}/{version}/完成PNG')


def filename(topic, pmid=materials.PMID):
    return f'PMID_{pmid}_{topic}.png'


def pending_approvals(repo, workspace, kit):
    # A crash can occur after the local commit and before reporting it to OneDrive.
    if not kit.get('page_id') or notion_sync.read(workspace, 'owner', {}).get('device_id') != workspace.device_id:
        return {}
    database = notion_sync.local_root(repo, workspace)/'ledger.sqlite3'
    if not database.is_file():
        return {}
    prefix = 'materials:'+kit['version']+':'+kit['page_id']+':png:'
    approvals = {}
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        for key, raw in db.execute('SELECT key,value FROM settings WHERE key LIKE ?', (prefix+'%',)):
            state = json.loads(raw)
            if isinstance(state, dict) and 'approval' in state:
                approvals[key[len(prefix):]] = dict(sha=state['sha'], approved_at=state['approval']['at'])
    return approvals


def snapshot(repo, workspace, pmid=materials.PMID):
    kit = materials.current(workspace, pmid)
    if not kit:
        return kit
    folder = inbox(workspace, kit['version'], pmid)
    master = json.loads((folder.parent/'png-master.json').read_bytes())
    pending = pending_approvals(repo, workspace, kit)
    rows = []
    for topic in image_topics(master):
        key = topic['topic_id']
        receipt = kit.get('pngs', {}).get(key) or pending.get(key, {})
        name = filename(key, pmid)
        rows.append(dict(topic=key, title=topic['topic_title'], name=name,
                         arrived=(folder/name).is_file(), **receipt))
    return dict(kit, images=rows, pmid=pmid)


def review_data(workspace, version, topic, sha=None, pmid=materials.PMID):
    folder = materials.checked_folder(workspace, version, pmid)
    master = json.loads((folder/'png-master.json').read_bytes())
    item = next((t for t in image_topics(master) if t['topic_id'] == topic), None)
    if not item:
        raise ValueError('教材のPNG依頼にないテーマです')
    path = workspace.path(f'{materials.destination(pmid)}/{version}/完成PNG/{filename(topic, pmid)}')
    if not path.is_file() or path.stat().st_size > 20*1024*1024:
        raise ValueError('完成PNGの保存名・同期状態・ファイルサイズを確認してください（上限20 MB）')
    raw = path.read_bytes()
    if sha is not None and digest(raw) != sha:
        raise ValueError('確認中にPNGが変わりました。画像を開き直してください')
    with Image.open(io.BytesIO(raw)) as image:
        if image.format != 'PNG':
            raise ValueError('PNG形式の画像を保存してください')
        image.verify()
    return fixed_facts(graphic_data(master, item, version)), raw


def publish(workspace, version, page, topic, state, pmid):
    with workspace_lock(workspace):
        kit = materials.current(workspace, pmid)
        if kit.get('version') != version or kit.get('page_id') != page:
            raise ValueError('教材版または登録先が変わりました')
        kit.setdefault('pngs', {})[topic] = dict(sha=state['sha'], approved_at=state['approval']['at'],
                                                 block_id=state.get('block_id'))
        atomic_write(workspace.path(materials.destination(pmid)+'/current.json'), encode(kit))


def matching_image(children, caption):
    found = [b for b in children if b['type'] == 'image' and ''.join(
        t.get('plain_text', t.get('text', {}).get('content', '')) for t in b['image'].get('caption', [])) == caption]
    if len(found) > 1:
        raise ValueError('同じ教材PNGの添付が複数あります。Notionページを確認してください')
    return found[0] if found else None


def approve(store, workspace, base, key, data):
    version, topic, sha = data['version'], data['topic'], data['sha']
    facts, raw = review_data(workspace, version, topic, sha, data.get('pmid', materials.PMID))
    approval = human_review(facts, data.get('observed_pmid'), data.get('value'), data.get('checks'))
    if approval['status'] != 'approved':
        raise ValueError(approval['reason'])
    approved = store.rows('SELECT value FROM settings WHERE key LIKE ?', (base+':png:%',))
    if any(isinstance(entry, dict) and entry.get('sha') == sha
           for entry in (json.loads(row['value']) for row in approved)):
        raise ValueError('同じPNGがすでに承認されています。テーマを確認してください')
    persist_file(store.root/'materials'/version/(sha+'.png'), raw)
    state = dict(sha=sha, title=facts['topic_title'], approval=dict(approval, device=workspace.device_name))
    store.set_setting(key, state)
    return state


def register(repo, workspace, data, api=None):
    version, topic, sha = data['version'], data['topic'], data['sha']
    pmid = data.get('pmid', materials.PMID)
    if not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{64}', sha):
        raise ValueError('確認した画像を開き直してください')
    materials.checked_folder(workspace, version, pmid)
    kit = materials.current(workspace, pmid)
    page = kit.get('page_id')
    if not page:
        raise ValueError('先に教材HTMLと原本PDFをNotionへ登録してください')
    base = 'materials:'+version+':'+page
    with notion_sync.ledger(repo, workspace) as store:
        html = store.setting(base, {})
        paper = store.resolve(pmid)
        if paper['page_id'] != page or not all(html.get(k+'_block') for k in ('html','pdf')):
            raise ValueError('この教材版のHTMLと原本PDFの登録記録を確認してください')
        key = base+':png:'+topic
        state = store.setting(key, {})
        if state and state['sha'] != sha:
            raise ValueError('このテーマには別のPNGが承認済みです')
        if not state:
            state = approve(store, workspace, base, key, data)
        publish(workspace, version, page, topic, state, pmid)
        client = notion_sync.client_for(repo, store, api)
        existing = client.find_page(paper)
        if not existing or existing['id'] != page:
            raise ValueError('登録済みの教材ページを特定できません')
        children = client.children(page)
        by_id = {b['id']: b for b in children}
        verify_blocks([by_id[k] for k in (html['html_block'], html['pdf_block']) if k in by_id], html)
        caption = f"{state['title']} · PMID {pmid} / {topic} / {version[:16]}"
        block = matching_image(children, caption)
        if not block:
            if state.get('block_id') or store.setting(key+':append'):
                raise ValueError('前回の添付をまだ照合できません。Notionの反映後に再開してください')
            saved = store.root/'materials'/version/(sha+'.png')
            if digest(saved.read_bytes()) != sha:
                raise ValueError('承認時のPNGと保存ファイルが一致しません')
            upload = client.upload_file(version, state, 'image', saved, lambda: store.set_setting(key, state))
            body = dict(type='file_upload', file_upload={'id':upload['id']}, caption=rich(caption))
            request_once(client, store, key+':append', 'PATCH', '/blocks/'+page+'/children',
                         {'children':[dict(type='image', image=body)]})
            block = matching_image(client.children(page), caption)
        upload = state.get('image_upload', {})
        locator = unquote(urlsplit((block or {}).get('image', {}).get('file', {}).get('url', '')).path)
        if (not block or not locator or Path(locator).name != upload.get('name') or
                state.get('block_id', block['id']) != block['id'] or state.get('path', locator) != locator):
            raise ValueError('Notion上のPNGを承認済みの添付と照合できません')
        state.update(block_id=block['id'], path=locator)
        store.set_setting(key, state)
        publish(workspace, version, page, topic, state, pmid)
        return state['title']+'のPNGを教材ページで確認しました。'
