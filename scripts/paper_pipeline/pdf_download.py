"""Selected public-PDF downloads into the shared, versioned workspace."""
import copy
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .intake import workspace_lock, PENDING
from .manual import pmid_value
from .pdf_finder import PdfFinder
from .pubmed import PubMedClient, polite_pause
from .sources import inspect_pdf, matches_pdf
from .store import atomic_write, encode, now

RESULT_FILE = 'PDF取得結果.json'
MAX_SELECTION = 50


def public_url(value):
    """Display only HTTP(S) URLs, without query credentials or fragments."""
    parts = urlsplit(value or '')
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username:
        return ''
    return urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))


def result_path(workspace):
    return workspace.path('状況/' + workspace.device_id + '/' + RESULT_FILE)


def _pending_pdf(workspace, pmid):
    # Do not silently compete with manually supplied PDFs, including ambiguous
    # flat bundles. The explicit intake operation should resolve them first.
    root = workspace.path(PENDING)
    for path in root.rglob('*'):
        if path.is_file() and path.suffix.lower() == '.pdf':
            if path.parent == root or ('PMID_' + pmid) in path.relative_to(root).parts or path.name.startswith((pmid + '_', 'PMID_' + pmid)):
                return True
    return any(p.is_file() and p.suffix.lower() == '.pdf' for p in (workspace.incoming(pmid) / 'PDF').iterdir())


def eligible(workspace, pmid, heads):
    event = workspace.current(pmid, heads)
    if any(f['kind'] == 'pdf' for f in event['data']['files']):
        raise ValueError('PDF取得済みです。既存のPDF・後続成果物を保持します')
    if workspace.category(event['data']) != 'missing_pdf':
        raise ValueError('PDF取得待ちの論文を選んでください')
    if _pending_pdf(workspace, pmid):
        raise ValueError('手動保存したPDFがあります。先に「取り込み・整理」または保存状況の確認をしてください')
    return event


def validate_selection(workspace, items):
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_SELECTION:
        raise ValueError(f'取得する論文を1〜{MAX_SELECTION}本選んでください')
    selected, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('論文一覧から対象を選んでください')
        pmid = pmid_value(item.get('pmid'))
        heads = item.get('heads')
        if not isinstance(heads, list) or len(heads) != 1 or not all(isinstance(v, str) for v in heads):
            raise ValueError('一覧を更新して、競合のない論文を選んでください')
        if pmid not in seen:
            selected.append(dict(pmid=pmid, heads=list(heads)))
            seen.add(pmid)
    return selected


