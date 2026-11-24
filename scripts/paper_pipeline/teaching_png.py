"""Project a validated teaching edition into the existing image-generation contract."""
import json
from pathlib import Path

from .graphics import graphic_data, image_topics, validate_master, web_prompt_text
from .manual import png_request
from .store import digest
from .teaching import STATUS


def encode(data):
    return json.dumps(data, ensure_ascii=False, indent=2).encode()


def png_exports(master):
    meta, ov, teaching = master['metadata'],master['overview'],master['teaching']
    sources = {s['id']:s for s in master['source_references']}
    facts = {e['id']:e for e in master['evidence']}
    chapters = master['chapters']
    def references(ids):
        refs = dict.fromkeys(s for eid in ids for s in facts[eid]['source_refs'])
        return [dict(location='PDF p.'+str(sources[s]['pdf_page_start']),supporting_text=sources[s]['quote']) for s in refs]

    kind = {'rct':'original_article','observational':'original_article','case_report':'original_article',
            'meta_analysis':'systematic_review','scoping_review':'narrative_review','guideline':'guideline_consensus',
            'consensus':'guideline_consensus'}.get(master['article_type'],master['article_type'])
    evidence = [dict(result_index=i,**ref) for i,ids in enumerate(ov['result_evidence_ids']) for ref in references(ids)]
    rec_ids = [eid for eid,f in facts.items() if f['kind'] in ('guideline_recommendation','expert_consensus')]
    recs = []
    for eid in rec_ids:
        f = facts[eid]
        recs.append(dict(kind='recommendation' if f['kind']=='guideline_recommendation' else 'expert_consensus',
                         text=f['statement_ja'],population=f.get('population',''),conditions='／'.join(f.get('conditions',[])),
                         exceptions='／'.join(f.get('exceptions',[])),agreement='',evidence_strength=f.get('reported_certainty',''),
                         evidence_references=references([eid])))
    sections = []
    for ch in chapters:
        ref = sources['range-'+ch['id']]
        sections.append(dict(section_id=ch['id'],title=ch['title_ja'],location=f"PDF pp.{ref['pdf_page_start']}–{ref['pdf_page_end']}"))
    message = ov['one_line_summary'] or STATUS[teaching['fields']['one_line_summary']['status']]
    topics = []
    for i, unit in enumerate(teaching['units'],1):
        unit_facts = {eid for ch in chapters if ch['id'] in unit['chapter_ids'] for b in ch['blocks'] for eid in b['evidence_ids']}
        if 'evidence_ids' in unit:
            unit_facts = set(unit['evidence_ids'])
        ri = [j for j,ids in enumerate(ov['result_evidence_ids']) if unit_facts.intersection(ids)]
        gi = [j for j,eid in enumerate(rec_ids) if eid in unit_facts]
        if ri or gi:
            topics.append(dict(topic_id=f'topic-{i}',topic_title=unit['title'],
                               central_message=ov['main_results'][ri[0]] if ri else recs[gi[0]]['text'],
                               clinical_question=unit['title'],source_sections=unit['chapter_ids'],
                               result_indices=ri,recommendation_indices=gi,practice_change=None,practice_change_reason='テーマ別には未評価'))
    if not topics:
        topics.append(dict(topic_id='main',topic_title=(ov['topic'] or [meta['title']])[0],central_message=message,
                           clinical_question=message,source_sections=[ch['id'] for ch in chapters],result_indices=[],
                           recommendation_indices=[],practice_change=ov['practice_change'],practice_change_reason=ov['practice_change_reason']))
    legacy = {k:(meta[k] or '') for k in ('pmid','title','journal','year','doi')}
    legacy.update(ov, schema_version='hybrid-v1',paper_type=kind,source_hash=meta['pdf_sha256'],
                  classification_reason=master['classification_reason'],study_type=master['article_type'],
                  clinical_impact='／'.join(b['text_ja'] for b in master['clinical_interpretation']),
                  evidence_notes='原本PDFの引用に照合。医学専門家の確認状況は教材の品質記録を参照。',
                  summary_jp=ov.get('summary_jp') or message, result_evidence=evidence, recommendations=recs,
                  document_structure=sections,clinical_questions=[t['clinical_question'] for t in topics],
                  visualization_strategy=dict(mode='single' if len(topics)==1 else 'topics',reason='学習単元ごとに主要結果と関連声明を対応付けた。',
                    independent_question_count=len(topics),information_loss_if_single='各研究の対象・結果・限界',gallery_message=message,
                    include_overview=len(topics)>1), overview=dict(central_message=message,clinical_areas=[ov['clinical_area']]),topics=topics)
    for key, record in teaching['fields'].items():
        if record['status'] == 'recorded' or key not in legacy or key in ('pico','practice_change'):
            continue
        description = STATUS[record['status']] + '　' + record['reason']
        if isinstance(legacy[key],str):
            legacy[key] = description
        elif isinstance(legacy[key],list):
            legacy[key] = [description]
    for key in ('applicability_to_japanese_pediatric_clinic','practice_change_reason','clinical_impact'):
        if legacy[key]:
            legacy[key] = 'AIによる臨床的解釈：'+legacy[key]
    legacy['tomorrow_action'] = ['AIによる臨床的解釈：'+s for s in ov['tomorrow_action']]
    validate_master(legacy)
    master['png_plan'] = []
    for topic in image_topics(legacy):
        ids = list(dict.fromkeys(e for i in topic['result_indices'] for e in ov['result_evidence_ids'][i]))
        ids.extend(rec_ids[i] for i in topic['recommendation_indices'] if rec_ids[i] not in ids)
        if not ids:
            ids = teaching['fields']['one_line_summary']['evidence_ids'] or list(facts)
        master['png_plan'].append(dict(id=topic['topic_id'],role='overview' if len(topics)==1 or topic['topic_id']=='overview' else 'additional',
                    title=topic['topic_title'], message=topic['central_message'],evidence_ids=ids,
                    required_caveats=list(dict.fromkeys(note for eid in ids for note in facts[eid].get('exceptions',[])))))
    raw = encode(legacy)
    files = {'png-master.json':raw}
    instruction = (Path(__file__).resolve().parents[2]/'prompts/grarec-v2.md').read_text()
    for topic in image_topics(legacy):
        key = topic['topic_id']
        graphic = encode(graphic_data(legacy,topic,digest(raw)))
        prompt = web_prompt_text(meta,{'id':digest(raw)},key,digest(graphic),instruction,graphic.decode())
        files[f'png-request-{key}.json'] = png_request(meta['pmid'],key,prompt).encode()
    return files
