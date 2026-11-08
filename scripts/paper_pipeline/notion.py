from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path

import requests

from .store import digest, doi_key, encode


def rich(text):
    return [{'type':'text','text':{'content':str(text)}}]


def plain(block):
    return ''.join(x.get('plain_text',x.get('text',{}).get('content','')) for x in block.get(block.get('type'),{}).get('rich_text',[]))


class NotionAPI:
    def __init__(self, token, session=None):
        if not token:
            raise ValueError('NOTION_TOKENが未設定です')
        self.session = session or requests.Session()
        self.session.headers.update({'Authorization':'Bearer '+token,'Notion-Version':'2026-03-11'})

    def call(self, method, path, **kwargs):
        for retry in range(3 if method=='GET' else 1):
            try:
                response = self.session.request(method,'https://api.notion.com/v1'+path,timeout=120,**kwargs)
                if response.status_code >= 400:
                    raise RuntimeError(f'Notion HTTP {response.status_code}')
                return response.json()
            except (requests.RequestException,RuntimeError):
                if retry == (2 if method=='GET' else 0):
                    raise
                time.sleep(2**retry)


class Notion:
    def __init__(self, store, api, database_id, data_source_id=None):
        self.store, self.api = store, api
        self.database_id, self.data_source_id = database_id, data_source_id
        self.properties = None
        self.file_limit = None

    def setup(self):
        if not self.database_id:
            raise ValueError('NOTION_DATABASE_IDが未設定です')
        database = self.api.call('GET','/databases/'+self.database_id)
        sources = database.get('data_sources',[])
        if not self.data_source_id:
            if len(sources) != 1:
                raise ValueError('NOTION_DATA_SOURCE_IDを指定してください。複数または未検出です。')
            self.data_source_id = sources[0]['id']
        elif self.data_source_id not in [x['id'] for x in sources]:
            raise ValueError('データソースが指定データベースに属していません')
        schema = self.api.call('GET','/data_sources/'+self.data_source_id)
        if 'Automation ID' not in schema['properties']:
            raise ValueError('Automation ID（rich_text）が必要です。DB構造は自動変更しません')
        self.properties = schema['properties']
        if self.properties['Automation ID']['type'] != 'rich_text':
            raise ValueError('Automation IDはrich_text型が必要です')
        user = self.api.call('GET','/users/me')
        self.file_limit = user.get('bot',{}).get('workspace_limits',{}).get('max_file_upload_size_in_bytes')
        if not self.file_limit:
            # Safe common denominator; never assume a paid workspace.
            self.file_limit = 5*1024*1024

    def all_results(self, path, method='GET', payload=None):
        results, cursor = [], None
        while True:
            args = dict(payload or {})
            if cursor:
                args['start_cursor'] = cursor
            data = self.api.call(method,path,**({'params':args} if method=='GET' else {'json':args}))
            results.extend(data.get('results',[]))
            if not data.get('has_more'):
                return results
            cursor = data['next_cursor']

    def find_page(self, paper):
        matches = {}
        for name,value in (('Automation ID',paper['id']),('PMID',paper['pmid']),('DOI',paper['doi'])):
            if not value or name not in self.properties:
                continue
            kind = self.properties[name]['type']
            if kind not in ('rich_text','title','url','number'):
                continue
            if kind=='number':
                value = int(value)
            query = {'filter':{'property':name,kind:{'equals':value}}}
            for page in self.all_results('/data_sources/'+self.data_source_id+'/query','POST',query):
                matches[page['id']] = page
        if paper.get('page_id'):
            page = self.api.call('GET','/pages/'+paper['page_id'])
            if not page.get('archived') and page.get('parent',{}).get('data_source_id') == self.data_source_id:
                matches[page['id']] = page
        # Legacy DOI properties sometimes contain a doi.org URL or upper case.
        # Read candidates without updating them; normalized comparison prevents duplicates.
        if paper.get('doi') and 'DOI' in self.properties:
            kind = self.properties['DOI']['type']
            if kind in ('rich_text','url'):
                query = {'filter':{'property':'DOI',kind:{'is_not_empty':True}}}
                for page in self.all_results('/data_sources/'+self.data_source_id+'/query','POST',query):
                    prop = page['properties'].get('DOI',{})
                    value = prop.get('url') if kind=='url' else ''.join(x.get('plain_text',x.get('text',{}).get('content','')) for x in prop.get('rich_text',[]))
                    if doi_key(value) == paper['doi']:
                        matches[page['id']] = page
        if len(matches) > 1:
            raise ValueError('Notionに同一論文の複数候補があります。新規作成しません。')
        return next(iter(matches.values())) if matches else None

    def save(self, revision, state):
        self.store.update('graphics',revision['_graphic_id'],notion=encode(state))

    def children(self, identifier):
        return self.all_results('/blocks/'+identifier+'/children')

    def upload(self, revision, state, kind, path):
        entry = state.get(kind+'_upload',{})
        if entry.get('id'):
            status = self.api.call('GET','/file_uploads/'+entry['id'])
            if status.get('status') == 'uploaded':
                return entry
            if status.get('status') not in ('pending','expired','failed'):
                raise ValueError('Notion添付状態の確認が必要です')
            if status.get('status') == 'pending' and entry.get('send_intent'):
                raise ValueError('Notion添付送信結果が未確定です。次回再確認します')
        if path.stat().st_size > min(self.file_limit,20*1024*1024):
            raise ValueError('ファイル上限超過：自動縮小や公開URL化は行いません')
        name = revision['id']+'_'+digest(path.read_bytes())[:12]+path.suffix
        content_type = mimetypes.guess_type(name)[0]
        if not entry.get('id') or status.get('status') != 'pending':
            upload = self.api.call('POST','/file_uploads',json={'mode':'single_part','filename':name,'content_type':content_type})
            entry = {'id':upload['id'],'name':name}
            state[kind+'_upload'] = entry
            self.save(revision,state)
        entry['send_intent'] = True
        self.save(revision,state)
        with path.open('rb') as handle:
            self.api.call('POST','/file_uploads/'+entry['id']+'/send',files={'file':(name,handle,content_type)})
        status = self.api.call('GET','/file_uploads/'+entry['id'])
        if status.get('status') != 'uploaded':
            raise ValueError('Notion添付完了をまだ確認できません')
        return entry

    def sync(self, revision):
        from .graphics import active_graphics, check_graphic, refresh_manifest
        paper = self.store.one('papers',revision['paper_id'])
        if revision['status'] == 'superseded' or paper['disposition'] != 'active' or paper['blocker']:
            raise ValueError('旧版・保留・識別未確認の論文は送信できません')
        rows = active_graphics(self.store, revision['id'])
        approved = [r for r in rows if r['status'] in ('IMAGE_APPROVED','COMPLETED')]
        if not approved:
            raise ValueError('承認済み画像がありません。prepare-web、import-image、review-imageを実行してください')
        # Validate all selected files before the first external operation.
        for row in approved:
            check_graphic(self.store, row, approved=True)
        for row in approved:
            if row['status'] == 'COMPLETED':
                continue
            data = check_graphic(self.store, row, approved=True)
            proxy = dict(revision, id=revision['id']+':'+row['topic_id']+':'+row['json_hash'][:12]+':'+row['image_hash'][:12],
                         summary=row['json_path'], image=row['image_path'], notion=row['notion'],
                         _graphic_id=row['id'], _topic_title=data['topic_title'],
                         _master=str(Path(row['json_path']).parent.parent/'master.json'))
            self._sync_one(proxy)
            refresh_manifest(self.store, revision['id'])

    def _sync_one(self, revision):
        if self.properties is None:
            self.setup()
        paper = self.store.one('papers',revision['paper_id'])
        summary = json.loads((self.store.root/revision['summary']).read_text())
        state = json.loads(revision['notion'])
        page = self.find_page(paper)
        if page is None:
            if state.get('page_intent'):
                raise ValueError('Notionページ作成結果が不明です。重複防止のため再作成を保留します')
            from upload_to_notion import build_chatgpt_summary_payload
            master = json.loads((self.store.root/revision['_master']).read_text())
            payload = build_chatgpt_summary_payload(master,self.database_id,{'properties':self.properties})
            payload['parent'] = {'data_source_id':self.data_source_id}
            payload['children'] = []
            payload['properties']['Automation ID'] = {'rich_text':rich(paper['id'])}
            payload.pop('cover',None)
            state['page_intent'] = True
            state['created_new'] = True
            self.save(revision,state)
            page = self.api.call('POST','/pages',json=payload)
        else:
            current_id = ''.join(x.get('plain_text',x.get('text',{}).get('content','')) for x in page['properties'].get('Automation ID',{}).get('rich_text',[]))
            if current_id and current_id != paper['id']:
                raise ValueError('NotionのAutomation IDが別の論文に紐付いています')
            if not current_id:
                self.api.call('PATCH','/pages/'+page['id'],json={'properties':{'Automation ID':{'rich_text':rich(paper['id'])}}})
        self.store.update('papers',paper['id'],page_id=page['id'])
        state['page_id'] = page['id']
        self.save(revision,state)
        marker = f"AI精読・画像承認済み {revision['_topic_title']} [{revision['id']}]"
        if state.get('section_copy',0):
            marker += f" 保存版{state['section_copy']+1}"
        sections = [b for b in self.children(page['id']) if b['type']=='toggle' and plain(b)==marker]
        if len(sections) > 1:
            raise ValueError('同じ生成版の領域が複数あります')
        if not sections:
            if state.get('section_intent'):
                raise ValueError('生成領域作成結果が不明です。次回照合します')
            state['section_intent'] = True
            self.save(revision,state)
            response = self.api.call('PATCH','/blocks/'+page['id']+'/children',json={'children':[{'object':'block','type':'toggle','toggle':{'rich_text':rich(marker)}}]})
            sections = response['results']
        section_id = sections[0]['id']
        state['section_id'] = section_id
        self.save(revision,state)
        from upload_to_notion import chatgpt_summary_children
        blocks = [{'object':'block','type':'paragraph','paragraph':{'rich_text':rich('画像はローカルで人が承認済み。AI精読全体の確認とは区別します。ページのHuman Checkedは以前の版を指す場合があります。')}}]
        blocks += chatgpt_summary_children(summary)
        for rec in summary.get('recommendations', []):
            text = '\n'.join(f'{k}: {rec[k]}' for k in ('kind','text','population','conditions','exceptions','agreement','evidence_strength'))
            text += '\n' + '\n'.join(e['location']+' / '+e['supporting_text'] for e in rec['evidence_references'])
            for start in range(0,len(text),1800):
                blocks.append({'object':'block','type':'paragraph','paragraph':{'rich_text':rich(text[start:start+1800])}})
        for index, evidence in enumerate(summary.get('evidence_references', [])):
            text = f"根拠 {index+1}: {evidence['location']} / {evidence['supporting_text']}"
            for start in range(0,len(text),1800):
                blocks.append({'object':'block','type':'paragraph','paragraph':{'rich_text':rich(text[start:start+1800])}})
        existing = self.children(section_id)
        # Compare deterministic textual prefix. User edits are never overwritten.
        for index,block in enumerate(blocks):
            if index < len(existing):
                if existing[index]['type'] != block['type'] or plain(existing[index]) != plain(block):
                    # Preserve edited blocks and append a new storage version, reusing artifacts.
                    previous = state.get('previous_sections',[]) + [{'section_id':section_id,'content_hash':state.get('content_hash')}]
                    state = {'page_id':page['id'],'previous_sections':previous,'section_copy':state.get('section_copy',0)+1}
                    self.save(revision,state)
                    return self._sync_one(dict(revision, notion=encode(state)))
                continue
            if state.get('text_intent') == index:
                raise ValueError('本文追加結果が不明です。再送せず次回照合します')
            state['text_intent'] = index
            self.save(revision,state)
            self.api.call('PATCH','/blocks/'+section_id+'/children',json={'children':[block]})
            state.pop('text_intent',None)
            self.save(revision,state)
        attachments = (('json',revision['summary']),('master',revision['_master']),('image',revision['image']))
        for kind,relative in attachments:
            marker = f"{kind}:{revision['id']}"
            children = self.children(section_id)
            matched = [b for b in children if b['type'] in ('file','image') and
                       ''.join(x.get('plain_text',x.get('text',{}).get('content','')) for x in b[b['type']].get('caption',[])) == marker]
            if len(matched) > 1:
                raise ValueError('同じ版の添付が重複しています')
            if matched:
                state[kind+'_block'] = matched[0]['id']
                self.save(revision,state)
                continue
            if state.get(kind+'_attach_intent'):
                raise ValueError('添付追加結果が不明です。再送せず次回照合します')
            upload = self.upload(revision,state,kind,self.store.root/relative)
            block_type = 'image' if kind=='image' else 'file'
            item = {'object':'block','type':block_type,block_type:{'type':'file_upload','file_upload':{'id':upload['id']},'caption':rich(marker)}}
            if kind!='image':
                item['file']['name'] = upload['name']
            state[kind+'_attach_intent'] = True
            self.save(revision,state)
            response = self.api.call('PATCH','/blocks/'+section_id+'/children',json={'children':[item]})
            state[kind+'_block'] = response['results'][0]['id']
            self.save(revision,state)
        # Never change an existing page's cover or clinical properties.
        fresh_page = self.api.call('GET','/pages/'+page['id'])
        # The existing gallery uses this files property rather than page cover.
        # Fill only an empty slot; preserve images chosen by the user.
        if self.properties.get('Graphic Image',{}).get('type') == 'files' and not fresh_page['properties'].get('Graphic Image',{}).get('files'):
            image_upload = state.get('image_upload')
            if image_upload:
                self.api.call('PATCH','/pages/'+page['id'],json={'properties':{'Graphic Image':{'files':[
                    {'name':image_upload['name'],'type':'file_upload','file_upload':{'id':image_upload['id']}}]}}})
                fresh_page = self.api.call('GET','/pages/'+page['id'])
                if not fresh_page['properties'].get('Graphic Image',{}).get('files'):
                    raise ValueError('ギャラリー画像の再取得確認に失敗しました')
        checked = fresh_page['properties'].get('Human Checked',{}).get('checkbox',False)
        if state.get('created_new') and not checked and not fresh_page.get('cover'):
            image_upload = state.get('image_upload')
            if image_upload:
                self.api.call('PATCH','/pages/'+page['id'],json={'cover':{'type':'file_upload','file_upload':{'id':image_upload['id']}}})
        verified = self.children(section_id)
        ids = {b['id'] for b in verified}
        if not all(state.get(kind+'_block') in ids for kind,_ in attachments):
            raise ValueError('Notion添付の再取得確認に失敗しました')
        if len(verified) < len(blocks) or any(plain(a)!=plain(b) or a['type']!=b['type'] for a,b in zip(verified,blocks)):
            raise ValueError('Notion本文の再取得確認に失敗しました')
        state['content_hash'] = digest(blocks)
        state['verified'] = True
        self.store.update('graphics',revision['_graphic_id'],notion=encode(state),status='COMPLETED')
