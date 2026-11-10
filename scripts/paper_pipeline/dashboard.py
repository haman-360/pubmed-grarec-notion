"""Local desktop dashboard; explicit jobs reuse the existing ledger and approval gates."""
from __future__ import annotations
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from .store import Store
from .sources import Sources
from .batches import API, Batches
from .graphics import active_graphics, check_graphic, import_image, review_image
from .notion import Notion, NotionAPI
from .library import expose_library
from .workspace import remember_state_dir
from .cli import LABELS, dispatch, parser

class Dashboard:
    def __init__(self, repo, root):
        self.repo, self.root = Path(repo), Path(root)
        self.inbox = self.repo/'input/web_images'
        self.inbox.mkdir(exist_ok=True)

    def latest(self, store, paper):
        rows = store.rows('SELECT * FROM revisions WHERE paper_id=? ORDER BY created_at DESC,rowid DESC', (paper['id'],))
        return rows[0] if rows else None

    def candidates(self, store):
        result=[]
        for path in sorted(self.inbox.glob('*.png')):
            match=re.fullmatch(r'PMID_(\d+)_([a-z0-9_-]+)\.png', path.name)
            item={'name':path.name,'error':'ファイル名を PMID_番号_topic.png にしてください'}
            if match:
                try:
                    paper=store.resolve(match[1]); revision=self.latest(store,paper)
                    if not revision: raise ValueError('精読JSONがありません')
                    rows=[g for g in active_graphics(store,revision['id']) if g['topic_id']==match[2]]
                    if len(rows)!=1: raise ValueError('画像テーマが見つかりません。Web用ファイルを準備してください')
                    g=rows[0]; sha=hashlib.sha256(path.read_bytes()).hexdigest()
                    item.update(pmid=match[1],topic=match[2],revision=revision['id'],json_hash=g['json_hash'],sha=sha,
                                status=g['status'],synced=g['status']=='COMPLETED' and g['image_hash']==sha,error='')
                    if paper['disposition']!='active' or paper['blocker']: item['error']='保留・識別確認待ちです'
                except ValueError as e: item['error']=str(e)
            result.append(item)
        return result

    def snapshot(self, store):
        papers=store.rows('SELECT * FROM papers ORDER BY added_at DESC'); rows=[]
        counts=dict(total=len(papers),unsearched=0,missing_pdf=0,json_ready=0,completed=0,png_synced=0,png_pending=0)
        for p in papers:
            r=self.latest(store,p); graphics=active_graphics(store,r['id']) if r else []
            searched=bool(store.rows('SELECT 1 FROM searches WHERE paper_id=? LIMIT 1',(p['id'],)))
            counts['unsearched']+=int(not searched and p['access']!='available' and p['disposition']=='active' and not p['legacy'])
            counts['missing_pdf']+=int(p['access']!='available')
            counts['json_ready']+=int(bool(r and r['summary']))
            counts['completed']+=int(bool(r and r['status']=='completed'))
            counts['png_synced']+=sum(g['status']=='COMPLETED' for g in graphics)
            counts['png_pending']+=sum(bool(g['image_path']) and g['status']!='COMPLETED' for g in graphics)
            rows.append({'pmid':p['pmid'] or p['id'],'title':p['title'],'access':LABELS.get(p['access'],p['access']),
                         'progress':LABELS.get(r['status'],r['status']) if r else 'PDF・精読待ち',
                         'error':r['error'] if r else p['blocker'], 'topics':','.join(g['topic_id'] for g in graphics)})
        candidates=self.candidates(store)
        counts['inbox_pending']=sum(not c.get('synced') and not c['error'] for c in candidates)
        budget=store.setting('budget',{})
        return {'counts':counts,'papers':rows,'candidates':candidates,'inbox':str(self.inbox),
                'stop':budget.get('suspended_reason',''),'budget_checked':budget.get('checked_at','未設定'),
                'budget_end':budget.get('end','未設定')}

    def run(self, action, data, store):
        if action=='add':
            ids=[v.strip() for v in data.get('pmids','').splitlines() if v.strip() and not v.lstrip().startswith('#')]
            if not ids or any(not re.fullmatch(r'[1-9]\d{0,8}',v) for v in ids): raise ValueError('PMIDを1行1件の数字で入力してください')
            for pmid in dict.fromkeys(ids): store.paper(pmid=pmid)
            return f'{len(set(ids))}件のPMIDを取り込みました。'
        if action in ('search','auto'):
            sources=Sources(store)
            papers=store.rows("SELECT * FROM papers p WHERE disposition='active' AND legacy=0 AND access!='available' AND NOT EXISTS (SELECT 1 FROM searches s WHERE s.paper_id=p.id)")
            for p in papers:
                try: sources.metadata(p)
                except Exception: pass  # discover records the actual retrieval failure below.
            sources.import_folder(self.repo/'input/pdfs')
            for p in papers:
                current=store.one('papers',p['id'])
                if current['access']!='available': sources.discover(current)
            if action=='search': return f'未検索の{len(papers)}件を確認しました。取得できなかった理由は履歴に残ります。'
        if action in ('json','auto','collect'):
            if not os.getenv('OPENAI_API_KEY'): raise ValueError('.envにOPENAI_API_KEYを設定してください')
            api=API(os.environ['OPENAI_API_KEY'],audit_dir=store.root/'http_audit')
            runner=Batches(store,api,allow_image_api=False)
            runner.collect()
            if action!='collect':
                budget=store.setting('budget',{})
                if budget.get('suspended_reason'): raise ValueError('JSON作成は品質確認のため停止中です: '+budget['suspended_reason'])
                api.check_models(include_images=False)
                runner.prepare_revisions(); runner.submit(5)
            return '精読結果を回収しました。新規依頼は予算内で最大5論文です。API待ちは画面の自動回収で更新されます。'
        if action=='prepare':
            dispatch(store,parser().parse_args(['prepare-web','--paper',data['pmid']]))
            return 'Web用依頼文を準備しました。論文フォルダのready_for_webを使ってください。'
        if action=='upload':
            if data.get('confirmed') is not True or not data.get('reviewer','').strip() or not data.get('note','').strip():
                raise ValueError('原文・JSON・画像の確認と、確認者・確認内容の入力が必要です')
            requested=data.get('items',[])
            if not requested: raise ValueError('未登録PNGがありません')
            current={c['name']:c for c in self.candidates(store)}
            # Validate the entire displayed selection before importing or sending anything.
            for item in requested:
                c=current.get(item['name'])
                if not c or c['error'] or c.get('synced') or any(c.get(k)!=item.get(k) for k in ('sha','revision','json_hash','topic')):
                    raise ValueError('PNGまたはJSONが一覧表示後に変わりました。更新して確認し直してください')
                g=next(g for g in active_graphics(store,item['revision']) if g['topic_id']==item['topic'])
                check_graphic(store,g)
            client=Notion(store,NotionAPI(os.getenv('NOTION_TOKEN','')),os.getenv('NOTION_DATABASE_ID'),os.getenv('NOTION_DATA_SOURCE_ID'))
            failures=[]; completed=0
            for item in requested:
                try:
                    r=store.one('revisions',item['revision'])
                    imported=import_image(store,r,item['topic'],self.inbox/item['name'])
                    if imported['image_hash']!=item['sha']: raise ValueError('PNGが取り込み中に変わりました。再確認してください')
                    review_image(store,r,item['topic'],True,data['reviewer'],data['note'])
                    client.sync(store.one('revisions',r['id'])); completed+=1
                except Exception as e: failures.append(item['name']+': '+type(e).__name__+' '+str(e)[:180])
            return f'{completed}件のPNGを登録しました。'+ ('\n要確認:\n'+'\n'.join(failures) if failures else '')
        if action=='budget':
            held=store.setting('budget',{}).get('suspended_reason')
            if data.get('confirmed') is not True: raise ValueError('請求画面で残額・期限・自動チャージ無効を確認してください')
            args=['budget','--start',data['start'],'--end',data['end'],'--remaining',str(data['remaining']),
                  '--other-usage',str(data.get('other',0)),'--no-auto-recharge','--purchased-checked']
            dispatch(store,parser().parse_args(args))
            if held:
                budget=store.setting('budget'); budget['suspended_reason']=held; store.set_setting('budget',budget)
            return '予算情報を更新しました。品質停止設定は保持しています。'
        if action in ('folder','inbox','notion','preview'):
            if action=='inbox': target=str(self.inbox)
            elif action=='preview':
                names={c['name'] for c in self.candidates(store)}
                if data.get('name') not in names: raise ValueError('一覧にあるPNGを選んでください')
                target=str(self.inbox/data['name'])
            elif action=='folder':
                target=str(self.repo/'papers')
                if data.get('pmid'):
                    p=store.resolve(data['pmid']); target=str(self.repo/'papers'/('PMID_'+p['pmid'] if p['pmid'] else 'PAPER_'+p['id']))
            else:
                identifier=os.getenv('NOTION_DATABASE_ID','').replace('-','')
                if not re.fullmatch('[a-fA-F0-9]{32}',identifier): raise ValueError('NOTION_DATABASE_IDが未設定です')
                target='https://www.notion.so/'+identifier
            subprocess.run(['open',target],check=True); return '開きました。'
        raise ValueError('未対応の操作です')
