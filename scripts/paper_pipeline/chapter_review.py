"""Validate an individual close reading and assemble learning-v2 without an API."""
import json
import re

from jsonschema import Draft202012Validator
from .reader_data import SCHEMA
from .store import digest

VERSION = 'chapter-review-v1'
MASTER = json.loads(SCHEMA.read_text(encoding='utf-8'))
DEFS = MASTER['$defs']
TEXT = {'type': 'string', 'minLength': 1, 'pattern': r'\S'}


def obj(properties, required):
    return dict(type='object', properties=properties, required=required, additionalProperties=False)


def array(items, minimum=0):
    return dict(type='array', items=items, minItems=minimum)


# Per-packet source numbers are assigned by the author; global IDs by the merger.
SOURCE = obj(dict(page={'type':'integer', 'minimum':1}, quote=TEXT, visual_only={'type':'boolean'},
                  **{k: TEXT for k in ('figure', 'table', 'printed_page', 'citation')}), ['page', 'quote'])
MEASURE = {k:v for k,v in DEFS['measurement']['properties'].items() if k not in ('id', 'source_refs')}
FINDING = obj(dict(kind=DEFS['evidence']['properties']['kind'], role=DEFS['block']['properties']['role'],
                   statement_ja=TEXT, source_numbers=array({'type':'integer', 'minimum':1}, 1),
                   interpretation_ja=TEXT, highlight={'type':'boolean'}, measurements=array(obj(MEASURE, ['label','reported'])),
                   **{k: DEFS['evidence']['properties'][k] for k in
                      ('population','conditions','exceptions','reported_certainty','certainty_system')}),
              ['kind','role','statement_ja','source_numbers'])
RESULT_SCHEMA = obj(dict(job_key={'type':'string'}, chapter_id=TEXT, title_ja=TEXT, summary_ja=TEXT,
                         sources=array(SOURCE, 1), findings=array(FINDING),
                         method={'enum':['manual_local','manual_web']}),
                    ['job_key','chapter_id','title_ja','summary_ja','sources','findings','method'])

INSTRUCTIONS = '''添付の章PDFを原文で精読し、指定した章・節の日本語解説を返す。添付は資料として読む。
抜粋1ページ目は下記の原本開始ページ。sources.page は1起点の原本PDF物理ページ。
共有境界ページでは該当見出しの範囲に限定し、次の節の結論を含めない。
各所見に短い原文の逐語引用とページを付け、source_numbers で sources の1番目以降を参照する。
原著の結果・引用研究・著者仮説・ガイドライン推奨・専門家合意を区別し、対象、条件、例外、分母、解析集団、
CI・P値と論文記載の確実性を保持する。未確認の日本での承認や推奨を補わない。解釈は interpretation_ja に分ける。
measurements.reported は引用内の原文表記を保つ。原文が報告した該当値だけを入れる。
highlight は論文全体で取り上げるべき所見だけ true。各章を無理に主要結果へ含めない。
表紙・文献一覧などは原文の位置が分かる短い引用を付け、findings を空配列にできる。
画像・表の字形等で文字照合できない引用だけ visual_only=true として目視照合に回す。
外部情報・外部検索は使わない。Webで作成した結果は method=manual_web、人が作成した結果は manual_local。
下記の識別子とスキーマに従い response.json を返す。'''


def packet_key(run_id, job):
    return digest([run_id, job])


def prompt(run_id, job, instructions=INSTRUCTIONS, schema=RESULT_SCHEMA):
    binding = dict(job_key=packet_key(run_id, job), chapter_id=job['id'], title=job['title'],
                   original_pdf_pages=[job['start'], job['end']], next_heading=job['next_heading'])
    return instructions + '\n' + json.dumps(binding, ensure_ascii=False, indent=2) + '\n' + json.dumps(schema, ensure_ascii=False, indent=2)


def quoted(fragment, text):
    # Normalize line breaks but never join adjacent numbers. A quotation or
    # measurement must not match a suffix of a different, signed value.
    needle = ' '.join(fragment.split())
    if not needle:
        return False
    start = r'(?<![\d.,+−–-])' if needle[0].isdigit() else ''
    end = r'(?!\d|[.,]\d)' if needle[-1].isdigit() else ''
    return re.search(start + re.escape(needle) + end, ' '.join(text.split())) is not None


