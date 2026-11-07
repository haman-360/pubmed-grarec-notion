"""Source discovery adapted from pubmed-pdf-fetcher; no legacy PMC scraping."""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import asdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse
import xml.etree.ElementTree as ET

import requests
from pypdf import PdfReader

from .pubmed import PubMedClient
from .store import atomic_write, digest, doi_key, encode, now

PMC = "https://pmc-oa-opendata.s3.amazonaws.com"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
DOI = re.compile(r'10\.\d{4,9}/[^\s<>"\]]+', re.I)


def pdf_dois(text):
    # Publisher PDFs may wrap a DOI after "10." or a numeric suffix separator.
    unwrapped = re.sub(r'(?<=[./])\s*\n\s*(?=\d)', '', text)
    return list(dict.fromkeys(doi_key(x.rstrip('.,;)')) for x in DOI.findall(unwrapped)))


def error_reason(error):
    # requests errors can include an NCBI API key in the request URL.
    if isinstance(error,requests.RequestException):
        status = getattr(error.response,'status_code',None)
        return type(error).__name__ + (f' HTTP {status}' if status else '')
    return str(error)[:500]


def identifiers_match(article, candidate):
    matched = False
    for key in ('pmid','doi'):
        left, right = article.get(key), candidate.get(key)
        if left and right:
            left, right = (doi_key(left),doi_key(right)) if key=='doi' else (str(left),str(right))
            if left != right:
                return False
            matched = True
    return matched


def inspect_pdf(path):
    path = Path(path)
    if path.stat().st_size > 49 * 1024 * 1024:
        raise ValueError("PDFが49MiBを超えています。分割等を確認してください（自動切り捨てなし）")
    with path.open('rb') as handle:
        if handle.read(5) != b'%PDF-':
            raise ValueError("PDFではありません")
    reader = PdfReader(path, strict=True)
    if reader.is_encrypted:
        raise ValueError("暗号化されたPDFです")
    if not reader.pages:
        raise ValueError("空のPDFです")
    texts = [page.extract_text() or '' for page in reader.pages]
    return {'first': '\n'.join(texts[:2]), 'text_bytes': len('\n'.join(texts).encode()),
            'pages': len(texts), 'title': str((reader.metadata or {}).get('/Title') or '')}


