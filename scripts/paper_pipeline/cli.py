from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .batches import API, Batches, config
from .notion import Notion, NotionAPI
from .sources import Sources, inspect_pdf
from .store import Store, atomic_write, encode, now

REPO = Path(__file__).resolve().parents[2]
LABELS = {'unsearched':'未調査','manual_candidate':'無料候補あり','free_unconfirmed':'無料版未確認',
          'available':'PDF入手済み','search_error':'調査エラー','analysis_ready':'解析待ち','analysis_submitted':'解析中',
          'image_ready':'画像待ち','image_submitted':'画像中','notion_ready':'Notion待ち','completed':'処理済み',
          'budget_wait':'予算待ち','quality_wait':'品質確認待ち','review_required':'要確認'}


def migrate(store):
    if store.setting('migrated'):
        return
    paths = list((REPO/'output/jobs').glob('*.json')) + list((REPO/'output/summaries').glob('*.json'))
    paths += list((REPO/'input/chatgpt_summaries').rglob('*.json'))
    mapping_path = REPO/'output/notion_pages.json'
    if mapping_path.exists():
        paths.append(mapping_path)
    paths += list((REPO/'output/batches').glob('*.json'))
    count = 0
    for path in paths:
        relative = str(path.relative_to(REPO))
        try:
            data = json.loads(path.read_text())
        except (ValueError,OSError):
            continue
        with store.db:
            store.db.execute('INSERT OR IGNORE INTO legacy VALUES(?,?)',(relative,encode(data)))
        destination = store.root/'legacy'/relative
        atomic_write(destination,path.read_bytes())
        if not isinstance(data,dict):
            if path == mapping_path and isinstance(data,list):
                for record in data:
                    if not isinstance(record,dict) or not str(record.get('pmid','')).isdigit():
                        continue
                    paper = store.paper(pmid=str(record['pmid']))
                    store.update('papers',paper['id'],page_id=record.get('notion_page_id'),legacy=1,blocker='legacy_review')
            continue
        pmid = str(data.get('pmid') or data.get('PMID') or '')
        if not pmid.isdigit():
            continue
        article = data.get('article') or data
        try:
            paper = store.paper(pmid=pmid,doi=article.get('doi',''),title=article.get('title',''),metadata=article)
            store.update('papers',paper['id'],legacy=1,blocker='legacy_review',page_id=data.get('notion_page_id') or paper['page_id'])
        except ValueError:
            continue
        for key in ('source_path','summary_path','image_path'):
            old = data.get(key)
            if not old:
                continue
            source = (REPO/old).resolve()
            if not source.is_relative_to(REPO) or not source.is_file():
                continue
            atomic_write(store.root/'legacy'/source.relative_to(REPO),source.read_bytes())
            if key=='source_path' and source.suffix.lower()=='.pdf':
                try:
                    inspect_pdf(source)
                    store.source(source,paper['id'],details={'migration':relative})
                except Exception:
                    pass
        count += 1
    # A legacy job may outlive its Batch manifest. Keep its Batch ID as an outstanding obligation.
    archived = [(r,json.loads(r['data'])) for r in store.rows('SELECT * FROM legacy')]
    known = {d.get('batch_id') for r,d in archived if '/batches/' in r['path'] and isinstance(d,dict)}
    for row,data in archived:
        if isinstance(data,dict) and data.get('batch_id') and data.get('status')=='batch_submitted' and data['batch_id'] not in known:
            recovered = {'batch_id':data['batch_id'],'recovered_from_job':row['path']}
            with store.db:
                store.db.execute('INSERT OR IGNORE INTO legacy VALUES(?,?)',('output/batches/recovered_'+data['batch_id']+'.json',encode(recovered)))
            known.add(data['batch_id'])
    # Retain published/manual images as legacy artifacts, without touching originals.
    for path in (REPO/'images').rglob('*'):
        if path.is_file() and path.suffix.lower() in ('.png','.jpg','.jpeg','.webp'):
            atomic_write(store.root/'legacy'/path.relative_to(REPO),path.read_bytes())
    store.set_setting('migrated',now())
    print(f'旧記録を保存しました: {count}件。旧版の再解析は activate を明示してください。')


def legacy_pending(store):
    return [(row,json.loads(row['data'])) for row in store.rows('SELECT * FROM legacy')
            if '/batches/' in row['path'] and isinstance(json.loads(row['data']),dict)
            and json.loads(row['data']).get('batch_id') and not json.loads(row['data']).get('collected_at')]


