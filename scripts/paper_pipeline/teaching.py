"""Source-bound inputs for specialist reading and a separately resumable whole-paper synthesis."""
from copy import deepcopy
import json
import math

from jsonschema import Draft202012Validator

from . import chapter_review as chapter
from .store import atomic_write, digest

PROTOCOL = 'chapter-learning-v2'
REVISION = 'learning-v3'
TEXT = chapter.TEXT
obj, array = chapter.obj, chapter.array
IDS = dict(type='array', items=TEXT, minItems=1, uniqueItems=True)
STATUS = {'recorded': '記載済み', 'not_acquired': '未取得', 'not_reported': '原文に記載なし',
          'not_applicable': '該当なし', 'not_assessed': '未評価'}
QUALITY = ('医学的正確性', '原文に対する重要情報の網羅性', '医師向けの専門性', '全体要約の完成度',
           'PNG生成用JSONの完成度', 'HTMLの読みやすさ', '図解による理解の向上', '原文への追跡可能性')
STUDY = obj({'reference': TEXT, 'design': TEXT, 'outcome': TEXT, 'population': TEXT, 'exposure': TEXT,
             'comparator': TEXT, 'adjustments': TEXT, 'stage': TEXT},
            ['reference', 'design', 'outcome', 'population', 'exposure'])
PACKET = deepcopy(chapter.RESULT_SCHEMA)
finding = PACKET['properties']['findings']['items']
finding['properties'].update(study=STUDY, result_key={'type':'string', 'pattern':'^[a-z][a-z0-9_-]{0,63}$'})
finding['dependentRequired'] = {'result_key':['study']}
finding['allOf'] = [{'if':{'properties':{'kind':{'enum':['cited_study','study_result']}}},
                     'then':{'required':['study']}}]
INSTRUCTIONS = chapter.INSTRUCTIONS + """
医師向け教材。章の医学的重要性と情報密度に応じ、研究対象・症例数・曝露条件・比較・方法・評価時点・効果量・
信頼区間・交絡・限界を原本にある範囲で具体的に書く。文字数の上限は設けない。研究ごとに著者、年、文献番号を
study.referenceに記録。動物、ヒト観察、介入、著者仮説を明示。本文に加えて図、凡例、表の各研究行を読み、
異なる研究・集団・アウトカム・解析を別所見にする。帰無結果も保持する。同じ解析を本文と表で関連付ける場合のみ
同じresult_keyとstudyを使う。引用は対象・解析・主張を支持するものを選ぶ。比の推定値はlabelをaOR、aHR等とし
reportedに出典表記、estimate、ci_level、ci_lower、ci_upperを原文どおり分離する。不明情報の推測・独自の統合は禁止。
"""
API_PROTOCOL = 'chapter-learning-batch-v1'
API_PACKET = deepcopy(PACKET)
API_PACKET['properties']['method'] = {'const':'api_batch'}
API_INSTRUCTIONS = INSTRUCTIONS.replace('Webで作成した結果は method=manual_web、人が作成した結果は manual_local。',
                                       '今回の作成方法はmethod=api_batch。') + '\n応答は指定スキーマのJSONオブジェクトのみ。'
FIELD_KEYS = ('topic','clinical_area','one_line_summary','why_important','study_design','population_setting',
              'figure_table_summary','limitations','safety','pico','applicability_to_japanese_pediatric_clinic',
              'practice_change','practice_change_reason','tomorrow_action','clinical_interpretation','abstract')
AI_FIELDS = {'applicability_to_japanese_pediatric_clinic','practice_change','practice_change_reason','tomorrow_action',
             'clinical_interpretation'}
FIELD = obj(dict(status={'enum':list(STATUS)}, evidence_ids=array(TEXT), reason=TEXT), ['status','evidence_ids'])
UNIT = obj(dict(title=TEXT, chapter_ids=IDS, evidence_ids=IDS, introduction=TEXT), ['title','chapter_ids'])
PART = obj(dict(page={'type':'integer','minimum':1}, fragments=array(TEXT,1), translation_ja=TEXT),
           ['page','fragments','translation_ja'])
SYNTHESIS = obj(dict(basis=TEXT, overview=chapter.DEFS['clinical'],
                     fields=obj(dict.fromkeys(FIELD_KEYS, FIELD), list(FIELD_KEYS)),
                     abstract_parts=array(PART), clinical_interpretation=array(chapter.DEFS['block']),
                     units=array(UNIT,1), visualizations=array(chapter.DEFS['panel'])),
                ['basis','overview','fields','abstract_parts','clinical_interpretation','units','visualizations'])
SYNTHESIS['$defs'] = chapter.DEFS
ASSESSMENT = obj(dict(status={'enum':['未評価','確認済み','要修正']}, reviewer=TEXT, note=TEXT),['status'])
ASSESSMENT['allOf'] = [{'if':{'properties':{'status':{'enum':['確認済み','要修正']}}},
                         'then':{'required':['reviewer','note']}}]
