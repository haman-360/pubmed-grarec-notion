"""Source-bound graphic facts and local, fail-closed PNG review."""
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

from .store import now

VALUES = ('Yes', 'No', 'Unclear', '未評価')
LAYOUT = {
    'guideline_consensus': '更新範囲を主見出しにする。推奨・専門家合意・引用試験結果を分ける。合意率、推奨強度、エビデンスの確実性を区別する。欧州承認を日本承認へ読み替えない。RCTの群間差を作らない。',
    'original_article': '対象・研究デザインから主要評価項目と解析集団別の結果へ進む。観察上の関連から因果を断定しない。',
    'systematic_review': '評価項目と対象集団ごとに統合結果を分ける。全体推定値をサブグループに転用しない。解析方法・異質性・限界を保持する。',
    'narrative_review': '扱った範囲と論点を図解する。統合効果量や推奨を創作しない。',
}
CHECKS = {
    'scope': '対象集団・主題・更新範囲が一致し、イラストもその範囲を示す',
    'results': '数値・単位・群名・解析方法・効果の方向を、評価項目と集団ごとに照合した',
    'guidance': '試験結果・推奨強度・確実性・専門家合意・未確認のAI提案を区別した',
    'message': 'Take Home Message・限界・日本への適用が原文と整合し、未記載の医学的事実がない',
    'text': '日本語・数字が読め、誤字・脱字・文字化けや矛盾する別の表示がない',
}


def fixed_facts(data):
    if 'topic' in data and not (isinstance(data['topic'], list) and all(isinstance(t, str) for t in data['topic'])):
        raise ValueError('topicには文字列の配列が必要です')
    if 'topic_title' in data and not isinstance(data['topic_title'], str):
        raise ValueError('topic_titleには文字列が必要です')
    value = data.get('practice_change')
    if value is not None and value not in VALUES[:3]:
        raise ValueError('practice_changeの値が不正です')
    facts = {key: data.get(key) for key in (
        'pmid', 'title', 'journal', 'year', 'topic_id', 'topic_title', 'topic',
        'paper_type', 'study_type', 'study_design', 'clinical_question', 'population_setting',
        'one_line_summary', 'pico', 'main_results', 'safety', 'limitations', 'practice_change_reason',
        'applicability_to_japanese_pediatric_clinic', 'tomorrow_action')}
    # Keep each number with its outcome, units, denominator and population in its source sentence.
    # Keep recommendation conditions and votes together, without duplicating lengthy quotations.
    facts['recommendations'] = [{k: v for k, v in rec.items() if k != 'evidence_references'}
                                for rec in data.get('recommendations', [])]
    facts.update(practice_change=value, practice_change_display='Practice Change：' + (value or '未評価'),
                 take_home_evidence_fields=['one_line_summary', 'main_results', 'recommendations', 'limitations',
                                            'practice_change_reason', 'applicability_to_japanese_pediatric_clinic'],
                 warnings=[] if 'practice_change' in data else ['practice_change 欠落のため未評価'])
    return facts


def inspect_png(path, facts):
    """OCR can flag a mismatch. Even a perfect match never grants approval."""
    status = dict(status='review_required', reason='OCRを利用できません。画像を目視で照合してください。')
    if sys.platform != 'darwin':
        return status
    try:
        result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript',
                                 str(Path(__file__).with_name('read_png.js')), str(path)],
                                capture_output=True, timeout=45, check=True)
        text = unicodedata.normalize('NFKC', result.stdout.decode())
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return status
    pmids = re.findall(r'(?<![a-z0-9])PMID\s*[:#]?\s*([0-9]+)', text, re.I)
    values = re.findall(r'Practice\s*Change\s*[:：]?\s*(Yes(?![a-z])|No(?![a-z])|Unclear(?![a-z])|未\s*評\s*価)', text, re.I)
    expected = facts['practice_change'] or '未評価'
    wrong = ['PMID '+p for p in pmids if p != str(facts['pmid'])]
    wrong += ['Practice Change：'+v for v in values if ''.join(v.split()).lower() != expected.lower()]
    if wrong:
        return dict(status='rejected', reason='OCRで固定情報と不一致：' + '、'.join(dict.fromkeys(wrong)))
    status['reason'] = ('OCRでPMIDと評価の一致を読み取りました。' if pmids and values else
                        'OCRで必須表示をすべて特定できません。') + '画像全体の目視確認が必要です。'
    return status


def labels_match(facts, observed_pmid, observed_value):
    expected_pmid = str(facts['pmid'] or '')
    if (not isinstance(observed_pmid, str) or (expected_pmid and not re.fullmatch(r'[0-9]+', observed_pmid))
            or observed_value not in VALUES):
        raise ValueError('PNGから読めたPMIDとPractice Changeを入力してください。判読できない場合は保留してください')
    return observed_pmid == expected_pmid and observed_value == (facts.get('practice_change') or '未評価')


def human_review(facts, observed_pmid, observed_value, checked, reject=False):
    if reject:
        return dict(status='rejected', reason='目視で不一致を確認しました。')
    receipt = dict(observed_pmid=observed_pmid, observed_value=observed_value, at=now())
    if not labels_match(facts, observed_pmid, observed_value):
        return dict(receipt, status='rejected', reason='画像から読み取ったPMIDまたはPractice Changeが固定情報と一致しません。')
    if not isinstance(checked, list) or not all(isinstance(k, str) for k in checked) or set(checked) != set(CHECKS):
        raise ValueError('画像全体を固定情報と照合し、全項目を確認してください')
    return dict(receipt, status='approved', checks=list(CHECKS), reason='固定情報との目視照合済み')
