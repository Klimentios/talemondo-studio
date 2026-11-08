"""Independent durable intake, so deployment never restarts an active renderer."""
from fastapi import FastAPI
from .server_queue import install

app = FastAPI(title='STUDIO Server Queue')
queue = install(app, 'http://127.0.0.1:18188', renderer_url='http://127.0.0.1:18189')
