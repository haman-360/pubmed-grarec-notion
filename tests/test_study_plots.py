"""Keep each plotted measurement bound to its own source, even with sibling results."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from paper_pipeline.reader_data import Paper
from paper_pipeline.study_plots import ForestPlots, render_study_plots


class StudyPlotTests(unittest.TestCase):
    def test_second_measurement_drives_geometry_study_and_primary_citation(self):
        study = dict(reference='Smith', design='cohort', population='adults',
                     exposure='exposure', outcome='endpoint')
        primary = dict(id='s2', pdf_page_start=9, quote='Selected <source>')
        other = dict(id='s1', pdf_page_start=3, quote='Other source')
        value = dict(id='m2', label='aHR', reported='Selected estimate', estimate='2',
                     ci_lower='1', ci_upper='5', ci_level='95%', source_refs=['s2'])
        fact = dict(id='e2', source_refs=['s1','s2'],
                    measurements=[dict(value, id='m1', estimate='1.1', reported='Other estimate'), value])
        master = dict(teaching=dict(studies={'e2': study}), evidence=[dict(id='e1'), fact],
                      source_references=[other, primary])
        panel = dict(title_ja='Effects', items=[dict(label='Endpoint', evidence_ids=['e1','e2'],
                                                     measurement_ids=['m2'])])
        html = ForestPlots(master, True).panel(panel)
        main, related = html.split('<summary>この研究についての追加出典</summary>')
        self.assertIn('data-measurement="m2"', main)
        self.assertIn('aHR 2</b>', main)
        self.assertIn('circle cx="160.0"', main)  # 2 is the midpoint of [0.8, 5] on a log axis
        self.assertIn('Smith', main)
        self.assertIn('e2 ／ m2', main)
        self.assertIn('Selected &lt;source&gt;', main)
        self.assertIn('data-pdf-page="9"', main)
        self.assertNotIn('data-pdf-page="3"', main)
        self.assertIn('data-pdf-page="3"', related)
        self.assertNotIn('Other estimate', html)
        without_pdf = ForestPlots(master, False).panel(panel)
        self.assertIn('原本PDF p.9', without_pdf)
        self.assertNotIn('data-pdf-page=', without_pdf)

    def test_rejects_unsupported_or_empty_master_for_plot_export(self):
        paper = Paper('1','Paper',[])
        with self.assertRaisesRegex(ValueError, 'learning-v3'):
            render_study_plots(paper)
        paper.teaching_master = {'visualizations': []}
        with self.assertRaisesRegex(ValueError, 'パネルがありません'):
            render_study_plots(paper)
