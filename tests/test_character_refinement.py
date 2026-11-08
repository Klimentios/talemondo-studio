import importlib.util
import json
from pathlib import Path
import unittest
import numpy as np
from app.character_refinement import normalize_character_refinement, build_character_refinement
from app.contract import GenerationRequest

spec = importlib.util.spec_from_file_location('tracking', Path(__file__).parents[1]/'app/comfy_nodes/h3_character_refine/tracking.py')
tracking = importlib.util.module_from_spec(spec); spec.loader.exec_module(tracking)


class RefinementTests(unittest.TestCase):
    def config(self):
        return {'characters':[{'name':'VEGA','picture':1,'box':[0,.1,.3,.7]}, {'name':'ORION','picture':2,'box':[.6,.1,.3,.7]}]}

    def test_independent_references_audio_and_full_model(self):
        g=build_character_refinement(GenerationRequest('scene', 5, '9:16', 'native',steps=24,seed=12), ['vega.png','orion.png'], 'source.mp4', self.config())
        cond=[n['inputs'] for n in g.values() if n['class_type']=='MiniMaxH3ReferenceToVideo']
        self.assertEqual(len(cond), 2)
        self.assertTrue(all('ref_images.ref_image_1' not in n for n in cond))
        self.assertEqual(g['510']['inputs']['image'], 'vega.png')
        self.assertEqual(g['520']['inputs']['image'], 'orion.png')
        self.assertEqual(g['130']['inputs']['audio'], ['501',1])
        self.assertFalse(any('Cache' in n['class_type'] or 'Lora' in n['class_type'] for n in g.values()))
        for n in g.values():
            for v in n['inputs'].values():
                if isinstance(v,list) and len(v)==2 and isinstance(v[0],str): self.assertIn(v[0],g)

    def test_invalid_reference_box_and_duplicate_owner(self):
        for mutate in (lambda c:c['characters'][0].update(picture=3), lambda c:c['characters'][0].update(box=[0,0,1.1,1]), lambda c:c['characters'][1].update(name='VEGA')):
            c=self.config(); mutate(c)
            with self.assertRaises(ValueError): normalize_character_refinement(c,2)

class RefinementApiTests(unittest.TestCase):
    def test_completed_source_is_copied_once_and_original_preserved(self):
        import asyncio
        import tempfile
        import httpx
        from local_studio import main
        from local_studio.store import StudioStore
        with tempfile.TemporaryDirectory() as tmp:
            old = main.ctx.store
            main.ctx.store = StudioStore(Path(tmp))
            source = main.ctx.store.results_dir/'done.mp4'
            source.write_bytes(b'original video')
            with main.ctx.store.connect() as db:
                db.execute('INSERT INTO jobs(job_id,state,original_prompt,compiled_prompt,settings_json,manifest_json,mapping_json,output_path) VALUES (?,?,?,?,?,?,?,?)',
                           ('done','completed','prompt','prompt','{}','{}','{}',str(source)))
            async def check():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://127.0.0.1') as client:
                    a=await client.post('/api/character-refinement/source/done')
                    b=await client.post('/api/character-refinement/source/done')
                    self.assertEqual(a.status_code,200,a.text)
                    self.assertEqual(a.json()['asset_id'],b.json()['asset_id'])
                    self.assertEqual(Path(a.json()['path']).read_bytes(), source.read_bytes())
                    self.assertTrue(source.is_file())
                    missing=await client.post('/api/character-refinement/source/missing')
                    self.assertEqual(missing.status_code,422)
            try: asyncio.run(check())
            finally: main.ctx.store=old
