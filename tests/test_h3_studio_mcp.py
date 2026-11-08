import importlib.util
import json
import threading
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "h3_studio_mcp", Path(__file__).resolve().parents[1] / "scripts/h3_studio_mcp.py")
mcp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mcp)


def test_companion_advertises_real_ui_resource_and_image_only_tools():
    with unittest.mock.patch.dict(mcp.os.environ, {"H3_IMAGE_COMPANION_ONLY": "1"}):
        tools = mcp.handle({"id": 1, "method": "tools/list"})["result"]["tools"]
        names = {t["name"] for t in tools}
        assert "image_request_complete" in names and "image_request_status" in names
        assert not names & {"render_enqueue", "keyframe_create", "studio_build_scene"}
        ui = next(t for t in tools if t["name"] == "image_companion")
        assert ui["_meta"]["ui"]["resourceUri"] == mcp.COMPANION_URI
        assert {e["type"] for e in ui["_meta"]["openai/ui"]["entrypoints"]} == {"global", "thread"}
        denied = mcp.call_tool("render_enqueue", {})
        assert denied["isError"]
    resource = mcp.handle({"id": 2, "method": "resources/read", "params": {"uri": mcp.COMPANION_URI}})["result"]["contents"][0]
    assert resource["mimeType"] == "text/html;profile=mcp-app"
    assert "ui/message" in resource["text"] and "/*COMPANION_SCRIPT*/" not in resource["text"]
    assert '"target"' not in resource["uri"]


def test_image_context_delegates_to_the_shared_scene_context_with_scope():
    with unittest.mock.patch.object(mcp, "t_studio_shot_snapshot", return_value={"scene_id": "scoped"}), \
            unittest.mock.patch.object(mcp, "request", return_value={"fingerprint": "packet"}) as api:
        assert mcp.t_studio_image_context({"scene_id": "input", "shot_id": "shot", "purpose": "fantasy_world"}) == {"fingerprint": "packet"}
        api.assert_called_once_with("POST", "/api/chat-images/context", {"scene_id": "scoped", "shot_id": "shot", "purpose": "fantasy_world", "notes": ""})


def test_chat_file_import_uses_host_file_without_exposing_url():
    file = {"download_url": "https://files.oaiusercontent.com/image?secret=temporary", "file_id": "file-test"}
    response = unittest.mock.MagicMock()
    response.__enter__.return_value.read.return_value = b"\x89PNG\r\n\x1a\nimage"
    opener = unittest.mock.Mock()
    opener.open.return_value = response
    with unittest.mock.patch.object(mcp.urllib.request, "build_opener", return_value=opener), \
            unittest.mock.patch.object(mcp, "t_asset_import_image", return_value={"asset_id": "saved"}) as upload:
        assert mcp.t_asset_import_chat_file({"image": file}) == {"asset_id": "saved"}
        assert upload.call_args.args[0]["role"] == "Окружение"
    with unittest.mock.patch.object(mcp.urllib.request, "build_opener") as network:
        try:
            mcp.t_asset_import_chat_file({"image": {**file, "download_url": "http://127.0.0.1/private"}})
        except mcp.StudioError:
            pass
        else:
            raise AssertionError("non-ChatGPT URL accepted")
        network.assert_not_called()
    with unittest.mock.patch.object(mcp, "all_tools", return_value=mcp.TOOLS):
        tools = mcp.handle({"id": 1, "method": "tools/list"})["result"]["tools"]
    tool = next(t for t in tools if t["name"] == "asset_import_chat_file")
    assert tool["_meta"]["openai/fileParams"] == ["image"]
    assert tool["annotations"]["readOnlyHint"] is False


def test_motion_director_compile_uses_local_api_without_gpu():
    payload = {"width": 768, "height": 1344, "segments": [
        {"id": "a", "mode": "t2v", "prompt": "A quiet room", "duration": 5}]}
    handler, description, schema = mcp.TOOLS["motion_director_compile"]
    with unittest.mock.patch.object(mcp, "request", return_value={"submitted": False}) as request:
        assert handler(payload) == {"submitted": False}
        request.assert_called_once_with("POST", "/api/studio/motion-director/compile", payload)
    assert mcp.annotations("motion_director_compile")["readOnlyHint"] is True
    for operation in ("graph", "preflight"):
        handler, _, graph_schema = mcp.TOOLS[f"motion_director_{operation}"]
        assert graph_schema["properties"]["segments"] == schema["properties"]["segments"]
        assert "seed" in graph_schema["required"]
        with unittest.mock.patch.object(mcp, "request", return_value={"submitted": False}) as request:
            handler({**payload, "seed": 42})
            request.assert_called_once_with("POST", f"/api/studio/motion-director/{operation}", {**payload, "seed": 42})
    assert not mcp.annotations("motion_director_upload_video")["readOnlyHint"]
    with unittest.mock.patch.object(mcp, "SCENE_ID", "own-scene"), unittest.mock.patch.object(mcp, "request") as request:
        mcp.TOOLS["motion_director_plan_get"][0]({})
        request.assert_called_once_with("GET", "/api/scene-runs/own-scene/motion-plan")
        request.reset_mock()
        with unittest.TestCase().assertRaises(mcp.StudioError):
            mcp.TOOLS["motion_director_plan_save"][0]({"scene_id": "other-scene", "plan": {}, "expected_revision": 0})
        request.assert_not_called()
        with unittest.TestCase().assertRaises(mcp.StudioError):
            mcp.TOOLS["motion_director_execute"][0]({"expected_revision": 1, "story_approved": False})
        request.assert_not_called()
        mcp.TOOLS["motion_director_execute"][0]({"expected_revision": 1, "story_approved": True})
        request.assert_called_once_with("POST", "/api/studio/motion-director/scenes/own-scene/execute",
                                        {"expected_revision": 1, "story_approved": True, "full_run": False})
        request.reset_mock()
        with unittest.TestCase().assertRaises(mcp.StudioError):
            mcp.TOOLS['motion_director_cancel_waiting'][0]({'scene_id': 'other-scene', 'motion_id': 'job'})
        request.assert_not_called()
        mcp.TOOLS['motion_director_cancel_waiting'][0]({'motion_id': 'job'})
        request.assert_called_once_with('POST', '/api/studio/motion-director/scenes/own-scene/jobs/job/cancel-waiting', {})
        assert not mcp.annotations('motion_director_cancel_waiting')['readOnlyHint']


