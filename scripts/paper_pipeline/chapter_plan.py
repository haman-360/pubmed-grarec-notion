"""Map a PDF outline (or supplied headings) to bounded, source-numbered packets."""
import re

from pypdf import PdfWriter

# Restrict text inference to identifiable headings. Unrecognized prose remains in the packet.
HEADING = re.compile(
    r'(?:(?P<number>[1-9]\d?(?:\.\d+){1,4}|[1-9]\d?[.)])\s+(?-i:[A-Z])[^.!?]{1,85}'
    r'|(?:Abstract|Introduction|Background|Methods|Results|Discussion|Limitations|Conclusions?|References|'
    r'Recommendations|Evidence summary|背景|方法|結果|考察|結論|参考文献)'
    r'|第[0-9０-９一二三四五六七八九十]+[章節]\s*[^。]{0,70})', re.I)


def outline_entries(reader):
    entries = []

    def visit(items, level):
        for item in items:
            if isinstance(item, list):
                visit(item, level + 1)
            else:
                page = reader.get_destination_page_number(item)
                title = str(item.title or '').strip()
                if title and page is not None and 0 <= page < len(reader.pages):
                    entries.append(dict(title=title, page=page + 1, level=level))

    visit(reader.outline, 1)
    return entries


def page_text(page):
    # PDFs may emit a bold heading word by word. Match complete bold runs back
    # to whole extracted lines to avoid promoting inline emphasis to headings.
    bold, run = set(), []

    def flush():
        bold.update(''.join(line.split()).casefold() for line in ''.join(run).splitlines())
        run.clear()

    def visitor(value, _cm, _tm, font, _size):
        if font and 'bold' in str(font.get('/BaseFont', '')).lower():
            run.append(value)
        elif value.strip():
            flush()
        elif run:
            run.append(value)

    text = page.extract_text(visitor_text=visitor) or ''
    flush()
    return text, bold


def text_headings(texts, emphasis):
    entries, seen = [], set()
    back_matter = False
    for page, (text, bold) in enumerate(zip(texts, emphasis), 1):
        for line in text.splitlines():
            # Manuscript PDFs may append a separate line number to each line.
            title = line.strip()
            cleaned = re.sub(r'\s+\d+$', '', title)
            if back_matter:
                # Bibliographic entries and column headings are not new sections.
                if not re.fullmatch(r'(?:figure legends?|tables?|figures?|appendix(?: [A-Z0-9])?|'
                                    r'supplement(?:ary material)?|付録|図表)', cleaned, re.I):
                    continue
            match = HEADING.fullmatch(title) or HEADING.fullmatch(cleaned)
            if match:
                title = match[0]
            else:
                if ''.join(title.split()).casefold() not in bold:
                    title = cleaned
                if (''.join(title.split()).casefold() not in bold or not 4 <= len(title) <= 100
                        or not any(c.isalpha() for c in title) or title.endswith(('.', '。', ':', '：'))
                        or re.match(r'(?:fig(?:ure)?|table)\.?\s*\d', title, re.I)):
                    continue
            if title.casefold() not in seen:
                seen.add(title.casefold())
                if title.casefold() in ('references', 'bibliography', '参考文献'):
                    back_matter = True
                number = match['number'] if match else None
                entries.append(dict(title=title, page=page, level=1 + number.rstrip('.)').count('.') if number else 1))
    return entries


def validate_headings(entries, total):
    if not isinstance(entries, list) or not entries:
        raise ValueError('章指定は title・page・任意のlevelを持つ配列で指定してください')
    result = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {'title', 'page', 'level'}:
            raise ValueError('章指定の項目は title、page、level です')
        title, page, level = entry.get('title'), entry.get('page'), entry.get('level', 1)
        if (not isinstance(title, str) or not title.strip() or type(page) is not int
                or not 1 <= page <= total or type(level) is not int or level < 1):
            raise ValueError('章見出し・開始ページ・階層を確認してください')
        if result and result[-1]['page'] > page:
            raise ValueError('開始ページは原本順に指定してください')
        result.append(dict(title=title.strip(), page=page, level=level))
    return result


def chapter_plan(reader, headings=None, max_pages=10, max_chars=32000):
    if reader.is_encrypted or not reader.pages:
        raise ValueError('暗号化されていない本文PDFが必要です')
    if max_pages < 1 or max_chars < 1:
        raise ValueError('分割上限は正の整数が必要です')
    pages = [page_text(p) for p in reader.pages]
    texts = [text for text, _ in pages]
    if headings is not None:
        mode = 'manual'
    else:
        headings = outline_entries(reader)
        # Flat publisher outlines sometimes contain only a title and references.
        inferred = text_headings(texts, [bold for _, bold in pages])
        if len(headings) < 3 and len(inferred) > len(headings):
            mode, headings = 'text_headings', inferred
        else:
            mode = 'outline' if headings else 'page_ranges'
            headings.sort(key=lambda e: e['page'])
        if not headings:
            headings = [dict(page=1, title='本文', level=1)]
    headings = validate_headings(headings, len(texts))
    if headings[0]['page'] > 1:
        headings.insert(0, dict(page=1, title='冒頭部分', level=1))
    jobs, parents = [], []
    for i, entry in enumerate(headings):
        title, start, level = entry['title'], entry['page'], entry['level']
        following = headings[i + 1] if i + 1 < len(headings) else None
        end = following['page'] if following else len(texts)
        # Two sections can share a page. Include it in both packets when the
        # next heading does not clearly start at the very top of the page.
        if following and end > start:
            boundary = heading_offset(texts[end-1], following['title'])
            if boundary is not None and not texts[end-1][:boundary].strip():
                end -= 1
        while parents and parents[-1][0] >= level:
            parents.pop()
        parent = parents[-1][1] if parents else None
        first = None
        while start <= end:
            stop, size = start, len(texts[start-1])
            while stop < end and stop - start + 1 < max_pages and size + len(texts[stop]) <= max_chars:
                size += len(texts[stop])
                stop += 1
            identifier = f'ch{len(jobs)+1:04d}'
            jobs.append(dict(id=identifier, title=title, start=start, end=stop, parent=first or parent,
                             start_heading=title if first is None else None,
                             next_heading=following['title'] if following and stop == following['page'] else None))
            first = first or identifier
            start = stop + 1
        parents.append((level, first))
    return mode, jobs, texts


def heading_offset(text, title):
    if not title:
        return None
    expected = ' '.join(title.split()).casefold()
    offset = 0
    for line in text.splitlines(keepends=True):
        value = ' '.join(line.split()).casefold()
        if expected in (value, re.sub(r'\s+\d+$', '', value)):
            return offset
        offset += len(line)
    return None


def source_window(texts, job):
    """Keep the complete PDF page visually, but bind quotations to this section's text."""
    pages = texts[job['start']-1:job['end']]
    start = heading_offset(pages[0], job['start_heading'])
    if start is not None:
        pages[0] = pages[0][start:]
    end = heading_offset(pages[-1], job['next_heading'])
    if end is not None:
        pages[-1] = pages[-1][:end]
    return pages


def write_excerpt(reader, job, path):
    writer = PdfWriter()
    for index in range(job['start'] - 1, job['end']):
        writer.add_page(reader.pages[index], excluded_keys=('/Annots',))
    with path.open('wb') as handle:
        writer.write(handle)
