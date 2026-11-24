"""Durable PMID requests, chapter Batch collection, and publication of teaching kits."""
import json
from pathlib import Path

from jsonschema import ValidationError
from pypdf.errors import PyPdfError

from openai_batch import extract_response_text
from . import chapter_run, json_batch, materials, materials_notion, notion_sync, pdf_download, reading_guide
from .batches import Batches, config
from .intake import workspace_lock
from .manual import pmid_value
from .store import atomic_write, digest, encode, now, uid

QUEUE = '教材/作成依頼'
ACTIVE = {'reserved', 'submitting', 'unknown', 'submitted'}
TYPE_MAP = [('meta-analysis','meta_analysis'), ('systematic review','systematic_review'),
            ('scoping review','scoping_review'), ('practice guideline','guideline'), ('guideline','guideline'),
            ('consensus development conference','consensus'), ('randomized controlled trial','rct'),
            ('observational study','observational'), ('case reports','case_report'), ('narrative review','narrative_review')]


def directory(workspace, pmid):
    return workspace.path(QUEUE+'/'+pmid_value(pmid))


def read(path):
    return json.loads(path.read_bytes()) if path.is_file() else {}


def write(path, **data):
    old = read(path)
    if any(old.get(k) != v for k,v in data.items()):
        atomic_write(path, encode(dict(old, **data, at=now())))


def requests_list(workspace):
    return [p.parent.name for p in sorted(workspace.path(QUEUE).glob('*/request.json'))
            if p.parent.name.isascii() and p.parent.name.isdigit()]


def source_for(workspace, pmid):
    event = workspace.current(pmid)
    if workspace.category(event['data']) == 'archived':
        raise ValueError('この論文はアーカイブされています')
    files = [f for f in event['data']['files'] if f['kind']=='pdf']
    if len(files) > 1:
        raise ValueError('原本PDFを一意に特定できません')
    return event, files[0] if files else None


def request(repo, workspace, data):
    pmid = pmid_value(data.get('pmid'))
    folder = directory(workspace, pmid)
    with workspace_lock(workspace):
        workspace.add(pmid)
        event, pdf = source_for(workspace, pmid)
        old = read(folder/'request.json')
        if old.get('source') and pdf and pdf['sha'] != old['source']:
            raise ValueError('依頼時の原本PDFが差し替えられました。版を確認してください')
        write(folder/'request.json', pmid=pmid, paid=data.get('paid') is True, request_id=uid(),
              notion=data.get('notion') is True,
              source=old.get('source') or (pdf['sha'] if pdf else ''))
        if pmid == materials.PMID and not materials.current(workspace, pmid):
            if (Path(repo)/materials.EDITION/'master.json').is_file() and (Path(repo)/materials.PILOT_PDF).is_file():
                if pdf and digest((Path(repo)/materials.PILOT_PDF).read_bytes()) != pdf['sha']:
                    raise ValueError('検証済み教材と選択された原本PDFが一致しません')
                materials.generate(repo, workspace)
        if pdf and not materials.current(workspace, pmid):
            master = reusable(repo, pmid, pdf['sha'])
            if master:
                materials.publish(workspace, pmid, master.read_bytes(), workspace.read_asset(pdf))
        if materials.current(workspace, pmid):
            verify_current(workspace, pmid, pdf)
            return '保存済み教材を確認しました。精読APIの再送は不要です。'
    if not pdf:
        result = pdf_download.download_selected(workspace, [dict(pmid=pmid, heads=[event['id']])])
        reason = result['items'][0]['reason']
        write(folder/'pdf.json',message=reason)
        return reason
    return '教材の作成依頼を保存しました。'


def verify_current(workspace, pmid, pdf):
    kit = materials.current(workspace, pmid)
    folder = materials.checked_folder(workspace, kit['version'], pmid)
    if pdf and read(folder/'manifest.json').get(f'PMID_{pmid}.pdf') != pdf['sha']:
        raise ValueError('保存済み教材と現在の原本PDFが異なります')
    return kit


