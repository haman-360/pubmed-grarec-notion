"""Offline full-text JSON handoff. Imported content joins the normal graphic workflow."""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import ValidationError, validate

from .batches import config, review_prompt
from .graphics import master_schema, prepare_web, validate_master
from .library import paper_folder
from .store import atomic_write, digest, encode


def inbox(store, paper):
    return paper_folder(store, paper) / 'web_input'


def source_for_web(store, paper):
    if paper['disposition'] != 'active' or paper['legacy'] or paper['blocker']:
        raise ValueError('保留・識別確認待ちの論文です')
    sources = store.rows("SELECT * FROM sources WHERE paper_id=? AND status='confirmed' ORDER BY created_at DESC,rowid DESC", (paper['id'],))
    revision = latest(store, paper)
    if revision:
        # Match the source.pdf shortcut displayed for the current revision.
        sources = [s for s in sources if s['id'] == revision['source_id']]
    if not sources or not (store.root / sources[0]['path']).is_file():
        raise ValueError('先に本文PDFを取得してください')
    return sources[0]


def output_schema(source):
    result = master_schema()
    result['properties']['source_hash'] = {'type': 'string', 'const': source['hash']}
    result['required'].append('source_hash')
    return result


def json_request(store, paper):
    source = source_for_web(store, paper)
    text = (f"# PMID {paper['pmid']}：本文PDFからJSONを作成\n\n"
            '同じ論文フォルダの source.pdf をこの依頼文と一緒に添付して使います。\n'
            '添付PDFの論文名・DOIが下の書誌情報と一致することを確認してください。'
            'PDFがない・別論文・全文を確認できない場合は作成を止めて知らせてください。\n'
            + review_prompt(paper, 'fulltext-v4') + '\n'
            '以下のJSON Schemaを満たすJSONファイルを1個作り、master.jsonとしてダウンロードできるようにしてください。'
            'source_hashは下記の定数をそのまま転記してください。JSON以外の説明やMarkdownの囲みをファイルに入れないでください。\n'
            '作成したmaster.jsonは、この論文のweb_inputフォルダに保存します。アプリで取り込むと、'
            '各テーマの画像作成依頼文が使えます。画像はweb_input/images/<topic_id>.pngに保存します。\n\n'
            '```json\n' + json.dumps(output_schema(source), ensure_ascii=False, indent=2) + '\n```\n')
    folder = inbox(store, paper)
    (folder / 'images').mkdir(parents=True, exist_ok=True)
    # Version the generated instruction so a new PDF never overwrites an older request.
    atomic_write(folder / ('json_request_' + digest(text.encode())[:16] + '.md'), text)
    return text


def read_master(store, paper, raw):
    source = source_for_web(store, paper)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('JSONは8MB以下で保存してください')
    try:
        master = json.loads(raw.decode('utf-8-sig'))
        validate(master, output_schema(source))
        validate_master(master)
    except (ValueError, UnicodeError, ValidationError) as error:
        message = error.message if isinstance(error, ValidationError) else str(error)
        raise ValueError('Web JSONの形式を確認してください: ' + message[:240]) from error
    for key in ('pmid', 'doi', 'title'):
        if str(master.get(key) or '') != str(paper.get(key) or ''):
            raise ValueError('論文の対応が不一致です: ' + key)
    metadata = json.loads(paper['metadata'])
    for key in ('journal', 'year'):
        if metadata.get(key) and str(master[key]) != str(metadata[key]):
            raise ValueError('書誌情報が不一致です: ' + key)
    if digest((store.root / source['path']).read_bytes()) != source['hash']:
        raise ValueError('保存済みPDFが変更されています')
    return source, master


def latest(store, paper):
    rows = store.rows('SELECT * FROM revisions WHERE paper_id=? ORDER BY created_at DESC,rowid DESC', (paper['id'],))
    return rows[0] if rows else None


def imported(store, paper, sha):
    revision = latest(store, paper)
    return bool(revision and revision['summary'] and revision['status'] not in ('analysis_ready', 'image_ready') and
                json.loads(revision['config']).get('web_import_hash') == sha)


def import_master(store, paper, raw):
    source, master = read_master(store, paper, raw)
    sha = digest(raw)
    if imported(store, paper, sha):
        return latest(store, paper)
    if store.rows("""SELECT a.id FROM attempts a JOIN revisions r ON r.id=a.revision_id
                     WHERE r.paper_id=? AND a.status NOT IN ('completed','failed','rejected')""", (paper['id'],)):
        raise ValueError('APIの処理中・結果不明の要求があります。先に結果を回収・確認してください')
    cfg = config()
    cfg.update(mode='web_import', analysis_model='ChatGPT Web (user supplied)', web_import_hash=sha)
    revision = latest(store, paper)
    if not revision or json.loads(revision['config']).get('web_import_hash') != sha:
        revision = store.revision(paper, source, cfg, force=True)
    # Retry an interrupted local import in the same revision, never with a paid API.
    raw_path = Path('artifacts') / revision['id'] / 'web_original.json'
    atomic_write(store.root / raw_path, raw)
    master.update(human_checked=False, source_level='fulltext_pdf', automation_id=paper['id'],
                  revision_id=revision['id'], model=cfg['analysis_model'], prompt_version='fulltext-v4',
                  creation_method='chatgpt_web', imported_file_hash=sha)
    path = Path('artifacts') / revision['id'] / 'review.json'
    atomic_write(store.root / path, encode(master))
    store.update('revisions', revision['id'], summary=str(path), status='image_ready')
    prepare_web(store, store.one('revisions', revision['id']))
    # Preserve old data/approvals, but never submit an older waiting revision after import.
    with store.db:
        store.db.execute("UPDATE revisions SET status='superseded' WHERE paper_id=? AND id!=?", (paper['id'], revision['id']))
    return store.one('revisions', revision['id'])