RATINGS = obj(dict.fromkeys(QUALITY, ASSESSMENT),list(QUALITY))
STORED_FIELD = obj(dict(FIELD['properties'], origin={'enum':['paper','ai_interpretation']}),[*FIELD['required'],'origin'])
TEACHING = obj(dict(fields=obj(dict.fromkeys(FIELD_KEYS,STORED_FIELD),list(FIELD_KEYS)), units=array(UNIT,1),
                    studies={'type':'object','additionalProperties': STUDY}, basis=TEXT,
                    abstract_pages=array({'type':'integer','minimum':1}),
                    quality=RATINGS),
               ['fields','units','studies','basis','abstract_pages','quality'])
SYNTHESIS_PROMPT = """全章の受理済み知見、章概要、出典および添付の章PDFを照合し、医師向けに全体を統合する。
overviewの各項目に根拠または欠測状態を付ける。記載なしは確認範囲、非該当・未評価は理由を付ける。
主要結果は各結果に対応する根拠IDを付ける。abstract_partsは原本の逐語断片を掲載順に収録し対訳を付ける。
日本の外来への適用、Practice Change、理由、明日の行動はAIの解釈として記述し、著者の記述と分ける。
PICOは該当時のみ記載。unitsは医師が学びたい問いで章を束ねる。背景・機序・限界も扱う。
visualizationsは理解に有用なtimeline（ライフステージ）、forest_plot、mechanism_diagram、comparisonを選び、
全項目をEvidenceに結ぶ。比のプロットには同一の指標、デザイン、集団ごとのパネルを使い、各行に一つの研究別測定値を指定。
機序の矢印は根拠と仮説の範囲を明示。異質な研究の統合推定値・原文にない知識を補わない。"""


def validate(schema, value):
    error = next(Draft202012Validator(schema).iter_errors(value), None)
    if error:
        raise ValueError(f'教材データの形式を確認してください：{error.json_path} ({error.validator})')


def synthesis_stage(folder, base, studies, pages):
    context = dict(master=base, studies=studies)
    basis = digest([context, SYNTHESIS_PROMPT, SYNTHESIS])
    dest = folder / 'synthesis' / basis
    dest.mkdir(parents=True, exist_ok=True)
    for name, content in [('context.json', context), ('schema.json', SYNTHESIS), ('master-schema.json', master_schema())]:
        atomic_write(dest/name, json.dumps(content, ensure_ascii=False, indent=2))
    atomic_write(dest/'request.txt', SYNTHESIS_PROMPT + '\nbasis=' + basis + '\nresponse.json を保存してください。')
    response = dest/'response.json'
    if not response.exists():
        return None, str(dest)
    data = json.loads(response.read_text(encoding='utf-8-sig'))
    validate(SYNTHESIS, data)
    if data['basis'] != basis:
        raise ValueError('統合結果が受理済みの章の版と一致しません')
    parts = []
    for i, part in enumerate(data['abstract_parts'], 1):
        text = ' '.join(pages.get(part['page'], '').split())
        for fragment in part['fragments']:
            token = ' '.join(fragment.split())
            pos = text.find(token)
            if pos < 0:
                raise ValueError('抄録の逐語断片または順序を原ページで確認できません')
            text = text[pos+len(token):]
        parts.append(dict(id=f'a{i}', label=None, language='original',
                          original=''.join(('' if j==0 or part['fragments'][j-1].endswith('-') else ' ')+f for j,f in enumerate(part['fragments'])), translation_ja=part['translation_ja']))
    abstract_status = data['fields']['abstract']['status']
    base.update(schema_version=REVISION, abstract=dict(source='original_pdf' if parts else None,
                status='unverified' if abstract_status=='not_assessed' else abstract_status, parts=parts),
                overview=data['overview'], clinical_interpretation=data['clinical_interpretation'],
                visualizations=data['visualizations'])
    base['generation_metadata'].update(schema_revision=REVISION,prompt_version=API_PROTOCOL if base['generation_metadata']['method']=='api_batch' else PROTOCOL)
    field_records = {k:dict(v,origin='ai_interpretation' if k in AI_FIELDS else 'paper') for k,v in data['fields'].items()}
    base['teaching'] = dict(basis=basis, fields=field_records, units=data['units'], studies=studies,
                            abstract_pages=[p['page'] for p in data['abstract_parts']],
                            quality={k:{'status':'未評価'} for k in QUALITY})
    for ch in base['chapters']:
        ids = {eid for b in ch['blocks'] for eid in b['evidence_ids']}
        ch['visualization_ids'] = [p['id'] for p in base['visualizations'] if ids.intersection(p['evidence_ids'])]
    validate_teaching(base)
    return base, str(dest)


