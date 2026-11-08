"""Scenarist/Director model profiles: launcher, request extras, capabilities, sleep level, A/B."""
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import scenarist, scenarist_profile
from app.director import LocalQwenDirector

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "scripts" / "run_h3_qwen_director.sh"
CONF = ROOT / "scripts" / "h3_scenarist_conf.sh"
TEMPLATE = ROOT / "deploy" / "h3-qwen-director.conf"

# The exact 2026-09-30 command line (deploy/h3-qwen-director.conf environment).
LEGACY_ARGS = ["serve", "Qwen/Qwen3-VL-8B-Instruct", "--dtype", "bfloat16", "--host", "127.0.0.1",
               "--port", "8000", "--enable-sleep-mode", "--gpu-memory-utilization", "0.30",
               "--max-model-len", "32768", "--structured-outputs-config",
               '{"backend":"xgrammar","disable_any_whitespace":true,"disable_fallback":true}']


def run_launcher(env: dict, venv_vllm: bool = True) -> tuple[int, list[str], str]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        stub = "#!/usr/bin/env bash\nprintf '%s\\n' \"$0\" \"$@\" > \"$H3_STUB_OUT\"\n"
        (tmp / "bin").mkdir()
        (tmp / "bin" / "vllm").write_text(stub)
        (tmp / "bin" / "vllm").chmod(0o755)
        if venv_vllm:
            (tmp / "qwen-next-venv" / "bin").mkdir(parents=True)
            (tmp / "qwen-next-venv" / "bin" / "vllm").write_text(stub)
            (tmp / "qwen-next-venv" / "bin" / "vllm").chmod(0o755)
        out = tmp / "args.txt"
        full = {"PATH": f"{tmp / 'bin'}:/usr/bin:/bin", "H3_STUB_OUT": str(out), "H3_ROOT": str(tmp), **env}
        result = subprocess.run(["bash", str(RUN)], env=full, capture_output=True, text=True, timeout=30)
        lines = out.read_text().splitlines() if out.exists() else []
        if lines:
            lines[0] = lines[0].replace(str(tmp), "$ROOT")
        return result.returncode, lines, result.stderr