def reusable(repo, pmid, sha):
    # Only adopt content-addressed editions whose complete file set still matches the edition ID.
    candidates = {}
    for parent in ('chapter_reviews','learning-verification'):
        for master in (Path(repo)/'output'/parent/('PMID_'+pmid)).glob('*/editions/*/master.json'):
            data = read(master)
            if data.get('schema_version') != 'learning-v3' or data['metadata']['pmid'] != pmid or data['metadata']['pdf_sha256'] != sha:
                continue
            if pmid == materials.PMID and digest(master.read_bytes()) != materials.MASTER_SHA:
                continue
            edition = master.parent
            from .teaching_png import png_exports
            names = ['master.json','reader.html',*png_exports(data)]
            if all((edition/n).is_file() for n in names) and digest([digest((edition/n).read_bytes()) for n in names]) == edition.name:
                candidates[digest(master.read_bytes())] = master
    if len(candidates) > 1:
        raise ValueError('同じ原本に複数の教材版があります。使用するMasterの確認が必要です')
    return next(iter(candidates.values()), None)


class TeachingBatches(Batches):
    def accept(self, attempt, record):
        response = record.get('response') or {}
        body = response.get('body') or {}
        cfg = json.loads(self.store.one('revisions', attempt['revision_id'])['config'])
        usage = body.get('usage')
        self.store.update('attempts', attempt['id'], actual=self.actual_cost(usage, 'analysis', cfg['rates']),
                          usage=encode(usage))
        if response.get('status_code') != 200 or body.get('status') != 'completed' or body.get('incomplete_details'):
            self.store.update('attempts', attempt['id'], status='review_required')
            return
        try:
            result = json.loads(extract_response_text(body))
            if not isinstance(result, dict):
                raise ValueError('JSON object required')
        except (ValueError, KeyError, TypeError):
            self.store.update('attempts', attempt['id'], status='review_required')
            return
        target = self.store.setting('teaching-target:'+attempt['id'])
        atomic_write(self.store.root/target/'response.json', encode(result))
        self.store.update('attempts', attempt['id'], status='completed')

    def packet(self, revision, folder, pdf, prompt, cfg):
        target = str(folder.relative_to(self.store.root))
        prior = self.store.rows('SELECT * FROM attempts WHERE revision_id=? ORDER BY rowid DESC', (revision['id'],))
        for attempt in prior:
            if self.store.setting('teaching-target:'+attempt['id']) == target:
                if attempt['status'] in ACTIVE:
                    return
                if attempt['status'] != 'rejected':
                    raise ValueError('章または統合の応答を確認してください：'+str(folder))
                # No create intent was sent. A subsequent explicit resume may retry.
                break
        self.store.reserve(revision, 'analysis', self.analysis_reserve(0, cfg), check_only=True)
        body = dict(model=cfg['analysis_model'], store=False, reasoning={'effort':cfg['effort']},
                    max_output_tokens=cfg['max_output_tokens'], input=[dict(role='user', content=[
                        dict(type='input_text',text=prompt), dict(type='input_file',file_id=self.api.upload(pdf,'user_data'),
                                                                 detail=cfg['detail'])])])
        tokens = self.api.count_input(body)
        if type(tokens) is not int or not 0 < tokens <= 922000:
            raise ValueError('教材依頼の入力トークン数が上限外です')
        attempt = self.store.reserve(revision, 'analysis', self.analysis_reserve(tokens, cfg))
        self.store.set_setting('teaching-target:'+attempt['id'], target)
        if not self.send_reserved(revision, 'analysis', attempt, body):
            raise ValueError('Batch作成前に送信できませんでした。設定を確認して再開してください')


