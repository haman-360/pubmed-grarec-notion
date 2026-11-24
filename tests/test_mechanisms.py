"""Check that a mechanism diagram preserves the Master's topology and evidence ownership."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from paper_pipeline.mechanisms import MechanismDiagrams, render_mechanisms
from paper_pipeline.reader_data import Paper


class MechanismTests(unittest.TestCase):
    def test_branch_order_follows_edges_and_relation_evidence_is_separate(self):
        ids = ('a', 'b', 'c', 'd')
        master = dict(evidence=[dict(id=k, kind='author_hypothesis', statement_ja='所見'+k,
                                     source_refs=['s'+k]) for k in ids],
                      source_references=[dict(id='s'+k, pdf_page_start=n, quote='<'+k+'>')
                                         for n,k in enumerate(ids, 1)], teaching=dict(studies={}))
        # The second edge returns to a different item order. The source is not the first array item.
        panel = dict(title_ja='候補', items=[dict(id=k, label=k, evidence_ids=[k]) for k in ('c','a','b')],
                     edges=[dict(label='関係1', **{'from':'a','to':'b'}, evidence_ids=['d']),
                            dict(label='関係2', **{'from':'a','to':'c'}, evidence_ids=['c'])])
        html = MechanismDiagrams(master, True).panel(panel)
        self.assertEqual(html.count('class="group"'), 1)
        self.assertEqual(html.count('class="branch"'), 2)
        overview, branches = html.split('data-scene="1"')
        first, second = branches.split('data-scene="2"')
        self.assertIn('data-evidence="a"', overview)
        self.assertNotIn('data-evidence="c"', first)
        self.assertIn('data-evidence="b"', first)
        self.assertIn('data-evidence="d"', first.split('class="extra"')[1])
        self.assertIn('data-pdf-page="3"', second)
        self.assertIn('&lt;c&gt;', second)
        plain = MechanismDiagrams(master, False).panel(panel)
        self.assertIn('原本PDF p.3', plain)
        self.assertNotIn('data-pdf-page=', plain)

    def test_empty_or_non_teaching_master(self):
        paper = Paper('1','Title',[])
        with self.assertRaisesRegex(ValueError, 'learning-v3'):
            render_mechanisms(paper)
        paper.teaching_master = {'visualizations': []}
        with self.assertRaisesRegex(ValueError, 'パネルがありません'):
            render_mechanisms(paper)
