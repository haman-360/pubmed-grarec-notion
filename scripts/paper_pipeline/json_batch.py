"""Explicit JSON-only Batch jobs; shared artifacts, local credentials and billing ledger."""
import json
import math
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from jsonschema import ValidationError, validate

from .batches import API, Batches, config, schema
from .intake import PENDING, workspace_lock
from .pdf_download import validate_selection
from .store import Store, atomic_write, digest, encode, now, uid

SHARED = 'API_JSON'
MAX_SELECTION = 5


def api_key(repo):
    value = os.environ.get('OPENAI_API_KEY', '').strip()
    if value:
        return value
    path = Path(repo) / '.env'
    if path.is_file():
        for line in path.read_text().splitlines():
            match = re.fullmatch(r'\s*(?:export\s+)?OPENAI_API_KEY\s*=\s*(.*?)\s*', line)
            if match:
                return match[1].strip('"\'')
    return ''


class JsonAPI(API):
    def call(self, method, path, **kwargs):
        try:
            return super().call(method, path, **kwargs)
        except requests.RequestException as error:
            raise RuntimeError('OpenAIとの通信に失敗しました（' + type(error).__name__ + '）') from None


def local_root(repo, workspace):
    return Path(repo) / 'output/json_batch' / digest(str(workspace.root).encode())[:24]


def read_shared(workspace, name, default=None):
    path = workspace.path(SHARED + '/' + name + '.json')
    return json.loads(path.read_text()) if path.is_file() else default


def status(repo, workspace):
    owner = read_shared(workspace, 'owner', {})
    report = read_shared(workspace, 'status', {})
    return dict(report, owner=owner.get('device_name', ''),
                is_owner=owner.get('device_id') == workspace.device_id,
                key_ready=bool(api_key(repo)), configured=bool(owner))


@contextmanager
def ledger(repo, workspace, claim=False):
    root = local_root(repo, workspace)
    owner = read_shared(workspace, 'owner', {})
    if owner and (owner.get('device_id') != workspace.device_id or not (root / 'ledger.sqlite3').is_file()):
        raise ValueError('API担当Mac（' + owner.get('device_name', '') + '）の元の台帳から操作してください。新規台帳への切り替えは再送防止のため停止します。')
    if not owner and not claim:
        raise ValueError('先にこのMacでAPI予算を設定してください')
    store = Store(root)
    try:
        with store.lock():
            identity = store.setting('identity')
            if not identity:
                identity = uid(); store.set_setting('identity', identity)
            if not owner:
                owner = dict(device_id=workspace.device_id, device_name=workspace.device_name, ledger_id=identity)
                path = workspace.path(SHARED + '/owner.json'); path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('x') as out:
                    out.write(encode(owner))
            if owner.get('ledger_id') != identity:
                raise ValueError('API台帳の識別が一致しません。元の台帳を復元してください')
            # OneDrive may produce conflict copies after simultaneous offline claims.
            if len(list(workspace.path(SHARED).glob('owner*.json'))) != 1:
                raise ValueError('API担当Macの設定が競合しています。送信せず設定を確認してください')
            try:
                yield store
            finally:
                publish(workspace, store)
    finally:
        store.close()


def publish(workspace, store):
    jobs = []
    for revision in store.rows('SELECT * FROM revisions ORDER BY created_at DESC'):
        binding = store.setting('binding:' + revision['id'], {})
        attempts = store.rows('SELECT * FROM attempts WHERE revision_id=? ORDER BY created_at', (revision['id'],))
        jobs.append(dict(id=revision['id'], pmid=binding.get('pmid'), source_hash=binding.get('source_hash'), status=revision['status'],
                         message=revision['error'], at=revision['created_at'],
                         result=store.setting('result:' + revision['id'], ''),
                         attempts=[{k: a[k] for k in ('status', 'batch_id', 'reserve', 'actual')} for a in attempts]))
    budget = store.setting('budget', {})
    # Credentials and account balances never go to OneDrive.
    report = dict(at=now(), jobs=jobs, preview=store.setting('preview'),
                  budget_checked_at=budget.get('checked_at'), budget_end=budget.get('end'))
    atomic_write(workspace.path(SHARED + '/status.json'), encode(report))


