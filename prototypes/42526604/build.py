"""Package the dedicated visual prototype beside its verified teaching edition."""
from base64 import b64encode
from hashlib import sha256
from pathlib import Path
import json
import re
from tempfile import NamedTemporaryFile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EDITION = ROOT / (
    'output/learning-verification/PMID_42526604/'
    'a424445fdf00ad5cca339fee968653dbcb88b1a5f836e2aa74d10b6e28868105/'
    'editions/d24698c24ab1887ab2db659f53f3d1b56c04167dc31e09fd9f6b54c74eac91a9'
)
EXPECTED = 'a547585cb64bcfb745d9853b1ad4cd5ea595ace7f6d94af1e0f49635d3a50f5f'


def build():
    master = (EDITION / 'master.json').read_bytes()
    pdf = (ROOT / 'input/pdfs/42526604_Use electronic cigarettes asthma across lifespan.pdf').read_bytes()
    if sha256(master).hexdigest() != EXPECTED or sha256(pdf).hexdigest() != json.loads(master)['metadata']['pdf_sha256']:
        raise ValueError('検証版Masterまたは原本PDFのハッシュが一致しません')
    page = (HERE / 'prototype.html.in').read_text()
    script = re.search(r'<script id="app">(.*?)</script>', page, re.S)[1]
    css = re.search(r'<style>(.*?)</style>', page, re.S)[1]
    for key, data in [('SCRIPT', script), ('STYLE', css)]:
        page = page.replace(f'@@{key}_HASH@@', b64encode(sha256(data.encode()).digest()).decode())
    page = page.replace('@@PDF@@', b64encode(pdf).decode())
    page = page.replace('@@MASTER@@', master.decode().replace('<', r'\u003c'))
    out = EDITION / 'reader_visual_prototype.html'
    with NamedTemporaryFile(dir=EDITION, delete=False) as tmp:
        tmp.write(page.encode())
    Path(tmp.name).replace(out)
    print(out)


if __name__ == '__main__':
    build()
