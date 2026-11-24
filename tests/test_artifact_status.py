import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from paper_pipeline import artifact_status, materials
from paper_pipeline.manual import Workspace
from paper_pipeline.store import digest


class ArtifactStatusTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.ws = Workspace.create(temp.name, 'a'*32, 'Test')
        self.paper = dict(pmid='23', heads=['head'], category='completed', missing=[], notion_page='abc',
                          files=[dict(kind='pdf', name='a.pdf', sha='pdf'), dict(kind='json',name='a.json',sha='json')])
        self.hashes = {materials.HTML:'html', 'source.json':'json', 'PMID_23.pdf':'pdf'}

    def edition(self, route='existing', page='abc'):
        version = digest(self.hashes)
        folder = self.ws.path(materials.destination('23',route))/version
        folder.mkdir(parents=True, exist_ok=True)
        (folder/'manifest.json').write_text(json.dumps(self.hashes))
        return dict(pmid='23', version=version, route=route, page_id=page)

    def status(self, *jobs, master=None):
        return artifact_status.paper_status(self.ws, self.paper, jobs, master or {})

    def test_png_then_local_html_then_registered_and_new_source(self):
        self.assertEqual(self.status()['group'], 'png')
        pending = self.status(dict(pmid='23'))
        self.assertTrue(pending['requested'] and pending['pending'])
        local = self.status(self.edition(page=None))
        self.assertTrue(local['made'])
        self.assertEqual(local['group'], 'png')
        job = self.edition()
        self.assertEqual(self.status(job)['group'], 'complete')
        self.assertEqual(self.status(dict(job,page_id='def'))['group'], 'both')
        self.paper['category'] = 'web_pending'
        self.assertEqual(self.status(job)['group'], 'html')
        self.paper['files'][0]['sha'] = 'replacement'
        self.assertEqual(self.status(job)['group'], 'neither')
        self.assertFalse(self.status(job)['made'])

    def test_missing_pdf_and_partial_master_pngs_do_not_complete(self):
        self.paper['files'].pop(0)
        self.hashes.pop('PMID_23.pdf')
        self.assertEqual(self.status(self.edition())['group'],'both')
        self.paper['category'] = 'missing_pdf'
        self.hashes = {materials.HTML:'html', 'master.json':'master', 'PMID_23.pdf':'pdf'}
        job = self.edition('master')
        kit = dict(job, requests=2, images=[dict(block_id='one'), {}])
        self.assertEqual(self.status(job,master=kit)['group'],'html')
        kit['images'][1]['block_id'] = 'two'
        self.assertEqual(self.status(job,master=kit)['group'],'complete')
