"""Extra Codex / Claude subscriptions for the Studio agent (local_studio/agent_accounts.py)."""
import json
import stat
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_studio import agent_accounts, agent_brain

FAKE_CODEX = """#!/usr/bin/env python3
import os, sys, time
home = os.environ.get("CODEX_HOME", "MAIN")
logged = os.path.join(home, "auth.json") if home != "MAIN" else None
if sys.argv[1:] == ["login", "status"]:
    ok = home == "MAIN" or os.path.exists(logged)
    print("Logged in using ChatGPT" if ok else "Not logged in"); sys.exit(0 if ok else 1)
if sys.argv[1:] == ["login"]:
    print("Starting local login server on http://localhost:1455.", flush=True)
    print("If your browser did not open, navigate to this URL to authenticate:", flush=True)
    print("https://auth.openai.com/oauth/authorize?client=x&state=y", flush=True)
    open(logged, "w").write("{}")
    sys.exit(0)
if sys.argv[1:] == ["logout"]:
    os.remove(logged); sys.exit(0)
"""


@pytest.fixture
def client(tmp_path):
    exe = tmp_path / "codex"
    exe.write_text(FAKE_CODEX)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex/config.toml").write_text('[mcp_servers.h3-studio]\ncommand = "python3"\n')
    app = FastAPI()
    app.include_router(agent_accounts.make_router())
    with patch.object(agent_accounts, "ROOT", tmp_path / "accounts"), \
         patch.object(agent_brain, "executable", return_value=str(exe)), \
         patch("pathlib.Path.home", return_value=home), TestClient(app) as test_client:
        yield test_client, tmp_path


def test_second_codex_subscription_logs_in_and_becomes_the_agent_account(client):
    api, tmp = client
    listed = api.get("/api/agent-accounts/codex").json()
    assert listed["active"] == "main" and [a["id"] for a in listed["accounts"]] == ["main"]
    added = api.post("/api/agent-accounts/codex", json={"label": "Рабочий ChatGPT"}).json()
    assert added["login_url"].startswith("https://auth.openai.com/oauth/authorize")
    folder = tmp / "accounts" / f"codex-{added['id']}"
    assert (folder / "config.toml").read_text().startswith("[mcp_servers.h3-studio]")  # the Studio tools come along
    import time
    for _ in range(50):
        accounts = api.get("/api/agent-accounts/codex").json()["accounts"]
        if accounts[1]["logged_in"]:
            break
        time.sleep(0.05)
    assert accounts[1]["label"] == "Рабочий ChatGPT" and accounts[1]["logged_in"]
    assert api.post(f"/api/agent-accounts/codex/{added['id']}/activate").json()["active"] == added["id"]
    assert agent_accounts.env_overrides() == {"CODEX_HOME": str(folder)}
    assert agent_brain.clean_env()["CODEX_HOME"] == str(folder)  # every agent run uses it now
    # signing out of the extra account forgets it and returns the agent to the main login
    assert api.post(f"/api/agent-accounts/codex/{added['id']}/logout").json()["active"] == "main"
    assert not folder.exists() and agent_accounts.env_overrides() == {}


def test_the_main_login_of_this_mac_is_never_signed_out(client):
    api, _ = client
    assert api.post("/api/agent-accounts/codex/main/logout").status_code == 409
    assert api.post("/api/agent-accounts/codex/main/login").status_code == 409
    assert api.post("/api/agent-accounts/codex/deadbeef/activate").status_code == 404
