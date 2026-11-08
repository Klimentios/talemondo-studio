import asyncio
import sqlite3
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_studio import orchestrator


class Turn:
    def __init__(self):
        self.done = False
        self.changed = asyncio.Event()


class FakeHub:
    """agent_chat's hub without a CLI: records what the Studio tells the agent."""

    def __init__(self):
        self.chats, self.runs, self.on_done, self.sent, self.stopped = {}, {}, [], [], []
        self.helper_answer = "Шот 3: смени крупность на крупный."

    def new_chat(self, provider, model="", mode="read", title="", effort="", **extra):
        chat = {"id": f"c{len(self.chats):031d}", "provider": provider, "mode": mode, "title": title, "effort": effort,
                "messages": [], **extra}
        self.chats[chat["id"]] = chat
        return chat

    def load(self, chat_id):
        return self.chats[chat_id]

    async def start(self, chat_id, text, context=None, studio_url="", author="user", attachments=()):
        self.sent.append((chat_id, text))
        self.last_attachments = list(attachments)
        turn = Turn()
        chat = self.chats[chat_id]
        if chat.get("role"):  # a helper answers at once
            chat["messages"].append({"role": "assistant", "text": self.helper_answer})
            turn.done = True
        else:
            self.runs[chat_id] = turn
        return turn

    def stop(self, chat_id):
        self.stopped.append(chat_id)
        return True

    def end_turn(self, chat_id, reply=None):
        self.runs.pop(chat_id, None)
        for hook in self.on_done:
            hook(self.chats[chat_id], reply or {"text": "ok"})


class FakeStore:
    def __init__(self, root):
        self.root = root
        self.jobs = {}
        self.draft = {}
        self.assets = {}

    def get_asset(self, asset_id):
        return self.assets.get(asset_id)

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def load_draft(self):
        return self.draft

    def load_draft_snapshot(self):
        return self.draft, 1


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "POLL_S", 0.05)
    hub, store = FakeHub(), FakeStore(tmp_path)
    app = FastAPI()
    app.include_router(orchestrator.make_router(store, hub))
    with TestClient(app) as client:
        yield client, hub, store


def settle(client):
    # hooks and the watcher run as tasks on the app loop; a request lets them run
    for _ in range(5):
        client.get("/api/runs")


