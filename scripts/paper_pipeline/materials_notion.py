"""Attach teaching HTML before PNG review, without updating gallery completion."""
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import materials, notion_sync
from .intake import workspace_lock
from .notion import NotionHTTPError, plain, rich
from .store import atomic_write, doi_key, encode


def request_once(client, store, key, method, route, payload):
    if store.setting(key):
        raise ValueError('前回のNotion書込結果をまだ照合できません。時間をおいて登録を再開してください')
    store.set_setting(key, True)
    try:
        return client.api.call(method, route, json=payload)
    except NotionHTTPError as exc:
        if exc.status in (400, 401, 403, 404, 413, 429):
            store.set_setting(key, False)
        raise


def page_for(client, store, master, local):
    pmid = master['pmid']
    rows = store.rows('SELECT * FROM papers WHERE pmid=?', (pmid,))
    paper = rows[0] if rows else dict(id=local.get('legacy_id') or 'onedrive-pmid-'+pmid, pmid=pmid,
                                     doi=doi_key(master['doi']), page_id=local.get('notion_page'))
    if rows and doi_key(master['doi']) != (paper['doi'] or ''):
        raise ValueError('教材のDOIが以前のNotion登録記録と異なります')
    page = client.find_page(paper)
    identifier = notion_sync.prop_text(page, 'Automation ID') if page else ''
    if rows and identifier and identifier != paper['id']:
        raise ValueError('既存ページのAutomation IDが台帳と一致しません')
    if not rows:
        new = store.paper(pmid, master['doi'], master['title'])
        store.update('papers', new['id'], id=identifier or paper['id'])
        paper = store.resolve(pmid)
    if not page:
        from upload_to_notion import build_chatgpt_summary_payload
        payload = build_chatgpt_summary_payload(dict(master, human_checked=False, graphic_url=''),
                                                 client.database_id, {'properties':client.properties})
        payload.update(parent={'data_source_id':client.data_source_id}, children=[])
        payload.pop('cover', None)
        payload['properties']['Automation ID'] = {'rich_text':rich(paper['id'])}
        page = request_once(client, store, 'material-page:'+pmid, 'POST', '/pages', payload)
    store.update('papers', paper['id'], page_id=page['id'])
    return page['id']


def verify_blocks(blocks, state):
    if len(blocks) != 2 or [b['type'] for b in blocks] != ['embed','pdf']:
        raise ValueError('教材HTML・原本PDFの登録状態を確認できません')
    for block, kind in zip(blocks, ('html','pdf')):
        body = block[block['type']]
        url = body.get('url') if kind == 'html' else body.get('file', {}).get('url')
        stored_id = state.get(kind+'_block')
        if not url or (stored_id and stored_id != block['id']):
            raise ValueError('教材の添付が変更されています')
        path = unquote(urlsplit(url).path)
        if state.get(kind+'_path', path) != path:
            raise ValueError('教材ブロックの添付ファイルが変更されています')
        if not stored_id and Path(path).name != state[kind+'_upload']['name']:
            raise ValueError('登録結果不明の添付を元ファイルと照合できません')
        state[kind+'_path'] = path


def register(repo, workspace, version, api=None, pmid=materials.PMID):
    folder = materials.checked_folder(workspace, version, pmid)
    master = json.loads((folder/'png-master.json').read_bytes())
    if master['pmid'] != pmid:
        raise ValueError('教材のPMIDが一致しません')
    heads = workspace.heads(pmid)
    if len(heads) > 1:
        raise ValueError('PMIDの工程記録に競合があります')
    local = heads[0]['data'] if heads else {}
    with notion_sync.ledger(repo, workspace, claim=True) as store:
        client = notion_sync.client_for(repo, store, api)
        page = page_for(client, store, master, local)
        key = 'materials:'+version+':'+page
        state = store.setting(key, {})
        def persist():
            store.set_setting(key, state)
        marker = 'インタラクティブ教材（自動生成） · '+version[:16]
        children = client.children(page)
        positions = [i for i,b in enumerate(children) if b['type']=='heading_2' and plain(b)==marker]
        if not positions:
            for kind, name in [('html',materials.HTML), ('pdf',f'PMID_{pmid}.pdf')]:
                client.upload_file(version, state, kind, folder/name, persist)
            items = [{'type':'heading_2','heading_2':{'rich_text':rich(marker)}}]
            for kind, block_type in [('html','embed'), ('pdf','pdf')]:
                items.append({'type':block_type,block_type:{'type':'file_upload',
                              'file_upload':{'id':state[kind+'_upload']['id']}}})
            response = request_once(client, store, key+':append', 'PATCH', '/blocks/'+page+'/children', {'children':items})
            for block, kind in zip(response['results'][1:], ('html','pdf')):
                state[kind+'_block'] = block['id']
            persist()
            children = client.children(page)
            positions = [i for i,b in enumerate(children) if b['type']=='heading_2' and plain(b)==marker]
        if len(positions) != 1 or not all(kind+'_upload' in state for kind in ('html','pdf')):
            raise ValueError('登録した教材版を一意に特定できません')
        offset = positions[0]+1
        blocks = children[offset:offset+2]
        verify_blocks(blocks, state)
        for block, kind in zip(blocks, ('html','pdf')):
            state[kind+'_block'] = block['id']
        persist()
        with workspace_lock(workspace):
            value = materials.current(workspace, pmid)
            if value.get('version') == version:
                atomic_write(workspace.path(materials.destination(pmid)+'/current.json'), encode(dict(value, page_id=page)))
    return 'HTMLと原本PDFのNotion登録を確認しました。'