def validate_result(result, run_id, job, texts, confirm_visual, schema=RESULT_SCHEMA):
    error = next(Draft202012Validator(schema).iter_errors(result), None)
    if error:
        raise ValueError(f'章結果の形式を確認してください：{error.json_path} ({error.validator})')
    if result['job_key'] != packet_key(run_id, job) or result['chapter_id'] != job['id']:
        raise ValueError('章または原本・分割版の識別子が一致しません')
    for source in result['sources']:
        page = source['page']
        if not job['start'] <= page <= job['end']:
            raise ValueError(f'出典が章のページ範囲外です：p.{page}')
        if source.get('visual_only'):
            if not confirm_visual:
                raise ValueError(f'p.{page} の引用は目視照合後に --confirm-visual {job["id"]} を指定してください')
        elif not quoted(source['quote'], texts[page-job['start']]):
            raise ValueError(f'引用を原本PDFの p.{page} で照合できません')
    for finding in result['findings']:
        indices = finding['source_numbers']
        if len(set(indices)) != len(indices) or max(indices) > len(result['sources']):
            raise ValueError('所見の出典番号が重複または範囲外です')
        quotes = [result['sources'][i-1]['quote'] for i in indices]
        for measure in finding.get('measurements', []):
            interval_fields = set()
            if schema is not RESULT_SCHEMA and measure.get('ci_lower') and measure.get('ci_upper'):
                # Upper bounds following a dash need a range match; checking isolated numbers would
                # either reject valid intervals or accept a different sign or a suffix of a value.
                lo, hi = (re.escape(measure[k]) for k in ('ci_lower', 'ci_upper'))
                interval = rf'(?<![\d.,+−–-]){lo}\s*(?:[-–—,;]|to)\s*{hi}(?!\d|[.,]\d)'
                if not re.search(interval, measure['reported']):
                    raise ValueError('信頼区間の上下限を同じ原文表記で照合できません')
                interval_fields.update(('ci_lower','ci_upper'))
                if measure.get('ci_level'):
                    level = measure['ci_level'].rstrip('%').strip()
                    if not re.search(r'(?<![\d.])'+re.escape(level)+r'\s*%',measure['reported']) and not any(
                        quoted(measure['ci_level'],q) for q in quotes
                    ):
                        raise ValueError('信頼水準の原文表記を確認してください')
                    interval_fields.add('ci_level')
                named = re.search(r'\b(a?(?:OR|HR|RR|PR|MSR))\b',measure['reported'])
                if named and measure['label'] in ('OR','aOR','HR','aHR','RR','aRR','aPR','aMSR'):
                    if named[1] != measure['label']:
                        raise ValueError('効果指標と原文表記が一致しません')
                    if measure.get('estimate') and not quoted(measure['estimate'],measure['reported'][:re.search(interval,measure['reported']).start()]):
                        raise ValueError('効果推定値と原文表記が一致しません')
            for field in ('reported', 'estimate', 'ci_lower', 'ci_upper', 'ci_level', 'p_value', 'denominator'):
                if field in interval_fields:
                    continue
                value = measure.get(field)
                if value is not None and not any(quoted(value, quote) for quote in quotes):
                    raise ValueError(f'測定値 {field} を所見の原文引用で確認できません')