def test_task_lifecycle_plan_gpu_gate_wait_and_done(setup):
    client, hub, store = setup
    run = client.post("/api/runs", json={"goal": "ADAM & VEGA: ролик по идее 20", "franchise_id": "f1"}).json()
    rid, chat = run["id"], run["chat_id"]
    assert hub.chats[chat]["run_id"] == rid and hub.chats[chat]["mode"] == "act"
    assert hub.chats[chat]["franchise_id"] == "f1"
    assert f"задача {rid}" in hub.sent[0][1] and "идее 20" in hub.sent[0][1]
    client.post(f"/api/runs/{rid}/plan", json={"steps": [{"id": "scene", "title": "Сцена", "kind": "scene"},
                                                         {"id": "render", "title": "Рендер", "kind": "render"}]})
    assert client.post(f"/api/runs/{rid}/step", json={"step_id": "scene", "status": "done",
                                                      "artifacts": {"scene": "s1"}}).json()["steps"][0]["status"] == "done"
    assert client.post(f"/api/runs/{rid}/step", json={"step_id": "nope", "status": "done"}).status_code == 404
    assert client.get(f"/api/runs/{rid}/gate?kind=gpu").json()["allowed"] is False
    ask = client.post(f"/api/runs/{rid}/ask", json={"question": "Ставлю 7 рендеров по ~4 мин GPU?", "gate": "gpu"}).json()
    hub.end_turn(chat)
    settle(client)
    state = client.get(f"/api/runs/{rid}").json()
    assert state["status"] == "waiting_user" and state["asks"][0]["options"] == ["Да", "Нет"]
    client.post(f"/api/runs/{rid}/answer", json={"ask_id": ask["ask_id"], "answer": "Да"})
    assert client.get(f"/api/runs/{rid}/gate?kind=gpu").json()["allowed"] is True
    assert client.post(f"/api/runs/{rid}/gate/claim?kind=gpu", json={}).json()["remaining"] == 0
    assert client.get(f"/api/runs/{rid}/gate?kind=gpu").json()["allowed"] is False
    assert client.post(f"/api/runs/{rid}/gate/claim?kind=gpu", json={}).status_code == 409
    assert "ответил" in hub.sent[-1][1] and "Да" in hub.sent[-1][1]
    assert client.post(f"/api/runs/{rid}/answer", json={"ask_id": ask["ask_id"], "answer": "Нет"}).status_code == 409
    store.jobs["j1"] = {"state": "queued_gpu"}
    client.post(f"/api/runs/{rid}/wait", json={"job_ids": ["j1"]})
    hub.end_turn(chat)
    settle(client)
    assert client.get(f"/api/runs/{rid}").json()["status"] == "waiting_jobs"
    store.jobs["j1"] = {"state": "completed"}
    for _ in range(60):
        time.sleep(0.05)  # the watcher polls every POLL_S
        if client.get(f"/api/runs/{rid}").json()["status"] == "running":
            break
    assert "j1: completed" in hub.sent[-1][1]
    assert client.post(f"/api/runs/{rid}/done", json={"summary": "Ролик собран"}).status_code == 409
    client.post(f"/api/runs/{rid}/step", json={"step_id": "render", "status": "done", "artifacts": {"job_ids": ["j1"]}})
    final = client.post(f"/api/runs/{rid}/done", json={"summary": "Ролик собран"}).json()
    assert final["status"] == "done" and final["summary"] == "Ролик собран"


def test_gpu_gate_has_bounded_uses_and_new_approval_replaces_the_limit(setup):
    client, _, _ = setup
    assert client.post("/api/runs", json={"goal": "Без франшизы"}).status_code == 422
    rid = client.post("/api/runs", json={"goal": "Два шота", "franchise_id": "f1"}).json()["id"]
    ask = client.post(f"/api/runs/{rid}/ask", json={"question": "Два отдельных рендера?", "gate": "gpu", "gpu_uses": 2}).json()
    client.post(f"/api/runs/{rid}/answer", json={"ask_id": ask["ask_id"], "answer": "Да"})
    assert client.post(f"/api/runs/{rid}/gate/claim", json={}).json()["remaining"] == 1
    assert client.post(f"/api/runs/{rid}/gate/claim", json={}).json()["remaining"] == 0
    assert client.post(f"/api/runs/{rid}/gate/claim", json={}).status_code == 409
    denied = client.post(f"/api/runs/{rid}/ask", json={"question": "Ещё один?", "gate": "gpu"}).json()
    client.post(f"/api/runs/{rid}/answer", json={"ask_id": denied["ask_id"], "answer": "Нет"})
    assert client.get(f"/api/runs/{rid}/gate").json()["remaining"] == 0


def test_explicit_generate_request_authorizes_task_without_repeat_question(setup):
    client, _, _ = setup
    rid = client.post("/api/runs", json={"goal": "Сгенерируй ролик", "franchise_id": "f1"}).json()["id"]
    assert client.get(f"/api/runs/{rid}/gate").json()["remaining"] == 20
    answer = client.post(f"/api/runs/{rid}/ask", json={"question": "Генерировать?", "gate": "gpu"}).json()
    assert answer["already_authorized"] is True
    assert client.post(f"/api/runs/{rid}/gate/claim", json={}).json()["remaining"] == 19


