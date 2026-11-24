"""Render a single self-contained reader. No API or workspace writes."""
from base64 import b64encode
import hashlib
from html import escape
from io import BytesIO
import os
from pathlib import Path
from string import Template
import tempfile

from PIL import Image

from . import reading_guide
from .manual import Workspace
from .reader_data import Bars, paper_from_json

TEMPLATES = Path(__file__).resolve().parents[1] / 'templates/reader'
LABELS = {
    'p': '対象', 'i_or_exposure': '介入・曝露', 'c': '比較', 'o': 'アウトカム',
    'section_id': '章ID', 'title': '見出し', 'location': '原文の位置', 'supporting_text': '原文抜粋',
    'kind': '分類', 'text': '記述', 'population': '対象', 'conditions': '条件', 'exceptions': '例外',
    'agreement': '合意率', 'evidence_strength': '根拠の強さ', 'evidence_references': '出典',
    'id': 'ID', 'statement_ja': '記述', 'source_refs': '出典', 'measurements': '測定結果',
    'source': '出典の種類', 'quote': '原文抜粋', 'pdf_page_start': 'PDF開始ページ',
    'pdf_page_end': 'PDF終了ページ', 'printed_page': '誌面ページ', 'section': '節', 'figure': '図',
    'table': '表', 'citation': '引用', 'reported': '原文の値', 'label': '項目名', 'group': '群',
    'unit': '単位', 'timepoint': '時点', 'denominator': '分母', 'analysis_set': '解析集団',
    'estimate': '推定値', 'ci_lower': '信頼区間下限', 'ci_upper': '信頼区間上限', 'p_value': 'P値',
    'reported_certainty': '論文記載の確実性', 'certainty_system': '確実性の評価法',
}
MISSING = '<span class="missing">未取得</span>'


def value_html(value):
    if value is None or value == '' or value == [] or value == {}:
        return MISSING
    if isinstance(value, Bars):
        rows = ''.join(f'<li>{escape(label)} <meter min="0" max="{value.maximum}" value="{amount}" '
                       f'aria-label="{escape(label)}"></meter> {amount} {escape(value.unit)}</li>'
                       for label, amount in value.values)
        return '<ul class="bars">' + rows + '</ul>'
    if isinstance(value, dict):
        rows = ''.join(f'<tr><th scope="row">{escape(LABELS.get(k, k))}</th><td>{value_html(v)}</td></tr>'
                       for k, v in value.items())
        return f'<div class="table-wrap"><table><tbody>{rows}</tbody></table></div>'
    if isinstance(value, list):
        return '<ul>' + ''.join(f'<li>{value_html(item)}</li>' for item in value) + '</ul>'
    return '<span class="text">' + escape(str(value)) + '</span>'


def png_html(caption, raw):
    with Image.open(BytesIO(raw)) as image:
        if image.format != 'PNG':
            raise ValueError('PNG形式の画像を指定してください')
        image.verify()
    data = b64encode(raw).decode('ascii')
    return (f'<figure><img src="data:image/png;base64,{data}" alt="{escape(caption)}">'
            f'<figcaption>{escape(caption)}</figcaption></figure>')


def render(paper, images=(), image_notes=()):
    if paper.teaching_master is not None:
        from .teaching_reader import render_teaching
        return render_teaching(paper, images, image_notes)
    figures = []
    for label, raw in images:
        try:
            figures.append(png_html(label, raw))
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            figures.append(f'<p>{escape(label)}：PNGを読み込めません</p>')
    figures.extend(f'<p>{escape(note)}</p>' for note in image_notes)
    content = [(title, ''.join(f'<h3>{escape(label)}</h3>{value_html(value)}' for label, value in fields))
               for title, fields in paper.sections]
    content.insert(0 if images else 1, ('グラフィックレコーディング', ''.join(figures) or MISSING))
    sections, toc = [], []
    for index, (title, body) in enumerate(content):
        anchor = f'section-{index + 1}'
        toc.append(f'<li><a href="#{anchor}">{escape(title)}</a></li>')
        opened = ' open' if index < 2 else ''
        sections.append(f'<section id="{anchor}"><details{opened}><summary><h2>{escape(title)}</h2></summary>'
                        f'{body}</details></section>')
    assets = {}
    for key, filename in [('js', 'reader.js'), ('css', 'reader.css')]:
        assets[key] = (TEMPLATES / filename).read_text(encoding='utf-8')
        assets[key + '_hash'] = b64encode(hashlib.sha256(assets[key].encode()).digest()).decode('ascii')
    template = Template((TEMPLATES / 'page.html').read_text(encoding='utf-8'))
    return template.substitute(assets, title=escape(paper.title), pmid=escape(paper.pmid),
                               citation=escape('　·　'.join(paper.citation)), toc=''.join(toc),
                               sections=''.join(sections)).encode('utf-8')


def save_reader(root, paper, images=(), image_notes=(), *, study_plots=False, mechanisms=False):
    filename = 'reader.html'
    if study_plots and mechanisms:
        raise ValueError('研究別グラフと機序図は個別に出力してください')
    if study_plots:
        from .study_plots import render_study_plots
        html = render_study_plots(paper)
        filename = 'reader_study_plots.html'
    elif mechanisms:
        from .mechanisms import render_mechanisms
        html = render_mechanisms(paper)
        filename = 'reader_mechanisms.html'
    else:
        html = render(paper, images, image_notes)
    folder = Path(root) / ('PMID_' + paper.pmid) / hashlib.sha256(html).hexdigest()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as handle:
            tmp = Path(handle.name)
            try:
                handle.write(html)
                handle.flush()
                os.link(tmp, path)
            except FileExistsError:
                pass  # A concurrent export may have published the same reader.
            finally:
                tmp.unlink(missing_ok=True)
    if path.read_bytes() != html:
        raise ValueError('保存先のReaderが変更されています。別の出力先を指定してください')
    return path


def workspace_reader_inputs(workspace, pmid, data):
    masters = [item for item in data['files'] if item['kind'] == 'json']
    if len(masters) != 1 or data.get('structure_wait'):
        raise ValueError('構造確認済みの精読JSONを1つ選んでください')
    master = masters[0]
    cached = reading_guide.metadata(workspace, pmid)
    raw_json = workspace.read_asset(master)
    paper = paper_from_json(raw_json, cached.get('abstract', ''))
    if paper.pmid != pmid:
        raise ValueError('論文と精読JSONのPMIDが一致しません')
    images, notes = [], []
    for graphic in (item for item in data['files'] if item['kind'] == 'graphic'):
        topic = graphic['topic']
        title = graphic.get('topic_title') or topic
        files = [item for item in data['files'] if item.get('topic') == topic
                 and item['kind'] in ('png', 'png_candidate')]
        if graphic.get('basis') != master['sha'] or not Workspace.medically_approved({'files': [graphic, *files]}):
            notes.append(title + '：この版の確認済みPNGは未取得')
            continue
        try:
            raw = workspace.read_asset(files[0])
        except (OSError, ValueError):
            notes.append(title + '：PNGを読み込めません')
            continue
        images.append(('PMID ' + pmid + ' ／ ' + title, raw))
    return paper, images, notes, raw_json


def workspace_reader(repo, workspace, pmid, heads):
    data = workspace.current(pmid, heads)['data']
    paper, images, notes, _ = workspace_reader_inputs(workspace, pmid, data)
    return save_reader(Path(repo) / 'output/readers', paper, images, notes)
