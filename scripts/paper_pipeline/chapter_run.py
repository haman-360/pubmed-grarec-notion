"""Local, resumable handoff of source-bound chapter packets."""
from contextlib import contextmanager
import fcntl
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from import_chatgpt_summary import strip_markdown_json_fence
from .chapter_plan import chapter_plan, source_window, write_excerpt
from .chapter_review import INSTRUCTIONS, RESULT_SCHEMA, VERSION, MASTER, build_master, packet_key, prompt, validate_result
from .manual import pmid_value
from .reader import render
from .reader_data import paper_from_json
from .store import atomic_write, digest


def dump(data):
    return json.dumps(data, ensure_ascii=False, indent=2) + '\n'


@contextmanager
def locked(folder):
    with (folder / '.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def prepare(pdf, output, *, pmid, title, article_type, headings=None, max_pages=10, max_chars=32000,
            journal=None, year=None, doi=None):
    pmid_value(pmid)
    if article_type not in MASTER['properties']['article_type']['enum']:
        raise ValueError('論文種別が未対応です')
    if year is not None and (len(year) != 4 or not year.isascii() or not year.isdecimal()):
        raise ValueError('出版年は西暦4桁で指定してください')
    raw = Path(pdf).read_bytes()
    reader = PdfReader(io.BytesIO(raw))
    mode, jobs, texts = chapter_plan(reader, headings, max_pages, max_chars)
    title = (title or (reader.metadata and reader.metadata.title) or '').strip()
    if not title:
        raise ValueError('PDFに書誌タイトルがありません。--title を指定してください')
    for job in jobs:
        job['text_sha256'] = digest(source_window(texts, job))
    plan = dict(version=VERSION, protocol_sha256=digest([INSTRUCTIONS, RESULT_SCHEMA]),
                metadata=dict(pmid=pmid, title=title, journal=journal, year=year, doi=doi, pdf_sha256=digest(raw)),
                article_type=article_type, split_method=mode, pages=len(texts), max_pages=max_pages,
                max_chars=max_chars, jobs=jobs)
    run_id = digest(plan)
    folder = Path(output) / ('PMID_' + pmid) / run_id
    folder.mkdir(parents=True, exist_ok=True)
    with locked(folder):
        manifest = folder / 'manifest.json'
        if manifest.exists() and json.loads(manifest.read_text()) != plan:
            raise ValueError('既存の作業計画が一致しません')
        for job in jobs:
            packet = folder / job['id']
            assets = ('source.pdf', 'source.sha256', 'request.txt', 'response-template.json', 'pages.json')
            if all((packet / name).is_file() for name in assets):
                continue
            with TemporaryDirectory(dir=folder) as temp:
                staging = Path(temp) / 'packet'
                staging.mkdir()
                write_excerpt(reader, job, staging / 'source.pdf')
                (staging / 'pages.json').write_text(dump(source_window(texts, job)), encoding='utf-8')
                template = dict(job_key=packet_key(run_id, job), chapter_id=job['id'], method='manual_local',
                                title_ja='', summary_ja='', sources=[dict(page=job['start'], quote='')],
                                findings=[dict(kind='author_statement', role='finding', statement_ja='', source_numbers=[1])])
                (staging / 'response-template.json').write_text(dump(template), encoding='utf-8')
                (staging / 'request.txt').write_text(prompt(run_id, job), encoding='utf-8')
                (staging / 'source.sha256').write_text(digest((staging / 'source.pdf').read_bytes()))
                if packet.exists():
                    # Restore missing generated inputs without touching the reader's response or checkpoint.
                    for name in assets:
                        if not (packet / name).exists():
                            (staging / name).rename(packet / name)
                else:
                    staging.rename(packet)
        if not manifest.exists():
            atomic_write(manifest, dump(plan))
    return folder


def read_packet(folder, job):
    packet = folder / job['id']
    texts = json.loads((packet / 'pages.json').read_text(encoding='utf-8'))
    if digest(texts) != job['text_sha256']:
        raise ValueError('原ページの抽出記録が変更されています')
    return texts


def accept(folder, job, confirm, revise):
    packet = folder / job['id']
    checkpoint = packet / 'accepted.json'
    if checkpoint.exists() and not revise:
        receipt = json.loads(checkpoint.read_text(encoding='utf-8'))
        if digest(receipt['result']) != receipt['sha256']:
            raise ValueError('受理済み結果が変更されています。response.json を確認し --revise で再取込してください')
        result = receipt['result']
        validate_result(result, folder.name, job, read_packet(folder, job), True)
        return result
    response = packet / 'response.json'
    if not response.exists():
        if revise:
            raise ValueError('再取込に必要な response.json がありません')
        return None
    if digest((packet / 'source.pdf').read_bytes()) != (packet / 'source.sha256').read_text():
        raise ValueError('抜粋PDFが準備時から変更されています')
    result = json.loads(strip_markdown_json_fence(response.read_text(encoding='utf-8-sig')))
    validate_result(result, folder.name, job, read_packet(folder, job), confirm)
    atomic_write(checkpoint, dump(dict(sha256=digest(result), result=result)))
    return result


def resume(folder, confirm_visual=(), revise=()):
    folder = Path(folder).resolve()
    with locked(folder):
        plan = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        if folder.name != digest(plan) or plan['version'] != VERSION or plan['protocol_sha256'] != digest([INSTRUCTIONS, RESULT_SCHEMA]):
            raise ValueError('原本・分割設定・精読形式が一致しません。prepare の出力先を確認してください')
        ids = [job['id'] for job in plan['jobs']]
        if ids != [f'ch{i:04d}' for i in range(1, len(ids)+1)] or (set(confirm_visual) | set(revise)) - set(ids):
            raise ValueError('章IDが作業計画と一致しません')
        results, pending, errors = [], [], {}
        for job in plan['jobs']:
            key = job['id']
            try:
                result = accept(folder, job, key in confirm_visual, key in revise)
                if result is None:
                    pending.append(key)
                else:
                    results.append(result)
            except (ValueError, OSError, KeyError, TypeError, PyPdfError) as error:
                errors[key] = str(error)
        report = dict(total=len(ids), accepted=len(results), pending=pending, errors=errors)
        if pending or errors:
            return report
        master = dump(build_master(plan, results, folder.name)).encode()
        html = render(paper_from_json(master))
        # A revised reading or renderer gets a separate, atomically published pair.
        output = folder / 'editions' / digest([digest(master), digest(html)])
        output.parent.mkdir(exist_ok=True)
        if not output.exists():
            with TemporaryDirectory(dir=output.parent) as temp:
                staging = Path(temp) / 'edition'
                staging.mkdir()
                (staging / 'master.json').write_bytes(master)
                (staging / 'reader.html').write_bytes(html)
                staging.rename(output)
        elif (output / 'master.json').read_bytes() != master or (output / 'reader.html').read_bytes() != html:
            raise ValueError('統合済みファイルが変更されています。保存先を確認してください')
        report.update(master=str(output / 'master.json'), reader=str(output / 'reader.html'))
        return report
