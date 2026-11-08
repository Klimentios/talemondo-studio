#!/usr/bin/env python3
"""Mock ComfyUI for testing scripts/h3_ab.py end to end on the local stand (no GPU, no weights).

    python ab_mock_comfyui.py OBJECT_INFO.json [--port 18188] [--lora FILE ...]

Speaks the part of the ComfyUI API the GPU API uses: /object_info (a real dump, plus --lora files in
LoraLoaderModelOnly), /upload/image, /prompt (validated with scripts/h3_graph_validate.py), /queue,
/history/{id}, /view, /system_stats (VRAM rises while a job runs), /free and /ws (executing/progress
events, so the GPU API records per-phase timings). Each job "renders" a 2 s ffmpeg test pattern whose
hue depends on the graph, so variants look different side by side. Runs with aiohttp (ComfyUI venv).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from aiohttp import WSMsgType, web

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, "/repo-scripts")
from h3_graph_validate import validate  # noqa: E402

GIB = 2**30
ORDER = ["127", "313", "128", "119", "120", "136", "310", "311", "312", "129", "123", "124", "126", "125",
         "122", "121", "130", "92"]


class Mock:
    def __init__(self, info: dict, root: Path) -> None:
        self.info, self.root = info, root
        (root / "input").mkdir(parents=True, exist_ok=True)
        (root / "output/video").mkdir(parents=True, exist_ok=True)
        self.pending: list[list] = []
        self.running: list | None = None
        self.history: dict[str, dict] = {}
        self.sockets: dict[str, web.WebSocketResponse] = {}
        self.used = 3.0 * GIB
        self.number = 0
        self.wake = asyncio.Event()

    async def send(self, client_id: str, event: str, data: dict) -> None:
        socket = self.sockets.get(client_id)
        if socket is not None and not socket.closed:
            await socket.send_str(json.dumps({"type": event, "data": data}))

    async def worker(self) -> None:
        while True:
            while not self.pending:
                self.wake.clear()
                await self.wake.wait()
            self.running = self.pending.pop(0)
            _, prompt_id, graph, extra = self.running
            client = extra.get("client_id", "")
            start = time.time()
            steps = int(graph.get("124", {}).get("inputs", {}).get("steps", 25))
            per_step = 0.03 if "313" in graph else 0.05  # "sparse" is faster
            self.used = (62.0 + (1.5 if "312" in graph else 0.0) - (4.0 if "313" in graph else 0.0)) * GIB
            for node in [n for n in ORDER if n in graph] + [n for n in graph if n not in ORDER]:
                await self.send(client, "executing", {"node": node, "prompt_id": prompt_id})
                if node == "125":
                    for step in range(1, steps + 1):
                        await asyncio.sleep(per_step)
                        await self.send(client, "progress", {"value": step, "max": steps, "node": "125",
                                                             "prompt_id": prompt_id})
                else:
                    await asyncio.sleep(0.05)
            await self.send(client, "executing", {"node": None, "prompt_id": prompt_id})
            name = f"MiniMax_H3_Studio_{prompt_id[:8]}.mp4"
            hue = int(hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()[:4], 16) % 360
            await asyncio.to_thread(subprocess.run, [
                "ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=180x320:d=2:r=24",
                "-f", "lavfi", "-i", "sine=f=440:d=2", "-vf", f"hue=h={hue}", "-pix_fmt", "yuv420p",
                "-shortest", str(self.root / "output/video" / name)], check=True)
            end = time.time()
            self.history[prompt_id] = {
                "prompt": self.running,
                "status": {"status_str": "success", "completed": True, "messages": [
                    ["execution_start", {"prompt_id": prompt_id, "timestamp": int(start * 1000)}],
                    ["execution_success", {"prompt_id": prompt_id, "timestamp": int(end * 1000)}]]},
                "outputs": {"92": {"images": [{"filename": name, "subfolder": "video", "type": "output"}],
                                   "animated": [True]}},
            }
            self.running = None
            self.used = 20.0 * GIB  # models stay cached between jobs


def make_app(mock: Mock) -> web.Application:
    routes = web.RouteTableDef()

    @routes.get("/object_info")
    async def object_info(_):
        return web.json_response(mock.info)

    @routes.get("/system_stats")
    async def system_stats(_):
        total = 80 * GIB
        return web.json_response({"system": {"comfyui_version": "mock"}, "devices": [
            {"name": "mock", "type": "cuda", "vram_total": total, "vram_free": total - mock.used,
             "torch_vram_total": total, "torch_vram_free": total - mock.used}]})

    @routes.post("/free")
    async def free(_):
        return web.json_response({})

    @routes.post("/upload/image")
    async def upload(request):
        form = await request.post()
        item = form["image"]
        (mock.root / "input" / item.filename).write_bytes(item.file.read())
        return web.json_response({"name": item.filename, "subfolder": "", "type": "input"})

    @routes.post("/prompt")
    async def prompt(request):
        body = await request.json()
        graph = body["prompt"]
        problems = validate(graph, mock.info)
        if problems:
            return web.json_response({"error": {"type": "prompt_outputs_failed_validation", "message": "; ".join(problems)},
                                      "node_errors": {"mock": problems}}, status=400)
        prompt_id = body.get("prompt_id") or str(uuid.uuid4())
        mock.number += 1
        mock.pending.append([mock.number, prompt_id, graph, {"client_id": body.get("client_id", "")}])
        mock.wake.set()
        return web.json_response({"prompt_id": prompt_id, "number": mock.number, "node_errors": {}})

    @routes.get("/queue")
    async def queue(_):
        return web.json_response({"queue_running": [mock.running] if mock.running else [],
                                  "queue_pending": mock.pending})

    @routes.get("/history/{prompt_id}")
    async def history(request):
        prompt_id = request.match_info["prompt_id"]
        return web.json_response({prompt_id: mock.history[prompt_id]} if prompt_id in mock.history else {})

    @routes.get("/view")
    async def view(request):
        path = mock.root / request.query.get("type", "output") / request.query.get("subfolder", "") / request.query["filename"]
        return web.FileResponse(path)

    @routes.get("/ws")
    async def ws(request):
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        mock.sockets[request.query.get("clientId", "")] = socket
        async for message in socket:
            if message.type == WSMsgType.ERROR:
                break
        return socket

    app = web.Application(client_max_size=600 * 2**20)
    app.add_routes(routes)

    async def start_worker(app):
        app["worker"] = asyncio.create_task(mock.worker())

    app.on_startup.append(start_worker)
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("object_info")
    parser.add_argument("--port", type=int, default=18188)
    parser.add_argument("--lora", action="append", default=[])
    args = parser.parse_args()
    info = json.loads(Path(args.object_info).read_text())
    lora = info.setdefault("LoraLoaderModelOnly", {"input": {"required": {"lora_name": [[]]}}})
    names = lora["input"]["required"]["lora_name"][0]
    names.extend(name for name in args.lora if name not in names)
    web.run_app(make_app(Mock(info, Path(tempfile.mkdtemp(prefix="h3-mock-")))), host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