def collect_legacy(store, api):
    for row,data in legacy_pending(store):
        batch = api.retrieve(data['batch_id'])
        if batch['status'] not in ('completed','expired','cancelled','failed'):
            print(f"旧Batch {data['batch_id']}: {batch['status']}")
            continue
        for key in ('output_file_id','error_file_id'):
            if batch.get(key):
                atomic_write(store.root/'legacy/results'/(data['batch_id']+'_'+key+'.jsonl'),api.download(batch[key]))
        data['collected_at'] = now()
        data['new_pipeline_note'] = '回収のみ。内容と費用の確認後にactivateで新版を開始。'
        with store.db:
            store.db.execute('UPDATE legacy SET data=? WHERE path=?',(encode(data),row['path']))


def parser():
    result = argparse.ArgumentParser(description='PMID/PDF → 精読JSON → AIグラレコ → Notion（継続台帳）')
    result.add_argument('--state-dir',default=os.getenv('PAPER_PIPELINE_HOME') or str(REPO/'.paper_pipeline'))
    commands = result.add_subparsers(dest='command',required=True)
    p = commands.add_parser('direct',help='指定した最大4論文を通常APIで試験（Batchなし）')
    p.add_argument('--paper',action='append',required=True,help='PMIDまたは論文内部ID、繰り返し指定')
    p.add_argument('--yes',action='store_true')
    p.add_argument('--notion',action='store_true',help='完成後にNotionにも登録（省略時はローカルのみ）')
    for name in ('prepare','auto'):
        p = commands.add_parser(name)
        p.add_argument('--pmids-file',default=str(REPO/'input/pmids.txt'))
        p.add_argument('--pmid',action='append')
        p.add_argument('--pdf-dir',default=str(REPO/'input/pdfs'))
        p.add_argument('--offline',action='store_true',help='ネットワーク探索せず既存台帳・PDFを確認')
        p.add_argument('--skip-legacy',action='store_true',help='独立した試験台帳で旧記録を移行しない')
        p.add_argument('--max-papers',type=int,default=5)
        p.add_argument('--yes',action='store_true')
        p.add_argument('--interval-minutes',type=float,default=30)
        p.add_argument('--timeout-hours',type=float,default=72)
        if name=='auto':
            p.add_argument('--resume-only',action='store_true',help='PMID取り込み・PDF再探索をせず未完了工程を続行')
    p = commands.add_parser('submit')
    p.add_argument('--max-papers',type=int,default=5)
    p.add_argument('--yes',action='store_true')
    p = commands.add_parser('resume')
    p.add_argument('--notion',action='store_true')
    p.add_argument('--advance',action='store_true',help='回収後、予算内で次工程のBatchを投入')
    p.add_argument('--yes',action='store_true')
    p.add_argument('--no-render',action='store_true',help='互換オプション：回収のみ（再生成はsubmit）')
    for name in ('status','review','migrate','menu'):
        commands.add_parser(name)
    for name in ('hold','exclude','release','activate'):
        p = commands.add_parser(name)
        p.add_argument('paper')
    p = commands.add_parser('link')
    p.add_argument('paper')
    p.add_argument('--pmid',default='')
    p.add_argument('--doi',default='')
    p = commands.add_parser('identify')
    p.add_argument('source')
    p.add_argument('--paper')
    p.add_argument('--pmid',default='')
    p.add_argument('--doi',default='')
    p.add_argument('--title')
    p = commands.add_parser('regenerate')
    p.add_argument('paper')
    p.add_argument('--stage',choices=['analysis','image'],default='analysis')
    p.add_argument('--source')
    for name in ('export','backup'):
        p = commands.add_parser(name)
        p.add_argument('destination')
    p = commands.add_parser('budget')
    p.add_argument('--start',required=True,help='請求期間開始 YYYY-MM-DD')
    p.add_argument('--end',required=True,help='失効日時 ISO8601、時差付き')
    p.add_argument('--remaining',type=float,required=True,help='確認した未使用クレジット額（USD）')
    p.add_argument('--other-usage',type=float,default=0,help='他用途へ確保する額')
    p.add_argument('--no-auto-recharge',action='store_true',required=True)
    p.add_argument('--purchased-checked',action='store_true',required=True)
    p.add_argument('--production',action='store_true',help='5論文の評価基準を満たした後だけ指定')
    p = commands.add_parser('settle')
    p.add_argument('attempt')
    p.add_argument('--actual',type=float,required=True,help='請求画面で確認した実費')
    p.add_argument('--note',required=True)
    return result