def test_swap_draft_guard_requires_full_body_mapping_and_source_sound():
    source = {"asset_id": "video", "role": "Исходное видео",
              "performers": [{"owner": "VEGA", "performer": "brown driver at the wheel"}]}
    draft = {"references": [source], "prompt": "[Shot 1] VEGA drives", "mode": "replace",
             "steps": 24, "cache_mode": "safe", "ref_image_size": "max", "use_video_audio": True}
    assert "ровно одно фото" in " ".join(mcp.check_swap_draft(draft))
    draft["references"].append({"asset_id": "vega", "role": "Персонаж целиком", "owner": "VEGA"})
    assert mcp.check_swap_draft(draft) == []
    draft["references"].append({"asset_id": "orion", "role": "Персонаж целиком", "owner": "ORION"})
    assert "нет своего исходного исполнителя" in " ".join(mcp.check_swap_draft(draft))
    draft["references"].pop()
    draft["use_video_audio"] = False
    assert "исходный звук" in " ".join(mcp.check_swap_draft(draft))


def test_motion_reference_tool_is_an_explicit_write():
    assert 'motion_reference_prepare' in mcp.all_tools()
    assert mcp.annotations('motion_reference_prepare')['readOnlyHint'] is False


def test_shot_patch_uses_saved_revision_and_scene_scope():
    draft = {"revision": 4, "project": {"scenes": [{"id": "own", "franchiseSource": {"franchise_id": "f1"},
                                          "cuts": [{"id": "c1", "prompt": "old"}]}]}}
    with unittest.mock.patch.object(mcp, "SCENE_ID", "own"), \
         unittest.mock.patch.object(mcp, "FRANCHISE_ID", "f1"), \
         unittest.mock.patch.object(mcp, "RUN_ID", "run-1"), \
         unittest.mock.patch.object(mcp, "request", side_effect=[draft, {"revision": 5, "replayed": False},
                                                                  {"recorded": True}]) as request:
        result = mcp.t_studio_patch_shot({"shot_id": "c1", "event_id": "evt", "base_revision": 4,
                                          "prompt": "new"})
        assert result["revision"] == 5
        request.assert_any_call("POST", "/api/draft/scenes/own/shots/c1/proposals",
                                {"event_id": "evt", "agent_role": "shot_director", "base_revision": 4,
                                 "patch": {"prompt": "new"}})
        request.assert_any_call("POST", "/api/runs/run-1/events",
                                {"event_id": "evt", "kind": "shot_updated", "scene_id": "own",
                                 "shot_id": "c1", "revision": 5})
    assert not mcp.annotations("studio_patch_shot")["readOnlyHint"]
    with unittest.mock.patch.object(mcp, "SCENE_ID", "own"), \
         unittest.mock.patch.object(mcp, "request") as request:
        with unittest.TestCase().assertRaises(mcp.StudioError):
            mcp.t_studio_shot_snapshot({"scene_id": "other", "shot_id": "c1"})
        request.assert_not_called()


def test_scene_readiness_is_scoped_and_read_only():
    with unittest.mock.patch.object(mcp, "SCENE_ID", "own"), \
         unittest.mock.patch.object(mcp, "FRANCHISE_ID", "f1"), \
         unittest.mock.patch.object(mcp, "request", return_value={"franchise_id": "f1", "next_action": "review_full_mp4"}) as request:
        assert mcp.t_studio_scene_readiness({})["next_action"] == "review_full_mp4"
        request.assert_called_once_with("GET", "/api/draft/scenes/own/readiness")
        with unittest.TestCase().assertRaises(mcp.StudioError):
            mcp.t_studio_scene_readiness({"scene_id": "other"})
        assert request.call_count == 1
    assert mcp.annotations("studio_scene_readiness")["readOnlyHint"]

