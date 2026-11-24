"""Export one selected paper to a portable HTML file."""
import argparse
from pathlib import Path
import re

from PIL import Image

from paper_pipeline.reader import save_reader
from paper_pipeline.reader_data import paper_from_json


def check_filename(path, pmid):
    # Standard flat exports and PMID folders both carry source identity.
    for part in [path.stem, *[p.name for p in path.resolve().parents]]:
        match = re.match(r'(?i)^PMID[_-]?(\d+)(?:\D|$)', part)
        if not match and part == path.stem:
            match = re.fullmatch(r'(\d+)(?:_.*)?', part)
        if match and match[1] != pmid:
            raise ValueError(f'ファイル名のPMIDが精読JSONと一致しません：{part}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='精読JSONと任意のPNGからオフラインReaderを作成します')
    parser.add_argument('json', type=Path, help='版を指定した精読JSON')
    parser.add_argument('--png', type=Path, action='append', default=[], help='対応を確認したPNG。複数指定可')
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'output/readers')
    args = parser.parse_args(argv)
    try:
        paper = paper_from_json(args.json.read_bytes())
        check_filename(args.json, paper.pmid)
        images = []
        for path in dict.fromkeys(args.png):
            check_filename(path, paper.pmid)
            images.append((path.name + '（手動指定）', path.read_bytes()))
        print(save_reader(args.output_dir, paper, images).resolve())
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
        parser.exit(2, f'Readerを作成できません：{error}\n')


if __name__ == '__main__':
    main()