def read_quality(location, files, current):
    # Bind assessments to the actual unassessed edition, including the selected images, PDF and renderer.
    review_basis = digest({name:digest(raw) for name,raw in files.items()})
    dest = location / 'assessments' / review_basis
    dest.mkdir(parents=True,exist_ok=True)
    template = dest/'template.json'
    atomic_write(template,json.dumps(dict(basis=review_basis,quality=current),ensure_ascii=False,indent=2))
    response = dest/'response.json'
    if not response.exists():
        return current, str(template)
    review = json.loads(response.read_text())
    validate(obj(dict(basis={'const':review_basis},quality=RATINGS),['basis','quality']),review)
    return review['quality'], str(template)


def master_schema():
    return dict(chapter.MASTER, title='Interactive medical reader · learning-v3',
                required=[*chapter.MASTER['required'],'teaching'],
                properties=dict(chapter.MASTER['properties'],schema_version={'const':REVISION},teaching=TEACHING))


def validate_teaching(master):
    from .reader_data import learning_sections
    validate(master_schema(), master)
    teaching = master.get('teaching')

    learning_sections(master)  # Includes chapter, measurement, panel and source reference validation.
    facts = {e['id']:e for e in master['evidence']}
    overview = master['overview']
    if len(overview['main_results']) != len(overview['result_evidence_ids']):
        raise ValueError('主要結果と根拠の件数が一致しません')
    for ids in overview['result_evidence_ids']:
        if not set(ids) <= facts.keys():
            raise ValueError('主要結果の根拠が見つかりません')
    if not teaching['studies'].keys() <= facts.keys():
        raise ValueError('研究に対応する根拠が見つかりません')
    chapter_ids = {c['id'] for c in master['chapters']}
    if len(teaching['abstract_pages']) != len(master['abstract']['parts']):
        raise ValueError('抄録と原ページの対応が不正です')
    for unit in teaching['units']:
        if not set(unit['chapter_ids']) <= chapter_ids:
            raise ValueError('教材の章参照が不正です')
        available = {eid for c in master['chapters'] if c['id'] in unit['chapter_ids'] for b in c['blocks'] for eid in b['evidence_ids']}
        if not set(unit.get('evidence_ids',[])) <= available:
            raise ValueError('教材単元の根拠が参照章にありません')
    for key, info in teaching['fields'].items():
        value = master['abstract']['parts'] if key=='abstract' else master.get(key,overview.get(key))
        present = info['status']=='recorded'
        if info['origin'] != ('ai_interpretation' if key in AI_FIELDS else 'paper'):
            raise ValueError(f'{key}の記述主体が不正です')
        if present != bool(value) or not set(info['evidence_ids']) <= facts.keys():
            raise ValueError(f'{key}の状態・値・根拠が一致しません')
        if (present and not info['evidence_ids']) or (not present and not info.get('reason')):
            raise ValueError(f'{key}の根拠または欠測理由を指定してください')
    if any(b['origin']!='ai_interpretation' for b in master['clinical_interpretation']):
        raise ValueError('臨床的解釈は論文の記述と分けてください')
    measures = {m['id']:(e['id'],m) for e in facts.values() for m in e.get('measurements',[])}
    for panel in master['visualizations']:
        members = {eid for i in panel['items'] for eid in i['evidence_ids']}
        members.update(eid for edge in panel.get('edges',[]) for eid in edge['evidence_ids'])
        if not members <= set(panel['evidence_ids']):
            raise ValueError('図解全体と各項目の根拠が一致しません')
        if panel['type'] not in ('timeline','forest_plot','mechanism_diagram','comparison'):
            raise ValueError('教材の図解形式が未対応です')
        if panel['type']!='forest_plot':
            continue
        strata = set()
        for item in panel['items']:
            if len(item.get('measurement_ids',[])) != 1:
                raise ValueError('研究別プロットの各行に測定値を1件指定してください')
            eid, m = measures[item['measurement_ids'][0]]
            study = teaching['studies'].get(eid,{})
            strata.add((study.get('design'),study.get('population')))
            try:
                lo, est, hi = map(float,(m['ci_lower'],m['estimate'],m['ci_upper']))
            except (TypeError,KeyError,ValueError) as error:
                raise ValueError('推定値と信頼区間を確認してください') from error
            if not (study and m['label'] in ('OR','aOR','HR','aHR','RR','aRR') and panel['axis']['scale']=='log'
                    and panel['axis'].get('null_value')=='1' and m['label']==panel['axis']['measure']
                    and (m.get('ci_level') or '').rstrip('%').strip()=='95' and math.isfinite(hi) and 0 < lo <= est <= hi):
                raise ValueError('比の指標・区間・対数軸の設定を確認してください')
        if len(strata)>1:
            raise ValueError('対象集団・研究デザインを分けて表示してください')