def set_budget(repo, workspace, data):
    if data.get('confirmed') is not True:
        raise ValueError('購入済み残高・自動チャージ無効・旧API処理分の確認をしてください')
    remaining, other = float(data.get('remaining', -1)), float(data.get('other_usage', -1))
    if not all(math.isfinite(x) and x >= 0 for x in (remaining, other)) or other > remaining:
        raise ValueError('残高と他の処理の使用・予約額を確認してください')
    end = datetime.fromisoformat(data.get('end', '')).replace(tzinfo=timezone.utc)
    if end <= datetime.now(timezone.utc) + timedelta(hours=50):
        raise ValueError('期限まで50時間以上必要です')
    with workspace_lock(workspace), ledger(repo, workspace, claim=True) as store:
        old = store.setting('budget', {})
        start = old.get('start', now())
        # Updating balance must not reset a live period's $20 spending cap.
        if old and datetime.fromisoformat(old['end']) <= datetime.now(timezone.utc):
            start = now()
        store.set_setting('budget', dict(old, start=start, end=end.isoformat(), checked_at=now(),
                          remaining=remaining, other_usage=other, no_auto_recharge=True,
                          purchased_checked=True, production=False))
    return 'API担当Macと予算を保存しました（期間$20・1処理版$4まで）。'


def pending_manual(workspace, pmid):
    seen = {f['sha'] for event in workspace.events(pmid).values() for f in event['data']['files']
            if f['kind'] in ('json', 'draft', 'pdf')}
    for folder in (workspace.path(PENDING), workspace.incoming(pmid)):
        for path in folder.rglob('*'):
            # Conservatively stop on any new common-inbox PDF/JSON; organize first.
            if path.is_file() and path.suffix.lower() in ('.json', '.pdf') and digest(path.read_bytes()) not in seen:
                return True
    return False


def eligible(workspace, pmid, heads):
    event = workspace.current(pmid, heads)
    if workspace.category(event['data']) != 'json_pending':
        raise ValueError('PDF取得済み・JSON未作成の論文を選んでください')
    if pending_manual(workspace, pmid):
        raise ValueError('手動保存したPDF/JSONがあります。先に取り込み・整理をしてください')
    pdf = next(f for f in event['data']['files'] if f['kind'] == 'pdf')
    workspace.read_asset(pdf)
    return event, pdf