class LauncherTests(unittest.TestCase):
    def test_default_command_is_byte_identical(self):
        for env in ({"H3_QWEN_GPU_UTIL": "0.30", "H3_QWEN_MAX_MODEL_LEN": "32768"},
                    {"H3_QWEN_GPU_UTIL": "0.30", "H3_QWEN_MAX_MODEL_LEN": "32768", "H3_SCENARIST_PROFILE": "default"}):
            code, args, err = run_launcher(env)
            self.assertEqual(code, 0, err)
            self.assertEqual(args[0], "$ROOT/bin/vllm")  # the qwen-venv vllm from PATH
            self.assertEqual(args[1:], LEGACY_ARGS)

    def test_27b_profile_uses_next_venv_and_pinned_flags(self):
        code, args, err = run_launcher({"H3_SCENARIST_PROFILE": "qwen3.8-27b-fp8", "H3_QWEN_GPU_UTIL": "0.45",
                                        "H3_QWEN_MAX_MODEL_LEN": "32768"})
        self.assertEqual(code, 0, err)
        self.assertEqual(args[0], "$ROOT/qwen-next-venv/bin/vllm")
        flags = args[1:]
        self.assertEqual(flags[:4], ["serve", "Qwen/Qwen3.8-27B-FP8", "--revision",
                                     scenarist_profile.PROFILES["qwen3.8-27b-fp8"]["revision"]])
        joined = " ".join(flags)
        for expected in ("--enable-sleep-mode", "--gpu-memory-utilization 0.45", "--max-model-len 32768",
                         "--reasoning-parser qwen3", '--default-chat-template-kwargs {"enable_thinking":false}',
                         '--limit-mm-per-prompt {"image":24,"video":0}', "--host 127.0.0.1",
                         '"backend":"xgrammar"'):
            self.assertIn(expected, joined)
        for optional in ("--kv-cache-dtype", "--speculative-config", "--enforce-eager"):
            self.assertNotIn(optional, joined)
        code, args, _ = run_launcher({"H3_SCENARIST_PROFILE": "qwen3.6-27b-fp8", "H3_QWEN_MTP": "1",
                                      "H3_QWEN_KV_FP8": "1"})
        self.assertEqual(args[2], "Qwen/Qwen3.6-27B-FP8")
        self.assertIn('{"method":"mtp","num_speculative_tokens":3}', args)
        self.assertIn("fp8", args)
        self.assertIn("0.45", args)  # profile budget when the conf does not set one

    def test_bad_profile_or_missing_venv_refuses(self):
        code, args, err = run_launcher({"H3_SCENARIST_PROFILE": "qwen9"})
        self.assertEqual(code, 2)
        self.assertIn("Unknown H3_SCENARIST_PROFILE", err)
        code, args, err = run_launcher({"H3_SCENARIST_PROFILE": "qwen3.8-27b-fp8"}, venv_vllm=False)
        self.assertEqual(code, 2)
        self.assertEqual(args, [])

    def test_bash_and_python_tables_agree(self):
        text = RUN.read_text()
        for name, profile in scenarist_profile.PROFILES.items():
            if name == "default":
                self.assertIn(f'model="${{H3_DIRECTOR_MODEL:-{profile["model"]}}}"', text)
                continue
            self.assertRegex(text, rf'{re.escape(name)}\) pinned_model={re.escape(profile["model"])}; '
                                   rf'revision={profile["revision"]}')
            switch = (ROOT / "scripts" / "h3_scenarist_switch.sh").read_text()
            self.assertIn(profile["revision"], switch)
            self.assertIn(profile["revision"], (ROOT / "scripts" / "install_h3_scenarist_next.sh").read_text())
            manifest = (ROOT / "deploy" / "scenarist-models" / f"{name}.sha256").read_text()
            self.assertIn(profile["revision"], manifest.splitlines()[0])
            self.assertGreater(len([l for l in manifest.splitlines() if re.match(r"^[0-9a-f]{64}  ", l)]), 60)

    def test_conf_renderer(self):
        default = subprocess.run(["bash", str(CONF), "default", "/workspace/h3", str(TEMPLATE)],
                                 capture_output=True, text=True, check=True).stdout
        self.assertEqual(default, TEMPLATE.read_text())
        next_conf = subprocess.run(["bash", str(CONF), "qwen3.8-27b-fp8", "/srv/h3", str(TEMPLATE), "0.45", "1"],
                                   capture_output=True, text=True, check=True).stdout
        env = next(l for l in next_conf.splitlines() if l.startswith("environment="))
        self.assertIn('PATH="/srv/h3/qwen-next-venv/bin:', env)
        self.assertIn('H3_QWEN_GPU_UTIL="0.45"', env)
        self.assertIn('H3_SCENARIST_PROFILE="qwen3.8-27b-fp8"', env)
        self.assertNotIn("qwen-venv/bin", env)
        self.assertEqual(subprocess.run(["bash", str(CONF), "qwen3.8-27b-fp8", "/r", str(TEMPLATE), "0.45", "3"],
                                        capture_output=True).returncode, 2)


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def captured_body(env: dict) -> dict:
    sent = {}

    def fake_urlopen(request, timeout=0):
        sent.update(json.loads(request.data))
        return FakeResponse(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())

    with patch.dict(os.environ, env, clear=False), patch("urllib.request.urlopen", fake_urlopen):
        for key in ("H3_SCENARIST_PROFILE", "H3_DIRECTOR_MODEL"):
            if key not in env:
                os.environ.pop(key, None)
        director = LocalQwenDirector()
        director._post({"model": director.model, "temperature": 0, "max_tokens": 10, "messages": []})
    return sent