OPENAPI = {
    "paths": {"/api/jobs": {"post": {"requestBody": {"content": {"application/json": {
        "schema": {"$ref": "#/components/schemas/JobInput"}}}}}}},
    "components": {"schemas": {"JobInput": {"title": "JobInput", "type": "object",
                                            "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]}}},
}
ENTITIES = [{"id": "e1", "type": "character", "title": "VEGA", "body": "кот " * 300, "revision": 1},
            {"id": "e2", "type": "location", "title": "Квартира", "body": "", "revision": 0},
            {"id": "e3", "type": "rule", "title": "Камера", "body": "Не менять ось без мотива", "state": "approved"},
            {"id": "e4", "type": "rule", "title": "Проба", "body": "Пока гипотеза", "state": "draft"}]


def test_assets_list_uses_only_explicit_franchise_membership():
    old_request, old_franchise = mcp.request, mcp.FRANCHISE_ID
    responses = {"/api/assets": [{"asset_id": "own", "kind": "image"}, {"asset_id": "foreign", "kind": "image"},
                                  {"asset_id": "shared", "kind": "image"}],
                 "/api/franchises": [{"id": "f1"}, {"id": "f2"}],
                 "/api/franchises/f1/entities": [{"asset_ids": ["own"]}],
                 "/api/franchises/f1/profile": {"roles": [{"assets": [{"asset_id": "shared"}]}]},
                 "/api/franchises/f2/entities": [{"asset_ids": ["foreign", "shared"]}],
                 "/api/franchises/f2/profile": {"roles": []}}
    try:
        mcp.request = lambda method, path, body=None: responses[path]
        mcp.FRANCHISE_ID = "f1"
        assert [x["asset_id"] for x in mcp.t_assets({})["assets"]] == ["own"]
        try:
            mcp.t_assets({"franchise_id": "f2"})
            assert False, "foreign franchise should be rejected"
        except ValueError:
            pass
    finally:
        mcp.request, mcp.FRANCHISE_ID = old_request, old_franchise


class FakeStudio(BaseHTTPRequestHandler):
    posts = []
    gpu_ok = False
    bf16_ok = True
    shot_lint_findings = []

    def log_message(self, *args):
        pass

    def reply(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/openapi.json":
            return self.reply(200, OPENAPI)
        if self.path == "/api/franchises":
            return self.reply(200, [{"id": "f1", "name": "ADAM & VEGA"}, {"id": "f2", "name": "VEGA & ORION"}])
        if self.path == "/api/health":
            return self.reply(200, {"status": "ok", "gpu_configured": True, "gpu_reachable": FakeStudio.gpu_ok})
        if self.path == "/api/queue":
            return self.reply(200, {"paused": False})
        if self.path == "/api/gpu/activity":
            return self.reply(200, {"reachable": FakeStudio.gpu_ok, "running": [], "pending_count": 0})
        if self.path == "/api/studio/render-options":
            return self.reply(200, {"gpu_reachable": FakeStudio.gpu_ok,
                                    "profiles": {"bf16": {"available": FakeStudio.bf16_ok}}})
        if self.path == "/api/jobs":
            return self.reply(200, [{"job_id": "j1", "state": "needs_reconciliation"},
                                    {"job_id": "j2", "state": "completed"}])
        if self.path == "/api/franchises/f1/entities":
            return self.reply(200, ENTITIES)
        if self.path == "/api/franchises/f1/profile":
            return self.reply(200, {"roles": [{"name": "VEGA", "kind": "character", "assets": [{"asset_id": "a1"}]}]})
        if self.path.startswith("/api/runs/r1/gate"):
            return self.reply(200, {"allowed": FakeStudio.gpu_ok})
        if self.path == "/api/assets":
            return self.reply(200, [{"asset_id": "a1", "kind": "image", "number": 939},
                                    {"asset_id": "yard", "kind": "image", "number": 940, "role": "Окружение"}])
        if self.path == "/api/assets/a1/thumbnail":
            return self.reply(200, b"\xff\xd8jpeg", "image/jpeg")
        if self.path == "/api/recipes/accepted":
            return self.reply(200, [{"id": "rec1", "title": "Орион вместо кота"}])
        if self.path == "/api/recipes/accepted/rec1":
            return self.reply(200, {"id": "rec1", "compiled_profile": {"steps": 24, "style_adapter": "character_swap"}})
        if self.path == "/api/assets/v1/frames?count=12":
            return self.reply(200, {"asset_id": "v1", "name": "cats.mp4", "duration_s": 11.0, "has_sound": True,
                                    "frame_times_s": [0.5], "hard_cuts_s": [], "sheet_jpeg_base64": "/9j/src"})
        if self.path == "/api/jobs/j2/frames?count=8":
            return self.reply(200, {"job_id": "j2", "technical": {"full_decode": True, "audio_present": True},
                                    "frame_times_s": [0.3], "hard_cuts_s": [2.1], "sheet_jpeg_base64": "/9j/sheet"})
        return self.reply(404, {"detail": "нет"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeStudio.posts.append((self.path, body))
        if self.path.startswith("/api/runs/r1/gate/claim"):
            return self.reply(200 if FakeStudio.gpu_ok else 409,
                              {"allowed": True} if FakeStudio.gpu_ok else {"detail": "Лимит GPU исчерпан"})
        if self.path == "/api/jobs" and not body.get("prompt"):
            return self.reply(422, {"detail": "prompt обязателен"})
        if self.path == "/api/shot-lint":
            return self.reply(200, {"findings": FakeStudio.shot_lint_findings})
        if self.path == "/api/staging/compile":
            bad = body["shots"][0]["setup"] == "far"
            return self.reply(200, {"ok": not bad, "shots": [{"shot": i + 1, "staging_line": "VEGA: screen-left.", "axis_side": "A"}
                                                             for i in range(len(body["shots"]))],
                                    "findings": [{"severity": "error", "shot": 1, "message": "VEGA не попадает в кадр"}] if bad else []})
        if self.path.endswith("/pack/check"):
            return self.reply(200, {"ok": False, "problems": ["референс a1 не из пакета идеи «Колбаса»"]}
                              if body["references"][0]["asset_id"] == "a1" else {"ok": True, "problems": []})
        return self.reply(200, {"job_id": "j1"})


class McpServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeStudio)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        mcp.STUDIO = f"http://127.0.0.1:{cls.server.server_port}"
        mcp._openapi = None

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def call(self, name, args):
        reply = mcp.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                            "params": {"name": name, "arguments": args}})
        return reply["result"]

    def test_initialize_and_notifications(self):
        init = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-03-26"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(init["result"]["serverInfo"]["name"], "h3-studio")
        self.assertIsNone(mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "nope"})["error"]["code"], -32601)

    def test_render_schema_comes_from_openapi(self):
        tools = mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]
        render = next(t for t in tools if t["name"] == "render_enqueue")
        payload = render["inputSchema"]["properties"]["payload"]
        self.assertEqual(payload["required"], ["prompt"])
        self.assertNotIn("title", payload)
        self.assertFalse(render["annotations"]["readOnlyHint"])
        self.assertTrue(next(t for t in tools if t["name"] == "assets_list")["annotations"]["readOnlyHint"])

    def test_deep_schemas_keep_their_types(self):
        deep = {"type": "object", "properties": {"a": {"$ref": "#/components/schemas/A"}}}
        components = {"A": {"type": "object", "properties": {"b": {"type": "array", "items": {"$ref": "#/components/schemas/B"}}}},
                      "B": {"anyOf": [{"type": "object", "properties": {"c": {"type": "object", "properties": {
                          "d": {"type": "object", "properties": {"e": {"type": "number", "minimum": 0}}}}}}}, {"type": "null"}]}}
        out = json.dumps(mcp.inline(deep, components))
        self.assertIn('"e": {"type": "number", "minimum": 0}', out)
        loop = {"L": {"type": "object", "properties": {"next": {"$ref": "#/components/schemas/L"}}}}
        self.assertIn('"type": "object"', json.dumps(mcp.inline({"$ref": "#/components/schemas/L"}, loop)))

    def test_entities_filter_and_shorten(self):
        result = self.call("franchise_entities", {"franchise_id": "f1", "type": "character"})
        items = json.loads(result["content"][0]["text"])
        self.assertEqual([e["id"] for e in items], ["e1"])
        self.assertLess(len(items[0]["body"]), 500)

    def test_bound_agent_cannot_read_other_franchise_ideas(self):
        previous = mcp.FRANCHISE_ID
        try:
            mcp.FRANCHISE_ID = "f1"
            self.assertTrue(self.call("franchise_entities", {"franchise_id": "f2", "type": "idea"})["isError"])
            self.assertTrue(self.call("franchise_entity", {"franchise_id": "f2", "entity_id": "any"})["isError"])
        finally:
            mcp.FRANCHISE_ID = previous

    def test_render_needs_approved_story(self):
        FakeStudio.posts.clear()
        result = self.call("render_enqueue", {"payload": {"prompt": "x"}, "story_approved": False})
        self.assertTrue(result["isError"])
        self.assertEqual(FakeStudio.posts, [])
        ok = self.call("render_enqueue", {"payload": {"prompt": "x"}, "story_approved": True})
        self.assertNotIn("isError", ok)
        self.assertEqual(FakeStudio.posts, [("/api/jobs", {"prompt": "x", "settings": {"prompt_blocks": "none"}})])
        FakeStudio.posts.clear()
        self.call("render_enqueue", {"payload": {"prompt": "x", "settings": {"prompt_blocks": "studio", "seed": 1}},
                                     "story_approved": True})
        self.assertEqual(FakeStudio.posts[0][1]["settings"], {"prompt_blocks": "studio", "seed": 1})

    def test_studio_validation_error_is_tool_error(self):
        result = self.call("render_enqueue", {"payload": {}, "story_approved": True})
        self.assertTrue(result["isError"])
        self.assertIn("prompt обязателен", result["content"][0]["text"])

    def test_view_image_returns_image_content(self):
        content = self.call("view_image", {"kind": "asset", "id": "a1"})["content"][0]
        self.assertEqual((content["type"], content["mimeType"]), ("image", "image/jpeg"))

    def test_composer_draft_checks_files_and_roles_and_queues_nothing(self):
        FakeStudio.posts.clear()
        ok = self.call("composer_draft", {"prompt": "<Picture 1> ADAM", "references": [{"asset_id": "a1", "role": "Лицо героя"}]})
        self.assertNotIn("isError", ok)
        self.assertEqual(FakeStudio.posts, [])
        wrong = self.call("composer_draft", {"prompt": "p", "references": [{"asset_id": "a1", "role": "Музыка"}]})
        self.assertTrue(wrong["isError"])
        self.assertIn("№939", wrong["content"][0]["text"])
        self.assertTrue(self.call("composer_draft", {"prompt": "p", "references": [{"asset_id": "zz", "role": "Стиль"}]})["isError"])

    def test_scene_draft_follows_board_rules_and_runs_lint(self):
        shot = {"title": "t", "beat": "b", "prompt": "<Picture 1> VEGA", "first_frame": "VEGA on the sofa", "duration": 5,
                "start_frame": "new_keyframe", "framing": "close", "lens_mm": 50, "camera_motion": "dolly",
                "transition_in": "cut", "state_before": "a", "state_after": "b"}
        scene = {"title": "S", "idea": "i", "references": [{"asset_id": "a1", "role": "Лицо героя", "owner": "VEGA"},
                                                          {"asset_id": "yard", "role": "Окружение"}],
                 "shots": [shot, {**shot, "start_frame": "previous_end"}]}
        FakeStudio.posts.clear()
        ok = self.call("scene_draft", scene)
        self.assertNotIn("isError", ok)
        self.assertEqual(json.loads(ok["content"][0]["text"])["seconds"], 10)
        self.assertEqual([path for path, *_ in FakeStudio.posts[:2]], ["/api/scene-runs/contract", "/api/shot-lint"])
        first = self.call("scene_draft", {**scene, "shots": [{**shot, "start_frame": "previous_end"}, shot]})
        self.assertTrue(first["isError"])
        chain = self.call("scene_draft", {**scene, "shots": [shot] + [{**shot, "start_frame": "previous_end"}] * 4})
        self.assertIn("продолжений", chain["content"][0]["text"])
        placeless = self.call("scene_draft", {**scene, "references": scene["references"][:1]})
        self.assertIn("нет места действия", placeless["content"][0]["text"])
        located = json.loads(self.call("assets_list", {"role": "Окружение"})["content"][0]["text"])
        self.assertEqual([item["asset_id"] for item in located["assets"]], ["yard"])

        FakeStudio.shot_lint_findings = [{"severity": "error", "shot": 2, "rule": "axis", "message": "нарушена ось"}]
        try:
            blocked = self.call("scene_draft", scene)
            assert blocked["isError"] and "нарушена ось" in blocked["content"][0]["text"]
        finally:
            FakeStudio.shot_lint_findings = []

        franchise_scene = {**scene, "franchise_id": "f1"}
        good = self.call("scene_draft", franchise_scene)
        self.assertNotIn("isError", good)
        wrong_cast = self.call("scene_draft", {**franchise_scene, "references": [
            {"asset_id": "a1", "role": "Лицо героя", "owner": "ORION"}, scene["references"][1]]})
        self.assertTrue(wrong_cast["isError"])
        self.assertIn("паспорт", wrong_cast["content"][0]["text"])

    def test_composer_draft_takes_a_recipe_and_performer_pairs(self):
        video = {"asset_id": "a1", "role": "Лицо героя", "owner": "ORION"}
        ok = self.call("composer_draft", {"prompt": "<Picture 1> ORION", "recipe_id": "rec1", "mode": "replace",
                                          "steps": 24, "use_video_audio": True, "references": [video]})
        self.assertNotIn("isError", ok)
        missing = self.call("composer_draft", {"prompt": "p", "recipe_id": "nope", "references": [video]})
        self.assertIn("рецепт nope", missing["content"][0]["text"])
        pairs = self.call("composer_draft", {"prompt": "p", "references": [{**video, "performers": [{"owner": "ORION", "performer": "cat"}]}]})
        self.assertIn("Исходное видео", pairs["content"][0]["text"])
        self.assertFalse(mcp.annotations("studio_apply_draft")["readOnlyHint"])

    def test_asset_frames_lets_the_agent_look_at_an_attached_video(self):
        content = self.call("asset_frames", {"asset_id": "v1"})["content"]
        self.assertEqual(content[0]["data"], "/9j/src")
        self.assertEqual(json.loads(content[1]["text"])["duration_s"], 11.0)

    def test_job_frames_gives_the_agent_the_sheet_and_the_facts(self):
        content = self.call("job_frames", {"job_id": "j2"})["content"]
        self.assertEqual((content[0]["type"], content[0]["mimeType"], content[0]["data"]), ("image", "image/jpeg", "/9j/sheet"))
        facts = json.loads(content[1]["text"])
        self.assertEqual(facts["hard_cuts_s"], [2.1])
        self.assertNotIn("sheet_jpeg_base64", facts)

    def test_franchise_key_reads_live_studio_names(self):
        self.assertEqual(mcp.franchise_key("SAMPLELAND · Iris & Mira"), "SAMPLELAND_IRIS_MIRA")
        self.assertEqual(mcp.franchise_key("ADAM & VEGA"), "ADAM_VEGA")
        self.assertEqual(mcp.franchise_key(""), "")
        self.assertFalse(mcp.annotations("production_review_record")["readOnlyHint"])

    def test_scene_draft_with_an_idea_stays_inside_its_pack(self):
        shot = {"title": "t", "beat": "b", "prompt": "<Picture 1> VEGA", "first_frame": "VEGA on the sofa", "duration": 5,
                "start_frame": "new_keyframe", "framing": "close", "lens_mm": 50, "camera_motion": "dolly",
                "transition_in": "cut", "state_before": "a", "state_after": "b"}
        scene = {"title": "S", "idea": "i", "idea_id": "idea1", "franchise_id": "f1",
                 "references": [{"asset_id": "a1", "role": "Лицо героя", "owner": "VEGA"}, {"asset_id": "yard", "role": "Окружение"}],
                 "shots": [shot, {**shot, "start_frame": "previous_end"}]}
        FakeStudio.posts.clear()
        outside = self.call("scene_draft", scene)
        self.assertTrue(outside["isError"])
        self.assertIn("не из пакета идеи", outside["content"][0]["text"])
        self.assertEqual(FakeStudio.posts[0][0], "/api/franchises/f1/ideas/idea1/pack/check")
        self.assertTrue(self.call("scene_draft", {**scene, "franchise_id": ""})["isError"])
        staged = {**scene, "idea_id": "", "references": scene["references"][1:] + scene["references"][:1],
                  "staging": {"actors": [], "setups": [], "shots": [{"setup": "near"}, {"setup": "near"}]}}
        staged["references"] = [{"asset_id": "yard", "role": "Окружение"}]
        reply = json.loads(self.call("scene_draft", {**staged, "franchise_id": ""})["content"][0]["text"])
        self.assertEqual(reply["staging"][1], {"shot": 2, "line": "VEGA: screen-left.", "axis_side": "A"})
        far = {**staged, "franchise_id": "", "staging": {**staged["staging"], "shots": [{"setup": "far"}, {"setup": "near"}]}}
        self.assertIn("VEGA не попадает в кадр", self.call("scene_draft", far)["content"][0]["text"])
        short = {**staged, "franchise_id": "", "staging": {**staged["staging"], "shots": [{"setup": "near"}]}}
        self.assertIn("другое число шотов", self.call("scene_draft", short)["content"][0]["text"])
        route = json.loads(self.call("studio_playbook", {"workflow": "idea", "franchise": "ADAM_VEGA"})["content"][0]["text"])
        self.assertEqual(route["skills"][0]["name"], "minimax-video-production")
        self.assertIn("idea_pack_build", route["actions"][1]["tools"])

    def test_scene_draft_sets_up_the_whole_canvas_with_studio_presets(self):
        shot = {"title": "t", "beat": "b", "prompt": "<Picture 1> VEGA", "first_frame": "VEGA on the sofa", "duration": 5,
                "start_frame": "new_keyframe", "framing": "medium_close", "angle": "low", "lens_mm": 35,
                "camera_motion": "zoom", "transition_in": "cut", "state_before": "a", "state_after": "b",
                "director": {"focus": "focus.shallow", "movement": "movement.crash_zoom_in"}}
        scene = {"title": "S", "idea": "i", "references": [{"asset_id": "a1", "role": "Лицо героя", "owner": "VEGA"},
                                                          {"asset_id": "yard", "role": "Окружение"}],
                 "mode": "realistic", "quality": 0.98, "steps": 30,
                 "director": {"camera": "camera.alexa35", "lighting": "lighting.golden", "color": "color.teal_orange",
                              "style": "style.realism", "acting": "acting.heist"},
                 "shots": [shot, {**shot, "start_frame": "previous_end"}]}
        self.assertNotIn("isError", self.call("scene_draft", scene))
        presets = json.loads(self.call("director_presets", {"categories": ["lighting"]})["content"][0]["text"])
        self.assertIn("lighting.golden", [preset["id"] for preset in presets["lighting"]])
        wrong = self.call("scene_draft", {**scene, "director": {"lighting": "lighting.moonbeam"}})
        self.assertTrue(wrong["isError"])
        self.assertIn("lighting=lighting.moonbeam", wrong["content"][0]["text"])
        self.assertTrue(self.call("scene_draft", {**scene, "director": {"lighting": "color.warm"}})["isError"])

    def test_playbook_routes_existing_skills_without_running_scripts(self):
        result = self.call("studio_playbook", {"workflow": "video_swap"})
        self.assertNotIn("isError", result)
        data = json.loads(result["content"][0]["text"])
        self.assertEqual(data["skills"][0]["name"], "minimax-video-production")
        self.assertEqual(len(data["skills"]), 1)
        self.assertEqual(len(data["actions"]), len(data["steps"]))
        self.assertIn("studio_readiness", data["actions"][2]["tools"])
        self.assertIn("run_delegate", data["execution_policy"]["helper"])
        self.assertNotIn("skill_text", data)
        edit = json.loads(self.call("studio_playbook", {"workflow": "edit"})["content"][0]["text"])
        self.assertEqual(edit["actions"][0]["tools"], ["montage_get"])
        self.assertEqual(edit["actions"][2]["tools"], ["montage_update"])
        self.assertIn("montage_render", edit["actions"][3]["tools"])
        selected = json.loads(self.call("studio_playbook", {"workflow": "scene", "franchise": "ADAM_VEGA",
                                                          "skill": "minimax-video-production"})["content"][0]["text"])
        self.assertIn("franchise_note", selected)
        self.assertTrue(self.call("studio_playbook", {"workflow": "scene", "skill": "../AGENTS.md"})["isError"])

    def test_readiness_reports_ambiguous_receipt_and_offline_gpu(self):
        FakeStudio.gpu_ok = False
        result = self.call("studio_readiness", {})
        self.assertNotIn("isError", result)
        data = json.loads(result["content"][0]["text"])
        self.assertFalse(data["ready_for_gpu"])
        self.assertIn("gpu_unreachable", data["blockers"])
        self.assertIn("ambiguous_receipts", data["blockers"])
        self.assertEqual(data["ambiguous_job_ids"], ["j1"])
        self.assertEqual(data["jobs_by_state"]["completed"], 1)

    def test_readiness_blocks_gpu_when_base_model_is_unavailable(self):
        FakeStudio.gpu_ok, FakeStudio.bf16_ok = True, False
        try:
            data = json.loads(self.call("studio_readiness", {})["content"][0]["text"])
            self.assertFalse(data["ready_for_gpu"])
            self.assertIn("bf16_render_unavailable", data["blockers"])
            self.assertFalse(data["bf16_available"])
        finally:
            FakeStudio.gpu_ok, FakeStudio.bf16_ok = False, True

    def test_task_scope_refuses_other_franchise(self):
        previous = mcp.FRANCHISE_ID
        mcp.FRANCHISE_ID = "f1"
        try:
            self.assertTrue(self.call("franchise_entities", {"franchise_id": "f2"})["isError"])
            self.assertTrue(self.call("studio_playbook", {"workflow": "scene", "franchise": "VEGA_ORION"})["isError"])
            allowed = self.call("studio_playbook", {"workflow": "scene", "franchise": "ADAM_VEGA"})
            self.assertNotIn("isError", allowed)
            data = json.loads(allowed["content"][0]["text"])
            self.assertEqual([card["id"] for card in data["approved_experience"]], ["e3"])
        finally:
            mcp.FRANCHISE_ID = previous

    def test_accepted_recipe_catalog_is_scoped(self):
        old_request, old_franchise = mcp.request, mcp.FRANCHISE_ID
        mcp.request = lambda method, path, *args, **kwargs: (
            [{"id": "accepted"}] if path == "/api/recipes/accepted" else
            [{"id": "f1", "name": "ADAM & VEGA"}, {"id": "f2", "name": "VEGA & ORION"}])
        try:
            mcp.FRANCHISE_ID = "f1"
            self.assertEqual(mcp.t_accepted_recipes({}), [{"id": "accepted"}])
            mcp.FRANCHISE_ID = "f2"
            self.assertEqual(mcp.t_accepted_recipes({}), [{"id": "accepted"}])
        finally:
            mcp.request, mcp.FRANCHISE_ID = old_request, old_franchise

    def test_gpu_tools_in_a_task_need_the_users_yes(self):
        FakeStudio.posts.clear()
        mcp.RUN_ID = "r1"
        try:
            FakeStudio.gpu_ok = False
            refused = self.call("render_enqueue", {"payload": {"prompt": "x"}, "story_approved": True})
            self.assertTrue(refused["isError"])
            self.assertIn("Лимит GPU", refused["content"][0]["text"])
            self.assertEqual([path for path, _ in FakeStudio.posts], ["/api/runs/r1/gate/claim?kind=gpu"])
            FakeStudio.gpu_ok = True
            self.assertNotIn("isError", self.call("render_enqueue", {"payload": {"prompt": "x"}, "story_approved": True}))
            self.assertTrue(self.call("run_plan", {"steps": [{"id": "a", "title": "A"}]}).get("isError") is None)
            self.assertEqual(FakeStudio.posts[-1][0], "/api/runs/r1/plan")
        finally:
            mcp.RUN_ID = ""

    def test_every_tool_schema_is_well_formed(self):
        tools = mcp.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/list"})["result"]["tools"]
        types = {"object", "array", "string", "integer", "number", "boolean", "null"}

        def walk(node, where):
            """A schema node; its "properties" map holds child schemas by name (a property may be called "type")."""
            if isinstance(node, list):
                for child in node:
                    walk(child, where)
                return
            if not isinstance(node, dict):
                return
            if "type" in node:
                kinds = node["type"] if isinstance(node["type"], list) else [node["type"]]
                self.assertTrue(all(isinstance(k, str) and k in types for k in kinds), f"{where}: type {node['type']!r}")
            for key, child in node.items():
                if key in ("properties", "$defs"):
                    self.assertIsInstance(child, dict, where)
                    for name, sub in child.items():
                        self.assertIsInstance(sub, dict, f"{where}.{name}")
                        walk(sub, f"{where}.{name}")
                elif key in ("items", "anyOf", "oneOf", "allOf", "additionalProperties", "not"):
                    walk(child, f"{where}.{key}")

        for tool in tools:
            self.assertEqual(tool["inputSchema"].get("type", "object"), "object", tool["name"])
            walk(tool["inputSchema"], tool["name"])

    def test_api_get_is_limited_to_api(self):
        self.assertTrue(self.call("studio_api_get", {"path": "/openapi.json"})["isError"])
        self.assertTrue(self.call("studio_api_get", {"path": "/api/../x"})["isError"])


