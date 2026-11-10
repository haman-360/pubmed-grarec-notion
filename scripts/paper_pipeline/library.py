"""Readable per-PMID copies. The original ledger and artifacts remain authoritative."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .store import atomic_write, digest, encode

MARKER = '.paper-library.json'
SCHEMA = 'paper-library-v1'
SHORTCUTS = ('source.pdf', 'review.json', 'master.json', 'plan.json',
             'graphics', 'ready_for_web', 'images', 'manifest.json')


def paper_folder(store, paper):
    name = 'PMID_' + paper['pmid'] if paper['pmid'] else 'PAPER_' + paper['id']
    return store.root / 'papers' / name


def copy_unchanged(source, destination):
    """Never overwrite an edited copy or an original, even when called repeatedly."""
    raw = Path(source).read_bytes()
    destination = Path(destination)
    if destination.is_symlink():
        raise ValueError('確認用コピーがリンクに変更されています: ' + str(destination))
    if destination.exists():
        if destination.read_bytes() != raw:
            raise ValueError('確認用コピーが編集されています。元データは保持しています: ' + str(destination))
    else:
        atomic_write(destination, raw)
    return destination


def shortcut(path, target):
    path = Path(path)
    if path.exists() and not path.is_symlink():
        raise ValueError('既存ファイルは置き換えません: ' + str(path))
    if target is None:
        if path.is_symlink():
            path.unlink()
        return
    relative = os.path.relpath(target, path.parent)
    if path.is_symlink() and os.readlink(path) == relative:
        return
    temporary = path.with_name(path.name + '.link-part')
    if temporary.exists() or temporary.is_symlink():
        raise ValueError('作業用リンクが残っています: ' + str(temporary))
    temporary.symlink_to(relative)
    temporary.replace(path)


def snapshot(folder, name, data):
    content = encode(data)
    version = folder / 'history' / (name + '_' + digest(content.encode()) + '.json')
    if not version.exists():
        atomic_write(version, content)
    shortcut(folder / (name + '.json'), version)


def sync_paper(store, paper):
    folder = paper_folder(store, paper)
    folder.mkdir(parents=True, exist_ok=True)
    sources = store.rows('SELECT * FROM sources WHERE paper_id=? ORDER BY created_at,rowid', (paper['id'],))
    for source in sources:
        copy_unchanged(store.root / source['path'], folder / 'pdfs' / (source['hash'] + '.pdf'))
    revisions = store.rows('SELECT * FROM revisions WHERE paper_id=? ORDER BY created_at,rowid', (paper['id'],))
    bundles = {}
    for revision in revisions:
        version = folder / 'versions' / revision['id']
        if revision['summary']:
            copy_unchanged(store.root / revision['summary'], version / 'review.json')
        # Include past bundles and replaced images as history, without re-preparing JSON.
        for bundle in sorted((store.root / 'artifacts' / revision['id'] / 'web').glob('*')):
            if not bundle.is_dir():
                continue
            destination = version / 'web' / bundle.name
            for path in sorted(bundle.rglob('*')):
                if path.is_dir():
                    (destination / path.relative_to(bundle)).mkdir(parents=True,exist_ok=True)
                elif path.is_file() and path.name != 'manifest.json':
                    copy_unchanged(path, destination / path.relative_to(bundle))
            manifest = bundle / 'manifest.json'
            if manifest.exists():
                snapshot(destination, 'manifest', json.loads(manifest.read_text()))
            bundles[str(bundle.relative_to(store.root))] = destination
        if revision['image']:
            copy_unchanged(store.root / revision['image'], version / 'api-image.png')

    targets = {}
    latest = revisions[-1] if revisions else None
    active = []
    if latest:
        if latest['summary']:
            targets['review.json'] = folder / 'versions' / latest['id'] / 'review.json'
        source = next((s for s in sources if s['id'] == latest['source_id']), None)
        if source:
            targets['source.pdf'] = folder / 'pdfs' / (source['hash'] + '.pdf')
        active = store.rows('SELECT * FROM graphics WHERE revision_id=? AND active=1 ORDER BY position', (latest['id'],))
        if active:
            original = str(Path(active[0]['json_path']).parent.parent)
            current = bundles.get(original)
        else:
            # Structure-review bundles have Master/plan but no active graphics.
            matches = [v for k, v in bundles.items() if k.startswith('artifacts/' + latest['id'] + '/web/')]
            current = matches[0] if len(matches) == 1 else None
        if current:
            for name in ('master.json', 'plan.json', 'graphics', 'ready_for_web', 'images', 'manifest.json'):
                if (current / name).exists():
                    targets[name] = current / name
    elif sources:
        source = next((s for s in reversed(sources) if s['status'] == 'confirmed'), None)
        if source:
            targets['source.pdf'] = folder / 'pdfs' / (source['hash'] + '.pdf')
    for name in SHORTCUTS:
        shortcut(folder / name, targets.get(name))
    snapshot(folder, 'status', {
        'pmid': paper['pmid'], 'title': paper['title'], 'access': paper['access'],
        'disposition': paper['disposition'], 'blocker': paper['blocker'],
        'latest_revision': latest['id'] if latest else None,
        'progress': latest['status'] if latest else None,
        'graphics': [{k: g[k] for k in ('topic_id', 'status', 'image_path', 'approval')} for g in active],
        'searches': store.rows('SELECT checked_at,result FROM searches WHERE paper_id=? ORDER BY id', (paper['id'],)),
    })
    kind = ''
    if 'master.json' in targets:
        kind = json.loads(targets['master.json'].read_text()).get('paper_type', '')
    text = (f"PMID: {paper['pmid'] or '未設定'}\n{paper['title']}\n論文種別: {kind or '未判定'}\n"
            f"入手状況: {paper['access']} / 処理状況: {latest['status'] if latest else 'PDF・精読待ち'}\n\n"
            'source.pdf: 現在の処理に使う原本PDF\nreview.json: 精読結果\nmaster.json: 論文全体\n'
            'graphics/: 画像ごとのJSON\nready_for_web/: ChatGPT Webへ渡す依頼文\n'
            'images/: 取り込んだPNG（承認状態はmanifest.jsonを確認）\n'
            'versions/: 以前の処理版・JSON・画像\nstatus.json: 取得履歴と現在の状態\n\n'
            'ここは確認用コピーです。元のPDF・JSON・画像・承認履歴は台帳側に保持しています。\n'
            '変更した分割設定は別名保存してメニュー12へ、生成PNGはメニュー13から取り込んでください。\n'
            'ファイルがない工程は未完了です。新しい版の精読待ちには旧版のWeb依頼文を表示しません。\n')
    atomic_write(folder / '案内.txt', text)
    return folder


def sync_library(store):
    root = store.root / 'papers'
    root.mkdir(exist_ok=True)
    marker = root / MARKER
    if not marker.exists():
        if any(root.iterdir()):
            raise ValueError('既存のpapersフォルダは変更しません: ' + str(root))
        atomic_write(marker, encode({'schema': SCHEMA}))
    elif json.loads(marker.read_text()).get('schema') != SCHEMA:
        raise ValueError('論文フォルダの管理情報が一致しません: ' + str(root))
    for paper in store.rows('SELECT * FROM papers ORDER BY added_at'):
        sync_paper(store, paper)
    return root


def expose_library(repo, store):
    """Add owned PMID links alongside any existing files in the visible directory."""
    target = sync_library(store)
    root = Path(repo) / 'papers'
    if root.is_symlink():
        raise ValueError('既存のpapersリンクは変更しません: ' + str(root))
    root.mkdir(exist_ok=True)
    marker = root / MARKER
    previous = json.loads(marker.read_text()) if marker.exists() else {'schema': SCHEMA, 'links': {}}
    if previous.get('schema') != SCHEMA or 'links' not in previous:
        raise ValueError('論文フォルダの管理情報が一致しません: ' + str(root))
    links = dict(previous['links'])
    pending = [(root / p.name, p) for p in target.iterdir() if p.is_dir()]
    # Check every conflict before changing any existing shortcut.
    for link, destination in pending:
        if link.exists() or link.is_symlink():
            if not link.is_symlink() or os.readlink(link) != links.get(link.name):
                raise ValueError('既存ファイルは置き換えません: ' + str(link))
        links[link.name] = os.path.relpath(destination, root)
    for link, destination in pending:
        shortcut(link, destination)
    atomic_write(marker, encode({'schema': SCHEMA, 'links': links}))
    return root
