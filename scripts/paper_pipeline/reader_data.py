"""Read-only projection of existing paper records for the offline reader."""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from import_chatgpt_summary import strip_markdown_json_fence
from .graphics import VERSION, validate_master
from .manual import pmid_value

SCHEMA = Path(__file__).resolve().parents[2] / 'schemas/learning-v2.json'


@dataclass
class Paper:
    pmid: str
    title: str
    citation: list[str]
    sections: list = field(default_factory=list)


@dataclass
class Bars:
    unit: str
    values: list[tuple[str, str]]
    maximum: str


def comparison_bars(panel, rows):
    """Only draw comparable nonnegative estimates explicitly requested on a linear axis."""
    axis = panel.get('axis', {})
    if (panel['type'] != 'bar_chart' or axis.get('scale') != 'linear' or not axis.get('unit')
            or len(rows) < 2 or any(len(item.get('measurement_ids', [])) != 1 for item in panel['items'])):
        return None
    for row in rows:
        if (row.get('label') != axis['measure'] or row.get('unit') != axis['unit']
                or not re.fullmatch(r'[0-9]{1,15}(?:\.[0-9]{1,15})?', row.get('estimate') or '')):
            return None
        # Keep richer or ambiguous reported strings in the source table.
        if row['reported'].strip() not in {row['estimate'], row['estimate'] + row['unit'],
                                          row['estimate'] + ' ' + row['unit']}:
            return None
    if len({(r.get('timepoint'), r.get('analysis_set')) for r in rows}) != 1:
        return None
    maximum = max((r['estimate'] for r in rows), key=Decimal)
    if Decimal(maximum) > 0:
        return Bars(axis['unit'], [(r['項目'], r['estimate']) for r in rows], maximum)
    return None


def entries(data, fields):
    return [(label, data.get(key)) for key, label in fields]


def index_by_id(items):
    index = {item['id']: item for item in items}
    if len(index) != len(items):
        raise ValueError('Master JSON内に重複したIDがあります')
    return index


def lookup(index, ids):
    try:
        return [index[key] for key in ids]
    except KeyError as error:
        raise ValueError(f'参照先のIDが見つかりません：{error.args[0]}') from error


