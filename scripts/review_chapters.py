"""Prepare chapter reading packets and collect completed responses offline."""
import argparse
import json
from pathlib import Path

from pypdf.errors import PyPdfError
from generate_reader import check_filename
from paper_pipeline.chapter_review import MASTER
from paper_pipeline.chapter_run import prepare, resume


def main(argv=None):
    parser = argparse.ArgumentParser(description='長文PDFの章別精読とオフラインReader生成')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('prepare', help='章PDF・精読依頼文を準備')
    create.add_argument('pdf', type=Path)
    create.add_argument('--pmid', required=True)
    create.add_argument('--title', help='省略時はPDFの書誌タイトル')
    create.add_argument('--article-type', required=True, choices=MASTER['properties']['article_type']['enum'])
    for field in ('journal', 'year', 'doi'):
        create.add_argument('--' + field)
    create.add_argument('--classic', action='store_true', help='従来の章別形式を使う')
    create.add_argument('--sections', type=Path, help='任意の章見出しJSON')
    create.add_argument('--max-pages', type=int, default=10)
    create.add_argument('--max-chars', type=int, default=32000)
    create.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parents[1]/'output/chapter_reviews')
    collect = commands.add_parser('resume', help='未完了の章を取り込み、全章完了時にMasterとReaderを出力')
    collect.add_argument('run', type=Path)
    collect.add_argument('--confirm-visual', action='append', default=[], metavar='章ID', help='この章の目視引用を原ページと照合済み')
    collect.add_argument('--pdf', type=Path, help='SHA-256が一致する原本PDFをHTMLに内包する')
    collect.add_argument('--png', type=Path, action='append', default=[], help='対応を確認したグラレコPNG')
    collect.add_argument('--revise', action='append', default=[], metavar='章ID', help='この章の修正版を取り込む')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            check_filename(args.pdf, args.pmid)
            headings = json.loads(args.sections.read_text(encoding='utf-8-sig')) if args.sections else None
            folder = prepare(args.pdf, args.output_dir, pmid=args.pmid, title=args.title, article_type=args.article_type,
                             headings=headings, max_pages=args.max_pages, max_chars=args.max_chars,
                             journal=args.journal, year=args.year, doi=args.doi, learning=not args.classic)
            print(folder.resolve())
        else:
            images = []
            for path in args.png:
                pmid = json.loads((args.run/'manifest.json').read_text())['metadata']['pmid']
                check_filename(path, pmid)
                images.append(('既存グラレコ · '+path.name+'（手動指定）',path.read_bytes()))
            report = resume(args.run, args.confirm_visual, args.revise, images, args.pdf.read_bytes() if args.pdf else None)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if report['errors']:
                return 2
            if report['pending']:
                return 1
    except (ValueError, OSError, PyPdfError) as error:
        parser.exit(2, f'章別精読を完了できません：{error}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
