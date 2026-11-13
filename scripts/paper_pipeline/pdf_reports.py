"""Evidence-based manual acquisition queue, shared history and CSV exports."""
import csv
import io
import json

from .pdf_download import public_url
from .paid_pdf import cooldown
from .store import atomic_write

LABELS = {'low': '低', 'medium': '中', 'high': '高', 'unknown': '未判定', 'done': '取得済み'}
RANK = {key: index for index, key in enumerate(LABELS)}


def difficulty(entry):
    if entry.get('status') == 'downloaded':
        return 'done', 'PDF保存済み'
    if entry.get('status') in ('error', 'skipped'):
        return 'unknown', entry.get('reason', '通信・記録を確認してください')
    hints = entry.get('manual_candidates', [])
    observations = entry.get('observations', [])
    if entry.get('status') == 'candidate':
        return 'low', 'PDF候補を保管済み。本文と書誌情報の照合が必要'
    free = next((h for h in hints if h.get('priority') == '1_high'), None)
    if free:
        return 'low', free.get('evidence') or '無料全文の候補あり（取得保証ではありません）'
    purchase = next((o for o in observations if o.get('kind') == 'purchase'), None)
    if purchase:
        return 'high', purchase['evidence']
    browser = next((o for o in observations if o.get('kind') == 'browser'), None)
    if browser:
        return 'medium', browser['evidence']
    pdf = next((o for o in observations if o.get('kind') == 'pdf_link'), None)
    if pdf:
        return 'low', pdf['evidence']
    if hints or any('pubmed.ncbi.nlm.nih.gov' not in u for u in entry.get('manual_urls', [])):
        return 'medium', '出版社・公開ページの候補あり。ブラウザーで無料全文・PDFリンクを確認'
    return 'high', '無料版の所在を確認できません。有料限定とは断定していません'


def search_rows(workspace):
    """Latest result per PMID and method, across Macs; no network or writes."""
    latest = {}
    paths = set(workspace.root.glob('状況/*/PDF取得履歴/*.json'))
    paths.update(workspace.root.glob('状況/*/PDF取得結果.json'))
    for path in sorted(paths):
        try:
            result = json.loads(path.read_text())
            for entry in result['items']:
                method = entry.get('method') or ('paid' if entry.get('source') == 'OpenAI Web検索' else result.get('method', 'free'))
                key = (entry['pmid'], method)
                at = result.get('finished_at') or result.get('at', '')
                if key in latest and latest[key]['at'] >= at:
                    continue
                level, reason = difficulty(entry)
                latest[key] = dict(entry, method=method, at=at, difficulty=level,
                                   difficulty_label=LABELS[level], difficulty_reason=reason,
                                   manual_urls=list(dict.fromkeys(public_url(u) for u in entry.get('manual_urls', []) if public_url(u))))
        except (ValueError, OSError, KeyError, TypeError):
            continue
    # Even interrupted API requests remain visible and subject to the cooldown.
    for path in workspace.root.glob('API_PDF/PMID_*.json'):
        try:
            record = json.loads(path.read_text()); key = (record['pmid'], 'paid')
            if key not in latest or latest[key]['at'] < record['at']:
                latest[key] = dict(pmid=record['pmid'], title=record.get('title', ''), method='paid',
                    at=record['at'], status=record.get('status'), difficulty='unknown', difficulty_label=LABELS['unknown'],
                    difficulty_reason='API検索記録あり。PDF取得結果は未確認',
                    manual_urls=[public_url(u) for u in record.get('urls', []) if public_url(u)])
        except (ValueError, OSError, KeyError, TypeError):
            continue
    rows = list(latest.values())
    for row in rows:
        row['cooldown'] = cooldown(workspace, row['pmid']) if row['method'] == 'paid' else {}
    return sorted(rows, key=lambda r: (RANK[r['difficulty']], r['pmid'], r['method']))


def export_csv(workspace):
    rows = search_rows(workspace)
    folder = workspace.path('状況/' + workspace.device_id + '/PDF検索一覧')
    def safe(value):
        value = str(value or '')
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value
    for method, label in (('free', '無料'), ('paid', '有料')):
        stream = io.StringIO(newline=''); writer = csv.writer(stream)
        writer.writerow(['PMID', '論文名', '検索方法', '検索日時', '状態', '難易度', '判定理由', '確認先URL', '有料再検索可能日時'])
        for row in rows:
            if row['method'] != method: continue
            writer.writerow([safe(v) for v in (row['pmid'], row.get('title'), label, row['at'], row['status'],
                row['difficulty_label'], row['difficulty_reason'], '\n'.join(row['manual_urls']), row.get('cooldown', {}).get('until'))])
        atomic_write(folder / (label + '_PDF検索結果.csv'), '\ufeff' + stream.getvalue())
    return folder