if __name__ == "__main__":
    unittest.main()


class SceneChatTests(unittest.TestCase):
    def test_a_scene_chat_sends_its_scene_and_cannot_hop_to_another(self):
        sent = []
        with unittest.mock.patch.object(mcp, "SCENE_ID", "scene-abc"), \
             unittest.mock.patch.object(mcp, "request", side_effect=lambda method, path, body=None, **kw:
                                        sent.append(body) or ({"id": "c1"} if path == "/api/ui/commands" else {"state": "done", "result": {"ok": 1}})):
            self.assertEqual(mcp.ui("board"), {"ok": 1})
            self.assertEqual(sent[0]["payload"], {"scene_id": "scene-abc"})
            with self.assertRaises(mcp.StudioError):
                mcp.ui("new_scene", {"title": "x"})


class SceneDraftComposeTests(unittest.TestCase):
    def test_the_engine_gets_the_scene_pictures_to_pick_costume_words(self):
        """scene_draft called /compose without references: a costume worn by picture got no words (01.11)."""
        sent = {}

        def fake(method, path, body=None, **kw):
            if path == "/api/scene-runs/compose":
                sent.update(body)
                return {"prompts": ["p"] * len(body["shots"]), "problems": []}
            raise mcp.StudioError("stop after compose")

        refs = [{"asset_id": "cyber", "role": "Костюм", "owner": "ADAM"}]
        with unittest.mock.patch.object(mcp, "request", side_effect=fake), unittest.mock.patch.object(mcp, "FRANCHISE_ID", ""):
            with self.assertRaises(mcp.StudioError):
                mcp.t_scene_draft({"franchise_id": "f1", "references": refs, "shots": [{"title": "t", "take": {"summary": "s"}}]})
        self.assertEqual(sent["references"], refs)

    def test_staging_lens_and_framing_must_match_the_card_and_its_line_reaches_the_engine(self):
        """05.11: a 35 mm card staged on an 85 mm setup passed, and the staging line never reached the prompt."""
        plan = {"setups": [{"id": "near", "lens_mm": 85}, {"id": "wide", "lens_mm": 24}],
                "shots": [{"setup": "near", "framing": "close"}, {"setup": "wide"}]}
        staged = {"shots": [{"shot": 1, "subject_framing": "close"}, {"shot": 2, "subject_framing": "extreme_wide"}]}
        cards = [{"lens_mm": 35, "framing": "medium"}, {"lens_mm": 24, "framing": "medium"}]
        found = mcp.staging_card_problems(plan, staged, cards)
        self.assertEqual(found[0], "шот 1: объектив установки near в постановке 85 мм, а в шоте 35 мм — поставь один "
                                   "объектив в обоих местах")
        self.assertEqual(found[1], "шот 1: в постановке крупность close, а в шоте medium — поставь одну крупность")
        self.assertIn("шот 2: шот заявляет medium, а установка wide на плане даёт extreme_wide", found[2])
        self.assertEqual(mcp.staging_card_problems(plan, staged, [{"lens_mm": 85, "framing": "close"},
                                                                  {"lens_mm": 24, "framing": "wide"}]), [])

        sent = {}
        line = "VEGA is screen left 1.2 m from the camera, in close-up, looking toward screen right."

        def fake(method, path, body=None, **kw):
            if path == "/api/staging/compile":
                return {"ok": True, "findings": [], "shots": [{"shot": n, "prompt_line": line, "staging_line": "x",
                                                               "axis_side": "A", "subject_framing": "close"} for n in (1, 2)]}
            if path == "/api/scene-runs/compose":
                sent.update(body)
                return {"prompts": ["p", "p"], "problems": [], "warnings": ["шот 1: take.camera называет pan"]}
            return [] if method == "GET" else {"problems": [], "findings": []}

        card = {"take": {"summary": "s"}, "framing": "close", "duration": 12, "start_frame": "new_keyframe"}
        shots = [{**card, "title": "t", "lens_mm": 35}, {**card, "title": "u", "lens_mm": 85}]
        with unittest.mock.patch.object(mcp, "request", side_effect=fake), unittest.mock.patch.object(mcp, "FRANCHISE_ID", ""):
            with self.assertRaises(mcp.StudioError) as caught:
                mcp.t_scene_draft({"franchise_id": "f1", "references": [], "shots": shots,
                                   "staging": {"setups": [{"id": "near", "lens_mm": 85}],
                                               "shots": [{"setup": "near"}, {"setup": "near"}]}})
        self.assertEqual([shot["staging_line"] for shot in sent["shots"]], [line, line])
        self.assertIn("шот 1: объектив установки near в постановке 85 мм, а в шоте 35 мм", str(caught.exception))
        self.assertNotIn("шот 2: объектив", str(caught.exception))


