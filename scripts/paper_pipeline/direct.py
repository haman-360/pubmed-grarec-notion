"""Explicit, bounded synchronous pilot. Shares validation and budget with Batch."""
import json

from .batches import Batches, config
from .store import atomic_write, digest, encode


class Direct(Batches):
    def run(self, identifiers):
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
                cfg = config()
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
                if revision['status'] not in ('analysis_ready','image_ready','budget_wait'):
                    break
                stage = 'image' if revision['summary'] else 'analysis'
                previous = self.store.rows('SELECT * FROM attempts WHERE revision_id=? AND stage=?',(identifier,stage))
                if previous:  # Explicit regeneration/review required; never retry an ambiguous paid POST.
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
                    body = self.request_body(revision,stage)
                except Exception:
                    self.store.update('attempts',attempt['id'],status='rejected',actual=0)
                    self.store.update('revisions',identifier,status='review_required',error='通常APIの要求準備失敗')
                    break
                endpoint = '/responses' if stage=='analysis' else '/images/generations'
                # Persist intent first, including mode so Batch collection never treats it as a Batch.
                self.store.update('attempts',attempt['id'],status='direct_submitting')
                self.store.update('revisions',identifier,status=stage+'_submitted',error='')
                try:
                    response = self.api.call('POST',endpoint,json=body,timeout=600)
                    record = {'response':{'status_code':response.status_code,'body':response.json()}}
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
