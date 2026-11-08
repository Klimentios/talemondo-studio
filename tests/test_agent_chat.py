import json
import stat
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_studio import agent_chat
from local_studio.agent_chat import claude_command, claude_events, codex_command, codex_events, merge_tool

CHAT = {"id": "c" * 32, "provider": "codex", "model": "", "mode": "read", "session_id": ""}


def test_claude_command_resumes_and_blocks_writes_in_read_mode():
    cmd = claude_command("claude", {**CHAT, "provider": "claude", "session_id": "s1", "model": "opus"}, "hi",
                         "http://127.0.0.1:18290", {"render_enqueue", "edl_render"})
    assert cmd[cmd.index("--resume") + 1] == "s1" and cmd[cmd.index("--model") + 1] == "opus"
    assert "mcp__h3-studio__render_enqueue" in cmd and "--strict-mcp-config" in cmd
    mcp = json.loads(cmd[cmd.index("--mcp-config") + 1])
    assert mcp["mcpServers"]["h3-studio"]["env"]["H3_STUDIO_URL"] == "http://127.0.0.1:18290"
    assert "Bash" not in " ".join(cmd)
    act = claude_command("claude", {**CHAT, "provider": "claude", "mode": "act"}, "hi", "u", {"render_enqueue"})
    assert "--disallowedTools" not in act and "--resume" not in act
    assert act[act.index("--model") + 1] == "opus"  # never the user's global default
    light = claude_command("claude", {**CHAT, "provider": "claude", "effort": "low"}, "hi", "u", set())
    assert light[light.index("--effort") + 1] == "low"
    assert "--effort" not in claude_command("claude", {**CHAT, "provider": "claude", "effort": "max"}, "hi", "u", set())


def test_codex_command_first_message_and_resume():
    first = codex_command("codex", CHAT, "hello")
    assert "--ignore-user-config" in first
    assert any(item.startswith('mcp_servers.h3-studio.command=') for item in first)
    assert any(item.startswith('mcp_servers.h3-studio.args=') for item in first)
    assert first[first.index("--sandbox") + 1] == "read-only"
    assert 'mcp_servers.h3-studio.default_tools_approval_mode="writes"' in first
    assert first[-1].endswith("hello") and "H3 Studio" in first[-1]
    again = codex_command("codex", {**CHAT, "session_id": "t1", "mode": "act", "model": "gpt-6-sol"}, "next")
    assert again[-3:-1] == ["resume", "t1"] and again[again.index("-m") + 1] == "gpt-6-sol"
    assert again[-1].endswith("next") and "Может действовать" in again[-1]  # resume restates the mode
    assert any("H3_CHAT_ID" in part for part in again)
    assert 'model_reasoning_effort="max"' in codex_command("codex", {**CHAT, "effort": "max"}, "x")
    assert 'model_reasoning_effort="low"' in codex_command("codex", CHAT, "x")
    assert codex_command("codex", CHAT, "x")[codex_command("codex", CHAT, "x").index("-m") + 1] == "gpt-6-sol"
    assert 'mcp_servers.h3-studio.default_tools_approval_mode="auto"' in again
    assert 'mcp_servers.h3-studio.env.H3_STUDIO_URL="http://127.0.0.1:18187"' in again
    assert "Never claim this chat is read-only when its mode is act" in again[-1]
    assert "franchise_profile_update" in again[-1] and "scene_draft" in again[-1]


def test_event_parsing():
    assert claude_events({"type": "system", "subtype": "init", "session_id": "s"}) == [{"kind": "session", "id": "s"}]
    tool = claude_events({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Смотрю"}, {"type": "tool_use", "id": "t", "name": "mcp__h3-studio__franchise_list", "input": {}}]}})
    assert tool[0]["text"] == "Смотрю" and tool[1]["name"] == "franchise_list"
    result = claude_events({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t",
                                                                     "content": [{"type": "text", "text": "ok"}]}]}})
    assert result == [{"kind": "tool", "id": "t", "status": "done", "output": "ok"}]
    error = claude_events({"type": "result", "is_error": True, "result": "401 OAuth token revoked", "session_id": "s"})
    assert "claude auth login" in error[1]["text"]
    assert codex_events({"type": "thread.started", "thread_id": "x"}) == [{"kind": "session", "id": "x"}]
    call = codex_events({"type": "item.completed", "item": {"id": "i", "type": "mcp_tool_call", "tool": "assets_list",
                                                           "status": "completed", "result": {"total": 3}}})
    assert call[0]["status"] == "done" and "total" in call[0]["output"]
    events = []
    merge_tool(events, {"kind": "tool", "id": "i", "name": "a", "status": "started"})
    merge_tool(events, {"kind": "tool", "id": "i", "status": "done", "output": "x"})
    assert events == [{"kind": "tool", "id": "i", "name": "a", "status": "done", "output": "x"}]