def normalized_title(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def matches_pdf(info, article):
    """Require a bibliographic identifier AND a contiguous title on the first pages."""
    title = normalized_title(article.get('title', ''))
    first = re.split(r'\n\s*(?:References|Bibliography|参考文献)\s*\n', info['first'], flags=re.I)[0]
    if len(title) < 15 or title not in normalized_title(info['title'] + '\n' + first):
        return False
    dois = set(pdf_dois(first))
    pmids = set(re.findall(r'\bPMID\s*[:：]?\s*(\d+)\b', first, re.I))
    return bool((article.get('doi') and doi_key(article['doi']) in dois) or
                (article.get('pmid') and str(article['pmid']) in pmids))


class PdfMetaParser(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base, self.url = base, ''

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('name', '').lower() == 'citation_pdf_url':
            self.url = urljoin(self.base, attrs.get('content', ''))


class Sources:
    def __init__(self, store, session=None):
        self.store = store
        self.session = session or requests.Session()
        self.pubmed = PubMedClient()

    def get(self, url, **kwargs):
        if url.startswith('s3://pmc-oa-opendata/'):
            url = PMC + '/' + url[len('s3://pmc-oa-opendata/'):]
        if urlparse(url).scheme not in ('http', 'https'):
            raise ValueError("HTTP(S)以外の取得URLです")
        for retry in range(3):
            try:
                response = self.session.get(url, timeout=30, **kwargs)
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                return response
            except requests.RequestException as error:
                status = getattr(error.response, 'status_code', 0)
                if retry == 2 or status and status != 429 and status < 500:
                    raise
                time.sleep(2 ** retry)

    def metadata(self, paper):
        if paper.get('pmid'):
            articles = self.pubmed.fetch_metadata([paper['pmid']])
        elif paper.get('doi'):
            data = self.get(EUTILS + '/esearch.fcgi', params={'db':'pubmed', 'term':paper['doi'] + '[doi]', 'retmode':'json'}).json()
            ids = data.get('esearchresult', {}).get('idlist', [])
            articles = self.pubmed.fetch_metadata(ids) if len(ids) == 1 else []
        else:
            return paper
        if articles and articles[0].title:
            meta = asdict(articles[0])
            if paper.get('doi') and doi_key(meta.get('doi')) != paper['doi']:
                raise ValueError('DOI検索の書誌情報が一致しません')
            return self.store.paper(pmid=meta['pmid'], doi=meta['doi'], title=meta['title'], metadata=meta)
        raise ValueError('書誌情報を取得できませんでした')

    def import_folder(self, directory):
        for path in sorted(p for p in Path(directory).glob('*') if p.is_file() and p.suffix.lower()=='.pdf'):
            sha = digest(path.read_bytes())
            if self.store.rows("SELECT id FROM sources WHERE hash=? AND status IN ('confirmed','revision_review')", (sha,)):
                continue
            try:
                info = inspect_pdf(path)
                papers = self.store.rows('SELECT * FROM papers')
                matches = [p for p in papers if matches_pdf(info, p)]
                if not matches:
                    # Candidate lookup only; never bind a reference DOI without title corroboration.
                    for doi in pdf_dois(info['first'])[:3]:
                        data = self.get(EUTILS + '/esearch.fcgi', params={'db':'pubmed', 'term':doi.rstrip('.,;)')+'[doi]', 'retmode':'json'}).json()
                        ids = data.get('esearchresult', {}).get('idlist', [])
                        if len(ids) == 1:
                            article = asdict(self.pubmed.fetch_metadata(ids)[0])
                            if matches_pdf(info, article):
                                matches.append(self.store.paper(pmid=article['pmid'], doi=article['doi'], title=article['title'], metadata=article))
                unique = {p['id']: p for p in matches}
                paper_id = next(iter(unique)) if len(unique) == 1 else None
                self.store.source(path, paper_id, 'confirmed' if paper_id else 'identity_review',
                                  {'inspection': info, 'method':'identifier_and_title' if paper_id else 'manual_required'})
            except Exception as error:
                self.store.source(path, status='identity_review', details={'error':error_reason(error)})

    def pmc_candidates(self, article):
        pmcid = article.get('pmcid')
        if not pmcid:
            return []
        xml = self.get(PMC, params={'list-type':'2', 'prefix':pmcid+'.', 'delimiter':'/'}).text
        root = ET.fromstring(xml)
        prefixes = [e.text for e in root.findall('.//{*}CommonPrefixes/{*}Prefix')]
        result = []
        for prefix in prefixes:
            meta = self.get(f"{PMC}/{prefix}{prefix.rstrip('/')}.json").json()
            if not identifiers_match(article,meta):
                continue
            if meta.get('pdf_url'):
                result.append({'url':meta['pdf_url'], 'source':'PMC', 'version':'acceptedVersion' if str(meta.get('is_manuscript')).lower() in ('yes','true') else 'publishedVersion',
                               'license':meta.get('license_code'), 'retracted':meta.get('is_retracted'), 'priority':'high', 'evidence':'PMC official metadata'})
        return sorted(result, key=lambda c:c['version'] != 'publishedVersion')

    def unpaywall_candidates(self, article):
        import os
        if not article.get('doi'):
            return []
        email = os.getenv('UNPAYWALL_EMAIL') or os.getenv('NCBI_EMAIL')
        if not email:
            raise ValueError('UNPAYWALL_EMAIL未設定（未調査）')
        data = self.get('https://api.unpaywall.org/v2/' + quote(article['doi'], safe=''), params={'email':email}).json()
        locations = data.get('oa_locations') or []
        if data.get('best_oa_location'):
            locations = [data['best_oa_location']] + locations
        return sorted([{'url':x.get('url_for_pdf') or x.get('url_for_landing_page'), 'source':'Unpaywall',
                        'version':x.get('version'), 'license':x.get('license'), 'manual':not x.get('url_for_pdf'),
                        'priority':'high', 'evidence':'Unpaywall OA location'} for x in locations if x.get('url_for_pdf') or x.get('url_for_landing_page')],
                      key=lambda c: {'publishedVersion':0,'acceptedVersion':1}.get(c['version'],2))

    def europe_candidates(self, article):
        query = f"EXT_ID:{article['pmid']} AND SRC:MED" if article.get('pmid') else f'DOI:"{article.get("doi", "")}"'
        data = self.get('https://www.ebi.ac.uk/europepmc/webservices/rest/search', params={'query':query,'format':'json','resultType':'core','pageSize':5}).json()
        candidates = []
        for item in data.get('resultList', {}).get('result', []):
            if not identifiers_match(article,{'pmid':item.get('id') if item.get('source')=='MED' else None,'doi':item.get('doi')}):
                continue
            for link in item.get('fullTextUrlList', {}).get('fullTextUrl', []):
                free = link.get('availabilityCode') in ('F','OA') or link.get('availability','').lower() in ('free','open access')
                if free and link.get('url'):
                    candidates.append({'url':link['url'],'source':'Europe PMC','priority':'high','evidence':'Free full-text link',
                                       'manual':link.get('documentStyle','').lower() != 'pdf'})
        return candidates

    def publisher_candidates(self, article):
        result = []
        for url in dict.fromkeys([article.get('free_full_text_url'), article.get('publisher_url')]):
            if not url:
                continue
            result.append({'url':url,'source':'Publisher','manual':True,'priority':'high' if article.get('free_full_text_url') == url else 'low',
                           'evidence':'PubMed free resource' if article.get('free_full_text_url') == url else 'Publisher landing page; free access unconfirmed'})
            try:
                response = self.get(url)
            except requests.RequestException as error:
                result[-1]['reason'] = 'Landing page unavailable: '+type(error).__name__
                result[-1]['fetch_error'] = True
                continue
            parser = PdfMetaParser(response.url)
            parser.feed(response.text)
            if parser.url:
                result.append({'url':parser.url, 'source':'Publisher','priority':'medium','evidence':'citation_pdf_url'})
        return result

    def download(self, candidate):
        path = self.store.root / 'downloads' / (digest(candidate['url']) + '.pdf')
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix('.part')
        try:
            with self.get(candidate['url'], stream=True) as response:
                size = 0
                with partial.open('wb') as handle:
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 49 * 1024 * 1024:
                            raise ValueError('PDF exceeds 49MiB')
                        handle.write(chunk)
            inspect_pdf(partial)
            partial.replace(path)
            return path
        finally:
            partial.unlink(missing_ok=True)

    def discover(self, paper):
        history = {'attempts':[], 'candidates':[]}
        errors = False
        available = False
        try:
            paper = self.metadata(paper)
            article = json.loads(paper['metadata'])
            article.update({k:paper[k] for k in ('pmid','doi','title')})
            seen = set()
            for name in ('pmc_candidates','unpaywall_candidates','europe_candidates','publisher_candidates'):
                discover = getattr(self,name)
                try:
                    candidates = discover(article)
                    history['attempts'].append({'source':name, 'status':'searched','candidates':len(candidates)})
                except Exception as error:
                    errors = True
                    history['attempts'].append({'source':name,'status':'error','reason':error_reason(error)})
                    continue
                for candidate in candidates:
                    if candidate['url'] in seen:
                        continue
                    seen.add(candidate['url'])
                    history['candidates'].append(candidate)
                    if candidate.get('fetch_error'):
                        errors = True
                    if candidate.get('manual') or candidate.get('version') == 'submittedVersion' or str(candidate.get('retracted')).lower() in ('yes','true'):
                        continue
                    try:
                        path = self.download(candidate)
                        info = inspect_pdf(path)
                        # Official PMC metadata binds the PDF to the record; other copies require corroboration.
                        if candidate['source'] != 'PMC' and not matches_pdf(info, article):
                            self.store.source(path, status='identity_review', details={'candidate':candidate, 'expected_paper':paper['id']})
                            candidate['reason'] = 'PDF identity requires confirmation'
                            continue
                        self.store.source(path, paper['id'], details=candidate)
                        available = True
                        break
                    except Exception as error:
                        errors = True
                        candidate['reason'] = error_reason(error)
                if available:
                    break
        except Exception as error:
            errors = True
            history['attempts'].append({'status':'error','reason':error_reason(error)})
        likely_free = any(c.get('priority')!='low' for c in history['candidates'])
        status = 'available' if available else ('manual_candidate' if likely_free else 'search_error' if errors else 'free_unconfirmed')
        history['status'] = status
        with self.store.db:
            self.store.db.execute('INSERT INTO searches(paper_id,checked_at,result) VALUES(?,?,?)', (paper['id'],now(),encode(history)))
        self.store.update('papers', paper['id'], access=status, blocker='search_error' if errors and not available else '')
        return history
