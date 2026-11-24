"""Exercise the early HTML route with zero workspace PNGs and a lost append response."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from paper_pipeline import materials, materials_notion, notion_sync
from paper_pipeline.manual import Workspace
from paper_pipeline.store import digest, encode
from test_notion_sync import FakeNotion


class EmbeddedNotion(FakeNotion):
    def handle(self, method, path, **kwargs):
        result = super().handle(method, path, **kwargs)
        if method=='PATCH' and path.endswith('/children'):
            for block in result['results']:
                kind = block['type']
                if kind in ('embed','pdf'):
                    upload = self.uploads[block[kind]['file_upload']['id']]
                    url = 'https://example.invalid/'+upload['filename']
                    block[kind] = {'url':url} if kind=='embed' else {'type':'file','file':{'url':url}}
        return result


class MaterialSyncTests(unittest.TestCase):
    def test_png_independent_reconciliation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = Workspace.create(root/'shared', 'a'*32, 'Test')
            files = {'master.json': b'fixture', materials.HTML:b'<!doctype html>', materials.PDF:b'%PDF',
                     'png-master.json':json.dumps({'pmid':materials.PMID, 'title':'Fixture', 'doi':''}).encode()}
            hashes = {name:digest(data) for name,data in files.items()}
            version = digest(hashes)
            folder = workspace.path(materials.DEST+'/'+version)
            folder.mkdir(parents=True)
            for name,data in files.items():
                (folder/name).write_bytes(data)
            (folder/'manifest.json').write_text(encode(hashes))
            (folder.parent/'current.json').write_text(encode({'version':version}))
            api = EmbeddedNotion()
            api.pages['existing'] = {'id':'existing','parent':{'data_source_id':'ds'},'properties':{
                'PMID':{'rich_text':[{'plain_text':materials.PMID}]}, 'Human Checked':{'checkbox':True}}}
            api.blocks['existing'] = []
            cfg = dict(NOTION_TOKEN='test',NOTION_DATABASE_ID='db',NOTION_DATA_SOURCE_ID='')
            with patch.object(materials,'MASTER_SHA',hashes['master.json']), patch.object(notion_sync,'credentials',return_value=cfg):
                api.fail_after = ('PATCH','/blocks/existing/children')
                with self.assertRaises(TimeoutError):
                    materials_notion.register(root, workspace, version, api)
                self.assertNotIn('page_id',materials.current(workspace))
                for _ in range(2):
                    materials_notion.register(root, workspace, version, api)
                self.assertEqual(materials.current(workspace)['page_id'],'existing')
                self.assertEqual(list(api.pages),['existing'])
                self.assertEqual([b['type'] for b in api.blocks['existing']], ['heading_2','embed','pdf'])
                self.assertEqual(len(api.uploads),2)
                self.assertTrue(api.pages['existing']['properties']['Human Checked']['checkbox'])
                self.assertFalse(workspace.heads(materials.PMID))
                (folder/materials.HTML).write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError,'変更'):
                    materials_notion.register(root, workspace, version, api)
