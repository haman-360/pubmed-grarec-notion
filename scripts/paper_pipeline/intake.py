"""Explicit, keyless intake shared by the desktop and the command launcher.

Producers (manual today, download/API adapters later) supply files here; all
accepted assets use Workspace.import_file and its existing revision rules.
"""
from __future__ import annotations

import copy
import fcntl
import json
import re
import tempfile
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

from .manual import pmid_value
from .store import atomic_write, digest, encode, now

PENDING = '00_取り込み待ち'
ARCHIVE = '01_取り込み済み'
PREFIX = re.compile(r'^(?:PMID[_ -]?)?([1-9][0-9]{0,8})(?=[_. -]|$)', re.I)
MARKER = re.compile(r'\bPMID\s*[:：_ -]?\s*([1-9][0-9]{0,8})\b|https://pubmed\.ncbi\.nlm\.nih\.gov/([1-9][0-9]{0,8})/?', re.I)


@contextmanager
def workspace_lock(workspace):
    """Serialize CLI and GUI on this Mac; cross-device forks remain visible."""
    path = Path(tempfile.gettempdir()) / ('pubmed-intake-' + digest(str(workspace.root).encode()) + '.lock')
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def ensure_folders(workspace):
    for name in (PENDING, ARCHIVE):
        workspace.path(name).mkdir(parents=True, exist_ok=True)


def inventory(workspace):
    root = workspace.path(PENDING)
    paths = sorted(p for p in root.rglob('*') if not p.is_dir() and not p.name.startswith('.'))
    return dict(folder=PENDING, pending_files=len(paths), files=[str(p.relative_to(root)) for p in paths])


def parse_pmids(text):
    """Explicit PMID labels, PubMed URLs or number-only lines; never arbitrary numbers."""
    result = []
    for line in text.lstrip('\ufeff').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if re.fullmatch(r'[1-9][0-9]{0,8}', line):
            result.append(line)
        else:
            result.extend(a or b for a, b in MARKER.findall(line))
    return list(dict.fromkeys(result))


def is_list(text):
    lines = [s.strip() for s in text.lstrip('\ufeff').splitlines() if s.strip() and not s.strip().startswith('#')]
    return bool(lines) and all(re.fullmatch(r'[1-9][0-9]{0,8}', s) or MARKER.fullmatch(s) for s in lines)


def _read(path, root, minimum_age):
    if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root.parent):
        raise ValueError('リンクは取り込めません。実ファイルを保存してください')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('取り込み待ちフォルダ外のファイルです')
    before = path.stat()
    if time.time() - before.st_mtime < minimum_age:
        raise ValueError('保存・同期直後です。数秒後にもう一度整理してください')
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('保存・同期中です。完了後にもう一度整理してください')
    return raw


def _path_ids(path, root):
    ids = set()
    for part in path.relative_to(root).parts:
        match = PREFIX.match(part)
        if match:
            ids.add(match[1])
    return ids


def _identify(path, root, raw):
    ids = _path_ids(path, root)
    if path.suffix.lower() == '.json':
        value = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError('Master JSONを保存してください')
        if value.get('request_type') == 'png-generation-request-v1':
            raise ValueError('これはPNG作成依頼です。Webへ渡し、完成したPNGをここに保存してください')
        ids.add(pmid_value(str(value.get('pmid', ''))))
    if len(ids) > 1:
        raise ValueError('フォルダ名・ファイル名・JSONのPMIDが一致しません')
    return next(iter(ids), None)


def _already_seen(workspace, pmid, kind, name, raw):
    topic = Path(name).stem if kind == 'png' else ''
    for event in workspace.events(pmid).values():
        for file in event['data']['files']:
            if (file['kind'] == kind or kind == 'json' and file['kind'] == 'draft') and file['sha'] == digest(raw):
                if kind != 'png' or file.get('topic') == topic:
                    workspace.read_asset(file)
                    return True
    return False


