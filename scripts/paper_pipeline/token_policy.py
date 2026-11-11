"""Offline output-budget heuristic; records evidence, not a clinical classification."""
import re

from pypdf import PdfReader


def output_budget(title, text):
    content = (title + '\n' + text).lower()
    guideline = bool(re.search(r'guideline|consensus|practice recommendation', content[:15000]))
    # Several distinct treatment/population domains plus substantial source material.
    domains = [name for name, pattern in (
        ('薬物治療', r'systemic therap|biologic|immunomodulator'),
        ('小児', r'paediatric|pediatric|children'),
        ('妊娠・授乳', r'pregnan|breastfeed|lactation'),
        ('監視・長期管理', r'monitoring|maintenance|follow.up'),
        ('急性期・臓器支持', r'resuscitation|vasoactive|ventilation'),
    ) if re.search(pattern, content[:15000])]
    complex_document = guideline and (len(text) >= 180000 or (len(text) >= 120000 and len(domains) >= 3))
    reason = ('長いガイドライン／複数領域: ' + '、'.join(domains)) if complex_document else '通常・範囲の限定された論文'
    return {'max_output_tokens':48000 if complex_document else 32000,
            'output_budget_policy':'content-v1', 'output_budget_reason':reason}


def source_output_budget(paper, path):
    reader = PdfReader(path)
    text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    return output_budget(paper.get('title') or '', text)