def test_readiness_reports_canvas_separately_from_gpu():
    health = {"status": "ok", "gpu_configured": True, "gpu_reachable": True}
    def fake_request(method, path):
        return {"/api/studio/render-options": {"gpu_reachable": True, "profiles": {"bf16": {"available": True}}},
                "/api/jobs": [], "/api/ui/tabs": {"open": 0}}[path]
    with unittest.mock.patch.object(mcp, "t_health", return_value={"health": health, "queue": {"paused": False},
                                                                    "gpu_activity": {"reachable": True}}), \
         unittest.mock.patch.object(mcp, "request", side_effect=fake_request):
        result = mcp.t_readiness({})
    assert result["ready_for_gpu"] is True
    assert result["canvas_bridge"] == "unavailable"


def test_graph_tools_call_studio_and_render_passes_the_gpu_gate():
    calls = []
    old_request, old_run = mcp.request, mcp.RUN_ID
    mcp.request = lambda method, path, body=None, **kw: calls.append((method, path, body)) or {}
    try:
        mcp.RUN_ID = "r1"
        mcp.call_tool("job_graph_edit", {"job_id": "j 1", "set": [{"node": "129", "input": "noise_seed", "value": 5}]})
        mcp.call_tool("job_graph_edit_render", {"job_id": "j1", "edit_id": "e1"})
    finally:
        mcp.request, mcp.RUN_ID = old_request, old_run
    assert calls[0] == ("POST", "/api/jobs/j%201/comfyui-edits", {"set": [{"node": "129", "input": "noise_seed", "value": 5}]})
    assert [path for _, path, _ in calls[1:]] == ["/api/runs/r1/gate/claim?kind=gpu", "/api/jobs/j1/comfyui-edits/e1/render"]
    assert {"job_graph_edit", "job_graph_edit_render"} <= mcp.WRITES


