import json
import os
import time

import pytest

from local_studio import tiktok_tokens as tokens


def credentials(tmp_path):
    path = tmp_path / 'tiktok-test.json'
    original = {'access_token': 'old', 'refresh_token': 'refresh-old', 'open_id': 'owner',
                'obtained_at': int(time.time()) - 86400, 'expires_in': 86400,
                'refresh_expires_in': 31536000, 'username': 'test'}
    path.write_text(json.dumps(original))
    (tmp_path / 'tiktok-app-sandbox.json').write_text(json.dumps({
        'client_key': 'key', 'client_secret': 'secret'}))
    return path, original


def fake_client(monkeypatch, payload, calls):
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, data):
            calls.append(data)
            class Response:
                status_code = 200
                def json(self): return payload
            return Response()
    monkeypatch.setattr(tokens.httpx, 'Client', Client)


def test_rotation_saved_privately_and_not_repeated(tmp_path, monkeypatch):
    path, original = credentials(tmp_path)
    calls = []
    fake_client(monkeypatch, {'access_token': 'new', 'refresh_token': 'refresh-new',
                'open_id': 'owner', 'expires_in': 86400, 'refresh_expires_in': 31536000,
                'scope': 'user.info.basic,video.list'}, calls)
    result = tokens.active_credentials(path)
    assert result['refresh_token'] == 'refresh-new'
    assert result['username'] == original['username']
    assert json.loads(path.read_text())['access_token'] == 'new'
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert tokens.active_credentials(path)['access_token'] == 'new'
    assert len(calls) == 1
    assert calls[0]['grant_type'] == 'refresh_token'


@pytest.mark.parametrize('payload', [
    {'error': 'invalid_grant', 'error_description': 'private response'},
    {'open_id': 'another-owner', 'access_token': 'wrong', 'refresh_token': 'wrong',
     'expires_in': 86400, 'refresh_expires_in': 31536000},
])
def test_bad_refresh_preserves_credentials(tmp_path, monkeypatch, payload):
    path, original = credentials(tmp_path)
    fake_client(monkeypatch, payload, [])
    with pytest.raises(ValueError) as error:
        tokens.active_credentials(path)
    assert 'private response' not in str(error.value)
    assert json.loads(path.read_text()) == original
