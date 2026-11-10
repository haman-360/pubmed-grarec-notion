"""Locate the existing ledger without creating a second, empty workspace."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .store import atomic_write, encode


def location_file(repo):
    return Path(repo) / 'output' / 'app_state.json'


def resolve_path(repo, value):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else Path(repo) / path).resolve()


def select_state_dir(repo, configured=None):
    repo = Path(repo).resolve()
    if configured:
        return resolve_path(repo, configured)
    saved = location_file(repo)
    if saved.exists():
        path = resolve_path(repo, json.loads(saved.read_text())['state_dir'])
        if not (path / 'ledger.sqlite3').is_file():
            raise ValueError('前回の保存先が見つかりません。保存フォルダを戻すか --state-dir で指定してください。')
        return path
    candidates = []
    for path in (repo / '.paper_pipeline', repo / '.paper_pipeline-pilot'):
        database = path / 'ledger.sqlite3'
        if not database.exists():
            continue
        # Read-only inspection: discovery never creates or migrates a database.
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if any(db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()
                   for table in ('papers', 'attempts', 'settings') if table in tables):
                candidates.append(path)
    if len(candidates) > 1:
        raise ValueError('複数の保存記録があります。最初の1回だけ --state-dir <保存先> menu で使用する記録を指定してください。既存記録は統合・削除しません。')
    return candidates[0] if candidates else repo / '.paper_pipeline'


def remember_state_dir(repo, root):
    repo, root = Path(repo).resolve(), Path(root).resolve()
    value = str(root.relative_to(repo)) if root.is_relative_to(repo) else str(root)
    atomic_write(location_file(repo), encode({'state_dir': value}))
