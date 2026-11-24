"""Build an immutable teaching bundle from the selected, verified pilot edition."""
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from import_chatgpt_summary import strip_markdown_json_fence

from .mechanisms import MechanismDiagrams, render_mechanisms
from .manual import pmid_value
from .reader import TEMPLATES, render as render_reader, workspace_reader_inputs
from .reader_data import paper_from_json
from .store import atomic_write, digest, encode
from .study_plots import ForestPlots, render_study_plots
from .teaching_png import png_exports
from .visual_reader import package_visual_reader

PMID = '42526604'
MASTER_SHA = 'a547585cb64bcfb745d9853b1ad4cd5ea595ace7f6d94af1e0f49635d3a50f5f'
EDITION = ('output/learning-verification/PMID_42526604/'
           'a424445fdf00ad5cca339fee968653dbcb88b1a5f836e2aa74d10b6e28868105/'
           'editions/d24698c24ab1887ab2db659f53f3d1b56c04167dc31e09fd9f6b54c74eac91a9')
DEST = '教材/PMID_' + PMID
HTML = 'reader_materials.html'
PDF = 'PMID_' + PMID + '.pdf'
PILOT_PDF = 'input/pdfs/42526604_Use electronic cigarettes asthma across lifespan.pdf'


def render(paper):
    master = paper.teaching_master
    graphs = ForestPlots(master, bool(paper.original_pdf))
    mechanisms = MechanismDiagrams(master, bool(paper.original_pdf))
    panels = master['visualizations']
    css = (TEMPLATES/'study_plots.css').read_text() + '\n' + (TEMPLATES/'mechanisms.css').read_text()
    js = (TEMPLATES/'study_plots.js').read_text() + '\n' + (TEMPLATES/'mechanisms.js').read_text()
    js += '\n' + (TEMPLATES/'materials.js').read_text()
    css += '\n' + (TEMPLATES/'materials.css').read_text()
    return package_visual_reader(
        paper, 'materials', ''.join(graphs.panel(p) for p in panels if p['type']=='forest_plot'),
        mechanisms=''.join(mechanisms.panel(p) for p in panels if p['type']=='mechanism_diagram'),
        symbols=(TEMPLATES/'mechanism_symbols.svg').read_text(), css=css, js=js)


def destination(pmid, route='master'):
    root = '教材/PMID_'+pmid_value(pmid)
    if route == 'existing':
        return root+'/既存JSON版'
    if route != 'master':
        raise ValueError('教材の作成方法を選び直してください')
    return root


def current(workspace, pmid=PMID, route='master'):
    path = workspace.path(destination(pmid, route)+'/current.json')
    if not path.exists():
        return {}
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or not re.fullmatch('[a-f0-9]{64}', str(value.get('version', ''))):
        raise ValueError('教材の版を確認できません。教材一式を作り直してください')
    return value


def checked_folder(workspace, version, pmid=PMID, route='master'):
    dest = destination(pmid, route)
    if not re.fullmatch('[a-f0-9]{64}', str(version)) or current(workspace, pmid, route).get('version') != version:
        raise ValueError('教材の版が変わりました。画面を更新してください')
    folder = workspace.path(dest+'/'+version)
    hashes = json.loads((folder/'manifest.json').read_bytes())
    if digest(hashes) != version or (route == 'master' and pmid == PMID and hashes.get('master.json') != MASTER_SHA):
        raise ValueError('教材のファイル一覧と版が一致しません')
    for name, sha in hashes.items():
        if Path(name).name != name or digest(workspace.path(dest+'/'+version+'/'+name).read_bytes()) != sha:
            raise ValueError('未同期または変更された教材ファイルがあります')
    return folder


