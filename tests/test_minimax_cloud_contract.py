"""Guard the paid cloud request before any reference upload or task creation."""
import json
import unittest

from local_studio.minimax_cloud import MiniMaxCloudTransport


class MiniMaxCloudContractTests(unittest.TestCase):
    def job(self, *, resolution="768P", images=None, prompt="An original shot"):
        return {
            "compiled_prompt": prompt,
            "settings_json": json.dumps({"backend": "minimax_api", "cloud_model": "MiniMax-H3",
                                         "cloud_resolution": resolution, "duration": 5,
                                         "aspect_ratio": "9:16", "style_adapter": "none"}),
            "manifest_json": json.dumps({"images": images or [], "videos": [], "audios": []}),
        }

    def test_current_resolution_is_enforced_before_upload(self):
        MiniMaxCloudTransport.validate(self.job())
        for resolution in ("480P", "720P", "2K"):
            with self.subTest(resolution=resolution), self.assertRaises(ValueError):
                MiniMaxCloudTransport.validate(self.job(resolution=resolution))

    def test_source_video_is_rejected_but_motion_is_allowed(self):
        job = self.job(images=[{"role": "Персонаж целиком", "owner": "VEGA"},
                               {"role": "Лицо героя", "owner": "VEGA"}, {"role": "Окружение"}])
        manifest = json.loads(job["manifest_json"])
        for role_key in ("role", "job_role"):
            manifest["videos"] = [{role_key: "Исходное видео"}]
            job["manifest_json"] = json.dumps(manifest)
            with self.subTest(role_key=role_key), self.assertRaisesRegex(ValueError, "Свой GPU"):
                MiniMaxCloudTransport.validate(job)
        manifest["videos"] = [{"role": "Движение"}]
        job["manifest_json"] = json.dumps(manifest)
        MiniMaxCloudTransport.validate(job)

    def test_prompt_limit_is_checked_before_paid_request(self):
        with self.assertRaisesRegex(ValueError, "7000"):
            MiniMaxCloudTransport.validate(self.job(prompt="x" * 7001))

class MiniMaxCloudRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_role_rejected_before_job_creation(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from fastapi import HTTPException
        from local_studio.routes.jobs import JobInput, make_router

        store = SimpleNamespace(get_asset=Mock(return_value={"kind": "video", "role": "Движение"}),
                                create_job=Mock())
        cloud = SimpleNamespace(resolve_settings=lambda settings: {"backend": "minimax_api"})
        ctx = SimpleNamespace(store=store, gpu_transport=SimpleNamespace(cloud=cloud))
        endpoint = next(route.endpoint for route in make_router(ctx).routes
                        if route.path == "/api/jobs" and "POST" in route.methods)
        payload = JobInput(prompt="test", asset_ids=["v"], reference_roles={"v": "Исходное видео"})
        with self.assertRaises(HTTPException) as caught:
            await endpoint(payload)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("Свой GPU", caught.exception.detail)
        store.create_job.assert_not_called()


class ApiSwapTrialTests(unittest.TestCase):
    def test_source_swap_blocked_unless_explicit_trial(self):
        job = MiniMaxCloudContractTests.job(MiniMaxCloudContractTests())
        manifest = json.loads(job["manifest_json"]); manifest["videos"] = [{"role": "Исходное видео"}]
        job["manifest_json"] = json.dumps(manifest)
        with self.assertRaisesRegex(ValueError, "точную замену"):
            MiniMaxCloudTransport.validate(job)
        settings = json.loads(job["settings_json"]); settings["api_swap_trial"] = True
        job["settings_json"] = json.dumps(settings)
        MiniMaxCloudTransport.validate(job)



class ApiSwapRecipeTests(unittest.TestCase):
    def job(self, prompt):
        images = [{"role": "Персонаж целиком", "owner": "VEGA"}, {"role": "Лицо героя", "owner": "VEGA"},
                  {"role": "Окружение", "owner": ""}]
        return {"compiled_prompt": prompt,
                "settings_json": json.dumps({"backend": "minimax_api", "cloud_model": "MiniMax-H3", "cloud_resolution": "768P",
                                             "duration": 6, "aspect_ratio": "9:16", "style_adapter": "none",
                                             "swap_recipe": "minimax-api-action-map-v1"}),
                "manifest_json": json.dumps({"images": images, "videos": [{"role": "Исходное видео"}], "audios": []})}

    def test_recipe_swap_passes_only_with_action_map_and_guard(self):
        MiniMaxCloudTransport.validate(self.job("[Shot 1] 00:00–00:02 VEGA sits.\nSOURCE LEAK GUARD: Only VEGA."))
        with self.assertRaisesRegex(ValueError, "рецепту MiniMax API"):
            MiniMaxCloudTransport.validate(self.job("VEGA sits."))