def test_done_refuses_missing_plan_and_unfinished_render(setup):
    client, _, store = setup
    rid = client.post("/api/runs", json={"goal": "Фильм", "franchise_id": "f1"}).json()["id"]
    assert client.post(f"/api/runs/{rid}/done", json={"summary": "Готово"}).status_code == 409
    client.post(f"/api/runs/{rid}/plan", json={"steps": [{"id": "render", "title": "Рендер", "kind": "render"}]})
    client.post(f"/api/runs/{rid}/step", json={"step_id": "render", "status": "done", "artifacts": {"job_id": "j1"}})
    store.jobs["j1"] = {"state": "running"}
    assert client.post(f"/api/runs/{rid}/done", json={"summary": "Готово"}).status_code == 409
    store.jobs["j1"] = {"state": "completed"}
    assert client.post(f"/api/runs/{rid}/done", json={"summary": "Готово"}).json()["status"] == "done"


def test_committed_shot_event_is_recorded_once_without_waking_model(setup):
    client, hub, store = setup
    run = client.post("/api/runs", json={"goal": "Правка шота", "franchise_id": "f1"}).json()
    rid = run["id"]
    store.db_path = store.root / "studio.sqlite3"
    store.draft = {"scenes": [{"id": "s1", "franchiseSource": {"franchise_id": "f1"}}]}
    with sqlite3.connect(store.db_path) as db:
        db.execute("CREATE TABLE agent_event_receipts (event_id TEXT, project_id TEXT, shot_id TEXT, revision INTEGER)")
        db.execute("INSERT INTO agent_event_receipts VALUES ('evt-1', 's1', 'c1', 2)")
    event = {"event_id": "evt-1", "kind": "shot_updated", "scene_id": "s1", "shot_id": "c1", "revision": 2}
    before = len(hub.sent)
    assert client.post(f"/api/runs/{rid}/events", json=event).json()["recorded"]
    assert client.post(f"/api/runs/{rid}/events", json=event).json()["recorded"]
    assert len(client.get(f"/api/runs/{rid}").json()["events"]) == 1
    assert len(hub.sent) == before
    assert client.post(f"/api/runs/{rid}/events", json={**event, "revision": 3}).status_code == 409


def test_turn_without_protocol_goes_idle_and_inbox_waits_for_the_turn(setup):
    client, hub, _ = setup
    run = client.post("/api/runs", json={"goal": "Сцена погони", "franchise_id": "f1"}).json()
    rid, chat = run["id"], run["chat_id"]
    client.post(f"/api/runs/{rid}/continue", json={"text": "Добавь титр героя"})  # agent is busy: queued
    assert len(hub.sent) == 1
    hub.end_turn(chat)
    settle(client)
    assert hub.sent[-1][1].endswith("Добавь титр героя")
    for _ in range(2):  # a turn that ends without ask/wait/done is re-woken by the Studio, at most twice
        hub.end_turn(chat)
        settle(client)
        assert "исправь её сам" in hub.sent[-1][1]
    hub.end_turn(chat)
    settle(client)
    assert client.get(f"/api/runs/{rid}").json()["status"] == "idle"
    stopped = client.post(f"/api/runs/{rid}/stop").json()
    assert stopped["status"] == "stopped" and chat in hub.stopped


def test_helper_answers_the_orchestrator(setup):
    client, hub, _ = setup
    rid = client.post("/api/runs", json={"goal": "Проверка сцены", "franchise_id": "f1"}).json()["id"]
    answer = client.post(f"/api/runs/{rid}/delegate", json={"role": "оператор", "task": "Проверь план шотов"}).json()
    assert answer["answer"] == hub.helper_answer
    helper = hub.chats[answer["helper_chat_id"]]
    assert helper["mode"] == "read" and helper["parent_run"] == rid and helper["effort"] == "low"  # helpers run light
    assert helper["franchise_id"] == "f1"
    assert "геометрию камеры" in hub.sent[-1][1]
    assert client.get(f"/api/runs/{rid}").json()["helpers"][0]["status"] == "done"
    assert client.get("/api/runs/nothex").status_code == 404