def generate(repo, workspace):
    edition = Path(repo)/EDITION
    local_master = edition/'master.json'
    local_pdf = Path(repo)/PILOT_PDF
    if not local_master.is_file() or not local_pdf.is_file():
        version = current(workspace).get('version')
        if not version:
            raise ValueError('PMID 42526604の検証版Masterと原本PDFを保存したMacで初回の教材を作成してください')
        saved = checked_folder(workspace, version)
        local_master, local_pdf = saved/'master.json', saved/PDF
    return publish(workspace, PMID, local_master.read_bytes(), local_pdf.read_bytes())


def publish(workspace, pmid, raw, pdf):
    paper = paper_from_json(raw)
    if paper.pmid != pmid or not paper.teaching_master or (pmid == PMID and digest(raw) != MASTER_SHA):
        raise ValueError('指定した教材版とMasterが一致しません')
    if digest(pdf) != paper.teaching_master['metadata']['pdf_sha256']:
        raise ValueError('教材の原本PDFが一致しません')
    if any(v['status'] == '要修正' for v in paper.teaching_master['teaching']['quality'].values()):
        raise ValueError('教材品質記録に要修正の項目があります。確認した版を使ってください')
    paper.original_pdf = pdf
    kinds = {p['type'] for p in paper.teaching_master['visualizations']}
    if {'forest_plot','mechanism_diagram'} <= kinds:
        html = render(paper)
    elif kinds == {'forest_plot'}:
        html = render_study_plots(paper)
    elif kinds == {'mechanism_diagram'}:
        html = render_mechanisms(paper)
    else:
        html = render_reader(paper)
    files = {'master.json':raw, f'PMID_{pmid}.pdf':pdf, HTML:html, **png_exports(paper.teaching_master)}
    return save_bundle(workspace, pmid, 'master', files)


def publish_existing(workspace, pmid, event, pdf, notion):
    paper, images, notes, raw = workspace_reader_inputs(workspace, pmid, event['data'])
    if paper.teaching_master:
        raise ValueError('教材用MasterはPDF精読版の作成方法を選んでください')
    data = json.loads(strip_markdown_json_fence(raw.decode('utf-8-sig')))
    meta = data['metadata'] if data.get('schema_version') == 'learning-v2' else {k.lower():v for k,v in data.items()}
    metadata = dict(pmid=paper.pmid, title=paper.title, doi=meta.get('doi') or '',
                    journal=meta.get('journal'), year=meta.get('year'))
    paper.citation.insert(0, '既存グラレコJSON版')
    files = {'source.json': raw, 'metadata.json': encode(metadata).encode(), HTML:render_reader(paper, images, notes)}
    if pdf:
        source = next(f for f in event['data']['files'] if f['kind']=='json')
        if source.get('basis') and source['basis'] != pdf['sha']:
            raise ValueError('既存JSONと原本PDFの版が一致しません')
        files[f'PMID_{pmid}.pdf'] = workspace.read_asset(pdf)
    return save_bundle(workspace, pmid, 'existing', files, notion=notion, has_pdf=bool(pdf), source_sha=digest(raw))


def save_bundle(workspace, pmid, route, files, **settings):
    hashes = {name:digest(content) for name, content in files.items()}
    version = digest(hashes)
    root = workspace.path(destination(pmid, route))
    root.mkdir(parents=True, exist_ok=True)
    folder = root/version
    if not folder.exists():
        with TemporaryDirectory(dir=root) as temp:
            staged = Path(temp)/'bundle'
            staged.mkdir()
            for name, content in files.items():
                (staged/name).write_bytes(content)
            (staged/'manifest.json').write_text(encode(hashes))
            staged.rename(folder)
    if json.loads((folder/'manifest.json').read_bytes()) != hashes or any(
            digest((folder/name).read_bytes()) != sha for name, sha in hashes.items()):
        raise ValueError('保存済みの教材版と内容が一致しません')
    previous = current(workspace, pmid, route)
    record = previous.copy() if previous.get('version') == version else {
        'version':version, 'requests':sum(n.startswith('png-request-') for n in files)}
    record.update(settings)
    if record != previous:
        atomic_write(root/'current.json', encode(record))
    return folder