# ---------- code mode (scripts/h3_code_mode.py): one sandboxed script, many tools, same gates

def code_mode_tools():
    render = (mcp.t_render_enqueue, "Queue an H3 render job (GPU, costs money).",
              mcp.obj({"payload": mcp.obj({"prompt": mcp.STR}), "story_approved": {"type": "boolean"}},
                      ["payload", "story_approved"]))
    return unittest.mock.patch.object(mcp, "dynamic_tools", return_value={"render_enqueue": render})


def run_code(code, tool="studio_run_code", **extra):
    reply = mcp.call_tool(tool, {"code": code, **extra})
    return reply.get("isError", False), json.loads(reply["content"][0]["text"])


def test_code_mode_read_script_joins_many_tools_in_one_call():
    jobs = [{"job_id": f"j{i}", "state": "done" if i % 2 else "failed", "error": "OOM" if i % 2 == 0 else None,
             "original_prompt": "A long prompt " * 40} for i in range(6)]
    seen = []
    def fake(method, path, body=None, **kw):
        seen.append((method, path))
        if path == "/api/jobs":
            return jobs
        return next(j for j in jobs if path == f"/api/jobs/{j['job_id']}")
    with code_mode_tools(), unittest.mock.patch.object(mcp, "request", side_effect=fake):
        failed, out = run_code("bad = [j['job_id'] for j in jobs_list(limit=10)['jobs'] if j['state'] == 'failed']\n"
                               "print(len(bad), 'failed')\n"
                               "{j: job_get(job_id=j)['error'] for j in bad}")
    assert not failed, out
    assert out["result"] == {"j0": "OOM", "j2": "OOM", "j4": "OOM"} and out["stdout"] == "3 failed\n"
    assert out["tool_calls"] == 4 and out["by_tool"] == {"jobs_list": 1, "job_get": 3}
    assert all(method == "GET" for method, _ in seen)
    assert "A long prompt" not in json.dumps(out)  # only what the script returns reaches the model