def download_selected(workspace, items, progress=None, client=None, finder=None):
    """Network work never holds the shared-folder lock; recheck before adoption."""
    selected = validate_selection(workspace, items)
    client, finder = client or PubMedClient(), finder or PdfFinder()
    method = getattr(type(finder), 'search_method', 'free')
    result = dict(method=method, id=uuid.uuid4().hex, at=now(), finished_at='', items=[], total=len(selected))

    def notify(index, pmid, message):
        if progress:
            progress(dict(index=index, total=len(selected), pmid=pmid, message=message))

    def save():
        atomic_write(result_path(workspace), encode(result))
        # Per-run history keeps downloaded candidates discoverable after retries.
        atomic_write(workspace.path('状況/' + workspace.device_id + '/PDF取得履歴/' + result['id'] + '.json'), encode(result))

    with workspace_lock(workspace):
        save()
    try:
        for index, item in enumerate(selected, 1):
            pmid = item['pmid']
            entry = dict(method=method, pmid=pmid, status='error', reason='', title='', source='', url='', manual_urls=[], file=None)
            try:
                with workspace_lock(workspace):
                    eligible(workspace, pmid, item['heads'])
                notify(index, pmid, 'PubMedの書誌情報を確認中')
                records = [r for r in client.fetch_metadata([pmid]) if r.pmid == pmid and r.title]
                if len(records) != 1:
                    raise ValueError('PubMedの書誌情報が取得できませんでした')
                article = records[0]
                entry['title'] = article.title
                entry['manual_candidates'] = [asdict(c) for c in finder.manual_check_candidates_for_article(article)]
                entry['manual_urls'] = list(dict.fromkeys(url for url in
                    [public_url(c.url) for c in finder.manual_check_candidates_for_article(article)] +
                    [f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/'] if url))
                notify(index, pmid, '公開PDFを検索・取得中（出版社により数分かかります）')
                with tempfile.TemporaryDirectory(prefix='pubmed-pdf-') as temporary:
                    path = Path(temporary) / ('PMID_' + pmid + '.pdf')
                    found = finder.download_pdf(article, path)
                    entry['observations'] = list(getattr(finder, 'observations', []))
                    entry.update(source=found.source, url=public_url(found.url))
                    entry['manual_urls'] = list(dict.fromkeys(entry['manual_urls'] + [public_url(u) for u in getattr(finder, 'search_urls', []) if public_url(u)]))
                    if found.success:
                        info = inspect_pdf(path)
                        raw = path.read_bytes()
                        confirmed = matches_pdf(info, asdict(article))
                        with workspace_lock(workspace):
                            # Preserve downloaded bytes even if another Mac changed
                            # the paper while this request was in flight.
                            entry['file'] = workspace.asset(pmid, 'pdf_candidate', path.name, raw)
                            event = eligible(workspace, pmid, item['heads'])
                            if confirmed:
                                event = workspace.import_file(pmid, 'pdf', path.name, raw, item['heads'])
                                entry['file'] = next(f for f in event['data']['files'] if f['kind'] == 'pdf')
                                entry.update(status='downloaded', reason='PDFを保存しました。JSON作成待ちです')
                            else:
                                entry.update(status='candidate', reason='PDFは保管しましたが、本文と書誌情報の一致を自動確認できません。原本を照合してください')
                            data = copy.deepcopy(event['data'])
                            if not confirmed:
                                data['files'].append(entry['file'])
                            data.update(title=article.title, pdf_download=copy.deepcopy(entry),
                                        note=(data.get('note', '') + '\n' + entry['reason']).strip())
                            workspace.current(pmid, [event['id']])
                            workspace.record(pmid, data, [event['id']])
                    else:
                        reason = '公開PDFを取得できませんでした。手動確認先から入手してください'
                        if any(word in found.reason.lower() for word in ('authentication', 'captcha', 'html', '403', '401')):
                            reason = '認証・ブラウザーでの確認が必要、またはPDFではない応答でした。手動確認先を開いてください'
                        elif any(word in found.reason.lower() for word in ('failed', 'timeout', 'connection')):
                            reason = '通信または保存に失敗しました。再実行するか手動確認先を開いてください'
                        entry.update(status='not_found', reason=reason)
                        with workspace_lock(workspace):
                            event = eligible(workspace, pmid, item['heads'])
                            data = copy.deepcopy(event['data'])
                            data.update(title=article.title, pdf_download=copy.deepcopy(entry),
                                        note=(data.get('note', '') + '\n' + reason).strip())
                            workspace.record(pmid, data, [event['id']])
            except ValueError as error:
                entry.update(status='candidate' if entry['file'] else 'skipped', reason=str(error)[:400])
            except Exception as error:
                # HTTP errors can contain NCBI API keys/emails. Never persist raw exceptions.
                entry.update(status='error', reason='取得・検証を完了できませんでした（' + type(error).__name__ + '）。再実行または手動取得を試してください')
            result['items'].append(entry)
            with workspace_lock(workspace):
                save()
            notify(index, pmid, entry['reason'])
            if index < len(selected):
                polite_pause()
        result['finished_at'] = now()
        with workspace_lock(workspace):
            save()
        return result
    finally:
        client.session.close()
        finder.session.close()


def result_message(result):
    items = result['items']
    downloaded = sum(e['status'] == 'downloaded' for e in items)
    return f'PDF取得終了：{downloaded}/{len(items)}本を保存。残りは取得結果を確認してください。'