def learning_sections(master):
    sources = index_by_id(master['source_references'])
    evidence = index_by_id(master['evidence'])
    chapters = index_by_id(master['chapters'])
    parts = index_by_id(master['abstract']['parts'])
    panels = index_by_id(master['visualizations'])
    units = index_by_id([m for e in evidence.values() for m in e.get('measurements', [])])
    index_by_id(master['png_plan'])
    for plan in master['png_plan']:
        lookup(evidence, plan['evidence_ids'])
    for source in sources.values():
        if source.get('pdf_page_end', source.get('pdf_page_start', 0)) < source.get('pdf_page_start', 0):
            raise ValueError('PDFページの範囲が逆順です')
        if 'abstract_part' in source:
            lookup(parts, [source['abstract_part']])
    for item in [*evidence.values(), *units.values()]:
        lookup(sources, item['source_refs'])
    for panel in panels.values():
        items = index_by_id(panel['items'])
        for item in [panel, *items.values(), *panel.get('edges', [])]:
            lookup(evidence, item['evidence_ids'])
        for item in items.values():
            selected = item.get('measurement_ids', [])
            available = {m['id'] for e in lookup(evidence, item['evidence_ids']) for m in e.get('measurements', [])}
            if not set(selected) <= available:
                raise ValueError('図の測定値が指定した根拠に含まれていません')
        for edge in panel.get('edges', []):
            lookup(items, [edge['from'], edge['to']])
        if 'answer' in panel:
            lookup(items, [panel['answer']['item_id']])
            lookup(evidence, panel['answer']['evidence_ids'])

    def cited_blocks(blocks):
        result = []
        for block in blocks:
            lookup(evidence, block['evidence_ids'])
            origin = 'AIによる臨床的解釈' if block['origin'] == 'ai_interpretation' else '論文の記述'
            roles = {'finding': '知見', 'specialist_detail': '専門的解説', 'mechanism': '機序',
                     'result': '結果', 'author_view': '著者の見解', 'uncertainty': '未解決の点',
                     'clinical_meaning': '臨床的意義', 'japan_applicability': '日本の診療への適用'}
            result.append((origin + ' ／ ' + roles[block['role']],
                           {'本文': block['text_ja'], '根拠ID': block['evidence_ids']}))
        return result

    sections = []
    for chapter in chapters.values():
        parents = {chapter['id']}
        parent = chapter['parent_id']
        while parent:
            if parent in parents:
                raise ValueError('章の親子関係が循環しています')
            parents.add(parent)
            parent = lookup(chapters, [parent])[0]['parent_id']
        body = [('原文見出し', chapter['section_title_original']), ('概要', chapter['summary_ja'])]
        body.extend(cited_blocks(chapter['blocks']))
        for panel in lookup(panels, chapter['visualization_ids']):
            rows = []
            for item in panel['items']:
                for measure in lookup(units, item.get('measurement_ids', [])):
                    rows.append({'項目': item['label'], **measure})
            if rows:
                chart = comparison_bars(panel, rows)
                if chart:
                    body.append((panel['title_ja'], chart))
                body.append((panel['title_ja'] + '（原文の値）', rows))
            elif panel['type'] == 'comparison':
                body.append((panel['title_ja'], panel['items']))
        body.append(('出典', lookup(sources, chapter['source_refs'])))
        sections.append((chapter['title_ja'], body))
    if master['clinical_interpretation']:
        sections.append(('論文全体の臨床的解釈', cited_blocks(master['clinical_interpretation'])))
    sections.append(('根拠と原文の対応', [(e['id'], {**e, 'source_refs': lookup(sources, e['source_refs'])})
                                     for e in evidence.values()]))
    if master['quality_control']['issues']:
        sections.append(('品質確認の記録', [('要確認箇所', master['quality_control']['issues'])]))
    return sections


