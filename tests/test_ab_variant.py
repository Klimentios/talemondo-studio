"""A/B-only GPU job fields (app/ab_variant.py): defaults change nothing, each option changes only its part."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ab_variant import (AB_LORAS, SPARSE_ATTENTION_DEFAULTS, ABVariant, apply_ab_variant,
                            graph_fingerprint, parse_ab_variant)
from app.director import missing_model_files

GOLDEN = Path(__file__).with_name("golden")

try:
    import httpx
    from app import main as remote_main
    from local_studio.worker import gpu_form_fields
    from tests.test_render_golden import CASES, _Env, _FakeComfy, _qwen_plan, _store_job
except ImportError:  # pragma: no cover - runtime deps missing
    httpx = None


def golden_graph() -> dict:
    return json.loads((GOLDEN / "pictures_precompiled_graph.json").read_text(encoding="utf-8"))


def diff_paths(before: dict, after: dict, prefix: str = "") -> set[str]:
    paths = set()
    for key in set(before) | set(after):
        path = f"{prefix}{key}"
        if key not in before or key not in after:
            paths.add(path)
        elif isinstance(before[key], dict) and isinstance(after[key], dict):
            paths |= diff_paths(before[key], after[key], path + ".")
        elif before[key] != after[key]:
            paths.add(path)
    return paths


def variant(**fields) -> ABVariant:
    args = {"name": "", "scheduler": "", "lora": "", "lora_strength": "", "sparse_attention": "",
            "director_plan_json": ""}
    args.update(fields)
    named = {k: args.pop(k) for k in ("resolution", "cache", "camera_words", "color_match", "vae", "sampler", "steps", "legacy_motion")
             if k in args}
    parsed, _ = parse_ab_variant(*args.values(), **named)
    return parsed


class ABVariantGraphTests(unittest.TestCase):
    def test_no_fields_is_no_variant_and_the_same_graph(self):
        self.assertEqual(parse_ab_variant("", "", "", "", "", ""), (None, None))
        graph = golden_graph()
        self.assertIs(apply_ab_variant(graph, None), graph)

    def test_baseline_and_explicit_defaults_build_the_golden_graph(self):
        for fields in ({"name": "baseline"}, {"name": "baseline", "scheduler": "simple", "sparse_attention": "off"}):
            with self.subTest(fields=fields):
                graph = golden_graph()
                self.assertEqual(apply_ab_variant(graph, variant(**fields)), golden_graph())
                self.assertEqual(graph, golden_graph(), "the input graph must not be mutated")

    def test_scheduler_changes_only_the_scheduler(self):
        for scheduler in ("beta", "normal"):
            graph = apply_ab_variant(golden_graph(), variant(name="s", scheduler=scheduler))
            self.assertEqual(diff_paths(golden_graph(), graph), {"124.inputs.scheduler"})
            self.assertEqual(graph["124"]["inputs"]["scheduler"], scheduler)

    def test_turbo_sets_its_lora_steps_sampler_and_drops_the_cache(self):
        variant, _ = parse_ab_variant("turbo4", "", "", "", "", "", turbo="lightx2v4")
        base = golden_graph()
        graph = apply_ab_variant(base, variant)
        self.assertEqual(graph["316"]["inputs"]["lora_name"],
                         "minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors")
        self.assertEqual(graph["124"]["inputs"]["steps"], 4)
        self.assertEqual(graph["123"]["inputs"]["sampler_name"], "er_sde")
        self.assertNotIn("310", graph)
        self.assertEqual(graph["124"]["inputs"]["model"], ["316", 0])
        with self.assertRaises(ValueError):
            parse_ab_variant("x", "", "", "", "", "", turbo="lightx2v4", cache="off")
        with self.assertRaises(ValueError):
            parse_ab_variant("x", "", "", "", "", "", turbo="hyperflow")

    def test_x2_detail_vae_swaps_only_the_video_decoder(self):
        base = golden_graph()
        graph = apply_ab_variant(base, variant(name="x", vae="x2detail"))
        self.assertEqual(graph["314"]["inputs"], {"vae_name": "MiniMax-H3-X2-Detail-v1.safetensors"})
        decoder = graph["122"]
        self.assertEqual(decoder["class_type"], "MiniMaxH3VAEDecodeFast")
        self.assertEqual(decoder["inputs"]["vae"], ["314", 0])
        self.assertEqual(decoder["inputs"]["samples"], base["122"]["inputs"]["samples"])
        self.assertEqual(graph["121"], base["121"], "audio decode is untouched")
        self.assertEqual({k for k in graph if k not in base}, {"314"})
        self.assertEqual(variant(name="x", vae="x2detail").required_nodes(), {"VAELoader", "MiniMaxH3VAEDecodeFast"})

    def test_x2_detail_down_scales_back_to_the_job_size(self):
        base = golden_graph()
        graph = apply_ab_variant(base, variant(name="x", vae="x2detail_down"))
        scale = graph["315122"]
        self.assertEqual(scale["class_type"], "ImageScale")
        self.assertEqual((scale["inputs"]["width"], scale["inputs"]["height"]),
                         (base["136"]["inputs"]["width"], base["136"]["inputs"]["height"]))
        users = [node for node in graph.values() if ["122", 0] in node["inputs"].values()]
        self.assertEqual(users, [scale])
        with self.assertRaises(ValueError):
            variant(name="x", vae="x4")

    def test_face_lora_trigger_and_missing_files(self):
        parsed = variant(name="f", lora="face_realism", lora_strength="0.5")
        graph = apply_ab_variant(golden_graph(), parsed)
        self.assertTrue(graph["136"]["inputs"]["prompt"].startswith("Facial Realism, "))
        self.assertEqual(parsed.missing_files({}), ["minimax-h3-facial-realism-closeup-cp2000.safetensors"])
        info = {"LoraLoaderModelOnly": {"input": {"required": {"lora_name": [[parsed.lora_file]]}}}}
        self.assertEqual(parsed.missing_files(info), [])

    def test_few_step_loras_are_unavailable(self):
        with self.assertRaises(ValueError):
            variant(name="t", lora="turbo_ref2v", lora_strength="1.0", sampler="euler", steps="8", cache="off")
        with self.assertRaises(ValueError):
            variant(name="t", steps="8")
        with self.assertRaises(ValueError):
            variant(name="t", lora="hyperflow", steps="30")

    def test_legacy_motion_uses_the_named_fast_cache(self):
        graph = apply_ab_variant(golden_graph(), variant(name="l", legacy_motion="on"))
        if "310" in graph:
            self.assertEqual(graph["310"]["inputs"]["mode"], "H3 Fast — 0.10 / max 2")
            self.assertFalse(graph["310"]["inputs"]["temporal_guard"])
        with self.assertRaises(ValueError):
            variant(name="l", legacy_motion="yes")

    def test_camera_motion_trigger_opens_the_prompt(self):
        graph = apply_ab_variant(golden_graph(), variant(name="c", lora="camera_motion", lora_strength="0.8"))
        self.assertTrue(graph["136"]["inputs"]["prompt"].startswith("camera motion, "))

    def test_realism_lora_adds_one_loader_and_the_trigger(self):
        base = golden_graph()
        graph = apply_ab_variant(base, variant(name="r", lora="realism_people", lora_strength="0.6"))
        self.assertEqual(diff_paths(base, graph),
                         {"312", "124.inputs.model", "126.inputs.model", "136.inputs.prompt"})
        self.assertEqual(graph["312"]["class_type"], "LoraLoaderModelOnly")
        self.assertEqual(graph["312"]["inputs"], {"model": ["310", 0], "strength_model": 0.6,
                                                  "lora_name": "h3-realism-people-t2v-i2v-r2v.safetensors"})
        self.assertEqual(graph["124"]["inputs"]["model"], ["312", 0])
        self.assertEqual(graph["126"]["inputs"]["model"], ["312", 0])
        self.assertEqual(graph["136"]["inputs"]["prompt"], "r34l1sm, " + base["136"]["inputs"]["prompt"])

    def test_trigger_opens_detailed_description_of_official_prompts(self):
        base = golden_graph()
        base["136"]["inputs"]["prompt"] = ("subject_definitions:\nADAM <Picture 1>\n\ndetailed_description:\n"
                                           "ADAM walks.\n\naudio_description:\nsteps")
        graph = apply_ab_variant(base, variant(name="r", lora="realism_people"))
        prompt = graph["136"]["inputs"]["prompt"]
        self.assertTrue(prompt.startswith("subject_definitions:"))
        self.assertIn("detailed_description:\nr34l1sm, ADAM walks.", prompt)
        self.assertEqual(graph["312"]["inputs"]["strength_model"], 1.0)

    def test_sparse_attention_patches_the_unet_with_node_defaults(self):
        base = golden_graph()
        graph = apply_ab_variant(base, variant(name="sp", sparse_attention="sol-attn"))
        self.assertEqual(diff_paths(base, graph), {"313", "310.inputs.model"})
        self.assertEqual(graph["313"]["class_type"], "BlockSparseAttention")
        self.assertEqual(graph["313"]["inputs"], {"model": ["127", 0], **SPARSE_ATTENTION_DEFAULTS})
        self.assertEqual(graph["310"]["inputs"]["model"], ["313", 0])

    def test_sparse_attention_without_cache_feeds_the_sampler(self):
        base = golden_graph()
        del base["310"]
        base["124"]["inputs"]["model"] = base["126"]["inputs"]["model"] = ["127", 0]
        graph = apply_ab_variant(base, variant(name="sp", sparse_attention="sol-attn"))
        self.assertEqual(diff_paths(base, graph), {"313", "124.inputs.model", "126.inputs.model"})

    def test_lora_refuses_to_stack_on_the_style_lora(self):
        base = golden_graph()
        base["311"] = {"inputs": {"model": ["310", 0]}, "class_type": "LoraLoaderModelOnly", "_meta": {"title": "x"}}
        with self.assertRaisesRegex(ValueError, "стилевой LoRA"):
            apply_ab_variant(base, variant(name="r", lora="style3d"))

    def test_strict_validation(self):
        bad = [{"scheduler": "karras"}, {"lora": "sketch2d"}, {"lora": "realism_people", "lora_strength": "1.3"},
               {"lora": "realism_people", "lora_strength": "-0.1"}, {"lora": "realism_people", "lora_strength": "x"},
               {"lora_strength": "0.6"}, {"sparse_attention": "sla"}, {"sparse_attention": "on"},
               {"name": "Baseline With Spaces"}, {"name": "b", "director_plan_json": "[1]"},
               {"name": "b", "director_plan_json": "{"}]
        for fields in bad:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                variant(**fields)

    def test_fingerprint_ignores_random_upload_names(self):
        base = golden_graph()
        other = copy.deepcopy(base)
        other["137"]["inputs"]["image"] = "studio-0123-other.png"
        self.assertEqual(graph_fingerprint(base), graph_fingerprint(other))
        other["129"]["inputs"]["noise_seed"] += 1
        self.assertNotEqual(graph_fingerprint(base)["graph_sha256"], graph_fingerprint(other)["graph_sha256"])

    def test_missing_lora_file_is_reported(self):
        info = {"LoraLoaderModelOnly": {"input": {"required": {"lora_name": [[AB_LORAS["style3d"][0]]]}}}}
        self.assertEqual(missing_model_files("realism_people", info), [AB_LORAS["realism_people"][0]])
        self.assertEqual(missing_model_files("style3d", info), [])


class _ABComfy(_FakeComfy if httpx else object):
    NODES = (_FakeComfy.NODES | {"BlockSparseAttention"}) if httpx else set()

    def __init__(self, loras=()):
        super().__init__()
        self.loras = list(loras)
        self.tracked = []

    async def object_info(self):
        info = {name: {} for name in self.NODES}
        info["LoraLoaderModelOnly"] = {"input": {"required": {"lora_name": [self.loras]}}}
        return info

    def track_vram(self, prompt_id):
        self.tracked.append(prompt_id)


@unittest.skipIf(httpx is None, "FastAPI/httpx runtime dependencies not installed")
class ABVariantApiTests(unittest.TestCase):
    """The real GPU API with the golden picture job (picture-only) and a fake ComfyUI."""

    def submit(self, extra: dict, loras=("h3-realism-people-t2v-i2v-r2v.safetensors",), qwen_ok=True):
        golden = json.loads((GOLDEN / f"pictures_gpu_qwen_{'plan' if qwen_ok else 'fails'}.json").read_text(encoding="utf-8"))
        fake = _ABComfy(loras)
        calls = []
        with _Env(), tempfile.TemporaryDirectory() as temp:
            store, job = _store_job(Path(temp), CASES["pictures"])
            manifest = json.loads(job["manifest_json"])

            class FakeDirector:
                def plan(self, *args, **kwargs):
                    calls.append(1)
                    if not qwen_ok:
                        raise RuntimeError("Qwen Director вернула ответ не по JSON-контракту: golden")
                    return _qwen_plan(manifest)

            files = []
            for entry in manifest["images"]:
                asset = store.get_asset(entry["asset_id"])
                files.append(("images", (asset["original_name"], Path(asset["path"]).read_bytes(), asset["mime"])))
            data = {**gpu_form_fields(job), **extra}

            async def post():
                transport = httpx.ASGITransport(app=remote_main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://gpu.test") as client:
                    return await client.post("/api/jobs", data=data, files=files)

            old = remote_main.comfy
            remote_main.comfy = fake
            os.environ["H3_JOB_REGISTRY_PATH"] = f"{temp}/registry.sqlite3"
            try:
                with patch.object(remote_main, "LocalQwenDirector", FakeDirector):
                    response = asyncio.run(post())
            finally:
                remote_main.comfy = old
        return response, fake, calls, golden

    def test_named_baseline_queues_the_golden_graph(self):
        response, fake, calls, golden = self.submit({"ab_variant": "baseline"})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(fake.graph, golden["graph"])
        ab = response.json()["director"]["ab"]
        self.assertEqual(ab["name"], "baseline")
        self.assertEqual(ab["scheduler"], "simple")
        self.assertEqual(ab["base_graph_sha256"], ab["graph_sha256"])
        self.assertEqual(ab["prompt"], golden["graph"]["136"]["inputs"]["prompt"])
        self.assertEqual(ab["director_plan"]["schema_version"], 1)
        self.assertEqual(fake.tracked, ["comfy-golden-1"])
        self.assertEqual(calls, [1])
        # The rest of the Director summary is what Studio jobs get.
        summary = {k: v for k, v in response.json()["director"].items() if k not in {"ab", "cache_execution", "execution_record"}}
        self.assertEqual(summary, golden["response"]["director"])

    def test_plan_replay_skips_qwen_and_keeps_the_prompt(self):
        first, _, _, golden = self.submit({"ab_variant": "baseline"})
        plan = first.json()["director"]["ab"]["director_plan"]
        response, fake, calls, _ = self.submit({"ab_variant": "beta", "ab_scheduler": "beta",
                                                "ab_director_plan_json": json.dumps(plan)})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(calls, [], "Qwen must not wake for a replayed plan")
        self.assertEqual(diff_paths(golden["graph"], fake.graph), {"124.inputs.scheduler"})
        ab = response.json()["director"]["ab"]
        self.assertTrue(ab["replayed_plan"])
        self.assertEqual(ab["base_prompt_sha256"], first.json()["director"]["ab"]["base_prompt_sha256"])

    def test_fallback_replay_repeats_the_studio_prompt_route(self):
        first, fake, calls, golden = self.submit({"ab_variant": "baseline"}, qwen_ok=False)
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(fake.graph, golden["graph"])
        plan = first.json()["director"]["ab"]["director_plan"]
        self.assertEqual(set(plan), {"director_fallback"})
        response, fake, calls, _ = self.submit({"ab_variant": "normal", "ab_scheduler": "normal",
                                                "ab_director_plan_json": json.dumps(plan)}, qwen_ok=False)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(calls, [])
        self.assertEqual(diff_paths(golden["graph"], fake.graph), {"124.inputs.scheduler"})

    def test_realism_lora_via_api(self):
        response, fake, _, golden = self.submit({"ab_variant": "realism-0.6", "ab_lora": "realism_people",
                                                 "ab_lora_strength": "0.6"})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(diff_paths(golden["graph"], fake.graph),
                         {"312", "124.inputs.model", "126.inputs.model", "136.inputs.prompt"})
        self.assertTrue(fake.graph["136"]["inputs"]["prompt"].startswith("r34l1sm, "))

    def test_missing_lora_weights_is_422_before_qwen(self):
        response, fake, calls, _ = self.submit({"ab_variant": "r", "ab_lora": "realism_people"}, loras=())
        self.assertEqual(response.status_code, 422)
        self.assertIn("h3-realism-people-t2v-i2v-r2v.safetensors", response.json()["detail"])
        self.assertEqual(calls, [])
        self.assertIsNone(fake.graph)

    def test_unknown_values_are_422(self):
        for extra in ({"ab_scheduler": "karras"}, {"ab_lora": "sketch2d"}, {"ab_sparse_attention": "sla"},
                      {"ab_variant": "b", "ab_lora": "realism_people", "ab_lora_strength": "2"}):
            with self.subTest(extra=extra):
                response, fake, _, _ = self.submit(extra)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIsNone(fake.graph)

    def test_capabilities_list_available_variants(self):
        caps = remote_main.capabilities_from_nodes({"LoraLoaderModelOnly": {"input": {"required": {
            "lora_name": [[AB_LORAS["realism_people"][0]]]}}}, "BlockSparseAttention": {}})
        self.assertEqual(caps["ab_v1"]["loras"], ["realism_people"])
        self.assertEqual(caps["ab_v1"]["sparse_attention"], ["off", "sol-attn"])
        self.assertEqual(remote_main.capabilities_from_nodes({})["ab_v1"]["sparse_attention"], ["off"])


class VramPeakTests(unittest.TestCase):
    @unittest.skipIf(httpx is None, "httpx not installed")
    def test_peak_is_sampled_only_for_tracked_jobs(self):
        from app.comfy_client import ComfyClient

        client = ComfyClient()
        frees = iter([30, 10, 20])

        async def fake_status(prompt_id):
            return {"status": "running"}

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"devices": [{"vram_total": 80 * 2**30, "vram_free": next(frees) * 2**30}]}

        async def fake_get(self, url, **kwargs):
            return Response()

        async def run():
            with patch.object(client, "_status", fake_status), patch("httpx.AsyncClient.get", fake_get):
                self.assertNotIn("vram", await client.status("studio"))
                client.track_vram("ab")
                for _ in range(3):
                    result = await client.status("ab")
            return result

        result = asyncio.run(run())
        self.assertEqual(result["vram"]["peak_used_gb"], 70.0)
        self.assertEqual(result["vram"]["samples"], 3)
        self.assertEqual(result["vram"]["total_gb"], 80.0)


if __name__ == "__main__":
    unittest.main()


class ShiftAndSamplerTests(unittest.TestCase):
    """Quality kit 2026-11-02: MiniMaxH3SigmaShift motion dial and er_sde (community templates)."""

    def test_shift_goes_last_in_the_model_chain(self):
        variant, _ = parse_ab_variant("shift-8", "", "realism_people", "0.8", "", "", shift="8")
        graph = apply_ab_variant(golden_graph(), variant)
        self.assertEqual(graph["317"]["class_type"], "MiniMaxH3SigmaShift")
        self.assertEqual(graph["317"]["inputs"], {"model": ["312", 0], "shift_video": 8.0, "shift_audio": 3.0})
        self.assertEqual(graph["124"]["inputs"]["model"], ["317", 0])
        self.assertIn("MiniMaxH3SigmaShift", variant.required_nodes())
        self.assertEqual(variant.describe()["shift"], 8.0)

    def test_shift_range_and_er_sde(self):
        for bad in ("2", "21", "x"):
            with self.assertRaises(ValueError):
                parse_ab_variant("s", "", "", "", "", "", shift=bad)
        variant, _ = parse_ab_variant("er", "beta", "", "", "", "", sampler="er_sde")
        graph = apply_ab_variant(golden_graph(), variant)
        self.assertEqual((graph["123"]["inputs"]["sampler_name"], graph["124"]["inputs"]["scheduler"]), ("er_sde", "beta"))
        self.assertNotIn("317", graph)


class ContinuationAnchorTests(unittest.TestCase):
    """«Продолжение» seam A/B (ComfyUI-H3-LongTake measurement): keyframe 5 frames, inpaint 5/22."""

    def graph(self, anchor, guides=()):
        from tests.test_continuation import CLIP, request
        from app.graph import build_prompt
        variant, _ = parse_ab_variant(anchor, "", "", "", "", "", anchor=anchor)
        prompt = "<Picture 1> walks into <Picture 2>." if guides else "<Picture 1> keeps walking down the corridor."
        names = ["hero.png", "scene.png"] if guides else ["hero.png"]
        return apply_ab_variant(build_prompt(request(prompt), names, continuation=CLIP, guide_frames=list(guides)), variant), variant

    def test_keyframe5_keeps_the_guide_with_five_frames_and_their_sound(self):
        graph, variant = self.graph("keyframe5")
        self.assertEqual((graph["352"]["inputs"]["batch_index"], graph["352"]["inputs"]["length"]), (-5, 5))
        self.assertEqual((graph["353"]["inputs"]["start_index"], graph["353"]["inputs"]["duration"]), (round(67 / 24, 4), round(5 / 24, 4)))
        self.assertIn("354", graph)
        self.assertNotIn("H3ContinuationInpaint", variant.required_nodes())

    def test_inpaint_replaces_the_guide_with_the_masked_latent(self):
        graph, variant = self.graph("inpaint5", guides=[(2, 40)])
        self.assertNotIn("354", graph)
        self.assertEqual(graph["356"]["class_type"], "H3ContinuationInpaint")
        self.assertEqual(graph["356"]["inputs"]["image"], ["352", 0])
        self.assertEqual(graph["125"]["inputs"]["latent_image"], ["356", 0])
        self.assertEqual(graph["320"]["inputs"]["positive"], ["136", 0])  # scene guide chains from Ref2VA now
        self.assertFalse(any(value == ["354", 0] for node in graph.values() for value in node["inputs"].values()))
        self.assertIn("H3ContinuationInpaint", variant.required_nodes())

    def test_refusals(self):
        with self.assertRaises(ValueError):
            parse_ab_variant("a", "", "", "", "", "", anchor="inpaint9")
        with self.assertRaises(ValueError):
            parse_ab_variant("a", "", "", "", "", "", hires="bislerp", anchor="inpaint5")
        variant, _ = parse_ab_variant("a", "", "", "", "", "", anchor="inpaint5")
        with self.assertRaises(ValueError):
            apply_ab_variant(golden_graph(), variant)  # no «Продолжение» in the job


class X2DetailHostRamTests(unittest.TestCase):
    """06.11: X2-Detail decode killed ComfyUI by OOM on a 128 GB host; refuse it before the GPU there."""

    def test_small_host_refuses_x2detail(self):
        with patch("app.ab_variant.host_ram_gb", return_value=120.7):
            with self.assertRaisesRegex(ValueError, "ОЗУ"):
                variant(name="x2", vae="x2detail")

    def test_large_host_accepts_x2detail(self):
        with patch("app.ab_variant.host_ram_gb", return_value=256.0):
            self.assertEqual(variant(name="x2", vae="x2detail").vae, "x2detail")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