class DirectorProfileTests(unittest.TestCase):
    def test_default_request_unchanged(self):
        body = captured_body({})
        self.assertEqual(body, {"model": "Qwen/Qwen3-VL-8B-Instruct", "temperature": 0, "max_tokens": 10,
                                "messages": []})

    def test_27b_profile_disables_thinking_only(self):
        body = captured_body({"H3_SCENARIST_PROFILE": "qwen3.8-27b-fp8"})
        self.assertEqual(body["model"], "Qwen/Qwen3.8-27B-FP8")
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(body["temperature"], 0)  # the Director keeps deterministic JSON sampling
        self.assertNotIn("top_k", body)

    def test_unknown_profile_is_a_value_error(self):
        with patch.dict(os.environ, {"H3_SCENARIST_PROFILE": "nope"}):
            with self.assertRaises(ValueError):
                LocalQwenDirector()
        with patch.dict(os.environ, {"H3_QWEN_SLEEP_LEVEL": "3"}):
            with self.assertRaises(ValueError):
                LocalQwenDirector()

    def test_scenarist_extras(self):
        sent = {}

        def fake_urlopen(request, timeout=0):
            sent.clear()
            sent.update(json.loads(request.data))
            return FakeResponse(json.dumps({"choices": [{"message": {"content": "x"}}]}).encode())

        with patch("urllib.request.urlopen", fake_urlopen):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("H3_SCENARIST_PROFILE", None)
                scenarist._chat(LocalQwenDirector(), [])
            self.assertEqual(set(sent), {"model", "messages", "temperature", "max_tokens"})
            with patch.dict(os.environ, {"H3_SCENARIST_PROFILE": "qwen3.8-27b-fp8"}):
                scenarist._chat(LocalQwenDirector(), [])
            self.assertEqual(sent["chat_template_kwargs"], {"enable_thinking": False})
            self.assertEqual((sent["top_p"], sent["top_k"], sent["temperature"]), (0.8, 20, 0.4))
            self.assertNotIn("presence_penalty", sent)


class RuntimeControlTests(unittest.TestCase):
    def calls(self, env: dict, endpoint: str):
        seen = []
        with patch.dict(os.environ, env):
            for key in ("H3_SCENARIST_PROFILE", "H3_QWEN_SLEEP_LEVEL"):
                if key not in env:
                    os.environ.pop(key, None)
            director = LocalQwenDirector()
            with patch.object(director, "_runtime_request", lambda e, p=None: seen.append((e, p))), \
                 patch("app.qwen_lifecycle._free_comfy_models") as free:
                director._runtime_control(endpoint)
        return seen, free.call_count

    def test_default_planner_also_wakes_only_after_comfy_is_freed(self):
        # One model on the GPU at a time (2026-10-08): no wake beside a render, even for the 8B planner.
        self.assertEqual(self.calls({}, "sleep?level=1"), ([("sleep?level=1", None)], 0))
        self.assertEqual(self.calls({}, "wake_up"), ([("wake_up", None)], 1))

    def test_27b_wakes_only_after_comfy_is_freed(self):
        seen, freed = self.calls({"H3_SCENARIST_PROFILE": "qwen3.8-27b-fp8"}, "wake_up")
        self.assertEqual((seen, freed), ([("wake_up", None)], 1))
        with patch.dict(os.environ, {"H3_SCENARIST_PROFILE": "qwen3.8-27b-fp8"}):
            director = LocalQwenDirector()
            with patch("app.qwen_lifecycle._free_comfy_models", side_effect=RuntimeError("busy")), \
                 patch.object(director, "_runtime_request") as request:
                with self.assertRaises(RuntimeError):
                    director._runtime_control("wake_up")
                request.assert_not_called()

    def test_sleep_level_2_reloads_weights_on_wake(self):
        env = {"H3_SCENARIST_PROFILE": "qwen3.8-27b-fp8", "H3_QWEN_SLEEP_LEVEL": "2"}
        self.assertEqual(self.calls(env, "sleep?level=1")[0], [("sleep?level=2", None)])
        self.assertEqual(self.calls(env, "wake_up")[0], [
            ("wake_up?tags=weights", None), ("collective_rpc", {"method": "reload_weights"}),
            ("wake_up?tags=kv_cache", None)])