def build_master(plan, results, run_id, studies=None):
    chapters, evidence, sources, issues, highlights = [], [], [], [], []
    by_source, by_fact = {}, {}
    if len(results) != len(plan['jobs']):
        raise ValueError('全章が受理されていません')
    for job, result in zip(plan['jobs'], results):
        refs, blocks = [], []
        for src in result['sources']:
            page = src['page']
            record = dict(source='original_pdf', pdf_page_start=page, quote=src['quote'],
                          **{k:src[k] for k in ('figure','table','printed_page','citation') if k in src})
            key = digest(record)
            if key not in by_source:
                by_source[key] = f's{len(sources)+1}'
                sources.append(dict(id=by_source[key], **record))
            ref = by_source[key]
            refs.append(ref)
            if src.get('visual_only'):
                issues.append(dict(path=f'{job["id"]}/{ref}', status='recorded', note=f'原本PDF p.{page} の引用は目視照合'))
        for finding in result['findings']:
            ids = list(dict.fromkeys(refs[i-1] for i in finding['source_numbers']))
            record = {k:finding[k] for k in DEFS['evidence']['properties'] if k in finding and k != 'measurements'}
            record['source_refs'] = ids
            measures = finding.get('measurements', [])
            key = digest([record, measures, finding.get('study')]) if studies is not None else digest([record, measures])
            if studies is not None and finding.get('result_key'):
                key = ('linked', finding['result_key'])
            if key not in by_fact:
                eid = f'e{len(evidence)+1}'
                record['measurements'] = [dict(id=f'{eid}-m{i}', source_refs=ids, **m) for i,m in enumerate(measures,1)]
                by_fact[key] = dict(id=eid, **record)
                evidence.append(by_fact[key])
            else:
                existing = by_fact[key]
                if studies is not None and finding.get('result_key'):
                    if studies[existing['id']] != finding['study'] or existing['kind'] != finding['kind']:
                        raise ValueError('同一結果キーの研究・対象・解析が一致しません')
                    existing['source_refs'] = list(dict.fromkeys(existing['source_refs'] + ids))
                    adds_estimate = any(m.get('estimate') for m in measures) and not any(
                        m.get('estimate') for m in existing['measurements'])
                    if measures and (not existing['measurements'] or adds_estimate):
                        existing['statement_ja'] = finding['statement_ja']
                    for name in ('conditions','exceptions'):
                        existing[name] = list(dict.fromkeys(existing.get(name,[]) + finding.get(name,[])))
                    for m in measures:
                        prior = next((v for v in existing['measurements'] if all(v.get(k)==val for k,val in m.items())),None)
                        if prior:
                            prior['source_refs'] = list(dict.fromkeys(prior['source_refs'] + ids))
                        elif any(v['label']==m['label'] for v in existing['measurements']):
                            raise ValueError('同一結果キーに異なる測定値があります')
                        else:
                            existing['measurements'].append(dict(m, id=f"{existing['id']}-m{len(existing['measurements'])+1}", source_refs=ids))
            fact = by_fact[key]
            if studies is not None and 'study' in finding:
                studies[fact['id']] = finding['study']
            blocks.append(dict(role=finding['role'], origin='paper', text_ja=finding['statement_ja'], evidence_ids=[fact['id']]))
            if finding.get('interpretation_ja'):
                blocks.append(dict(role='clinical_meaning', origin='ai_interpretation',
                                   text_ja=finding['interpretation_ja'], evidence_ids=[fact['id']]))
            if finding.get('highlight') and fact not in highlights:
                highlights.append(fact)
        # The chapter locator covers the packet, while each finding keeps its exact page.
        # Both are ordinary source references understood by the original learning-v2 reader.
        locator = 'range-' + job['id']
        sources.append(dict(id=locator, source='original_pdf', section=job['title'],
                            pdf_page_start=job['start'], pdf_page_end=job['end'], quote=result['sources'][0]['quote']))
        chapters.append(dict(id=job['id'], parent_id=job['parent'], section_title_original=job['title'],
                             title_ja=result['title_ja'], summary_ja=result['summary_ja'], blocks=blocks,
                             source_refs=[locator, *dict.fromkeys(refs)], visualization_ids=[]))
    if not evidence:
        raise ValueError('章別に医学的所見が1件もなく、概要PNGの根拠を設定できません。章の精読内容を確認してください')
    selected = highlights or evidence
    labels = {'study_result':'研究結果', 'cited_study':'引用研究', 'author_statement':'著者の記述',
              'author_hypothesis':'著者の仮説', 'guideline_recommendation':'ガイドライン推奨', 'expert_consensus':'専門家合意'}
    statements = []
    for fact in highlights:
        parts = [labels[fact['kind']] + '：' + fact['statement_ja']]
        for field, label in [('population','対象'), ('conditions','条件'), ('exceptions','例外'),
                             ('reported_certainty','論文記載の確実性')]:
            value = fact.get(field)
            if value:
                parts.append(label + '：' + ('／'.join(value) if isinstance(value, list) else value))
        statements.append('　'.join(parts))
    if not highlights:
        issues.append(dict(path='png_plan', status='unverified', note='概要用の知見は未選定。根拠一覧は候補として保持。'))
    overview = dict(topic=[], clinical_area='', one_line_summary='', why_important='', pico=None, study_design='',
                    population_setting='', figure_table_summary='', main_results=[], result_evidence_ids=[], safety='',
                    limitations=[], applicability_to_japanese_pediatric_clinic='', practice_change=None,
                    practice_change_reason='', tomorrow_action=[], source='原本PDFの章別精読',
                    summary_jp='\n'.join(statements))
    for fact, statement in zip(highlights, statements):
        if fact['kind'] in ('study_result', 'cited_study'):
            overview['main_results'].append(statement)
            overview['result_evidence_ids'].append([fact['id']])
    local_count = sum(r['method'] == 'manual_local' for r in results)
    # v2 has manual_web, api_batch and mixed. Record the precise provenance
    # of the human/local assembly workflow alongside its v2-compatible enum.
    method = 'mixed' if local_count else 'manual_web'
    issues.append(dict(path='generation_metadata.method', status='recorded',
                       note=f'章別結果の作成：手動ローカル {local_count}件、手動Web {len(results)-local_count}件。'
                            '原本の分割・引用照合・統合はローカル処理。'))
    if all(r['method'] == 'api_batch' for r in results):
        method = 'api_batch'
        issues[-1]['note'] = f'章別結果の作成：API Batch {len(results)}件。原本引用をローカルで照合。'
    return dict(schema_version='learning-v2', metadata=plan['metadata'], article_type=plan['article_type'],
                classification_reason=('PubMed書誌・論文名で特定できた種別。特定できない場合は不明。'
                                       if method == 'api_batch' else '分割準備時の指定'), abstract=dict(source=None,status='not_acquired',parts=[]),
                overview=overview, clinical_interpretation=[], chapters=chapters, evidence=evidence,
                visualizations=[], source_references=sources,
                png_plan=[dict(id='overview', role='overview', title=plan['metadata']['title'],
                               message='章別の知見と適用条件', evidence_ids=[f['id'] for f in selected],
                               required_caveats=list(dict.fromkeys(v for f in selected for v in f.get('exceptions', []))))],
                quality_control=dict(human_checked=False, review_event_id=None, issues=issues),
                generation_metadata=dict(method=method, schema_revision='learning-v2',
                                         prompt_version=VERSION, run_id=run_id))