def organize(workspace, minimum_age=2):
    """Caller holds workspace_lock. Successful files only are archived, never deleted."""
    ensure_folders(workspace)
    root = workspace.path(PENDING)
    result = dict(imported=0, archived=0, skipped=0, registered=0, errors=[], at=now())
    entries, lists = [], []
    blocked_ids, blocked_parents = set(), set()
    batch = uuid.uuid4().hex

    def error(path, reason):
        result['errors'].append(str(path.relative_to(root)) + ': ' + str(reason)[:500])

    for relative in inventory(workspace)['files']:
        path = root / relative
        try:
            if path.suffix.lower() not in ('.pdf', '.json', '.png', '.txt'):
                raise ValueError('PDF・JSON・PNG・TXTのみ対応しています')
            raw = _read(path, root, minimum_age)
            pmid = _identify(path, root, raw)
            kind = path.suffix.lower()[1:]
            if kind == 'txt':
                text = raw.decode('utf-8-sig')
                ids = parse_pmids(text)
                if is_list(text) and pmid is None:
                    lists.append((path, raw, ids))
                    continue
                if len(ids) > 1 or pmid and ids and ids != [pmid]:
                    raise ValueError('メモのPMIDが複数、または保存先と一致しません')
                pmid = pmid or (ids[0] if ids else None)
                kind = 'note'
            entries.append(dict(path=path, raw=raw, pmid=pmid, kind=kind))
        except (ValueError, OSError, UnicodeError) as exc:
            error(path, exc)
            if path.suffix.lower() in ('.pdf', '.json'):
                ids = _path_ids(path, root)
                blocked_ids.update(ids)
                if not ids:
                    blocked_parents.add(path.parent)

    # A flat bundle can inherit its one unambiguous PMID. Multiple papers need
    # PMID folders or explicit filenames; unsupported/invalid files stay put.
    peers = defaultdict(set)
    for entry in entries:
        if entry['pmid']:
            peers[entry['path'].parent].add(entry['pmid'])
    groups = defaultdict(list)
    for entry in entries:
        if not entry['pmid'] and len(peers[entry['path'].parent]) == 1:
            entry['pmid'] = next(iter(peers[entry['path'].parent]))
        if entry['kind'] in ('json', 'png') and (entry['pmid'] in blocked_ids or entry['path'].parent in blocked_parents):
            error(entry['path'], '同じ投入先のPDF・JSONに未確認の入力があります。先に修正してください')
        elif not entry['pmid']:
            error(entry['path'], 'PMIDを特定できません。PMID_番号フォルダに入れてください')
        else:
            groups[entry['pmid']].append(entry)

    def archive(path, raw):
        # Unique directory + atomic rename preserves even a concurrently edited
        # input. If it changed, restore it to pending when possible, never unlink it.
        destination = workspace.path(ARCHIVE + '/' + batch + '/' + str(path.relative_to(root)))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path.read_bytes() != raw:
            raise ValueError('取り込み中に変更されました。入力を残しました')
        path.rename(destination)
        if destination.read_bytes() != raw:
            if not path.exists():
                destination.rename(path)
            raise ValueError('取り込み中に変更されました。入力または取り込み済み/' + batch + 'を確認してください')
        result['archived'] += 1

    for path, raw, ids in lists:
        try:
            for pmid in ids:
                if not workspace.heads(pmid):
                    workspace.add(pmid)
                    result['registered'] += 1
            archive(path, raw)
        except (ValueError, OSError) as exc:
            error(path, exc)

    for pmid, items in sorted(groups.items()):
        try:
            # Reject competing new versions before changing this paper.
            candidates = defaultdict(set)
            for item in items:
                kind, path = item['kind'], item['path']
                name = PREFIX.sub('', path.stem).lstrip('_. -') + path.suffix.lower() if kind == 'png' else path.name
                item['name'] = name
                if kind != 'note' and not _already_seen(workspace, pmid, kind, name, item['raw']):
                    candidates[(kind, name if kind == 'png' else '')].add(digest(item['raw']))
            if any(len(values) > 1 for values in candidates.values()):
                raise ValueError('同じ種類・テーマの候補が複数あります。採用する1ファイルを残してください')
            if workspace.heads(pmid):
                current = workspace.current(pmid)['data']['files']
                for item in items:
                    kind = item['kind']
                    downstream = ('json', 'png') if kind == 'pdf' else ('png',) if kind == 'json' else ()
                    if downstream and _already_seen(workspace, pmid, kind, item['name'], item['raw']):
                        is_current = any(f['kind'] == kind and f['sha'] == digest(item['raw']) for f in current)
                        if not is_current and any(k[0] in downstream for k in candidates):
                            raise ValueError('古いPDF・JSONに新しい成果物が混在しています。現在の版との対応を確認してください')
            if not workspace.heads(pmid):
                workspace.add(pmid)
                result['registered'] += 1
            workspace.current(pmid)
        except (ValueError, OSError) as exc:
            for item in items:
                error(item['path'], exc)
            continue
        blocked = False
        for item in sorted(items, key=lambda e: ('pdf', 'json', 'png', 'note').index(e['kind'])):
            path, raw, kind, name = item['path'], item['raw'], item['kind'], item['name']
            try:
                if blocked and kind in ('json', 'png'):
                    raise ValueError('同じ論文のPDF・JSONを取り込めなかったため保留しました')
                event = workspace.current(pmid)
                if _already_seen(workspace, pmid, kind, name, raw):
                    result['skipped'] += 1
                elif kind == 'note':
                    data = copy.deepcopy(event['data'])
                    data['files'].append(workspace.asset(pmid, 'note', name, raw))
                    data['note'] = (data.get('note', '') + '\n作業メモ: ' + raw.decode('utf-8-sig')).strip()
                    workspace.record(pmid, data, [event['id']])
                    result['imported'] += 1
                else:
                    workspace.import_file(pmid, kind, name, raw, [event['id']])
                    result['imported'] += 1
                archive(path, raw)
            except Exception as exc:
                error(path, exc)
                if kind in ('pdf', 'json'):
                    blocked = True
    result['pending_files'] = inventory(workspace)['pending_files']
    atomic_write(workspace.path('状況/' + workspace.device_id + '/取り込み結果.json'), encode(result))
    return result


def result_message(result):
    return (f"整理完了：新規PMID {result['registered']}件、取り込み {result['imported']}ファイル、"
            f"取り込み済みへ移動 {result['archived']}ファイル（既存 {result['skipped']}）。"
            f"未整理 {result['pending_files']}ファイル。")
