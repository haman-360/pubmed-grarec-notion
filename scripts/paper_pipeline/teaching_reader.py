"""Render the same teaching sections for continuous reading, slides and source inspection."""
from base64 import b64encode
from hashlib import sha256
from html import escape
import math
from string import Template

from .reader import TEMPLATES, png_html
from .teaching import AI_FIELDS, STATUS


def paras(value):
    if isinstance(value,list):
        return ''.join(paras(s) for s in value)
    if isinstance(value,dict):
        return ''.join(paras(f'{k}　{v}') for k,v in value.items())
    return ''.join('<p>'+escape(s)+'</p>' for s in (value or '').splitlines() if s.strip())


def render_teaching(paper, images=(), image_notes=()):
    m = paper.teaching_master
    ov, t = m['overview'], m['teaching']
    facts = {e['id']:e for e in m['evidence']}
    refs = {s['id']:s for s in m['source_references']}
    chapters = {c['id']:c for c in m['chapters']}
    def links(ids):
        items, all_pages = [], set()
        for eid in ids:
            pages = sorted({refs[s]['pdf_page_start'] for s in facts[eid]['source_refs'] if 'pdf_page_start' in refs[s]})
            all_pages.update(pages)
            label = 'p.' + ', '.join(map(str,pages)) if pages else '抄録'
            study = t['studies'].get(eid,{})
            if len(ids)>1:
                label += '　' + ' · '.join(study.get(k,'') for k in ('reference','outcome')).strip(' ·')
                if not study:
                    label += facts[eid]['statement_ja']
            items.append(f'<a class="ref" href="#ev-{eid}">{escape(label)}</a>')
        if len(items)>1:
            label = '出典 p.' + ', '.join(map(str,sorted(all_pages))) if all_pages else '抄録の出典'
            return '<details class="refs"><summary>'+label+'</summary>'+''.join(items)+'</details>'
        return ''.join(items)

    def field(key, label):
        info = t['fields'][key]
        value = ov.get(key)
        content = paras(value) if info['status']=='recorded' else paras(STATUS[info['status']]+'　'+info.get('reason',''))
        origin = '<small>AIによる臨床的解釈</small>' if key in AI_FIELDS else ''
        return f'<h3>{label}</h3>{origin}{content}'+links(info['evidence_ids'])

    sections = []
    def section(label, title, body):
        sections.append((label,title,body))

    head = (ov['topic'] or [paper.title])[0]
    quick = field('one_line_summary','Take Home Message')
    metrics = []
    seen = set()
    for ids in ov['result_evidence_ids']:
        for eid in ids:
            study = t['studies'].get(eid,{})
            for value in facts[eid].get('measurements',[]):
                if not study or not all(value.get(k) for k in ('estimate','ci_lower','ci_upper','ci_level')):
                    continue
                if study['reference'] in seen or len(metrics) >= 3:
                    continue
                seen.add(study['reference'])
                metrics.append('<div><small>'+escape(study['reference']+' ／ '+study['design'])+'</small><b>'+
                               escape(value['label']+' '+value['estimate'])+'</b><p>'+
                               escape(value['ci_level']+' CI '+value['ci_lower']+'–'+value['ci_upper'])+'</p>'+
                               paras(study['outcome'])+'<small>'+escape(study['population']+' ／ '+study['exposure'])+'</small>'+links([eid])+'</div>')
    if metrics:
        quick += '<div class="metrics">'+''.join(metrics)+'</div>'
    for caption, raw in images:
        try:
            quick += '<details><summary>グラレコPNGを表示</summary>'+png_html(caption,raw)+'</details>'
        except (OSError,ValueError,SyntaxError):
            quick += paras(caption+'：PNGを読み込めません')
    quick += '<h3>主要結果</h3>'
    quick += ''.join('<article>'+paras(s)+links(ids)+'</article>' for s,ids in zip(ov['main_results'],ov['result_evidence_ids']))
    quick += field('applicability_to_japanese_pediatric_clinic','診療への示唆')
    quick += paras(list(image_notes))
    if not images:
        quick += '<p class="note">グラレコPNGは未取得</p>'
    section('Quick View','論文の要点',quick)
    section('Learning View','背景と研究の読み方',field('why_important','医学的背景')+
            field('study_design','研究方法')+field('population_setting','対象と設定')+field('pico','PICO'))
    measures = {v['id']:v for e in facts.values() for v in e.get('measurements',[])}

    def study_text(eid):
        s = t['studies'].get(eid,{})
        return ' ／ '.join(s[k] for k in ('reference','design','population','exposure','comparator','outcome','adjustments') if s.get(k))

    def item_body(item):
        parts = []
        for eid in item['evidence_ids']:
            s = t['studies'].get(eid,{})
            context = ' ／ '.join(s[k] for k in ('reference','design','population','exposure') if s.get(k))
            parts.append(('<small>'+escape(context)+'</small>' if context else '')+paras(facts[eid]['statement_ja'])+links([eid]))
        return ''.join(parts)

    def diagram(panel):
        items = panel['items']
        if panel['type']=='forest_plot':
            values = [measures[i['measurement_ids'][0]] for i in items]
            bounds = [math.log(float(v[k])) for v in values for k in ('ci_lower','ci_upper')] + [0.]
            low, high = min(bounds)-0.12, max(bounds)+0.12
            def x(v):
                return round(12+276*(math.log(float(v))-low)/(high-low),2)
            rows = []
            for item,v in zip(items,values):
                a,b,point,one = map(x,(v['ci_lower'],v['ci_upper'],v['estimate'],1))
                label = escape(item['label'])
                number = escape(f"{v['label']} {v['estimate']}（95% CI {v['ci_lower']}–{v['ci_upper']}）")
                svg = (f'<svg viewBox="0 0 300 36" role="img" aria-label="{number}"><title>{number}</title>'
                       f'<path class="null" d="M{one} 0v35"/><path d="M{a} 11v12m0 -6H{b}m0 -6v12"/>'
                       f'<circle cx="{point}" cy="17" r="4"/></svg>')
                rows.append(f'<tr><th>{label}</th><td>{svg}</td><td><strong>{number}</strong> {links(item["evidence_ids"])}</td></tr>')
            body = ('<p class="note">対数目盛。破線は比が1、点は各研究の推定値、横線は95%信頼区間。</p>'
                    '<div class="scroll"><table><thead><tr><th>研究・アウトカム</th><th>推定値と区間</th><th>報告値</th></tr></thead><tbody>'+
                    ''.join(rows)+'</tbody></table></div>')
        elif panel['type']=='comparison':
            rows = ''.join('<tr><th>'+escape(i['label'])+'</th><td>'+item_body(i)+'</td></tr>' for i in items)
            body = '<div class="scroll"><table><thead><tr><th>アウトカム</th><th>知見と出典</th></tr></thead><tbody>'+rows+'</tbody></table></div>'
        elif panel['type']=='mechanism_diagram':
            by_id = {i['id']:i for i in items}
            paths = []
            for edge in panel['edges']:
                a,b = (by_id[edge[k]] for k in ('from','to'))
                arrow = '<svg class="connector" viewBox="0 0 42 18" aria-hidden="true"><path d="M1 9h36m-7 -6 7 6-7 6"/></svg>'
                paths.append('<details><summary><b>'+escape(a['label'])+'</b>'+arrow+'<b>'+escape(b['label'])+
                             '</b></summary>'+paras(edge['label'])+item_body(b)+links(edge['evidence_ids'])+'</details>')
            body = ''.join(paths)
        else:
            body = ''.join('<details name="'+panel['id']+'"><summary>'+escape(i['label'])+'</summary>'+
                           item_body(i)+'</details>' for i in items)
        return '<div class="diagram '+panel['type']+'"><h3>'+escape(panel['title_ja'])+'</h3>'+body+'</div>'

    for unit in t['units']:
        body = [paras(unit.get('introduction'))]
        if unit.get('evidence_ids'):
            groups = {}
            for eid in unit['evidence_ids']:
                study = t['studies'].get(eid,{})
                key = (study.get('reference','論文の記述'),study.get('population',''))
                groups.setdefault(key,[]).append(eid)
            for (reference,population), ids in groups.items():
                study = t['studies'].get(ids[0],{})
                context = ' ／ '.join(study[k] for k in ('design','population','exposure','comparator','adjustments') if study.get(k))
                findings = ''.join(paras(facts[eid]['statement_ja'])+links([eid]) for eid in ids)
                limits = list(dict.fromkeys(s for eid in ids for s in facts[eid].get('exceptions',[])))
                body.append('<article><h3>'+escape(reference)+'</h3>'+paras(context)+findings+
                            ('<h4>解釈上の限界</h4>'+paras(limits) if limits else '')+'</article>')
            section('Learning View',unit['title'],''.join(body))
            continue
        for cid in unit['chapter_ids']:
            c = chapters[cid]
            body.append('<h3>'+escape(c['title_ja'])+'</h3>'+paras(c['summary_ja']))
            for block in c['blocks']:
                ids = block['evidence_ids']
                study = study_text(ids[0])
                title = '<h4>'+escape(study.split(' ／ ')[0])+'</h4>' if study else ''
                context = '<small>'+escape(study)+'</small>' if study else ''
                origin = '<small>AIによる臨床的解釈</small>' if block['origin']=='ai_interpretation' else ''
                body.append('<article>'+title+origin+paras(block['text_ja'])+context+links(ids)+'</article>')
        section('Learning View',unit['title'],''.join(body))
    if m['visualizations']:
        section('Learning View','図解で比較する',field('figure_table_summary','図表の読み方')+
                ''.join(diagram(p) for p in m['visualizations']))
    clinical = ''.join(paras(b['text_ja'])+links(b['evidence_ids']) for b in m['clinical_interpretation'])
    section('Learning View','限界と外来での応用',field('limitations','研究の限界')+field('safety','安全性')+
            field('practice_change','Practice Change')+field('practice_change_reason','判断の理由')+
            field('tomorrow_action','外来で検討する行動')+'<h3>AIによる臨床的解釈</h3>'+
            (clinical or paras(STATUS[t['fields']['clinical_interpretation']['status']])))
    abstract = ''.join(f'<h3>原文 · p.{page}</h3>'+paras(part['original'])+'<h3>日本語訳</h3>'+paras(part['translation_ja'])
                       for page,part in zip(t['abstract_pages'],m['abstract']['parts']))
    section('Learning View','抄録と対訳',abstract or paras(STATUS[t['fields']['abstract']['status']]))
    def source(s):
        label = ' · '.join(str(s[k]) for k in ('figure','table','citation') if s.get(k))
        page = s.get('pdf_page_start')
        pdf_link = (f'<a data-pdf-page="{page}" target="_blank" rel="noopener">原本PDF p.{page} を開く</a>'
                    if paper.original_pdf and page else (f'PDF p.{page}' if page else 'PubMed 抄録'))
        return '<blockquote><b>'+escape(s['id'])+' · '+pdf_link+' '+escape(label)+'</b>'+paras(s.get('quote'))+'</blockquote>'
    records = []
    for eid,e in facts.items():
        records.append(f'<details id="ev-{eid}"><summary>{eid}　{escape(e["statement_ja"])}</summary>'+
                       paras(study_text(eid))+paras(e.get('conditions'))+paras(e.get('exceptions'))+
                       paras([v['label']+'　'+v['reported'] for v in e.get('measurements',[])])+
                       ''.join(source(refs[r]) for r in e['source_refs'])+'</details>')
    quality = ''.join('<li>'+escape(key)+'　'+escape(' · '.join(value[k] for k in ('status','reviewer','note') if value.get(k)))+'</li>' for key,value in t['quality'].items())
    evidence = ('<details id="audit"><summary>Evidence View · 原文と品質記録</summary>'+''.join(records)+
                '<details><summary>全章と出典一覧</summary>'+''.join('<details><summary>'+escape(c['title_ja'])+'</summary>'+paras(c['summary_ja'])+''.join(paras(b['text_ja'])+links(b['evidence_ids']) for b in c['blocks'])+'</details>' for c in chapters.values())+
                ''.join(source(s) for s in refs.values())+'</details><h3>品質評価</h3><ul>'+quality+'</ul>'+
                '<h3>確認履歴</h3>'+paras('人による医学確認：'+('確認済み' if m['quality_control']['human_checked'] else '未確認'))+
                paras([issue['note'] for issue in m['quality_control']['issues']])+
                '<h3>生成記録</h3>'+paras(m['generation_metadata'])+paras('Master SHA-256　'+paper.sha256)+
                paras('PDF SHA-256　'+m['metadata']['pdf_sha256'])+'</details>')
    nav, content = [],[]
    for n,(label,title,body) in enumerate(sections,1):
        nav.append(f'<a href="#lesson-{n}">{escape(title)}</a>')
        content.append(f'<section id="lesson-{n}"><small>{label}</small><h2>{escape(title)}</h2>{body}</section>')
    css = (TEMPLATES/'teaching.css').read_text()
    js = (TEMPLATES/'teaching.js').read_text()
    def hash64(s):
        return b64encode(sha256(s.encode()).digest()).decode()
    return Template((TEMPLATES/'teaching.html').read_text()).substitute(title=escape(head),original=escape(paper.title),
           citation=escape(' · '.join([*paper.citation,'PMID '+paper.pmid])),nav=''.join(nav),body=''.join(content),
           evidence=evidence,pdf=b64encode(paper.original_pdf or b'').decode(),css=css,js=js,css_hash=hash64(css),js_hash=hash64(js)).encode()
