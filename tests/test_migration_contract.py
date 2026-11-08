import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from app import main


class MigrationContractTests(unittest.TestCase):
    def test_capabilities_keep_studio_contract_and_actual_node_availability(self):
        with patch.dict('os.environ', {'H3_ENABLED_STYLE_ADAPTERS': '', 'H3_STRUCTURAL_CONTROL_ENABLED': '0'}):
            empty = main.capabilities_from_nodes({})
            full = main.capabilities_from_nodes(dict.fromkeys([
                'MiniMaxH3ReferenceToVideo', 'H3CameraGuide', 'H3CameraPrompt',
                'H3OperatorCamera', 'H3ColorGrade', 'H3FilmLook',
                'H3CharacterTrack', 'H3CharacterCrop', 'H3CharacterComposite',
                'ApplyMiniMaxH3FirstBlockCache']))
        self.assertEqual(empty['limits'], {'images': 9, 'videos': 3, 'audios': 3, 'total': 12})
        self.assertTrue(empty['roles'])
        self.assertEqual(empty['cache_modes'], ['off'])
        self.assertFalse(empty['character_refinement_v1'])
        self.assertFalse(empty['color_grade_v1'])
        self.assertTrue(full['character_refinement_v1'])
        self.assertTrue(full['camera_guide']['available'])
        self.assertEqual(full['cache_modes'], ['off', 'safe', 'fast'])

    def test_capabilities_cache_and_unavailable_engine(self):
        async def exercise():
            with patch.dict(main._CAPABILITY_CACHE, at=0.0, value=None, clear=True), patch.object(main.comfy, 'object_info', AsyncMock(return_value={})) as info:
                first = await main.api_capabilities()
                self.assertIs(await main.api_capabilities(), first)
                self.assertEqual(info.await_count, 1)
            with patch.dict(main._CAPABILITY_CACHE, at=0.0, value=None, clear=True), patch.object(main.comfy, 'object_info', AsyncMock(side_effect=RuntimeError('offline'))):
                with self.assertRaises(HTTPException) as error:
                    await main.api_capabilities()
                self.assertEqual(error.exception.status_code, 503)
        asyncio.run(exercise())

    def test_camera_keys_bounded_by_clip_and_count(self):
        def key(t):
            return dict(t=t, orbit=0, elevation=0, distance=4, fov=45, target=[0, 1, 0])
        state = dict(version=1, keys=[key(0), key(5)])
        self.assertIs(main.validated_client_camera(state, 123), state)
        with self.assertRaisesRegex(ValueError, 'концом'):
            main.validated_client_camera(state, 120)
        with self.assertRaisesRegex(ValueError, '12'):
            main.validated_client_camera(dict(version=1, keys=[key(i/10) for i in range(13)]), 123)
        with self.assertRaises(ValueError):
            main.validated_client_camera([], 123)