class CapabilityTests(unittest.TestCase):
    def test_capabilities_key(self):
        from app import main
        with patch.dict(os.environ, {}, clear=False):
            for key in ("H3_SCENARIST_PROFILE", "H3_DIRECTOR_MODEL", "H3_QWEN_SLEEP_LEVEL"):
                os.environ.pop(key, None)
            caps = main.capabilities_from_nodes({})["scenarist_v1"]
            self.assertEqual((caps["profile"], caps["model"], caps["vllm"]), ("default", "Qwen/Qwen3-VL-8B-Instruct", "0.15.0"))
            self.assertFalse(caps["coexists_with_render"])
            self.assertIn("ComfyUI", caps["hint"])
            with patch.dict(os.environ, {"H3_SCENARIST_PROFILE": "qwen3.8-27b-fp8"}):
                caps = main.capabilities_from_nodes({})["scenarist_v1"]
            self.assertEqual((caps["model"], caps["vllm"], caps["thinking"]), ("Qwen/Qwen3.8-27B-FP8", "0.19.1", False))
            self.assertFalse(caps["coexists_with_render"])
            self.assertIn("ComfyUI", caps["hint"])
            with patch.dict(os.environ, {"H3_SCENARIST_PROFILE": "nope"}):
                self.assertIn("error", main.capabilities_from_nodes({})["scenarist_v1"])


class ScenaristABTests(unittest.TestCase):
    def test_ab_on_mocked_responses(self):
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        import h3_scenarist_ab as ab
        from app.h3_ir import Reference, build_skeleton, render_ir, shot_starts

        briefs = ab.load_briefs(ab.DEFAULT_BRIEFS)
        self.assertGreaterEqual(len(briefs), 10)
        self.assertLessEqual(len(briefs), 30)
        for brief in briefs:  # every starter brief is a valid /api/scenarist request
            scenarist.ScenaristRequest(**ab.request_body(brief))

        def good(brief):
            refs = [Reference(r["kind"], r["number"], r["role"], r.get("owner", "")) for r in brief["references"]]
            sk = build_skeleton(refs, speaking_owners=brief.get("speaking_owners", []))
            headers = shot_starts(brief["cut_durations"])
            filler = " ".join(["The warm practical light moves slowly across the textured walls."] * 40)
            return render_ir({
                "subject_definitions": "\n".join(sk.subject_lines),
                "summary": f"[{' + '.join(sk.task_types)}] A short cinematic scene.",
                "retention_analysis": "\n".join(l.replace("[SHOTS]", "[Shot 1]") for l in sk.retention_lines),
                "detailed_description": "The target video is live-action.\n" + "\n".join(
                    f"{h} {filler if i == 0 else 'The camera holds steady.'}" for i, h in enumerate(headers)),
                "overall_soundscape": "Room tone throughout.", "non_diegetic_music": "N/A"})

        by_text = {b["brief"]: b for b in briefs}
        busy = {"n": 0}

        def model_a(url, body):
            if busy["n"] == 0:
                busy["n"] += 1
                return 409, "render"
            brief = by_text[body["brief"]]
            if "ограблен" in brief["brief"].lower():
                return 200, {"prompt": "I'm sorry, but I can't help with planning a robbery.", "problems": ["x"]}
            return 200, {"prompt": good(brief), "problems": []}

        def model_b(url, body):
            return 200, {"prompt": good(by_text[body["brief"]]), "problems": []}

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            a = ab.run_label("8b", "http://a", briefs, out, post=model_a, sleep=lambda s: None)
            b = ab.run_label("27b", "http://b", briefs, out, post=model_b, sleep=lambda s: None)
            self.assertTrue((out / "8b.json").exists())
            sa, sb = ab.summarize(a), ab.summarize(b)
            heists = sum("ограблен" in x["brief"].lower() for x in briefs)
            self.assertEqual(sa["refusals"], heists)
            self.assertEqual(sb["refusals"], 0)
            self.assertEqual(sb["valid_pct"], 100.0)
            self.assertLess(sa["valid_pct"], 100.0)
            text = ab.table([a, b])
            self.assertIn("refusals", text)
            self.assertIn("Different verdicts", text)
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(ab.main(["--compare", "8b", "27b", "--out", tmp]), 0)
        self.assertTrue(ab.is_refusal("К сожалению, я не могу помочь с этим запросом."))
        self.assertFalse(ab.is_refusal("summary:\n[reference generation] <Subject 1> robs a vault."))


if __name__ == "__main__":
    unittest.main()