def test_code_mode_function_list_comes_from_tool_schemas():
    with code_mode_tools():
        listing = mcp.t_code_functions({"query": "job"})["functions"]
        names = mcp.code_functions(mcp.all_tools())
    assert any(line.startswith("[read] jobs_list(") and "limit: int = None" in line for line in listing)
    assert "[gpu] render_enqueue(payload: dict, story_approved: bool)" in " ".join(listing)
    assert not set(names) & (mcp.CODE_DENY | mcp.CODE_TOOLS)
    assert mcp.annotations("studio_run_code")["readOnlyHint"] and not mcp.annotations("studio_run_code_write")["readOnlyHint"]


def test_code_mode_keeps_write_and_gpu_gates():
    sent = []
    fake = lambda method, path, body=None, **kw: sent.append((method, path)) or {"job_id": "new"}
    enqueue = "render_enqueue(payload={'prompt': 'A cat on a hammock.'}, story_approved=%s)"
    with code_mode_tools(), unittest.mock.patch.object(mcp, "request", side_effect=fake), \
            unittest.mock.patch.object(mcp, "RUN_ID", "r1"):
        failed, out = run_code(enqueue % True)
        assert failed and "только чтение" in out["error"]
        failed, out = run_code(enqueue % True, "studio_run_code_write")
        assert failed and "тратит GPU" in out["error"]
        failed, out = run_code(enqueue % False, "studio_run_code_write", gpu=True)
        assert failed and "одобренного пользователем сюжета" in out["error"]  # the handler's own gate
        assert sent == []
        failed, out = run_code("call('story_approval_record', story='x')", "studio_run_code_write", gpu=True)
        assert failed and "недоступен в коде" in out["error"]
        failed, out = run_code(f"{enqueue % True}\n{enqueue % True}", "studio_run_code_write", gpu=True)
    assert failed and "одно GPU-действие" in out["error"]
    assert sent == [("POST", "/api/runs/r1/gate/claim?kind=gpu"), ("POST", "/api/jobs")]  # run gate claimed once


