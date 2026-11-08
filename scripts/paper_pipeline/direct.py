"""Explicit, bounded synchronous pilot. Shares validation and budget with Batch."""
import json

from .batches import Batches, config
from .store import atomic_write, digest, encode


class Direct(Batches):
    def run(self, identifiers):
        if self.store.setting('budget',{}).get('suspended_reason'):
            raise ValueError('生成品質の確認待ちです。新規要求を停止しています。')
        papers = {self.store.resolve(value)['id']:self.store.resolve(value) for value in identifiers}
        if not 1 <= len(papers) <= 4:
            raise ValueError('通常API試験は明示した1〜4論文に限定します')
        selected = []
        for paper in papers.values():
            if paper['disposition'] != 'active' or paper['legacy'] or paper['blocker']:
                raise ValueError('保留・旧記録・識別確認待ちです: '+paper['id'])
            revisions = self.store.rows('SELECT * FROM revisions WHERE paper_id=? ORDER BY created_at DESC',(paper['id'],))
            if not revisions:
                print('PDF待ち: '+paper['id'])
                continue
            revision = revisions[0]
            attempts = self.store.rows('SELECT * FROM attempts WHERE revision_id=?',(revision['id'],))
            if json.loads(revision['config']).get('mode') != 'direct':
                if attempts:
                    raise ValueError('Batch履歴のある版は通常APIに切り替えません。新版を明示作成してください')
                cfg = json.loads(revision['config'])
                rates = dict(cfg['rates'])
                for key in ('analysis_input','analysis_output','image_text','image_input','image_output'):
                    rates[key] *= 2
                rates.update(processing='standard',as_of='2026-11-08')
                cfg.update(mode='direct',rates=rates)
                source = self.store.one('sources',revision['source_id'])
                self.store.update('revisions',revision['id'],config=encode(cfg),signature=digest({'source':source['hash'],'config':cfg}))
            selected.append(revision['id'])
        for identifier in selected:
            for _ in range(2):
                revision = self.store.one('revisions',identifier)
                if revision['summary'] and revision['status'] in ('image_ready','budget_wait','quality_wait') and self.manual_handoff(revision):
                    break
                if revision['status'] not in ('analysis_ready','image_ready','budget_wait','quality_wait'):
                    break
                stage = 'image' if revision['summary'] else 'analysis'
                previous = self.store.rows('SELECT * FROM attempts WHERE revision_id=? AND stage=?',(identifier,stage))
                if previous:  # Explicit regeneration/review required; never retry an ambiguous paid POST.
                    break
                prepared_body = None
                if stage == 'analysis':
                    try:
                        prepared_body = self.request_body(revision,stage)
                        tokens = self.api.count_input(prepared_body)
                        if type(tokens) is not int or tokens <= 0:
                            raise ValueError('入力トークン数が不正です')
                        cfg = json.loads(revision['config'])
                        cfg['counted_input_tokens'] = tokens
                        cfg['counted_request_hash'] = digest(prepared_body)
                        self.store.update('revisions',identifier,config=encode(cfg))
                        revision = self.store.one('revisions',identifier)
                        print(f'全文入力確認 {paper_label(self.store,revision)}: {tokens} tokens',flush=True)
                    except Exception as error:
                        self.store.update('revisions',identifier,status='budget_wait',error='入力トークン数確認失敗: '+str(error)[:200])
                        break
                try:
                    amount = self.estimate(revision,stage)
                    attempt = self.store.reserve(revision,stage,amount)
                except ValueError as error:
                    self.store.update('revisions',identifier,status='budget_wait',error=str(error))
                    print(str(error))
                    break
                self.store.update('attempts',attempt['id'],details=encode({'mode':'direct'}))
                try:
                    body = prepared_body if prepared_body is not None else self.request_body(revision,stage)
                    atomic_write(self.store.root/'direct'/(attempt['id']+'_request.json'),encode(body))
                except Exception:
                    self.store.update('attempts',attempt['id'],status='rejected',actual=0)
                    self.store.update('revisions',identifier,status='review_required',error='通常APIの要求準備失敗')
                    break
                endpoint = '/responses' if stage=='analysis' else '/images/generations'
                # Persist intent first, including mode so Batch collection never treats it as a Batch.
                self.store.update('attempts',attempt['id'],status='direct_submitting')
                self.store.update('revisions',identifier,status=stage+'_submitted',error='')
                print(f'通常API送信 {paper_label(self.store,revision)} {stage} 予約 ${amount:.4f}',flush=True)
                try:
                    response = self.api.call('POST',endpoint,json=body,timeout=600,
                        audit_context={'attempt_id':attempt['id'],'revision_id':identifier})
                    record = {'response':{'status_code':response.status_code,'body':response.json()}}
                    audit_id = getattr(response,'paper_pipeline_audit_id',None)
                    if isinstance(audit_id,str):
                        record['transport_audit_id'] = audit_id
                    path = self.store.root/'direct'/(attempt['id']+'.json')
                    atomic_write(path,encode(record))
                except Exception:
                    self.store.update('attempts',attempt['id'],status='direct_unknown')
                    self.store.update('revisions',identifier,status='review_required',error='通常APIの応答不明。費用予約を保持し自動再送しません')
                    break
                self.accept(self.store.one('attempts',attempt['id']),record)
                print(f"通常API {paper_label(self.store,revision)} {stage}: {self.store.one('revisions',identifier)['status']}")


def paper_label(store,revision):
    paper = store.one('papers',revision['paper_id'])
    return paper['pmid'] or paper['id']
