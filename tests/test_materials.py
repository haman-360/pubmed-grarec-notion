"""Exercise the early HTML route with zero workspace PNGs and a lost append response."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from paper_pipeline import materials, material_pngs, materials_notion, notion_sync
from paper_pipeline.manual import Workspace
from paper_pipeline.store import digest, encode
from test_notion_sync import FakeNotion
from test_paper_pipeline import png, sample_summary
from paper_pipeline.graphics import adapt_legacy
from paper_pipeline.image_review import CHECKS


class EmbeddedNotion(FakeNotion):
    def handle(self, method, path, **kwargs):
        result = super().handle(method, path, **kwargs)
        if method=='PATCH' and path.endswith('/children'):
            for block in result['results']:
                kind = block['type']
                if kind in ('embed','pdf','image'):
                    upload = self.uploads[block[kind]['file_upload']['id']]
                    url = 'https://example.invalid/'+upload['filename']
                    caption = block[kind].get('caption', [])
                    block[kind] = {'url':url} if kind=='embed' else {'type':'file','file':{'url':url},'caption':caption}
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

    def test_two_separate_arrivals_resume_only_approved_png_on_html_page(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            ws = Workspace.create(repo/'shared', 'a'*32, 'Test')
            master = adapt_legacy(sample_summary(dict(pmid=materials.PMID, doi='', title='Fixture')))
            master['visualization_strategy']['include_overview'] = True
            files = {'png-master.json':encode(master).encode(), 'master.json':b'fixture',
                     materials.HTML:b'html', materials.PDF:b'pdf'}
            hashes = {name:digest(raw) for name,raw in files.items()}
            version = digest(hashes)
            folder = ws.path(materials.DEST+'/'+version)
            folder.mkdir(parents=True)
            for name, raw in files.items():
                (folder/name).write_bytes(raw)
            (folder/'manifest.json').write_text(encode(hashes))
            (folder.parent/'current.json').write_text(encode(dict(version=version,requests=2)))
            api = EmbeddedNotion()
            cfg = dict(NOTION_TOKEN='test', NOTION_DATABASE_ID='db', NOTION_DATA_SOURCE_ID='')
            with patch.object(materials,'MASTER_SHA', hashes['master.json']), patch.object(notion_sync,'credentials',return_value=cfg):
                def arriving(topic, raw):
                    inbox = material_pngs.inbox(ws, version)
                    inbox.mkdir(exist_ok=True)
                    (inbox/material_pngs.filename(topic)).write_bytes(raw)
                    return dict(version=version, topic=topic, sha=digest(raw), checks=list(CHECKS),
                                observed_pmid=materials.PMID, value=master['practice_change'])
                first = arriving('overview', png())
                with self.assertRaisesRegex(ValueError, '先に教材HTML'):
                    material_pngs.register(repo, ws, first, api)
                materials_notion.register(repo, ws, version, api)
                page = materials.current(ws)['page_id']
                with self.assertRaisesRegex(ValueError,'一致しません'):
                    material_pngs.register(repo, ws, dict(first, observed_pmid='1'), api)
                material_pngs.register(repo, ws, first, api)
                rows = material_pngs.snapshot(repo, ws)['images']
                self.assertTrue(rows[0]['block_id'])
                self.assertFalse(rows[1]['arrived'])
                with self.assertRaisesRegex(ValueError, '同じPNG'):
                    material_pngs.register(repo, ws, arriving('main', png()), api)
                second = arriving('main', png((1200,800)))
                with self.assertRaisesRegex(ValueError, 'PNGが変わり'):
                    material_pngs.register(repo, ws, dict(first,topic='main'), api)
                api.fail_after = ('PATCH','/blocks/'+page+'/children')
                with self.assertRaises(TimeoutError):
                    material_pngs.register(repo, ws, second, api)
                ws = Workspace(ws.root, ws.device_id, ws.device_name)
                row = material_pngs.snapshot(repo, ws)['images'][1]
                self.assertTrue(row['approved_at'])
                self.assertFalse(row['block_id'])
                (material_pngs.inbox(ws,version)/row['name']).unlink()
                for _ in range(2):
                    material_pngs.register(repo, ws, dict(version=version,topic='main',sha=row['sha']), api)
                    materials_notion.register(repo, ws, version, api)
                self.assertEqual(list(api.pages), [page])
                self.assertEqual([b['type'] for b in api.blocks[page]], ['heading_2','embed','pdf','image','image'])
                self.assertEqual(len(api.uploads), 4)
                # The dashboard also recovers a local approval if publication was interrupted.
                current = materials.current(ws)
                del current['pngs']['main']
                (folder.parent/'current.json').write_text(encode(current))
                fallback = material_pngs.snapshot(repo, ws)['images'][1]
                self.assertTrue(fallback['approved_at'])
                self.assertFalse(fallback.get('block_id'))
                material_pngs.register(repo, ws, second, api)
                self.assertEqual(len(api.blocks[page]), 5)
                self.assertTrue(all(r['block_id'] for r in material_pngs.snapshot(repo, ws)['images']))
                api.blocks[page].pop()
                with self.assertRaisesRegex(ValueError, '照合できません'):
                    material_pngs.register(repo, ws, second, api)
                self.assertEqual(len(api.blocks[page]), 4)
