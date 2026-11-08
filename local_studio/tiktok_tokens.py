"""Refresh owner-authorized TikTok tokens; never log secret response bodies."""
import fcntl
import json
import os
import tempfile
import time
from pathlib import Path

import httpx


def active_credentials(path: Path, *, force=False):
    # The native worker and manual sync must never rotate the same token concurrently.
    lock_fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock_fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        credentials = json.loads(path.read_text())
        now = int(time.time())
        expires_at = credentials.get('obtained_at', 0) + credentials.get('expires_in', 0)
        if not force and expires_at > now + 8 * 3600:
            return credentials
        app_path = path.parent / 'tiktok-app-sandbox.json'
        try:
            app = json.loads(app_path.read_text())
        except (OSError, ValueError):
            app = {}
        if not all(app.get(k) for k in ('client_key', 'client_secret')) or not credentials.get('refresh_token'):
            if not force:
                return credentials
            raise ValueError('TikTok: для продления нужны client_key, client_secret и refresh_token')
        refresh_until = credentials.get('obtained_at', 0) + credentials.get('refresh_expires_in', 0)
        if refresh_until <= now:
            raise ValueError('TikTok: срок refresh_token истёк; требуется повторный вход')
        try:
            with httpx.Client(timeout=25, follow_redirects=False) as client:
                response = client.post('https://open.tiktokapis.com/v2/oauth/token/', data={
                    'client_key': app['client_key'], 'client_secret': app['client_secret'],
                    'grant_type': 'refresh_token', 'refresh_token': credentials['refresh_token'],
                })
                renewed = response.json()
        except (httpx.HTTPError, ValueError):
            raise ValueError('TikTok: продление временно недоступно; секреты скрыты') from None
        if response.status_code != 200 or not isinstance(renewed, dict) or renewed.get('error'):
            raise ValueError('TikTok: продление отклонено; проверьте доступ приложения')
        if (renewed.get('open_id') != credentials.get('open_id') or
                not renewed.get('access_token') or not renewed.get('refresh_token') or
                not isinstance(renewed.get('expires_in'), int) or renewed['expires_in'] <= 0 or
                not isinstance(renewed.get('refresh_expires_in'), int) or renewed['refresh_expires_in'] <= 0):
            raise ValueError('TikTok: некорректный ответ продления; исходные доступы сохранены')
        required = {'user.info.basic', 'video.list'}
        if not required <= set(renewed.get('scope', '').split(',')):
            raise ValueError('TikTok: после продления отсутствуют необходимые разрешения')
        credentials.update({k: renewed[k] for k in (
            'access_token', 'refresh_token', 'expires_in', 'refresh_expires_in', 'scope', 'token_type', 'open_id'
        ) if k in renewed})
        credentials['obtained_at'] = int(time.time())
        fd, temporary = tempfile.mkstemp(prefix='.tiktok-token-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as output:
                json.dump(credentials, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return credentials