class JsonBatches(Batches):
    def estimate(self, revision, stage):
        if stage != 'analysis':
            raise ValueError('この画面はJSON作成専用です')
        from .sources import inspect_pdf
        cfg = json.loads(revision['config'])
        source = self.store.one('sources', revision['source_id'])
        info = inspect_pdf(self.store.root / source['path'])
        # Include the full shared prompt, its embedded schema and response schema.
        if type(cfg.get('counted_input_tokens')) is not int:
            cfg['counted_input_tokens'] = (info['text_bytes'] + info['pages'] * 16384 +
                len(cfg['shared_prompt'].encode()) + len(encode(schema('fulltext-v4')).encode()) + 512)
        return super().estimate(dict(revision, config=encode(cfg)), stage)

    def manual_handoff(self, revision):
        return bool(revision['summary'])  # Never start image jobs or the legacy handoff.

    def request_body(self, revision, stage):
        if stage != 'analysis':
            raise ValueError('この画面はJSON作成専用です')
        body = super().request_body(revision, stage)
        cfg = json.loads(revision['config'])
        body['input'][0]['content'][0]['text'] = cfg['shared_prompt']
        output_schema = schema('fulltext-v4')
        output_schema['properties']['source_hash'] = {'type': 'string', 'enum': [cfg['shared_source_hash']]}
        output_schema['required'].append('source_hash')
        body['text']['format']['schema'] = output_schema
        return body

    def accept(self, attempt, record):
        from openai_batch import extract_response_text
        revision = self.store.one('revisions', attempt['revision_id'])
        cfg = json.loads(revision['config'])
        response = record.get('response') or {}; body = response.get('body') or {}
        cost = self.actual_cost(body.get('usage'), 'analysis', cfg['rates'])
        self.store.update('attempts', attempt['id'], actual=cost, usage=encode(body.get('usage')))
        try:
            if response.get('status_code') != 200:
                raise ValueError('API要求失敗。HTTP ' + str(response.get('status_code')))
            if body.get('status') != 'completed' or body.get('incomplete_details'):
                raise ValueError('JSON出力が未完了です。回収データを保持しました')
            summary = json.loads(extract_response_text(body))
            paper = self.store.one('papers', revision['paper_id'])
            if not isinstance(summary, dict) or str(summary.get('pmid')) != paper['pmid']:
                raise ValueError('生成JSONのPMIDが一致しません')
            if summary.get('source_hash') != cfg['shared_source_hash']:
                raise ValueError('生成JSONの原本ハッシュが一致しません')
            if paper['title'] and summary.get('title') != paper['title']:
                raise ValueError('生成JSONの論文名が一致しません')
            core_schema = schema('fulltext-v4')
            validate({k: summary[k] for k in core_schema['properties'] if k in summary}, core_schema)
            summary.update(human_checked=False, source_level='fulltext_pdf', model=cfg['analysis_model'],
                           prompt_version=cfg['review_version'], batch_attempt_id=attempt['id'])
            path = 'artifacts/' + revision['id'] + '/master.json'
            atomic_write(self.store.root / path, encode(summary))
            self.store.update('revisions', revision['id'], summary=path, status='json_received', error='')
            self.store.update('attempts', attempt['id'], status='completed')
        except (ValueError, KeyError, TypeError, ValidationError):
            # Raw provider output is retained locally by collect; don't display source text/errors.
            self.store.update('attempts', attempt['id'], status='review_required')
            self.store.update('revisions', revision['id'], status='review_required',
                              error='API結果の完了状態・JSON形式・PMID・原本・論文名を確認してください。元の応答は担当Macの台帳に保管しています。')


def prepare(repo, workspace, items):
    selected = validate_selection(workspace, items)
    if len(selected) > MAX_SELECTION:
        raise ValueError('JSON作成は1回最大5本です')
    with ledger(repo, workspace) as store, workspace_lock(workspace):
        store.set_setting('preview', None)
        prepared = []
        for item in selected:
            pmid = item['pmid']; event, pdf = eligible(workspace, pmid, item['heads'])
            paper = store.paper(pmid=pmid, title=event['data'].get('title', ''))
            source = store.source(workspace.path(pdf['path']), paper['id'])
            existing = store.rows('SELECT * FROM revisions WHERE source_id=? ORDER BY created_at', (source['id'],))
            if existing:
                revision = existing[0]
                previous = store.rows('SELECT status FROM attempts WHERE revision_id=?', (revision['id'],))
                if len(previous) >= 2 or any(a['status'] != 'rejected' for a in previous):
                    raise ValueError('PMID ' + pmid + ' は送信履歴があります。再送せず「状況確認・JSON回収」を使ってください')
                if not previous:
                    cfg = config(paper, store.root / source['path'])
                    cfg.update(shared_prompt=workspace.json_prompt(pmid), shared_source_hash=pdf['sha'])
                    store.update('revisions', revision['id'], config=encode(cfg))
                    revision = store.one('revisions', revision['id'])
            else:
                cfg = config(paper, store.root / source['path'])
                cfg.update(shared_prompt=workspace.json_prompt(pmid), shared_source_hash=pdf['sha'])
                revision = store.revision(paper, source, cfg)
            store.set_setting('binding:' + revision['id'], dict(pmid=pmid, heads=item['heads'], source_hash=pdf['sha']))
            needs_count = False
            try:
                estimate = JsonBatches(store, None).estimate(revision, 'analysis')
            except ValueError as error:
                if '長文料金域' not in str(error):
                    raise
                # The explicit send confirmation also authorizes PDF upload for
                # token counting. No generation if the actual estimate exceeds caps.
                estimate, needs_count = 4.0, True
            prepared.append(dict(id=revision['id'], pmid=pmid, title=paper['title'],
                                 reserve=estimate, needs_count=needs_count))
        store.set_setting('preview', dict(id=uid(), items=prepared, total=round(sum(x['reserve'] for x in prepared), 6)))
    return '見積もりを準備しました。PDFの外部送信はまだ行っていません。対象と金額を確認してください。'


