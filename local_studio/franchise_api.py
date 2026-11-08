"""Temporary franchise-only API while the main Studio has an active render.

Run: .venv/bin/python -m uvicorn local_studio.franchise_api:app --host 127.0.0.1 --port 18208
The regular Studio API owns the same routes after its next normal restart.
"""
import os
from pathlib import Path
from types import SimpleNamespace
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from .franchises import make_router
from .analytics import make_router as make_analytics_router

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
ORIGINS = {'http://127.0.0.1:18187', 'http://localhost:18187'}
app.add_middleware(CORSMiddleware, allow_origins=list(ORIGINS),
                   allow_methods=['GET', 'POST', 'PUT', 'PATCH'], allow_headers=['Content-Type'])

@app.middleware('http')
async def local_only(request: Request, call_next):
    host = request.url.hostname
    origin = request.headers.get('origin')
    if host not in {'127.0.0.1', 'localhost'} or (origin and origin not in ORIGINS):
        return JSONResponse({'detail': 'Только локальная Studio'}, status_code=403)
    return await call_next(request)

root = Path(os.environ.get('H3_MAC_DATA_DIR', str(Path.home() / 'Library/Application Support/H3 Studio')))
root.mkdir(parents=True, exist_ok=True)
app.include_router(make_router(SimpleNamespace(root=root)))

app.include_router(make_analytics_router(SimpleNamespace(root=root)))

import asyncio
from .analytics import run_public_collector
@app.on_event('startup')
async def start_analytics_collector():
    app.state.analytics_task=asyncio.create_task(run_public_collector(root))
    from .publication_network import run
    app.state.network_task=asyncio.create_task(run(root))
@app.on_event('shutdown')
async def stop_analytics_collector():
    network=app.state.network_task;network.cancel()
    try:await network
    except asyncio.CancelledError:pass
    task=app.state.analytics_task;task.cancel()
    try:await task
    except asyncio.CancelledError:pass