def test_completed_agent_turn_records_only_reported_tokens():
    assert agent_chat.turn_usage("claude", {"type": "result", "usage": {
        "input_tokens": 120, "output_tokens": 35, "cache_read_input_tokens": 70}}) == {
            "input_tokens": 120, "output_tokens": 35, "cache_read_input_tokens": 70}
    assert agent_chat.turn_usage("codex", {"type": "turn.completed", "usage": {
        "last_token_usage": {"input_tokens": 90, "output_tokens": 20, "cached_input_tokens": 40},
        "total_token_usage": {"input_tokens": 1000}}}) == {
            "input_tokens": 90, "output_tokens": 20, "cached_input_tokens": 40}
    assert agent_chat.turn_usage("codex", {"type": "turn.completed"}) is None
    assert agent_chat.turn_usage("claude", {"type": "result", "usage": {"input_tokens": True}}) is None


def test_composer_draft_is_kept_whole_and_screen_context_reaches_the_agent():
    prompt = "x" * 3000
    claude = claude_events({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t",
        "name": "mcp__h3-studio__composer_draft", "input": {"prompt": prompt, "duration": 10}}]}})
    assert claude[0]["draft"] == {"prompt": prompt, "duration": 10} and len(claude[0]["input"]) < 500
    codex = codex_events({"type": "item.started", "item": {"id": "i", "type": "mcp_tool_call", "tool": "composer_draft",
                                                         "arguments": json.dumps({"prompt": "p"})}})
    assert codex[0]["draft"] == {"prompt": "p"}
    full_name = codex_events({"type": "item.started", "item": {"id": "s", "type": "mcp_tool_call",
        "tool": "mcp__h3-studio__scene_draft", "arguments": json.dumps({"title": "Scene", "shots": []})}})
    assert full_name[0]["draft"]["title"] == "Scene"
    other = codex_events({"type": "item.started", "item": {"id": "i", "type": "mcp_tool_call", "tool": "assets_list",
                                                         "arguments": {}}})
    assert "draft" not in other[0]
    text = agent_chat.with_context("сделай шот", {"page": "create", "prompt": "old"})
    assert text.startswith("[Экран Studio сейчас]") and text.endswith("сделай шот") and '"old"' in text
    assert agent_chat.with_context("hi", None) == "hi"


FAKE_CODEX = """#!/usr/bin/env python3
import json, sys, os
sys.stderr.write("noise " * 40000)  # more than a pipe buffer: must not block the run
sys.stderr.flush()
assert 'OPENAI_API_KEY' not in os.environ
args = sys.argv[1:]
thread = args[args.index('resume') + 1] if 'resume' in args else 'thread-1'
for line in [{"type": "thread.started", "thread_id": thread},
             {"type": "item.started", "item": {"id": "m", "type": "mcp_tool_call", "tool": "franchise_list", "arguments": {}}},
             {"type": "item.completed", "item": {"id": "m", "type": "mcp_tool_call", "tool": "franchise_list", "status": "completed", "result": "K&A"}},
             {"type": "item.completed", "item": {"id": "a", "type": "agent_message", "text": "Ответ на: " + args[-1][-12:]}},
             {"type": "turn.completed"}]:
    print(json.dumps(line, ensure_ascii=False), flush=True)
"""


class FakeStore:
    def __init__(self, root):
        self.root = root


@pytest.fixture
def client(tmp_path):
    exe = tmp_path / "codex"
    exe.write_text(FAKE_CODEX)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    app = FastAPI()
    app.include_router(agent_chat.make_router(FakeStore(tmp_path)))
    with patch.object(agent_chat, "executable", return_value=str(exe)), \
         patch.dict("os.environ", {"OPENAI_API_KEY": "leak"}), TestClient(app) as test_client:
        yield test_client


def test_scene_chat_is_created_once_and_kept_separate(client):
    payload = {"provider": "codex", "franchise_id": "vega-orion", "title": "Сцена A"}
    first = client.post("/api/agent-chat/scene-chats/scene-a", json=payload)
    repeat = client.post("/api/agent-chat/scene-chats/scene-a", json=payload)
    other = client.post("/api/agent-chat/scene-chats/scene-b", json=payload)
    assert first.status_code == repeat.status_code == other.status_code == 200
    assert first.json()["id"] == repeat.json()["id"] != other.json()["id"]
    assert first.json()["scene_id"] == "scene-a" and other.json()["scene_id"] == "scene-b"
    assert client.post("/api/agent-chat/scene-chats/scene-a", json={**payload, "franchise_id": "other"}).status_code == 409


