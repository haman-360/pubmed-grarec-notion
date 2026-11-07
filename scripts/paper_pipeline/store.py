from __future__ import annotations

import csv
import fcntl
import hashlib
import json
import math
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else encode(value).encode()).hexdigest()


def doi_key(value):
    value = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.startswith(prefix):
            value = value[len(prefix):].strip()
    return value


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(content if isinstance(content, bytes) else content.encode())
    temporary.replace(path)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "ledger.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS papers (
          id TEXT PRIMARY KEY, pmid TEXT UNIQUE, doi TEXT UNIQUE, title TEXT NOT NULL,
          metadata TEXT NOT NULL, added_at TEXT NOT NULL, disposition TEXT NOT NULL DEFAULT 'active',
          access TEXT NOT NULL DEFAULT 'unsearched', blocker TEXT NOT NULL DEFAULT '',
          page_id TEXT, legacy INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sources (
          id TEXT PRIMARY KEY, paper_id TEXT REFERENCES papers(id), hash TEXT NOT NULL,
          path TEXT NOT NULL, original TEXT NOT NULL, status TEXT NOT NULL, details TEXT NOT NULL,
          created_at TEXT NOT NULL, UNIQUE(paper_id,hash));
        CREATE TABLE IF NOT EXISTS revisions (
          id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id),
          source_id TEXT NOT NULL REFERENCES sources(id), signature TEXT NOT NULL,
          status TEXT NOT NULL, config TEXT NOT NULL, summary TEXT, image TEXT,
          error TEXT NOT NULL DEFAULT '', notion TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts (
          id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES revisions(id), stage TEXT NOT NULL,
          status TEXT NOT NULL, batch_id TEXT, input_file_id TEXT, reserve REAL NOT NULL,
          actual REAL, usage TEXT, period TEXT NOT NULL, details TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS searches (
          id INTEGER PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id),
          checked_at TEXT NOT NULL, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS legacy (path TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')

    def close(self):
        self.db.close()

    @contextmanager
    def lock(self):
        with (self.root / "run.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("別の処理が実行中です。終了してから再実行してください。")
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def rows(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args)]

    def one(self, table, identifier):
        rows = self.rows(f"SELECT * FROM {table} WHERE id=?", (identifier,))
        if not rows:
            raise ValueError(f"Unknown {table}: {identifier}")
        return rows[0]

    def update(self, table, identifier, **values):
        with self.db:
            self.db.execute(f"UPDATE {table} SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?",
                            (*values.values(), identifier))

    def setting(self, key, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, encode(value)))

    def paper(self, pmid="", doi="", title="", metadata=None):
        pmid, doi = str(pmid or "").strip(), doi_key(doi)
        if pmid and not pmid.isdigit():
            raise ValueError("PMID must contain digits only")
        matches = self.rows("SELECT * FROM papers WHERE (pmid=? AND ?!='') OR (doi=? AND ?!='')",
                            (pmid, pmid, doi, doi))
        if len(matches) > 1:
            raise ValueError("PMIDとDOIが異なる既存論文を指します。自動統合しません。")
        if matches:
            p = matches[0]
            if (pmid and p['pmid'] and pmid != p['pmid']) or (doi and p['doi'] and doi != p['doi']):
                raise ValueError("論文識別子が既存記録と矛盾しています。")
            meta = json.loads(p['metadata'])
            meta.update({k: v for k, v in (metadata or {}).items() if v})
            self.update("papers", p['id'], pmid=pmid or p['pmid'], doi=doi or p['doi'],
                        title=title or p['title'], metadata=encode(meta))
            return self.one("papers", p['id'])
        identifier = uid()
        with self.db:
            self.db.execute("INSERT INTO papers(id,pmid,doi,title,metadata,added_at) VALUES(?,?,?,?,?,?)",
                            (identifier, pmid or None, doi or None, title, encode(metadata or {}), now()))
        return self.one("papers", identifier)

    def resolve(self, value):
        rows = self.rows("SELECT * FROM papers WHERE id=? OR pmid=? OR doi=?", (value, value, doi_key(value)))
        if len(rows) != 1:
            raise ValueError("論文を一意に特定できません。内部IDを指定してください。")
        return rows[0]

    def import_pmids(self, path):
        seen_data = False
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            if not seen_data and value.casefold() == 'pmid':
                continue
            if not value.isdigit():
                raise ValueError(f"PMID以外の行があります: {value[:60]}")
            self.paper(pmid=value)
            seen_data = True

    def pending_search(self):
        return self.rows("SELECT * FROM papers WHERE disposition='active' AND legacy=0 AND access!='available'")

    def source(self, path, paper_id=None, status="confirmed", details=None):
        path = Path(path)
        content = path.read_bytes()
        sha = digest(content)
        if paper_id and self.rows('SELECT id FROM sources WHERE hash=? AND paper_id IS NOT NULL AND paper_id!=?',(sha,paper_id)):
            raise ValueError('同じPDFが別の論文に紐付いています。識別を確認してください。')
        matches = self.rows("SELECT * FROM sources WHERE hash=? AND (paper_id=? OR paper_id IS NULL)", (sha, paper_id))
        if matches:
            s = matches[0]
            if paper_id:
                if self.rows("SELECT id FROM sources WHERE paper_id=? AND id!=? AND status='confirmed'",(paper_id,s['id'])):
                    status = 'revision_review'
                self.update("sources", s['id'], paper_id=paper_id, status=status, details=encode(details or {}))
                self.update("papers", paper_id, access="available", blocker='' if status=='confirmed' else status)
            return self.one("sources", s['id'])
        identifier = uid()
        relative = f"pdfs/{sha}.pdf"
        atomic_write(self.root / relative, content)
        if paper_id and self.rows("SELECT id FROM sources WHERE paper_id=? AND status='confirmed'", (paper_id,)):
            status = "revision_review"
        with self.db:
            self.db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)",
                            (identifier, paper_id, sha, relative, str(path.resolve()), status, encode(details or {}), now()))
        if paper_id:
            self.update("papers", paper_id, access="available", blocker="" if status == "confirmed" else status)
        return self.one("sources", identifier)

    def revision(self, paper, source, config, force=False, summary=None):
        signature = digest({"source": source['hash'], "config": config})
        existing = self.rows("SELECT * FROM revisions WHERE paper_id=? AND signature=? ORDER BY created_at DESC",
                             (paper['id'], signature))
        if existing and not force:
            return existing[0]
        identifier = uid()
        with self.db:
            self.db.execute("INSERT INTO revisions(id,paper_id,source_id,signature,status,config,summary,created_at) VALUES(?,?,?,?,?,?,?,?)",
                            (identifier, paper['id'], source['id'], signature, 'image_ready' if summary else 'analysis_ready',
                             encode(config), summary, now()))
        return self.one("revisions", identifier)

    def reserve(self, revision, stage, amount):
        budget = self.setting("budget", {})
        current = datetime.now(timezone.utc)
        try:
            start = datetime.fromisoformat(budget['start'])
            end = datetime.fromisoformat(budget['end'])
            checked = datetime.fromisoformat(budget['checked_at'])
            remaining = float(budget['remaining']) - float(budget.get('other_usage', 0))
            if not all(math.isfinite(x) and x >= 0 for x in (remaining, amount)):
                raise ValueError("金額が不正です")
            if not (start <= current < end - timedelta(hours=50)):
                raise ValueError("請求期間外または有効期限まで50時間未満です")
            if checked > current or current - checked > timedelta(hours=24):
                raise ValueError("残額の確認が24時間以上前です")
            if not budget.get('no_auto_recharge') or not budget.get('purchased_checked'):
                raise ValueError("自動チャージ・購入済み残高の確認が必要です")
        except (KeyError, TypeError) as error:
            raise ValueError("budget コマンドで残額・期間を設定してください") from error
        period = budget['start']
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            attempts = self.rows("SELECT * FROM attempts")
            cost = lambda a: float(a['actual'] if a['actual'] is not None else a['reserve'])
            period_total = sum(cost(a) for a in attempts if a['period'] == period or a['actual'] is None)
            since_check = sum(cost(a) for a in attempts if a['created_at'] >= budget['checked_at'] or a['actual'] is None)
            revision_total = sum(cost(a) for a in attempts if a['revision_id'] == revision['id'])
            trial_total = sum(cost(a) for a in attempts)
            limit = 80.0 if budget.get('production') else 20.0
            if period_total + amount > limit or since_check + amount > remaining or revision_total + amount > 4:
                raise ValueError("予算上限に達するため新規Batchを停止しました")
            if not budget.get('production'):
                papers = {r['paper_id'] for r in self.rows("SELECT DISTINCT r.paper_id FROM attempts a JOIN revisions r ON a.revision_id=r.id")}
                if trial_total + amount > 20 or (revision['paper_id'] not in papers and len(papers) >= 5):
                    raise ValueError("初回5論文/$20の評価枠を超えます")
            identifier = uid()
            self.db.execute("INSERT INTO attempts(id,revision_id,stage,status,reserve,period,created_at) VALUES(?,?,?,?,?,?,?)",
                            (identifier, revision['id'], stage, 'reserved', amount, period, now()))
        return self.one("attempts", identifier)

    def export(self, path):
        rows = self.rows('''SELECT p.*, (SELECT checked_at FROM searches s WHERE s.paper_id=p.id ORDER BY id DESC LIMIT 1) last_checked,
          (SELECT result FROM searches s WHERE s.paper_id=p.id ORDER BY id DESC LIMIT 1) search_result,
          (SELECT status FROM revisions r WHERE r.paper_id=p.id ORDER BY created_at DESC LIMIT 1) progress
          FROM papers p ORDER BY added_at''')
        fields = list(rows[0]) if rows else ['id', 'pmid', 'title', 'access', 'progress']
        with Path(path).open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: ("'" + str(v) if str(v).startswith(('=', '+', '-', '@')) else v) for k,v in row.items()})

    def backup(self, destination):
        destination = Path(destination).resolve()
        if destination == self.root or self.root in destination.parents or destination.exists():
            raise ValueError("バックアップ先は管理領域外の新しいフォルダにしてください")
        destination.mkdir(parents=True)
        with sqlite3.connect(destination / 'ledger.sqlite3') as target:
            self.db.backup(target)
        for path in self.root.iterdir():
            if path.is_dir():
                shutil.copytree(path, destination / path.name)
        atomic_write(destination / 'BACKUP.txt', '停止中にこのフォルダ全体を新しい管理領域へコピーして復元できます。\n')