def process(repo, workspace, pmid, api=None):
    folder = directory(workspace, pmid)
    intent = read(folder/'request.json')
    progress = folder/'reading.json'
    status = read(progress)
    event, pdf = source_for(workspace, pmid)
    if not pdf:
        with workspace_lock(workspace):
            _, errors = workspace.scan(pmid=pmid, kinds={'pdf'})
            event, pdf = source_for(workspace, pmid)
        if errors:
            write(progress, message='PDFの取り込み待ち：'+errors[0])
            return
    if not pdf:
        if not (folder/'pdf.json').exists():
            result = pdf_download.download_selected(workspace,[dict(pmid=pmid, heads=[event['id']])])
            write(folder/'pdf.json',message=result['items'][0]['reason'])
        write(progress, message='PDF待ち：'+read(folder/'pdf.json')['message'])
        return
    if any(bound and bound != pdf['sha'] for bound in (intent.get('source'),status.get('source'))):
        raise ValueError('作成依頼後に原本PDFが変更されています')
    raw = workspace.read_asset(pdf)
    if not status.get('revision'):
        master = reusable(repo, pmid, pdf['sha'])
        if master:
            with workspace_lock(workspace):
                _, current_pdf = source_for(workspace, pmid)
                if not current_pdf or current_pdf['sha'] != pdf['sha']:
                    raise ValueError('教材準備中に原本PDFが変更されました')
                materials.publish(workspace, pmid, master.read_bytes(), raw)
            return
    if pmid == materials.PMID:
        raise ValueError('42526604の検証済みMasterと教材を保持するMacから作成・同期してください')
    if not intent.get('paid') and not status.get('revision'):
        write(progress, message='API利用の選択待ち：保存済みの教材Masterはありません')
        return
    if not api and not json_batch.api_key(repo):
        raise ValueError('API担当Macのキーを設定してください')
    with json_batch.ledger(repo, workspace) as store:
        runner = TeachingBatches(store, api or json_batch.JsonAPI(json_batch.api_key(repo)))
        paper = store.paper(pmid=pmid, title=event['data'].get('title',''))
        source = store.source(workspace.path(pdf['path']), paper['id'])
        original = store.root/source['path']
        if source['hash'] != pdf['sha'] or digest(original.read_bytes()) != pdf['sha']:
            raise ValueError('台帳の原本PDFが一致しません')
        if source['status'] != 'confirmed':
            raise ValueError('担当Macの台帳で原本PDFの版を確認してください')
        if status.get('revision'):
            revision = store.one('revisions',status['revision'])
            if revision['source_id'] != source['id'] or revision['paper_id'] != paper['id']:
                raise ValueError('台帳の論文と教材ジョブが一致しません')
            cfg = json.loads(revision['config'])
            if cfg.get('mode') != 'teaching_batch':
                raise ValueError('教材専用のジョブ台帳と一致しません')
            run = store.root/'teaching'/('PMID_'+pmid)/cfg['run']
        else:
            meta = reading_guide.metadata(workspace, pmid)
            if not meta or 'doi' not in meta:
                reading_guide.fetch_metadata(workspace, [pmid])
                meta = reading_guide.metadata(workspace, pmid)
            types = {x.casefold() for x in meta.get('publication_types', [])}
            publication = reading_guide.publication_info(meta, meta.get('title') or paper['title'])
            if publication['publication_type_inferred']:
                for name, label in reading_guide.REVIEW_NAMES.items():
                    if publication['publication_type'] == label:
                        types.add('meta-analysis' if 'meta-analysis' in name else name)
            kind = next((v for k,v in TYPE_MAP if k in types), 'other_unknown')
            run = chapter_run.prepare(original, store.root/'teaching', pmid=pmid, title=meta.get('title') or paper['title'],
                                      article_type=kind, learning=True, api_batch=True,
                                      doi=meta.get('doi') or None, year=meta.get('year') or None, journal=meta.get('journal') or None)
            cfg = config()
            cfg.update(mode='teaching_batch',run=run.name,max_output_tokens=16000)
            revision = store.revision(paper, source, cfg)
            write(progress, revision=revision['id'], source=source['hash'])
        runner.collect({revision['id']})
        report = chapter_run.resume(run, pdf=raw)
        if report['errors']:
            raise ValueError(str(report['errors'])+'　章の応答を確認：'+str(run))
        if report.get('master'):
            with workspace_lock(workspace):
                _, current_pdf = source_for(workspace, pmid)
                if not current_pdf or current_pdf['sha'] != pdf['sha']:
                    raise ValueError('教材生成中に原本PDFが変更されました')
                materials.publish(workspace, pmid, Path(report['master']).read_bytes(),raw)
            store.update('revisions',revision['id'],status='teaching_complete',error='')
            write(progress, message='教材を保存しました')
            return
        if not intent.get('paid'):
            write(progress, message='送信済み分を回収しました。続きの有料送信は選択待ちです')
            return
        def send_packet(dest, attachment, prompt):
            if read(folder/'request.json').get('request_id') != intent.get('request_id'):
                return
            _, selected = source_for(workspace, pmid)
            if not selected or selected['sha'] != pdf['sha']:
                raise ValueError('原本PDFが変わりました。送信対象を確認してください')
            runner.packet(revision,dest,attachment,prompt,cfg)

        if report.get('synthesis'):
            dest = Path(report['synthesis'])
            prompt = '\n'.join((dest/n).read_text() for n in ['request.txt','schema.json','context.json'])
            prompt += '\n原著はPICOの各欄に原文の記載または欠測理由を記入。レビューでは該当時のみ。スキーマに適合するJSONのみを返す。'
            send_packet(dest,original,prompt)
            note = '全章受理・全体統合の結果待ち'
        else:
            for chapter in report['pending']:
                dest = run/chapter
                if digest((dest/'source.pdf').read_bytes()) != (dest/'source.sha256').read_text():
                    raise ValueError('章の抜粋PDFが変更されています')
                send_packet(dest,dest/'source.pdf',(dest/'request.txt').read_text())
            note = f"章別精読 {report['accepted']}/{report['total']}章受理・結果待ち"
        write(progress, message=note)