def status(store, review=False):
    rows = store.rows('''SELECT p.*, (SELECT status FROM revisions r WHERE r.paper_id=p.id ORDER BY created_at DESC LIMIT 1) progress
                        FROM papers p ORDER BY p.added_at''')
    for p in rows:
        print(f"{p['id']}  PMID={p['pmid'] or '-'}  {LABELS.get(p['access'],p['access'])}  {LABELS.get(p['progress'],p['progress'] or 'PDF待ち')}  {p['disposition']} {p['blocker']}  {p['title'][:60]}")
        if review:
            searches = store.rows('SELECT result FROM searches WHERE paper_id=? ORDER BY id DESC LIMIT 1',(p['id'],))
            for row in searches:
                for c in json.loads(row['result']).get('candidates',[]):
                    print(f"  {c.get('priority','')} {c.get('evidence','')} {c['url']} {c.get('reason','')}")
            for r in store.rows("SELECT id,error FROM revisions WHERE paper_id=? AND error!=''",(p['id'],)):
                print(f"  生成版 {r['id']}: {r['error']}")
    for source in store.rows("SELECT * FROM sources WHERE status!='confirmed'"):
        details = json.loads(source['details'])
        print(f"PDF要確認 {source['id']} {source['status']} {source['original']} {details.get('error') or details.get('method','')} 論文候補={details.get('expected_paper','-')}")
    attempts = store.rows('SELECT id,status,reserve,actual FROM attempts')
    print(f"費用（全履歴）: 確認済み計算額 ${sum(a['actual'] or 0 for a in attempts):.4f} / 未確定予約 ${sum(a['reserve'] for a in attempts if a['actual'] is None):.4f}")
    budget = store.setting('budget',{})
    if budget:
        print(f"予算: {'通常 $80/期間' if budget.get('production') else '初回5件/$20'} / 1版$4 / 残額確認 {budget['checked_at']} / 失効 {budget['end']}")
        if budget.get('suspended_reason'):
            print('生成停止中: '+budget['suspended_reason'])
    else:
        print('予算未設定：新規Batchは投入しません。')
    for a in attempts:
        if a['status'] in ('unknown','review_required','direct_submitting','direct_unknown'):
            print(f"API要確認 {a['id']} {a['status']}")
    print(f"未回収の旧Batch: {len(legacy_pending(store))}")


def prepare(store,args):
    if not args.skip_legacy:
        migrate(store)
    if args.pmid:
        for pmid in args.pmid:
            store.paper(pmid=pmid)
    elif Path(args.pmids_file).is_file():
        store.import_pmids(args.pmids_file)
    sources = Sources(store)
    if not args.offline:
        # Populate candidate bibliography before matching local PDFs.
        for paper in store.pending_search():
            try:
                sources.metadata(paper)
            except Exception as error:
                store.update('papers',paper['id'],blocker='metadata_error')
                print(f"書誌確認待ち {paper['id']}: {type(error).__name__}")
        sources.import_folder(args.pdf_dir)
        for paper in store.pending_search():
            sources.discover(paper)
    else:
        for path in sorted(p for p in Path(args.pdf_dir).glob('*') if p.is_file() and p.suffix.lower()=='.pdf'):
            from .store import digest
            if store.rows("SELECT id FROM sources WHERE hash=? AND status IN ('confirmed','revision_review')",(digest(path.read_bytes()),)):
                continue
            try:
                info = inspect_pdf(path)
                from .sources import matches_pdf
                matches = [p for p in store.rows('SELECT * FROM papers') if matches_pdf(info,p)]
                store.source(path,matches[0]['id'] if len(matches)==1 else None,
                             'confirmed' if len(matches)==1 else 'identity_review',{'inspection':info})
            except Exception as error:
                store.source(path,status='identity_review',details={'error':str(error)[:500]})
    runner = Batches(store,None)
    runner.prepare_revisions()
    status(store)
    for revision in store.rows("SELECT * FROM revisions WHERE status IN ('analysis_ready','image_ready','budget_wait')"):
        stage = 'image' if revision['summary'] else 'analysis'
        try:
            print(f"見積もり予約 {revision['id']} {stage}: ${runner.estimate(revision,stage):.4f}（保証上限ではありません）")
        except Exception as error:
            print(f"見積もり不可 {revision['id']}: {error}")


