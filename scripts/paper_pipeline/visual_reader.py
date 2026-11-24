"""Shared source excerpts and packaging for offline visual readers."""
from base64 import b64encode
from hashlib import sha256
from html import escape
from string import Template

from .reader import TEMPLATES


def source_excerpt(source, has_pdf, reported=None):
    page = source.get('pdf_page_start')
    end = source.get('pdf_page_end', page)
    location = 'PubMed 抄録'
    if page:
        pages = str(page) if page == end else f'{page}–{end}'
        location = f'原本PDF p.{pages}'
        if has_pdf:
            location = f'<a data-pdf-page="{page}" target="_blank" rel="noopener">{location} ↗</a>'
    label = ' · '.join(escape(source[k]) for k in ('table', 'figure', 'citation') if source.get(k))
    original = source.get('quote') or ''
    quote = escape(original)
    if reported and reported in original:
        before, match, after = original.partition(reported)
        quote = escape(before) + '<mark>' + escape(match) + '</mark>' + escape(after)
    return (f'<div class="source" data-source="{escape(source["id"])}"><p>{location}　{label}</p>'
            f'<blockquote lang="und">{quote}</blockquote></div>')


def package_visual_reader(paper, template, panels):
    css = (TEMPLATES/f'{template}.css').read_text()
    js = (TEMPLATES/f'{template}.js').read_text()
    return Template((TEMPLATES/f'{template}.html').read_text()).substitute(
        title=escape((paper.teaching_master['overview']['topic'] or [paper.title])[0]), pmid=escape(paper.pmid),
        original=escape(paper.title), panels=panels,
        pdf=b64encode(paper.original_pdf or b'').decode(), css=css, js=js,
        css_hash=b64encode(sha256(css.encode()).digest()).decode(),
        js_hash=b64encode(sha256(js.encode()).digest()).decode()).encode()