def test_chat_round_trip_keeps_the_session(client):
    chat = client.post("/api/agent-chat/chats", json={"provider": "codex"}).json()
    lines = [json.loads(line) for line in client.post(f"/api/agent-chat/chats/{chat['id']}/messages",
                                                       json={"text": "Привет, студия"}).iter_lines() if line]
    kinds = [event["kind"] for event in lines]
    assert kinds[-1] == "done" and "tool" in kinds and "text" in kinds
    for _ in range(50):
        saved = client.get(f"/api/agent-chat/chats/{chat['id']}").json()
        if not saved["running"]:
            break
        time.sleep(0.05)
    assert saved["session_id"] == "thread-1" and saved["title"] == "Привет, студия"
    reply = saved["messages"][1]
    assert reply["text"].startswith("Ответ на:") and reply["events"][0]["status"] == "done"
    second = [json.loads(line) for line in client.post(f"/api/agent-chat/chats/{chat['id']}/messages",
                                                        json={"text": "дальше"}).iter_lines() if line]
    # the fake echoes the last 12 characters of the prompt; resume prefixes the mode reminder
    assert any(event.get("text", "").startswith("Ответ на: ") and event["text"].endswith("\n\nдальше") for event in second)
    assert [c["messages"] for c in client.get("/api/agent-chat/chats").json()] == [4]


def test_bad_chat_id_and_patch(client):
    assert client.get("/api/agent-chat/chats/../x").status_code == 404
    assert client.get("/api/agent-chat/chats/" + "z" * 32).status_code == 404
    chat = client.post("/api/agent-chat/chats", json={"provider": "codex", "mode": "read"}).json()
    assert client.patch(f"/api/agent-chat/chats/{chat['id']}", json={"mode": "act"}).json()["mode"] == "act"
    assert client.post("/api/agent-chat/chats", json={"provider": "gpt"}).status_code == 422
    assert client.delete(f"/api/agent-chat/chats/{chat['id']}").status_code == 204


def test_delete_all_chats(client):
    for _ in range(3):
        client.post("/api/agent-chat/chats", json={"provider": "codex", "mode": "read"})
    assert client.delete("/api/agent-chat/chats").json() == {"deleted": 3, "kept": 0}
    assert client.get("/api/agent-chat/chats").json() == []


FAKE_CODEX_IMAGES = """#!/usr/bin/env python3
import json, sys
images = [arg.split('=', 1)[1] for arg in sys.argv[1:] if arg.startswith('--image=')]
for line in [{"type": "thread.started", "thread_id": "thread-img"},
             {"type": "item.completed", "item": {"id": "a", "type": "agent_message",
                                                 "text": "images=" + ",".join(images) + " | " + sys.argv[-1][-90:]}},
             {"type": "turn.completed"}]:
    print(json.dumps(line, ensure_ascii=False), flush=True)
"""


class PhotoStore(FakeStore):
    def __init__(self, root, assets):
        super().__init__(root)
        self.assets = assets

    def get_asset(self, asset_id):
        return self.assets.get(asset_id)


def test_attached_photos_reach_the_agent_and_stay_in_the_chat(tmp_path):
    exe = tmp_path / "codex"
    exe.write_text(FAKE_CODEX_IMAGES)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    look = tmp_path / "look.jpg"
    look.write_bytes(b"\xff\xd8\xff")
    assets = {"a" * 8 + "-look": {"asset_id": "a" * 8 + "-look", "kind": "image", "path": str(look), "original_name": "look.jpg"},
              "v" * 8 + "-clip": {"asset_id": "v" * 8 + "-clip", "kind": "video", "path": str(look), "original_name": "clip.mp4"}}
    app = FastAPI()
    app.include_router(agent_chat.make_router(PhotoStore(tmp_path, assets)))
    with patch.object(agent_chat, "executable", return_value=str(exe)), TestClient(app) as client:
        chat = client.post("/api/agent-chat/chats", json={"provider": "codex"}).json()
        url = f"/api/agent-chat/chats/{chat['id']}/messages"
        lines = [json.loads(line) for line in client.post(url, json={"text": "свет как тут", "attachments": ["a" * 8 + "-look"]}).iter_lines() if line]
        reply = next(event["text"] for event in lines if event["kind"] == "text")
        assert reply.startswith(f"images={look}")  # the photo goes to the model itself
        assert "asset_id aaaaaaaa-look «look.jpg»" in reply  # and the agent is told its library id
        for _ in range(50):
            saved = client.get(f"/api/agent-chat/chats/{chat['id']}").json()
            if not saved["running"]:
                break
            time.sleep(0.05)
        assert saved["messages"][0]["attachments"] == [{"asset_id": "a" * 8 + "-look", "kind": "image", "name": "look.jpg"}]
        assert saved["messages"][0]["text"] == "свет как тут"  # the chat keeps the user's own words
        # A video goes too: not as a picture to the model, but named so the agent reads it with asset_frames.
        lines = [json.loads(line) for line in client.post(url, json={"text": "замени кота на Орион",
                 "attachments": ["v" * 8 + "-clip"]}).iter_lines() if line]
        reply = next(event["text"] for event in lines if event["kind"] == "text")
        assert reply.startswith("images= |") and "кадров, длительность, звук" in reply and "видео asset_id vvvvvvvv-clip" in reply
        assert client.post(url, json={"text": "x", "attachments": ["missing"]}).status_code == 422