def paper_from_json(raw, cached_abstract=''):
    data = json.loads(strip_markdown_json_fence(raw.decode('utf-8-sig')))
    if not isinstance(data, dict):
        raise ValueError('論文1本の精読JSONを指定してください')
    version = data.get('schema_version')
    if version not in (None, VERSION, 'learning-v2') or ('schema_version' in data and version is None):
        raise ValueError(f'未対応のスキーマ版です：{version}')
    try:
        if version == 'learning-v2':
            Draft202012Validator(json.loads(SCHEMA.read_text())).validate(data)
        elif version == VERSION:
            validate_master(data)
    except ValidationError as error:
        raise ValueError(f'精読JSONの形式を確認してください：{error.json_path}') from error

    if version == 'learning-v2':
        meta = data['metadata']
        overview = data['overview']
        abstract = [(part['label'] or '抄録', {'原文': part['original'], '日本語訳': part['translation_ja']})
                    for part in data['abstract']['parts']]
        if not abstract:
            statuses = {'not_reported': '原文に記載なし', 'not_applicable': '非該当', 'unverified': '未確認'}
            abstract = [('原文・日本語訳', statuses.get(data['abstract']['status'], '未取得'))]
        details = learning_sections(data)
        checked = data['quality_control']['human_checked']
        study_type = data['article_type']
    else:
        # Historical Web exports use both upper- and lower-case bibliographic keys.
        meta = overview = {key.lower(): value for key, value in data.items()}
        if 'topic_id' in meta or not any(k in meta for k in ('main_results', 'one_line_summary', 'summary_jp')):
            raise ValueError('画像用データや依頼文ではなく、論文全体の精読JSONを指定してください')
        abstract = [('原文', meta.get('abstract') or cached_abstract), ('日本語訳', meta.get('abstract_ja'))]
        details = []
        checked = meta.get('human_checked')
        study_type = meta.get('study_type') or meta.get('paper_type')
    pmid = pmid_value(str(meta.get('pmid', '')))
    title = meta.get('title')
    if not isinstance(title, str) or not title.strip():
        raise ValueError('論文タイトルがありません')
    citation = [str(meta[key]) for key in ('journal', 'year') if meta.get(key)]
    if meta.get('doi'):
        citation.append('DOI ' + str(meta['doi']))
    paper = Paper(pmid, title, citation)
    paper.sections.append(('論文の概要', [
        ('Take Home Message', overview.get('take_home_message') or overview.get('one_line_summary')),
        *entries(overview, [('summary_jp', '日本語要約'), ('why_important', 'なぜ重要か'),
                            ('topic', 'テーマ'), ('clinical_area', '臨床領域')])]))
    paper.sections.append(('Abstract', abstract or [('原文・日本語訳', None)]))
    paper.sections.append(('研究の方法と対象', [('論文種別', study_type),
        *entries(overview, [('study_design', '研究デザイン'), ('population_setting', '対象と設定'), ('pico', 'PICO')])]))

    results = overview.get('main_results')
    if version or (isinstance(results, list) and isinstance(overview.get('result_evidence'), list)):
        ids = overview.get('result_evidence_ids', [])
        if version == 'learning-v2' and len(ids) != len(results):
            raise ValueError('主要結果数と根拠IDの対応数が一致しません')
        result_rows = []
        evidence = index_by_id(data['evidence']) if version == 'learning-v2' else {}
        for i, result in enumerate(results):
            if version == 'learning-v2':
                references = [e['id'] for e in lookup(evidence, ids[i])]
            else:
                references = [e for e in overview['result_evidence'] if isinstance(e, dict) and e.get('result_index') == i]
            result_rows.append({'結果': result, '根拠': references})
        results = result_rows
    paper.sections.append(('主要結果と図表', [('主要結果', results), ('図表の要点', overview.get('figure_table_summary'))]))
    if version == VERSION:
        paper.sections[0][1].extend([
            ('全体の中心メッセージ', data['overview']['central_message']),
            ('臨床疑問', data['clinical_questions']),
        ])
        sections = {s['section_id']: s for s in data['document_structure']}
        for topic in data['topics']:
            body = [('臨床疑問', topic['clinical_question']), ('中心メッセージ', topic['central_message']),
                    ('関連結果', [results[i] for i in topic['result_indices']]),
                    ('関連する推奨・合意', [data['recommendations'][i] for i in topic['recommendation_indices']]),
                    ('テーマのPractice Change', topic.get('practice_change') or '未評価'),
                    ('判断の理由', topic['practice_change_reason']),
                    ('原文の対応箇所', lookup(sections, topic['source_sections']))]
            details.append((topic['topic_title'], body))
    paper.sections.extend(details)
    paper.sections.append(('安全性と限界', entries(overview, [('safety', '安全性'), ('limitations', '研究の限界')])))
    paper.sections.append(('日本の小児科外来への示唆', [
        ('Practice Change', overview.get('practice_change') or '未評価'),
        *entries(overview, [('practice_change_reason', '判断の理由'), ('clinical_impact', '臨床的意義'),
                            ('applicability_to_japanese_pediatric_clinic', '外来への適用可能性'),
                            ('tomorrow_action', '明日からの診療への示唆')])]))
    verification = ('確認済み' if checked else '未確認') if type(checked) is bool else '未取得'
    paper.sections.append(('出典と確認状況', [
        ('情報源', overview.get('source') or data.get('source_level')),
        ('根拠についての記録', overview.get('evidence_notes')),
        ('原文の構成', data.get('document_structure') if version == VERSION else
         ('章ごとの出典を参照' if version == 'learning-v2' else None)),
        ('精読JSONの人による確認記録', verification),
        ('入力した精読JSONのSHA-256', hashlib.sha256(raw).hexdigest()),
        ('精読元資料の記録上のハッシュ', meta.get('pdf_sha256') or data.get('source_hash'))]))
    return paper