def test_helper_with_evidence_uses_short_payload_without_repo_tools(setup, monkeypatch):
    from local_studio import agent_brain
    client, hub, _ = setup
    calls = []

    async def answer(request):
        calls.append(request)
        return {"data": {"verdict": "uncertain", "findings": ["Контакт лапы не виден"],
                         "evidence": ["shot-3, 2.1s"], "uncertainties": ["нет соседнего кадра"],
                         "suggested_fix": "проверить 2.0–2.3s"}, "usage": {"input_tokens": 180},
                "input_summary": {"prompt_characters": len(request.prompt), "images": 0, "read_repo": False},
                "seconds": 1.2}

    monkeypatch.setattr(agent_brain, "ask", answer)
    rid = client.post("/api/runs", json={"goal": "Проверка сцены", "franchise_id": "f1"}).json()["id"]
    before = len(hub.chats)
    result = client.post(f"/api/runs/{rid}/delegate", json={"role": "оператор", "task": "Проверь контакт лапы",
                                                       "evidence": "shot-3: контакт скрыт на 2.1s"}).json()
    assert len(hub.chats) == before
    assert calls[0].read_repo is False and calls[0].effort == "low"
    assert "shot-3: контакт скрыт" in calls[0].prompt
    assert "геометрию камеры" in calls[0].prompt
    assert result["report"]["verdict"] == "uncertain" and result["usage"]["input_tokens"] == 180
    helper = client.get(f"/api/runs/{rid}").json()["helpers"][0]
    assert helper["mode"] == "evidence_only" and helper["status"] == "done"
    assert helper["usage"]["input_tokens"] == 180


def test_invalid_specialist_report_is_recorded_as_failure(setup, monkeypatch):
    from local_studio import agent_brain
    client, _, _ = setup

    async def invalid(_request):
        return {"data": {"verdict": "pass"}}

    monkeypatch.setattr(agent_brain, "ask", invalid)
    rid = client.post("/api/runs", json={"goal": "Проверка сцены", "franchise_id": "f1"}).json()["id"]
    response = client.post(f"/api/runs/{rid}/delegate", json={"role": "оператор", "task": "Проверь кадр",
                                                        "evidence": "shot-1, frame 0"})
    assert response.status_code == 502
    assert client.get(f"/api/runs/{rid}").json()["helpers"][0]["status"] == "failed"


def test_film_completion_requires_accepted_final_result(setup, monkeypatch):
    from local_studio import production_review
    client, _, _ = setup
    rid = client.post('/api/runs', json={'goal': 'Собрать фильм', 'franchise_id': 'f1', 'deliverable_type': 'film'}).json()['id']
    client.post(f'/api/runs/{rid}/plan', json={'steps': [{'id': 'qc', 'title': 'Приёмка', 'kind': 'scene'}]})
    client.post(f'/api/runs/{rid}/step', json={'step_id': 'qc', 'status': 'done'})
    assert client.post(f'/api/runs/{rid}/done', json={'summary': 'Фильм готов'}).status_code == 409
    def missing(*args):
        raise ValueError('Нет приёмки')
    monkeypatch.setattr(production_review, 'require_creative_acceptance', missing)
    assert client.post(f'/api/runs/{rid}/done', json={'summary': 'Фильм готов', 'final_job_id': 'j1'}).status_code == 409
    monkeypatch.setattr(production_review, 'require_creative_acceptance', lambda *args: {'id': 'review1'})
    result = client.post(f'/api/runs/{rid}/done', json={'summary': 'Фильм готов', 'final_job_id': 'j1'})
    assert result.status_code == 200 and result.json()['final_review_id'] == 'review1'


