"""Read the current paper and teaching receipts for the Mac dashboard."""
import json
from collections import defaultdict

from . import materials
from .store import digest


def annotate(workspace, state):
    editions = defaultdict(list)
    for job in state.get('teaching_jobs', []):
        editions[job['pmid']].append(job)
    pilot = state.get('materials', {})
    if pilot.get('version') and not any(j.get('route') != 'existing' for j in editions[materials.PMID]):
        editions[materials.PMID].append(pilot)
    for paper in state['papers']:
        pmid = paper['pmid']
        master = pilot if pmid == materials.PMID else state.get('teaching_kits', {}).get(pmid, {})
        paper['artifacts'] = paper_status(workspace, paper, editions[pmid], master)


def paper_status(workspace, paper, editions, master):
    pmid = paper['pmid']
    files = paper['files']
    available = [f for f in files if f['name'] not in paper['missing']]
    pdf = next((f for f in files if f['kind'] == 'pdf'), {})
    summary = next((f for f in files if f['kind'] == 'json'), {})
    has_pdf = pdf in available
    has_json = summary in available and not paper.get('structure_wait')
    topics = {f['topic']: f['sha'] for f in files if f['kind'] == 'graphic'}
    saved = {f['topic'] for f in available if f['kind'] in ('png', 'png_candidate')
             and topics.get(f['topic']) == f.get('basis') and f.get('review', {}).get('status') != 'rejected'}
    png_registered = paper['category'] == 'completed'
    png_label = '登録済み' if png_registered else (f'{len(saved)}/{len(topics)}枚保存' if topics else '未登録')
    html_pages, pdf_pages = set(), set()
    png_pages = set()
    if png_registered and paper.get('notion_page'):
        png_pages.add(paper['notion_page'].replace('-', '').lower())
    current_versions = []
    master_json = False
    for job in editions:
        version = job.get('version')
        if not version:
            continue
        route = job.get('route', 'master')
        folder = workspace.path(materials.destination(pmid, route)) / version
        try:
            manifest = json.loads((folder/'manifest.json').read_bytes())
        except (OSError, ValueError):
            continue  # A publication receipt can arrive before the edition syncs.
        pdf_name = f'PMID_{pmid}.pdf'
        pdf_hash = manifest.get(pdf_name)
        if pdf and pdf_hash != pdf['sha']:
            continue
        if route == 'existing' and (manifest.get('source.json') != summary.get('sha') or not summary):
            continue
        if digest(manifest) != version or not manifest.get(materials.HTML):
            continue
        current_versions.append(version)
        has_pdf |= bool(pdf_hash and (folder/pdf_name).is_file())
        master_json |= route == 'master' and (folder/'master.json').is_file()
        page = job.get('page_id')
        if page:
            page = page.replace('-', '').lower()
            html_pages.add(page)
            if pdf_hash:
                pdf_pages.add(page)
        if route == 'master' and page and master.get('version') == version:
            images = master.get('images', [])
            count = sum(bool(image.get('block_id')) for image in images)
            if images and count == len(images) == master.get('requests'):
                png_registered = True
                png_pages.add(page)
                png_label = '登録済み'
            elif not png_registered and count:
                png_label = f'教材PNG {count}/{len(images)}枚登録'
    html_registered = bool(html_pages)
    made = bool(current_versions)
    if png_registered and html_registered:
        same_page = bool(pdf_pages & png_pages) if png_pages else bool(pdf_pages)
        group = 'complete' if same_page and len(paper['heads']) == 1 else 'both'
    elif png_registered:
        group = 'png'
    elif html_registered:
        group = 'html'
    else:
        group = 'neither'
    html_label = '登録済み' if html_registered else '作成済み・未登録' if made else '未作成'
    if not made and editions:
        html_label = '旧版・同期要確認' if any(j.get('version') for j in editions) else '依頼中・保留'
    return dict(group=group, requested=bool(editions), made=made, versions=current_versions,
                pending=any(not j.get('version') for j in editions),
                pdf='登録済み' if pdf_pages else '保存済み' if has_pdf else '未取得',
                json='グラレコ・Master' if has_json and master_json else '教材Master' if master_json else '保存済み' if has_json else '未作成',
                png=png_label, html=html_label, page_id=next(iter(sorted(html_pages)), paper.get('notion_page')))