def check_binding(workspace, store, revision):
    binding = store.setting('binding:' + revision['id'])
    _, pdf = eligible(workspace, binding['pmid'], binding['heads'])
    if pdf['sha'] != binding['source_hash']:
        raise ValueError('原本が変更されています。準備からやり直してください')


def submit(repo, workspace, data, api=None):
    if data.get('confirmed') is not True:
        raise ValueError('PDFのOpenAIへの送信と見積費用を確認してください')
    with ledger(repo, workspace) as store:
        preview = store.setting('preview')
        if not preview or preview['id'] != data.get('preview_id'):
            raise ValueError('対象と見積もりを準備し直してください')
        api = api or JsonAPI(api_key(repo))
        engine = JsonBatches(store, api)
        # No model lookup/upload until every selection passes. Long PDFs may be
        # uploaded for token counting; budget reservation always precedes Batch creation.
        with workspace_lock(workspace):
            for item in preview['items']:
                check_binding(workspace, store, store.one('revisions', item['id']))
        api.check_models(include_images=False)
        sent = 0
        try:
            for item in preview['items']:
                with workspace_lock(workspace):
                    check_binding(workspace, store, store.one('revisions', item['id']))
                sent += engine.submit(limit=1, revision_ids=[item['id']])
        finally:
            store.set_setting('preview', None)
    return f'{sent}本のBatch送信処理を記録しました。各論文の結果を確認してください。完了後は「状況確認・JSON回収」を押してください。'


def adopt(workspace, store):
    for revision in store.rows("SELECT * FROM revisions WHERE status='json_received'"):
        binding = store.setting('binding:' + revision['id']); pmid = binding['pmid']
        raw = (store.root / revision['summary']).read_bytes()
        with workspace_lock(workspace):
            candidate = SHARED + '/results/' + revision['id'] + '/PMID_' + pmid + '_master.json'
            atomic_write(workspace.path(candidate), raw)
            store.set_setting('result:' + revision['id'], candidate)
            # Crash recovery: already imported into any historical event, never re-adopt.
            seen = [e for e in workspace.events(pmid).values() if any(
                f['sha'] == digest(raw) and f['kind'] in ('json', 'draft') for f in e['data']['files'])]
            if seen:
                stage = 'structure_review_required' if any(e['data'].get('structure_wait') for e in seen) else 'saved'
                store.update('revisions', revision['id'], status=stage, error='回収済みJSONの保存記録を確認しました')
                continue
            try:
                check_binding(workspace, store, revision)
                event = workspace.import_file(pmid, 'json', 'PMID_' + pmid + '_master.json', raw, binding['heads'])
                stage = 'structure_review_required' if event['data'].get('structure_wait') else 'saved'
                store.update('revisions', revision['id'], status=stage, error=event['data']['note'])
            except ValueError:
                store.update('revisions', revision['id'], status='candidate',
                             error='手動成果物・原本・記録の変更等があるため候補として保管しました。既存の工程は変更していません。')


def collect(repo, workspace, api=None):
    with ledger(repo, workspace) as store:
        # Retry local adoption first, including after an interrupted save.
        adopt(workspace, store)
        try:
            JsonBatches(store, api or JsonAPI(api_key(repo))).collect()
        finally:
            adopt(workspace, store)
    return 'Batchの状況を確認し、完了分のJSONを回収しました。未完了分は後で再確認してください。'
