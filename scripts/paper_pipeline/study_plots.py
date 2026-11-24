"""Offline, interactive forest plots projected from validated learning-v3 panels."""
from html import escape
from math import log

from .visual_reader import package_visual_reader, source_excerpt


def details_for_point(item, value, fact, study, refs, has_pdf):
    fields = [('研究デザイン', study['design']), ('対象', study['population']),
              ('曝露・介入', study['exposure']), ('対照', study.get('comparator')),
              ('アウトカム', study['outcome']), ('調整因子', study.get('adjustments')),
              ('測定群', value.get('group')), ('評価時点', value.get('timepoint')),
              ('解析集団', value.get('analysis_set')), ('分母', value.get('denominator')),
              ('条件', '／'.join(fact.get('conditions', []))),
              ('限界・例外', '／'.join(fact.get('exceptions', [])))]
    description = ''.join(f'<dt>{key}</dt><dd>{escape(text)}</dd>' for key, text in fields if text)
    sources = ''.join(source_excerpt(refs[key], has_pdf, value['reported']) for key in value['source_refs'])
    related = ''.join(source_excerpt(refs[key], has_pdf) for key in fact['source_refs']
                      if key not in value['source_refs'])
    if related:
        related = '<details><summary>この研究についての追加出典</summary>'+related+'</details>'
    title = escape(study['reference']+' ／ '+item['label'])
    return (f'<dialog aria-label="{title}" data-measurement="{escape(value["id"])}">'
            f'<div class="drawer-head"><h3>{title}</h3><button type="button" class="close" autofocus>図に戻る</button></div>'
            f'<div class="drawer-body"><p class="identifier">{escape(fact["id"])} ／ {escape(value["id"])}</p>'
            f'<h4>選択した測定値</h4><p class="reported">{escape(value["reported"])}</p>'
            f'<h4>測定値の原文</h4>{sources}<h4>研究詳細</h4><dl>{description}</dl>{related}</div></dialog>')


class ForestPlots:
    """Resolve plot items by their measurement IDs; preserve source and study ownership."""

    def __init__(self, master, has_pdf):
        self.studies = master['teaching']['studies']
        self.refs = {entry['id']: entry for entry in master['source_references']}
        self.values = {value['id']: (value, fact) for fact in master['evidence']
                       for value in fact.get('measurements', [])}
        self.has_pdf = has_pdf

    def panel(self, panel):
        rows = [(item, *self.values[item['measurement_ids'][0]]) for item in panel['items']]
        low = min(0.8, *(float(value['ci_lower']) for _, value, _ in rows))
        high = max(1.25, *(float(value['ci_upper']) for _, value, _ in rows))
        left, right = log(low), log(high)

        def x(value):
            return round(20 + 280 * (log(float(value)) - left) / (right - left), 2)

        null = x(1)
        buttons = []
        for item, value, fact in rows:
            a, b, point = (x(value[k]) for k in ('ci_lower', 'ci_upper', 'estimate'))
            number = escape(value['label']+' '+value['estimate'])
            interval = escape(value['ci_level']+' CI '+value['ci_lower']+'–'+value['ci_upper'])
            study = self.studies[fact['id']]
            label = escape(item['label'])
            button = (f'<button type="button" class="point" data-measurement="{escape(value["id"])}" '
                      f'aria-haspopup="dialog" aria-label="{escape(study["reference"])}、{label}、{number}、{interval}の研究詳細">'
                      f'<span class="outcome">{label}</span><span class="result"><b>{number}</b>　{interval}</span>'
                      '<svg viewBox="0 0 320 25" aria-hidden="true">'
                      f'<path class="baseline" d="M{null} 0v25"/><path class="interval" d="M{a} 7v10m0-5H{b}m0-5v10"/>'
                      f'<circle cx="{point}" cy="12" r="4.5"/></svg></button>')
            buttons.append(button + details_for_point(item, value, fact, study, self.refs, self.has_pdf))

        ticks = []
        for tick in (low, 1, high):
            if tick != 1 and abs(x(tick)-null) < 26:
                continue
            ticks.append(f'<text x="{x(tick)}" y="18" text-anchor="middle">{tick:g}</text>')
        axis = '<svg class="ticks" viewBox="0 0 320 23" aria-hidden="true">'+''.join(ticks)+'</svg>'
        return (f'<article class="forest"><h2>{escape(panel["title_ja"])}</h2>'
                + ''.join(buttons) + axis + '</article>')


def render_study_plots(paper):
    master = paper.teaching_master
    if not master:
        raise ValueError('研究別グラフにはlearning-v3のMasterを指定してください')
    panels = [panel for panel in master['visualizations'] if panel['type'] == 'forest_plot']
    if not panels:
        raise ValueError('このMasterに研究別グラフのパネルがありません')
    component = ForestPlots(master, bool(paper.original_pdf))
    return package_visual_reader(paper, 'study_plots', ''.join(component.panel(panel) for panel in panels))