def test_code_mode_stops_a_script_at_its_timeout():
    with code_mode_tools():
        failed, out = run_code("while True:\n    pass", timeout_s=1)
    assert failed and out["error"].startswith("timeout") and out["seconds"] < 4


def test_code_mode_refuses_imports_network_and_file_writes():
    with code_mode_tools(), unittest.mock.patch.object(mcp, "request", side_effect=AssertionError("no Studio call")):
        failed, out = run_code("import urllib.request\nurllib.request.urlopen('http://127.0.0.1:18187/api/jobs')")
        assert failed and "import urllib.request is not allowed" in out["error"]
        # Escape past the restricted builtins to the real import: the audit hook still refuses the socket module.
        escape = ("try:\n    1/0\nexcept ZeroDivisionError as exc:\n    g = exc.__traceback__.tb_frame.f_back.f_globals\n"
                  "s = g['real_import']('_socket')\ns.socket().connect(('127.0.0.1', 18187))")
        failed, out = run_code(escape)
        assert failed and "_socket is not allowed" in out["error"]
        failed, out = run_code("g = None\ntry:\n    1/0\nexcept ZeroDivisionError as exc:\n"
                               "    g = exc.__traceback__.tb_frame.f_back.f_globals\ng['os'].open('x', g['os'].O_WRONLY | g['os'].O_CREAT)")
        assert failed and "writing files is not allowed" in out["error"]
