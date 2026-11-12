"""Copy current legacy selections without modifying or sharing the old SQLite DB."""
import json
import sqlite3
from pathlib import Path
from .store import atomic_write, digest


def migrate(workspace, legacy_root):
    root = Path(legacy_root).resolve()
    copied, skipped, warnings = 0, 0, []
    db = sqlite3.connect((root / 'ledger.sqlite3').as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')
        for paper in db.execute('SELECT * FROM papers ORDER BY added_at'):
            pmid = paper['pmid']
            if not pmid:
                warnings.append('PMID未設定の旧記録は移行対象外: ' + paper['id'])
                continue
            if workspace.heads(pmid):
                skipped += 1
                continue
            files = []
            note = paper['blocker'] or ''
            revision = db.execute('SELECT * FROM revisions WHERE paper_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (paper['id'],)).fetchone()
            if revision and revision['error']:
                note = (note + ' ' + revision['error']).strip()
            if revision:
                source = db.execute('SELECT * FROM sources WHERE id=?', (revision['source_id'],)).fetchone()
            else:
                source = db.execute("SELECT * FROM sources WHERE paper_id=? AND status='confirmed' ORDER BY created_at DESC,rowid DESC LIMIT 1", (paper['id'],)).fetchone()

            def take(kind, relative, topic='', basis=''):
                path = (root / relative).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    warnings.append(f'PMID {pmid}: 元ファイルを確認してください ({kind})')
                    return None
                file = workspace.asset(pmid, kind, path.name, path.read_bytes(), topic, basis)
                files.append(file)
                return file

            if source:
                take('pdf', source['path'])
            if revision and revision['summary']:
                graphics = list(db.execute('SELECT * FROM graphics WHERE revision_id=? AND active=1 ORDER BY position', (revision['id'],)))
                master = Path(graphics[0]['json_path']).parent.parent / 'master.json' if graphics else Path(revision['summary'])
                master_file = take('json', str(master), basis=source['hash'] if source else '')
                for graphic in graphics:
                    file = take('graphic', graphic['json_path'], graphic['topic_id'], master_file['sha'] if master_file else '')
                    prompt = Path(graphic['json_path']).parent.parent / 'ready_for_web' / (Path(graphic['json_path']).stem + '_prompt.md')
                    if (root / prompt).is_file():
                        take('prompt', str(prompt), graphic['topic_id'], file['sha'] if file else '')
                    if graphic['image_path']:
                        take('png', graphic['image_path'], graphic['topic_id'], file['sha'] if file else '')
            pending = db.execute("SELECT COUNT(*) FROM attempts a JOIN revisions r ON r.id=a.revision_id WHERE r.paper_id=? AND a.status NOT IN ('completed','failed','rejected')", (paper['id'],)).fetchone()[0]
            if pending:
                note += ' 旧APIの未確定記録あり。新アプリから回収・取消・再送は行いません。'
            data = dict(title=paper['title'], files=files, note=note.strip(),
                        done=bool(revision and revision['status'] == 'completed'),
                        legacy_id=paper['id'], legacy_status=revision['status'] if revision else '',
                        notion_page=paper['page_id'] or '')
            if revision:
                original_hash = json.loads(revision['config']).get('web_import_hash')
                if original_hash:
                    data['seen_inputs'] = [['json', '', original_hash]]
            # Create phone-readable instructions for PDF-only papers as well.
            if source and not any(f['kind'] == 'prompt' for f in files):
                workspace.prepare_prompts(pmid, data)
            workspace.record(pmid, data, [])
            incoming = root / 'papers' / ('PMID_' + pmid) / 'web_input'
            for path in [incoming / 'master.json', *sorted((incoming / 'images').glob('*.png'))]:
                if path.is_file():
                    kind = 'JSON' if path.suffix == '.json' else 'PNG'
                    destination = workspace.incoming(pmid) / kind / path.name
                    if destination.exists() and digest(destination.read_bytes()) != digest(path.read_bytes()):
                        warnings.append(f'PMID {pmid}: 受け渡しフォルダに同名の別ファイルがありコピーを保留')
                    elif not destination.exists():
                        atomic_write(destination, path.read_bytes())
            copied += 1
    finally:
        db.close()
    return dict(copied=copied, skipped=skipped, warnings=warnings)