def collect_teaching(repo, workspace, pmid, reading, api):
    if reading.get('revision'):
        with json_batch.ledger(repo, workspace) as store:
            revision = store.one('revisions', reading['revision'])
            if (store.one('papers',revision['paper_id'])['pmid'] != pmid or
                    json.loads(revision['config']).get('mode') != 'teaching_batch'):
                raise ValueError('教材専用のジョブ台帳と一致しません')
            TeachingBatches(store, api or json_batch.JsonAPI(json_batch.api_key(repo))).collect({revision['id']})


def advance(repo, workspace, pmid, api=None, notion_api=None):
    folder = directory(workspace, pmid)
    intent = read(folder/'request.json')
    kit = materials.current(workspace, pmid)
    if not kit and json_batch.read_shared(workspace,'owner',{}).get('device_id') == workspace.device_id:
        try:
            reading = read(folder/'reading.json')
            if reading.get('held') and reading.get('held_request') == intent.get('request_id'):
                collect_teaching(repo, workspace, pmid, reading, api)
                return
            process(repo, workspace, pmid, api)
            if reading.get('held'):
                write(folder/'reading.json', held=False)
        except ValidationError as error:
            write(folder/'reading.json', message='教材JSONを確認してください：'+error.json_path,
                  held=True,held_request=intent.get('request_id'))
        except (OSError, RuntimeError) as error:
            write(folder/'reading.json', message='通信・保存を次回再確認：'+str(error))
        except (ValueError, PyPdfError, KeyError) as error:
            write(folder/'reading.json', message=str(error), held=True, held_request=intent.get('request_id'))
        kit = materials.current(workspace, pmid)
    if kit and intent.get('notion') and not kit.get('page_id') and (
            notion_sync.read(workspace,'owner',{}).get('device_id') == workspace.device_id):
        try:
            _, pdf = source_for(workspace, pmid)
            verify_current(workspace, pmid, pdf)
            materials_notion.register(repo,workspace,kit['version'],notion_api,pmid=pmid)
            write(folder/'notion.json',message='Notion登録済み')
        except (ValueError, RuntimeError, OSError) as error:
            write(folder/'notion.json',message=str(error))


def progress(workspace):
    rows = []
    costs = json_batch.read_shared(workspace,'status',{}).get('teaching_costs',{})
    for pmid in requests_list(workspace):
        folder = directory(workspace,pmid)
        kit = materials.current(workspace,pmid)
        reading = read(folder/'reading.json')
        intent = read(folder/'request.json')
        pdf_report = read(folder/'pdf.json')
        note = reading.get('message') or pdf_report.get('message') or 'API担当Macの起動待ち'
        if kit:
            note = '教材作成済み'
            if kit.get('page_id'):
                note += ' · Notion登録済み'
            elif intent.get('notion'):
                note += ' · '+(read(folder/'notion.json').get('message') or 'Notion担当Macの登録待ち')
        rows.append(dict(pmid=pmid, message=note, version=kit.get('version'),page_id=kit.get('page_id'),
                         paid=intent.get('paid'), notion=intent.get('notion'), cost=costs.get(reading.get('revision')),
                         held=reading.get('held',False)))
    return rows
