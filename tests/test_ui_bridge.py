import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_studio import ui_bridge


def client(data_root):
    app = FastAPI()
    app.include_router(ui_bridge.make_router(data_root))
    return TestClient(app)


def test_a_tab_claims_runs_and_reports_a_command(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/ui/tabs").json()["open"] == 0
    command = c.post("/api/ui/commands", json={"action": "board"}).json()
    assert command["state"] == "pending"
    job = c.get("/api/ui/commands/next?tab=t1").json()
    assert job == {"id": command["id"], "action": "board", "payload": {}}
    assert c.get("/api/ui/commands/next?tab=t2").status_code == 204  # one tab carries it out
    assert c.get("/api/ui/tabs").json()["open"] == 2

    def answer():
        time.sleep(0.2)
        c.post(f"/api/ui/commands/{command['id']}/result", json={"tab": "t1", "ok": True, "result": {"shots": 3}})

    threading.Thread(target=answer).start()
    done = c.get(f"/api/ui/commands/{command['id']}?wait=5").json()
    assert done["state"] == "done" and done["result"] == {"shots": 3}


def test_errors_unknown_actions_and_cancel(tmp_path):
    c = client(tmp_path)
    assert c.post("/api/ui/commands", json={"action": "format_disk"}).status_code == 422
    command = c.post("/api/ui/commands", json={"action": "build_scene"}).json()
    assert c.post(f"/api/ui/commands/{command['id']}/cancel").json()["state"] == "failed"
    other = c.post("/api/ui/commands", json={"action": "reset_scene"}).json()
    c.get("/api/ui/commands/next?tab=t1")
    failed = c.post(f"/api/ui/commands/{other['id']}/result", json={"tab": "t1", "ok": False, "error": "нет сцены"}).json()
    assert failed["state"] == "failed" and failed["error"] == "нет сцены"


def test_stale_write_requires_reconciliation_instead_of_duplicate_claim(monkeypatch, tmp_path):
    c = client(tmp_path)
    command = c.post("/api/ui/commands", json={"action": "apply_scene", "payload": {"scene": {"shots": [1]}}}).json()
    assert c.get("/api/ui/commands/next?tab=t1").json()["id"] == command["id"]
    clock = time.time()
    monkeypatch.setattr(ui_bridge.time, "time", lambda: clock + ui_bridge.CLAIM_TTL_S + 1)
    assert c.get("/api/ui/commands/next?tab=t2").status_code == 204
    uncertain = c.get(f"/api/ui/commands/{command['id']}").json()
    assert uncertain["state"] == "needs_reconciliation"
    assert c.post(f"/api/ui/commands/{command['id']}/result", json={"tab": "t1", "ok": True, "result": {"applied": True}}).json()["state"] == "done"


def test_stale_read_can_be_reclaimed(monkeypatch, tmp_path):
    c = client(tmp_path)
    command = c.post("/api/ui/commands", json={"action": "board"}).json()
    c.get("/api/ui/commands/next?tab=t1")
    clock = time.time()
    monkeypatch.setattr(ui_bridge.time, "time", lambda: clock + ui_bridge.CLAIM_TTL_S + 1)
    assert c.get("/api/ui/commands/next?tab=t2").json()["id"] == command["id"]


def test_restart_preserves_commands_and_does_not_reissue_uncertain_writes(tmp_path):
    c = client(tmp_path)
    write = c.post("/api/ui/commands", json={"action": "build_scene"}).json()
    c.get("/api/ui/commands/next?tab=t1")
    read = c.post("/api/ui/commands", json={"action": "board"}).json()
    c.get("/api/ui/commands/next?tab=t1")
    restarted = client(tmp_path)
    assert restarted.get(f"/api/ui/commands/{write['id']}").json()["state"] == "needs_reconciliation"
    assert restarted.get("/api/ui/commands/next?tab=t2").json()["id"] == read["id"]
    assert restarted.get("/api/ui/commands/next?tab=t2").status_code == 204
    result = restarted.post(f"/api/ui/commands/{write['id']}/result",
                            json={"tab": "t1", "ok": True, "result": {"job_id": "j1"}})
    assert result.json()["state"] == "done"
    assert client(tmp_path).get(f"/api/ui/commands/{write['id']}").json()["result"] == {"job_id": "j1"}


def test_only_current_claim_owner_can_report(tmp_path, monkeypatch):
    c = client(tmp_path)
    command = c.post("/api/ui/commands", json={"action": "board"}).json()
    url = f"/api/ui/commands/{command['id']}/result"
    c.get("/api/ui/commands/next?tab=t1")
    assert c.post(url, json={"ok": True}).status_code == 409  # old page must refresh
    assert c.post(url, json={"tab": "t2", "ok": True}).status_code == 409
    clock = time.time()
    monkeypatch.setattr(ui_bridge.time, "time", lambda: clock + ui_bridge.CLAIM_TTL_S + 1)
    c.get("/api/ui/commands/next?tab=t2")
    assert c.post(url, json={"tab": "t1", "ok": True}).status_code == 409
    assert c.post(url, json={"tab": "t2", "ok": True}).json()["state"] == "done"
