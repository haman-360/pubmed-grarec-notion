"""PNG review regression tests. All writes use a disposable manual workspace."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from paper_pipeline.graphics import adapt_legacy
from paper_pipeline.image_review import CHECKS, fixed_facts
from paper_pipeline.intake import PENDING, organize, result_message
from paper_pipeline.manual import Workspace
from paper_pipeline.notion_sync import bundle
from paper_pipeline.store import encode
from test_paper_pipeline import sample_summary

GUIDE, REVIEW = '40317496', '40066935'


def labeled_png(pmid, value):
    image = Image.new('RGB', (960, 540), 'white')
    font_file = Path('/System/Library/Fonts/Supplemental/Arial.ttf')
    font = ImageFont.truetype(str(font_file), 48) if font_file.is_file() else ImageFont.load_default()
    ImageDraw.Draw(image).multiline_text((40, 70), f'PMID {pmid}\nPractice Change: {value}', font=font, fill='black')
    raw = io.BytesIO()
    image.save(raw, 'PNG')
    return raw.getvalue()


def master_for(pmid):
    guideline = pmid == GUIDE
    title = ('European Guideline (EuroGuiDerm) on atopic eczema: Living update.' if guideline else
             'Influence of outdoor time on the spherical equivalent and axial length in childhood myopia: A meta-analysis.')
    data = adapt_legacy(sample_summary(dict(pmid=pmid, title=title, doi='')))
    results = ['全身治療の更新'] if guideline else [
        '屈折変化：既近視群 MD +0.17 D', '眼軸伸長：既近視群 MD −0.01 mm、P=0.71、有意差なし',
        '眼軸伸長：全体 MD −0.08 mm']
    data.update(paper_type='guideline_consensus' if guideline else 'systematic_review',
                study_type='ガイドライン' if guideline else 'メタ解析', practice_change=None if guideline else 'Unclear',
                main_results=results, result_evidence=[dict(result_index=i, location='fixture', supporting_text=r)
                                                       for i, r in enumerate(results)],
                tomorrow_action=['未確認のAI提案：外来への適用を確認する'])
    data['topics'][0].update(topic_id='treatment' if guideline else 'intervention',
                             topic_title=results[0], central_message=results[0],
                             result_indices=list(range(len(results))), practice_change='Yes' if guideline else 'Unclear')
    if guideline:
        data['pico'] = None
        data['overview']['central_message'] = '全身治療の更新'
        data['visualization_strategy']['include_overview'] = True
    return data


class ImageReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ws = Workspace.create(Path(tmp.name), 'a'*32, 'Test')
        pdf = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(pdf)
        self.pdf = pdf.getvalue()

    def load(self, pmid, master):
        self.ws.add(pmid)
        self.ws.import_file(pmid, 'pdf', 'paper.pdf', self.pdf)
        return self.ws.import_file(pmid, 'json', 'master.json', encode(master).encode())

    def submit_png(self, pmid, topic, raw):
        inbox = self.ws.path(PENDING)
        inbox.mkdir(exist_ok=True)
        (inbox / f'PMID_{pmid}_{topic}.png').write_bytes(raw)
        result = organize(self.ws, minimum_age=0)
        self.assertEqual(result['errors'], [])
        event = self.ws.current(pmid)
        candidate = next(f for f in event['data']['files'] if f['topic'] == topic and f['kind'] == 'png_candidate')
        self.assertEqual(result['png_reviews'][0]['status'], candidate['review']['status'])
        if candidate['review']['status'] == 'rejected':
            self.assertIn(candidate['review']['reason'], result_message(result))
        return event, candidate

    def test_two_masters_in_one_intake_isolate_topic_results_and_ratings(self):
        inbox = self.ws.path(PENDING)
        inbox.mkdir()
        for pmid in (GUIDE, REVIEW):
            (inbox / f'PMID_{pmid}.pdf').write_bytes(self.pdf)
            (inbox / f'PMID_{pmid}.json').write_text(encode(master_for(pmid)))
        self.assertEqual(organize(self.ws, minimum_age=0)['errors'], [])
        for pmid, topic, expected in [(GUIDE, 'overview', '未評価'), (GUIDE, 'treatment', 'Yes'),
                                      (REVIEW, 'intervention', 'Unclear')]:
            text = self.ws.image_prompt(pmid, topic, '16:9 色鉛筆')
            other = REVIEW if pmid == GUIDE else GUIDE
            self.assertNotIn(other, text)
            source = json.loads(text.split('```json\n')[1].split('\n```')[0])
            facts = fixed_facts(source)
            self.assertEqual(facts['practice_change_display'], 'Practice Change：'+expected)
            self.assertIn(json.dumps(facts, ensure_ascii=False, indent=2), text)
            self.assertIsInstance(facts['topic'], list)
            self.assertIsInstance(facts['topic_title'], str)
            if pmid == GUIDE:
                self.assertEqual(facts['paper_type'], 'guideline_consensus')
                self.assertIsNone(facts['pico'])
                self.assertIn('全身治療', facts['one_line_summary'])
                self.assertNotIn('−0.08 mm', text)
            else:
                self.assertEqual(facts['main_results'], master_for(REVIEW)['main_results'])
                self.assertNotIn('全身治療', text)

    def test_missing_rating_pico_and_numbers_remain_absent_in_source(self):
        master = master_for(GUIDE)
        del master['pico'], master['practice_change'], master['topics'][0]['practice_change']
        master.update(main_results=[], result_evidence=[])
        master['topics'][0]['result_indices'] = []
        event = self.load(GUIDE, master)
        for file in event['data']['files']:
            if file['kind'] == 'graphic':
                facts = fixed_facts(json.loads(self.ws.read_asset(file)))
                self.assertTrue(facts['warnings'])
                self.assertIsNone(facts['pico'])
                self.assertEqual(facts['main_results'], [])
                self.assertEqual(facts['practice_change_display'], 'Practice Change：未評価')
            if file['kind'] == 'json':
                self.assertEqual(self.ws.read_asset(file), encode(master).encode())
        for value in ('Yes', 'No', 'Unclear', None):
            self.assertEqual(fixed_facts({'practice_change':value})['practice_change_display'],
                             'Practice Change：'+(value or '未評価'))
        with self.assertRaises(ValueError):
            fixed_facts({'practice_change':'yes'})

    def test_null_yes_png_blocked_at_intake_and_manual_and_notion_completion(self):
        master = master_for(GUIDE)
        # A single overview-equivalent graphic isolates the completion gate.
        master['visualization_strategy']['include_overview'] = False
        master['topics'][0]['practice_change'] = None
        self.load(GUIDE, master)
        transcript = subprocess.CompletedProcess([], 0, stdout=b'PMID 40317496\nPractice Change: Yes\n')
        with patch('paper_pipeline.image_review.sys.platform','darwin'), patch('subprocess.run',return_value=transcript):
            event, image = self.submit_png(GUIDE, 'treatment', labeled_png(GUIDE, 'Yes'))
        self.assertEqual(image['review']['status'], 'rejected')
        self.assertFalse(any(f['kind']=='png' for f in event['data']['files']))
        self.ws.review_png(GUIDE, [event['id']], 'treatment', image['sha'], GUIDE, '未評価', list(CHECKS))
        head = [self.ws.current(GUIDE)['id']]
        with self.assertRaisesRegex(ValueError, '承認'):
            bundle(self.ws, GUIDE, head)
        with self.assertRaisesRegex(ValueError, '承認'):
            self.ws.update(GUIDE, head, '登録済み', done=True)

    def test_unreadable_requires_visual_checks_and_replacement_preserves_formal_png(self):
        self.load(REVIEW, master_for(REVIEW))
        with patch('subprocess.run', side_effect=OSError):
            event, image = self.submit_png(REVIEW, 'intervention', labeled_png(REVIEW, 'Unclear'))
        args = (REVIEW, [event['id']], 'intervention', image['sha'])
        with self.assertRaises(ValueError):
            bundle(self.ws, REVIEW, [event['id']])
        with self.assertRaises(ValueError):
            self.ws.review_png(*args, REVIEW, '', list(CHECKS))
        with self.assertRaises(ValueError):
            self.ws.review_png(*args, REVIEW, 'Unclear', [])
        self.ws.review_png(*args, REVIEW, 'Unclear', list(CHECKS))
        with self.assertRaises(ValueError):
            self.ws.review_png(*args, REVIEW, 'No', list(CHECKS))
        head = [self.ws.current(REVIEW)['id']]
        saved = bundle(self.ws, REVIEW, head)['pairs'][0]['image']
        with patch('subprocess.run', side_effect=OSError):
            event, replacement = self.submit_png(REVIEW, 'intervention', labeled_png(REVIEW, 'Yes'))
        self.ws.review_png(REVIEW, [event['id']], 'intervention', replacement['sha'], REVIEW, 'Yes', [])
        event = self.ws.current(REVIEW)
        self.assertIn(saved, event['data']['files'])
        self.assertEqual(self.ws.read_asset(saved), labeled_png(REVIEW, 'Unclear'))
        self.assertEqual(next(f for f in event['data']['files'] if f['kind']=='png_candidate')['review']['status'], 'rejected')
        with self.assertRaises(ValueError):
            bundle(self.ws, REVIEW, [event['id']])

    def test_older_unregistered_png_requires_review_and_completed_record_is_preserved(self):
        event = self.load(REVIEW, master_for(REVIEW))
        data = event['data']
        graphic = next(f for f in data['files'] if f['kind'] == 'graphic')
        raw = labeled_png(REVIEW, 'Unclear')
        image = self.ws.asset(REVIEW, 'png', 'intervention.png', raw, 'intervention', graphic['sha'])
        data['files'].append(image)
        event = self.ws.record(REVIEW, data, [event['id']])
        with self.assertRaisesRegex(ValueError, '承認'):
            self.ws.update(REVIEW, [event['id']], 'checked', done=True)
        with self.assertRaisesRegex(ValueError, '承認'):
            bundle(self.ws, REVIEW, [event['id']])
        with patch('subprocess.run', side_effect=OSError):
            self.ws.review_png(REVIEW, [event['id']], 'intervention', image['sha'], REVIEW, 'Unclear', list(CHECKS))
        self.assertEqual(bundle(self.ws, REVIEW, [self.ws.current(REVIEW)['id']])['pairs'][0]['image']['sha'], image['sha'])
        # Simulate an already completed pre-upgrade record without a medical receipt.
        data['done'] = True
        self.ws.record(REVIEW, data, [self.ws.current(REVIEW)['id']])
        before = self.ws.current(REVIEW)
        self.assertEqual(self.ws.snapshot()['papers'][0]['category'], 'completed')
        self.assertEqual(self.ws.current(REVIEW), before)
        self.assertEqual(self.ws.read_asset(image), raw)

    @unittest.skipUnless(sys.platform == 'darwin', 'Apple Vision requires macOS')
    def test_native_ocr_of_wrong_png_never_approves(self):
        self.load(GUIDE, master_for(GUIDE))
        event, treatment = self.submit_png(GUIDE, 'treatment', labeled_png(GUIDE, 'Yes'))
        self.ws.review_png(GUIDE, [event['id']], 'treatment', treatment['sha'], GUIDE, 'Yes', list(CHECKS))
        event, overview = self.submit_png(GUIDE, 'overview', labeled_png(GUIDE, 'Yes'))
        assessment = overview['review']
        self.assertIn(assessment['status'], ('rejected', 'review_required'))
        self.assertFalse(self.ws.medically_approved(event['data']))
        with self.assertRaises(ValueError):
            bundle(self.ws, GUIDE, [event['id']])
        with self.assertRaises(ValueError):
            self.ws.update(GUIDE, [event['id']], '目視確認が必要', done=True)
        if assessment['status'] == 'rejected' or os.environ.get('GRAREC_TEST_NATIVE_OCR'):
            self.assertEqual(assessment['status'], 'rejected')
            self.assertIn('Practice Change：Yes', assessment['reason'])