def test_scene_run_wait_and_done_require_real_qc(setup):
    import json
    client, _, store = setup
    scene_id = "qc-scene"
    cut = {"id": "shot-1", "prompt": "Cat enters", "shotJobId": "j1"}
    store.draft = {"scenes": [{"id": scene_id, "franchiseSource": {"franchise_id": "f1"},
                               "cuts": [cut]}]}
    output = store.root / "j1.mp4"
    output.write_bytes(b"rendered")
    store.jobs["j1"] = {"state": "completed", "output_path": str(output)}
    run_file = store.root / "scene-runs" / f"{scene_id}.json"
    run_file.parent.mkdir()
    run_file.write_text(json.dumps({"scene_id": scene_id, "state": "awaiting", "cuts": [cut]}))
    rid = client.post("/api/runs", json={"goal": "Собрать сцену", "franchise_id": "f1"}).json()["id"]
    client.post(f"/api/runs/{rid}/plan", json={"steps": [{"id": "scene", "title": "Сцена", "kind": "scene"}]})
    client.post(f"/api/runs/{rid}/step", json={"step_id": "scene", "status": "done",
                                                "artifacts": {"scene": scene_id}})
    assert client.post(f"/api/runs/{rid}/done", json={"summary": "Готово"}).status_code == 409
    cut.update(shotQcReview="accepted", shotQc={"job_id": "j1", "decision": "accept"})
    run_file.write_text(json.dumps({"scene_id": scene_id, "state": "done", "cuts": [cut]}))
    assert client.post(f"/api/runs/{rid}/done", json={"summary": "Готово"}).status_code == 200


def test_story_gate_yes_is_recorded_in_decisions(setup, tmp_path, monkeypatch):
    from local_studio import decisions
    monkeypatch.setattr(decisions, "DECISIONS", tmp_path / "DECISIONS.md")
    client, hub, _ = setup
    rid = client.post("/api/runs", json={"goal": "Серия 3", "franchise_id": "f1"}).json()["id"]
    ask = client.post(f"/api/runs/{rid}/ask", json={"question": "Одобряешь сюжет E3 «Человек в плане»?", "gate": "story"}).json()
    client.post(f"/api/runs/{rid}/answer", json={"ask_id": ask["ask_id"], "answer": "Да"})
    text = (tmp_path / "DECISIONS.md").read_text()
    assert "Человек в плане" in text and "«Да»" in text and "кнопка в задаче агента" in text


def test_chat_approval_needs_the_users_own_words(tmp_path, monkeypatch):
    import json
    from local_studio import decisions
    monkeypatch.setattr(decisions, "DECISIONS", tmp_path / "DECISIONS.md")
    store = FakeStore(tmp_path)
    (tmp_path / "agent_chats").mkdir()
    chat_id = "a" * 32
    (tmp_path / "agent_chats" / f"{chat_id}.json").write_text(json.dumps({"messages": [
        {"role": "user", "text": "да  одобряю, делай третью серию"},
        {"role": "user", "text": "Одобряю всё", "from": "studio"}]}))
    app = FastAPI()
    app.include_router(decisions.make_router(store))
    client = TestClient(app)
    body = {"franchise": "VEGA & ORION", "story": "E3 «Человек в плане»", "chat_id": chat_id}
    assert client.post("/api/decisions/story", json={**body, "user_words": "Одобряю всё"}).status_code == 422
    ok = client.post("/api/decisions/story", json={**body, "user_words": "да одобряю, делай третью серию"})
    assert ok.status_code == 200
    assert "VEGA & ORION, одобрение сюжета в Studio" in (tmp_path / "DECISIONS.md").read_text()


def test_waiting_for_a_whole_scene_build(setup):
    client, hub, store = setup
    run = client.post("/api/runs", json={"goal": "Сцена", "franchise_id": "f1"}).json()
    rid, chat = run["id"], run["chat_id"]
    store.draft = {"project": {"activeSceneId": "s1", "cuts": [{"shotJobId": "j1"}, {}], "storyboard": {"running": True},
                               "scenes": [{"id": "s1"}]}}
    client.post(f"/api/runs/{rid}/wait", json={"scene_ids": ["s1"]})
    hub.end_turn(chat)
    settle(client)
    time.sleep(0.2)
    assert client.get(f"/api/runs/{rid}").json()["status"] == "waiting_jobs"  # still building
    store.jobs["j1"], store.jobs["j2"] = {"state": "completed"}, {"state": "completed"}
    store.draft["project"].update(cuts=[{"shotJobId": "j1"}, {"shotJobId": "j2"}], storyboard={"running": False})
    for _ in range(60):
        time.sleep(0.05)
        if client.get(f"/api/runs/{rid}").json()["status"] == "running":
            break
    assert "scene s1: completed" in hub.sent[-1][1]