def sync_notion(store):
    if not store.rows("SELECT id FROM revisions WHERE status='notion_ready'"):
        return
    client = Notion(store,NotionAPI(os.getenv('NOTION_TOKEN','')),os.getenv('NOTION_DATABASE_ID'),os.getenv('NOTION_DATA_SOURCE_ID'))
    for revision in store.rows("SELECT r.* FROM revisions r JOIN papers p ON p.id=r.paper_id WHERE r.status='notion_ready' AND p.disposition='active'"):
        try:
            client.sync(revision)
        except Exception as error:
            store.update('revisions',revision['id'],error=str(error)[:500])
            print(f"Notion待ち {revision['id']}: {error}")


def main(argv=None):
    from openai_batch import load_dotenv
    load_dotenv(str(REPO/'.env'))
    args = parser().parse_args(argv)
    store = Store(args.state_dir)
    try:
        with store.lock():
            dispatch(store,args)
    except (ValueError,RuntimeError,requests.RequestException) as error:
        print(f'停止: {error}',file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\n中断しました。台帳を保持しています。次回「続きから実行」で再開できます。')
        return 130
    finally:
        store.close()
    return 0


def dispatch(store,args):
    cmd = args.command
    if cmd == 'direct':
        from .direct import Direct
        if not args.yes and input('通常API料金で最大4論文を生成しますか？ [y/N]: ').strip().lower() not in ('y','yes'):
            return
        api = API(os.getenv('OPENAI_API_KEY',''))
        api.check_models()
        Direct(store,api).run(args.paper)
        if args.notion:
            # Restrict Notion registration to the selected papers as well.
            client = Notion(store,NotionAPI(os.getenv('NOTION_TOKEN','')),os.getenv('NOTION_DATABASE_ID'),os.getenv('NOTION_DATA_SOURCE_ID'))
            for value in args.paper:
                paper = store.resolve(value)
                for revision in store.rows("SELECT * FROM revisions WHERE paper_id=? AND status='notion_ready'",(paper['id'],)):
                    client.sync(revision)
        status(store)
    elif cmd == 'menu':
        menu(store)
    elif cmd == 'budget':
        start = datetime.fromisoformat(args.start)
        if start.tzinfo is None:
            from zoneinfo import ZoneInfo
            start = start.replace(tzinfo=ZoneInfo('Asia/Tokyo'))
        end = datetime.fromisoformat(args.end)
        if end.tzinfo is None or end <= start or not all(math.isfinite(x) and x >= 0 for x in (args.remaining,args.other_usage)):
            raise ValueError('期間・残額を確認してください。終了日時は時差が必要です。')
        store.set_setting('budget',{'start':start.isoformat(),'end':end.isoformat(),'remaining':args.remaining,
            'other_usage':args.other_usage,'checked_at':now(),'no_auto_recharge':True,'purchased_checked':True,'production':args.production})
        print('予算設定を保存しました。残額確認は24時間有効です。')
    elif cmd == 'settle':
        if not math.isfinite(args.actual) or args.actual < 0:
            raise ValueError('実費が不正です')
        attempt = store.one('attempts',args.attempt)
        details = json.loads(attempt['details'])
        details['manual_settlement'] = {'note':args.note,'at':now()}
        store.update('attempts',args.attempt,actual=args.actual,details=encode(details))
    elif cmd == 'link':
        paper = store.resolve(args.paper)
        pmid, doi = args.pmid or paper['pmid'], args.doi or paper['doi']
        from .store import doi_key
        if not (pmid or doi) or (pmid and not pmid.isdigit()):
            raise ValueError('正しいPMID/DOIを指定してください')
        conflicts = store.rows('SELECT id FROM papers WHERE id!=? AND (pmid=? OR doi=?)',
                               (paper['id'],pmid,doi_key(doi) or None))
        if conflicts or (paper['pmid'] and args.pmid and paper['pmid'] != args.pmid) or (paper['doi'] and args.doi and paper['doi'] != doi_key(args.doi)):
            raise ValueError('既存識別子との矛盾があります。自動統合しません')
        store.update('papers',paper['id'],pmid=pmid or None,doi=doi_key(doi) or None)
    elif cmd in ('hold','exclude','release','activate'):
        paper = store.resolve(args.paper)
        values = {'disposition':{'hold':'held','exclude':'excluded'}.get(cmd,'active')}
        if cmd == 'activate':
            values.update(legacy=0,blocker='')
        store.update('papers',paper['id'],**values)
    elif cmd == 'identify':
        source = store.one('sources',args.source)
        inspect_pdf(store.root/source['path'])
        if args.paper:
            paper = store.resolve(args.paper)
        else:
            if not args.title:
                raise ValueError('論文のタイトル確認が必要です。--titleを指定してください。')
            paper = store.paper(pmid=args.pmid,doi=args.doi,title=args.title)
        if source['paper_id'] and source['paper_id'] != paper['id']:
            raise ValueError('既に別の論文へ紐付いたPDFです')
        other = store.rows("SELECT id FROM sources WHERE paper_id=? AND status='confirmed' AND id!=?",(paper['id'],source['id']))
        store.update('sources',source['id'],paper_id=paper['id'],status='revision_review' if other else 'confirmed',details=encode({'method':'user_confirmed','at':now()}))
        store.update('papers',paper['id'],access='available',blocker='revision_review' if other else '')
        Batches(store,None).prepare_revisions()
    elif cmd == 'regenerate':
        paper = store.resolve(args.paper)
        if store.rows("SELECT a.id FROM attempts a JOIN revisions r ON r.id=a.revision_id WHERE r.paper_id=? AND a.status IN ('reserved','submitting','unknown','submitted','direct_submitting','direct_unknown')",(paper['id'],)):
            raise ValueError('進行中/結果不明の要求があります。先に回収・照合してください。')
        sources = store.rows("SELECT * FROM sources WHERE paper_id=? ORDER BY created_at DESC",(paper['id'],))
        source = store.one('sources',args.source) if args.source else next((s for s in sources if s['status']=='confirmed'),None)
        if not source or source['paper_id'] != paper['id']:
            raise ValueError('原本を指定してください')
        inspect_pdf(store.root/source['path'])
        summary = None
        if args.stage == 'image':
            revisions = store.rows('SELECT * FROM revisions WHERE paper_id=? AND source_id=? AND summary IS NOT NULL ORDER BY created_at DESC',(paper['id'],source['id']))
            if not revisions:
                raise ValueError('同じ原本の精読JSONがありません')
            summary = revisions[0]['summary']
        store.update('sources',source['id'],status='confirmed')
        store.update('papers',paper['id'],legacy=0,blocker='')
        with store.db:
            store.db.execute("UPDATE revisions SET status='superseded' WHERE paper_id=? AND status IN ('analysis_ready','image_ready','budget_wait','review_required','notion_ready')",(paper['id'],))
        revision = store.revision(paper,source,config(),force=True,summary=summary)
        if summary:
            data = json.loads((store.root/summary).read_text())
            data.update(revision_id=revision['id'],human_checked=False)
            target = 'artifacts/'+revision['id']+'/review.json'
            atomic_write(store.root/target,encode(data))
            store.update('revisions',revision['id'],summary=target)
        print('新しい処理版: '+revision['id'])
    elif cmd in ('export','backup'):
        getattr(store,cmd)(args.destination)
    elif cmd in ('status','review'):
        status(store,cmd=='review')
    elif cmd == 'migrate':
        migrate(store)
    else:
        if cmd == 'prepare' or cmd=='auto' and not args.resume_only:
            prepare(store,args)
        if cmd == 'prepare':
            return
        if cmd=='auto' and (not os.getenv('NOTION_TOKEN') or not os.getenv('NOTION_DATABASE_ID')):
            raise ValueError('自動処理にはNOTION_TOKENとNOTION_DATABASE_IDの初期設定が必要です')
        api = API(os.getenv('OPENAI_API_KEY',''))
        runner = Batches(store,api)
        if (cmd in ('submit','auto') or cmd=='resume' and args.advance) and not args.yes:
            if input('予算内で解析・画像Batchを投入しますか？ [y/N]: ').strip().lower() not in ('y','yes'):
                return
        if cmd == 'resume':
            collect_legacy(store,api)
            runner.collect()
            if args.advance:
                if legacy_pending(store):
                    raise ValueError('旧Batchの回収完了を待ってください')
                api.check_models()
                runner.prepare_revisions()
                runner.submit()
            if args.notion:
                sync_notion(store)
            status(store)
            return
        if legacy_pending(store):
            collect_legacy(store,api)
            if legacy_pending(store):
                raise ValueError('旧Batchが未回収です。完了を待ってresumeで回収してください。')
        api.check_models()
        runner.collect()
        runner.prepare_revisions()
        runner.submit(max(1,args.max_papers))
        if cmd == 'auto':
            deadline = time.monotonic() + max(1,args.timeout_hours)*3600
            while True:
                runner.collect()
                runner.submit(max(1,args.max_papers))
                sync_notion(store)
                pending = store.rows("SELECT id FROM attempts WHERE status='submitted'")
                if not pending:
                    status(store)
                    print('今回の自動処理を終了しました。要確認・予算待ちは台帳に保持しています。')
                    break
                if time.monotonic() >= deadline:
                    print('待機終了。次回autoで続きから再開できます。')
                    break
                print('Batch待機中。Terminalを閉じても次回再開できます。')
                time.sleep(max(5,args.interval_minutes)*60)


def menu(store):
    import shlex
    def ask(label):
        return input(label+': ').strip()
    def path_input(label):
        value = ask(label)
        if not value:
            return ''
        # Finder drag-and-drop escapes spaces; literal existing paths take precedence.
        expanded = str(Path(value).expanduser())
        if Path(expanded).exists():
            return expanded
        parts = shlex.split(value)
        return str(Path(parts[0]).expanduser()) if len(parts)==1 else value
    while True:
        print('\n1 新規取り込み・実行 / 2 状況 / 3 要確認 / 4 続きから実行（再検索なし） / 5 予算設定')
        print('6 PDF識別 / 7 保留・解除 / 8 再生成 / 9 CSV / 10 バックアップ / 11 旧版を対象化 / q 終了')
        choice = ask('番号')
        if choice == 'q':
            return
        command = None
        if choice == '1':
            path = path_input('PMID TXT（空欄は既定）')
            command = ['auto'] + (['--pmids-file',path] if path else [])
        elif choice in ('2','3','4'):
            command = {'2':['status'],'3':['review'],'4':['auto','--resume-only']}[choice]
        elif choice == '5':
            print('API請求画面で無料残高・失効日時・購入済み残高を確認し、自動チャージを無効にしてください。')
            start = ask('請求期間開始 YYYY-MM-DD')
            end = ask('失効日時 例 2026-12-01T00:00:00+09:00')
            remaining = ask('確認した無料残高 USD')
            other = ask('他アプリ用に確保する額 USD（空欄0）') or '0'
            if ask('自動チャージ無効・購入済み残高確認済みですか [y/N]').lower() != 'y':
                continue
            command = ['budget','--start',start,'--end',end,'--remaining',remaining,'--other-usage',other,
                       '--no-auto-recharge','--purchased-checked']
            if ask('5論文評価を合格済みで月$80運用にしますか [y/N]').lower()=='y':
                command.append('--production')
        elif choice == '6':
            source = ask('要確認PDFの内部ID')
            paper = ask('既存論文のPMID/内部ID（新規は空欄）')
            command = ['identify',source]
            if paper:
                command += ['--paper',paper]
            else:
                command += ['--title',ask('PDFで確認したタイトル'),'--pmid',ask('PMID（なければ空欄）'),
                            '--doi',ask('DOI（なければ空欄）')]
        elif choice == '7':
            paper = ask('論文PMID/内部ID')
            action = ask('hold 保留 / exclude 対象外 / release 解除')
            if action in ('hold','exclude','release'):
                command = [action,paper]
        elif choice == '8':
            paper = ask('論文PMID/内部ID')
            stage = ask('analysis 全文から / image 画像だけ')
            source = ask('原本の内部ID（既定原本は空欄）')
            if stage in ('analysis','image'):
                command = ['regenerate',paper,'--stage',stage] + (['--source',source] if source else [])
        elif choice in ('9','10'):
            command = ['export' if choice=='9' else 'backup',path_input('保存先の絶対パス')]
        elif choice == '11':
            command = ['activate',ask('旧版を再解析対象にするPMID/内部ID')]
        if command:
            try:
                dispatch(store,parser().parse_args(command))
            except (ValueError,RuntimeError,requests.RequestException) as error:
                print(f'保留: {error}')


if __name__ == '__main__':
    raise SystemExit(main())
