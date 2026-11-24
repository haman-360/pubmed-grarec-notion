"""Render each outgoing relation in a learning-v3 mechanism panel as its own view."""
from collections import defaultdict
from html import escape

from .visual_reader import package_visual_reader, source_excerpt


KINDS = {'author_hypothesis': '著者の仮説', 'author_statement': '著者の記述',
         'cited_study': '引用研究', 'study_result': '研究結果',
         'guideline_recommendation': 'ガイドラインの推奨', 'expert_consensus': '専門家の合意'}


def illustration(label):
    # Labels select decorative anatomical symbols only. All relations come from panel.edges.
    concept = next((name for term, name in [('肺胞', 'alveoli'), ('気道', 'airway'), ('遺伝子発現', 'expression')]
                    if term in label), 'finding')
    return (f'<svg class="symbol {concept}" aria-hidden="true" viewBox="0 0 120 96">'
            f'<use href="#symbol-{concept}"/></svg>')


class MechanismDiagrams:
    def __init__(self, master, has_pdf):
        self.facts = {fact['id']: fact for fact in master['evidence']}
        self.refs = {ref['id']: ref for ref in master['source_references']}
        self.studies = master['teaching']['studies']
        self.has_pdf = has_pdf

    def evidence_card(self, eid):
        fact = self.facts[eid]
        study = self.studies.get(eid, {})
        heading = ' · '.join(filter(None, [study.get('reference'), study.get('design') or KINDS[fact['kind']]]))
        fields = [('対象', study.get('population') or fact.get('population')),
                  ('曝露・介入', study.get('exposure')), ('対照', study.get('comparator')),
                  ('評価項目', study.get('outcome')), ('調整因子', study.get('adjustments')),
                  ('条件', ' ／ '.join(x for x in fact.get('conditions', []) if x != study.get('exposure'))),
                  ('限界', ' ／ '.join(fact.get('exceptions', [])))]
        terms = ''.join(f'<dt>{k}</dt><dd>{escape(v)}</dd>' for k, v in fields if v)
        measures = ''.join('<p>'+escape(v['reported'])+'</p>' for v in fact.get('measurements', []))
        sources = ''.join(source_excerpt(self.refs[k], self.has_pdf) for k in fact['source_refs'])
        pages = sorted({self.refs[k]['pdf_page_start'] for k in fact['source_refs'] if self.refs[k].get('pdf_page_start')})
        location = ' · p.'+'、'.join(map(str, pages)) if pages else ''
        return (f'<article class="fact" data-evidence="{escape(eid)}"><h4>{escape(heading)}</h4>'
                f'<p>{escape(fact["statement_ja"])}</p><details><summary>条件・根拠・原文{location}</summary>'
                f'<div class="record"><small>Evidence {escape(eid)}</small><dl>{terms}</dl>{measures}{sources}</div>'
                '</details></article>')

    def group(self, start, edges, items):
        cards = []
        scenes = []
        for pos, edge in enumerate(edges, 1):
            target = items[edge['to']]
            label = escape(target['label'])
            cards.append(f'<button type="button" class="branch" data-select="{pos}" aria-pressed="false">'
                         + illustration(target['label']) + f'<b>{label}</b></button>')
            ids = target['evidence_ids']
            findings = ''.join(self.evidence_card(eid) for eid in ids)
            # Relation-specific references may include background beyond the target studies.
            extra = ''.join(self.evidence_card(eid) for eid in edge['evidence_ids'] if eid not in ids)
            relation_refs = '・'.join(escape(eid) for eid in edge['evidence_ids'])
            scenes.append(f'<div class="scene" data-scene="{pos}" hidden><h3>{label}</h3>'
                          f'<p class="relation">{escape(edge["label"])}</p>{findings}'
                          f'<details class="extra"><summary>関係の説明を支える根拠</summary>'
                          f'<small>関係の根拠　{relation_refs}</small>{extra}</details></div>')
        context = ''.join(self.evidence_card(eid) for eid in start['evidence_ids'])
        if not edges:
            return f'<section><h3>{escape(start["label"])}</h3>{context}</section>'
        branches = 'branches many' if len(edges) > 3 else 'branches'
        caption = f'<p class="caption">同じ起点から分かれる{len(edges)}つの所見</p>' if len(edges) > 1 else ''
        return ('<section class="group"><div class="map" aria-label="図の項目を選択">'
                '<button type="button" class="origin" data-select="0" aria-pressed="true">'+escape(start['label'])+'</button>'
                f'<div class="{branches}">'+''.join(cards)+'</div>'+caption+'</div>'
                '<div class="controls"><button type="button" data-move="-1" disabled>前へ</button>'
                '<output aria-live="polite">全体図</output><button type="button" data-move="1">次の所見へ</button></div>'
                '<div class="scene" data-scene="0"><h3>'+escape(start['label'])+'</h3>'+context+'</div>'
                +''.join(scenes)+'</section>')

    def panel(self, panel):
        items = {item['id']: item for item in panel['items']}
        outgoing = defaultdict(list)
        targets = set()
        for edge in panel['edges']:
            outgoing[edge['from']].append(edge)
            targets.add(edge['to'])
        groups = [self.group(item, outgoing[key], items) for key, item in items.items()
                  if key in outgoing or key not in targets]
        return '<div class="panel"><h2>'+escape(panel['title_ja'])+'</h2>'+''.join(groups)+'</div>'


def render_mechanisms(paper):
    master = paper.teaching_master
    if not master:
        raise ValueError('機序図にはlearning-v3のMasterを指定してください')
    panels = [panel for panel in master['visualizations'] if panel['type'] == 'mechanism_diagram']
    if not panels:
        raise ValueError('このMasterに機序図のパネルがありません')
    component = MechanismDiagrams(master, bool(paper.original_pdf))
    return package_visual_reader(paper, 'mechanisms', ''.join(component.panel(panel) for panel in panels))