def test_task_video_attachments_created_and_queued(setup, tmp_path):
    client, hub, store = setup
    video = tmp_path / "reference.mp4"
    video.write_bytes(b"video")
    store.assets["v1"] = {"kind": "video", "path": str(video)}
    assert client.post("/api/runs", json={"goal": "Замена героя", "franchise_id": "f1", "attachments": ["missing"]}).status_code == 422
    run = client.post("/api/runs", json={"goal": "Замена героя", "franchise_id": "f1", "attachments": ["v1"]}).json()
    assert hub.last_attachments == ["v1"]
    assert client.post(f"/api/runs/{run['id']}/continue", json={"text": "Ещё видео", "attachments": ["v1"]}).status_code == 200
    assert client.get(f"/api/runs/{run['id']}").json()["inbox"][0]["attachments"] == ["v1"]
    hub.end_turn(run["chat_id"])
    settle(client)
    assert hub.last_attachments == ["v1"]
    assert "Ещё видео" in hub.sent[-1][1]


def test_specialist_profiles_are_focused_and_do_not_guess_composite_roles():
    from local_studio.agent_specialists import specialist_brief
    assert "траектории" in specialist_brief("постановка")
    assert "таймкоды" in specialist_brief("QC")
    assert "референсов" in specialist_brief("H3-промпт")
    assert not specialist_brief("режиссёр и оператор")
    assert not specialist_brief("неизвестный помощник")


def test_film_can_finish_with_reviewed_edl_export(setup, monkeypatch):
    import hashlib
    import json
    from local_studio import production_review
    client, _, store = setup
    uid = 'b' * 32
    folder = store.root / 'edl_renders'
    folder.mkdir()
    (folder / (uid + '.state')).write_text(json.dumps({'state': 'completed', 'draft': False}))
    output = folder / (uid + '.mp4')
    output.write_bytes(b'final edit')
    digest = hashlib.sha256(b'final edit').hexdigest()
    monkeypatch.setattr(production_review, 'probe', lambda _: (12.0, True))
    rid = client.post('/api/runs', json={'goal': 'Собрать фильм', 'franchise_id': 'f1',
                                       'deliverable_type': 'film'}).json()['id']
    client.post(f'/api/runs/{rid}/plan', json={'steps': [{'id': 'qc', 'title': 'Приёмка', 'kind': 'scene'}]})
    client.post(f'/api/runs/{rid}/step', json={'step_id': 'qc', 'status': 'done'})
    payload = {'summary': 'Готовый монтаж', 'final_artifact': {'kind': 'edl', 'id': uid}}
    assert client.post(f'/api/runs/{rid}/done', json=payload).status_code == 409
    evidence = dict(output_sha256=digest, reviewer='producer', evidence='Full playback including audio',
                    viewed_ranges=[(0, 12)], audio_reviewed=True)
    for verdict in ('technical_pass', 'creative_accepted'):
        production_review.record_review(store, uid, production_review.ReviewInput(verdict=verdict, **evidence), 'edl')
    accepted = client.post(f'/api/runs/{rid}/done', json=payload)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['final_artifact'] == {'kind': 'edl', 'id': uid}
    assert accepted.json()['final_review_id']


def test_render_request_words_grant_gate_and_negations_do_not():
    from local_studio.orchestrator import explicit_render_request
    for text in ("запуска генерацию", "запусти генерацию серии", "отрендерь сцену", "поставь рендер в очередь", "генерируй"):
        assert explicit_render_request(text), text
    for text in ("не запускай генерацию", "пока не запускай рендер", "покажи план рендера", "генерация была плохой"):
        assert not explicit_render_request(text), text
