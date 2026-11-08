#!/usr/bin/env python3
"""MCP server for H3 Studio: Claude Code, Codex or any MCP client drives the Studio from chat.

stdio, newline-delimited JSON-RPC 2.0, standard library only (runs on the system python3).
Every tool is a thin call to the running Studio API (default http://127.0.0.1:18187,
override with H3_STUDIO_URL), so validation and queue rules stay in the Studio itself.

Register:
  claude mcp add --scope user h3-studio -- python3 "<repo>/scripts/h3_studio_mcp.py"
  codex mcp add h3-studio -- python3 "<repo>/scripts/h3_studio_mcp.py"
"""
from __future__ import annotations

import base64
import functools
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

STUDIO = os.environ.get("H3_STUDIO_URL", "http://127.0.0.1:18187").rstrip("/")
FRANCHISE_ID = os.environ.get("H3_FRANCHISE_ID", "")
SCENE_ID = os.environ.get("H3_SCENE_ID", "")  # the chat's own Studio scene: canvas commands act there
VERSION = "1.0.0"
MAX_TEXT = 60_000
INSTRUCTIONS = (
    "H3 Studio (STUDIO): franchises and their bibles, asset library, keyframes, H3 render queue, "
    "storyboard assembly and the Studio brain. Project rules live in the repo AGENTS.md: no storyboard, "
    "keyframes or render of an episode before the user approved that story in docs/DECISIONS.md; every "
    "prompt and MP4 passes docs/guides/PRODUCER_QC.md. Renders and keyframes spend GPU time: enqueue only "
    "what the user asked for. Read a write tool's input schema before calling it. «Franchise + idea» is "
    "studio_playbook workflow=idea: idea_pack_build (one cast, reference set, look and mode for the idea, only from that "
    "franchise), staging_compile (heroes and cameras on a floor plan; fix its errors), then scene_draft with idea_id and "
    "staging, so nothing outside the pack and no broken geometry reaches the canvas.")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_ROOT = os.path.join(REPO, "skills")
PLAYBOOKS = {
    "idea": ("minimax-video-production",),
    "scene": ("minimax-video-production",),
    "video_swap": ("minimax-video-production",),
    "continuation": ("minimax-video-production",),
    "qc": ("minimax-video-production",),
    "edit": ("cinematic-director", "visual-dramaturgy"),
    "gpu_recovery": ("h3-fast-deploy",),
}
# Optional style skill per franchise key (skills/<name>/SKILL.md). Empty by default: a franchise without one
# takes its manner, tone and rules from its passport and the user's words. Add {"MY_SHOW": "my-style-skill"}.
FRANCHISE_SKILLS: dict[str, str | None] = {}
PLAYBOOK_STEPS = {
    "idea": ["Найти карточку идеи своей франшизы и посмотреть её картинки", "Собрать пакет идеи: герои, места, стиль, режим",
             "Показать сюжет в 3–5 строк и записать одобрение", "Постановка на плане: герои, ось, установки камеры, staging_compile без ошибок",
             "Черновик сцены внутри пакета идеи и проверка шотов", "Применить на холст; GPU — только по разрешению"],
    "scene": ["Проверить утверждение сюжета и паспорт франшизы", "Собрать карту шотов и референсов через franchise_resolve_shots",
              "Проверить промпты и физику действия через scene_draft/shot_lint",
              "Превиз в Blockout (только посмотреть глазами, если запущен; ошибки постановки до сборки ловит scene_staging_check без окна): герои в настоящем масштабе, метки, объектив и движение камеры каждого шота; "
              "посмотреть previs_view, сохранить «Композицию» для опорного кадра, а движущуюся камеру — previs_motion («Камера»)", "Применить сцену на холст",
              "Перед GPU сверить очередь и попросить разрешение в задаче; собрать, дождаться, проверить MP4"],
    "video_swap": ["Сверить весь исходный MP4 и все появления героев", "Выбрать только подтверждённый рецепт и роли референсов",
                   "Проверить манифест и очередь до GPU", "Рендерить по частям с квитанциями, QC всей части до следующей",
                   "Собрать полный MP4 с исходным звуком и проверить все стыки"],
    "continuation": ["Сверить последний кадр, звук, очередь и существующие квитанции", "Проверить следующую часть до GPU",
                     "Дождаться результата и провести QC до следующей части", "Собрать полный фильм и проверить стыки"],
    "qc": ["Сверить job ID и полный MP4", "Проверить декод, длительность и звук", "Просмотреть всё действие и стыки",
           "Записать конкретный PASS/FAIL и не продолжать после FAIL"],
    "edit": ["Прочитать открытый монтаж через montage_get", "Проверить исходники и порядок шотов",
             "Изменить открытый монтаж через montage_update, сохранив остальные правки пользователя",
             "Просчитать montage_render и проверить звук, цвет, титры и стыки", "Экспортировать мастер после QC"],
    "gpu_recovery": ["Сверить живой GPU, очередь и квитанции", "Не повторять неоднозначные задания",
                     "Проверить capabilities и граф без рендера", "Возобновлять только подтверждённую работу"],
}
# The agent can execute each level with Studio tools, without guessing shell scripts.
PLAYBOOK_TOOLS = {
    "idea": [("franchise_entities", "franchise_entity", "view_image"), ("idea_pack_build", "idea_pack_get"),
             ("story_approval_record",), ("staging_compile", "visual_profiles", "director_presets", "previs_stage", "previs_view"),
             ("scene_draft", "shot_lint"), ("studio_apply_scene", "run_ask", "studio_build_scene")],
    "scene": [("studio_readiness", "franchise_profile", "story_approval_record"),
              ("visual_profiles", "franchise_resolve_shots", "assets_list"), ("production_spec_validate", "scene_draft", "shot_lint"),
              ("previs_status", "previs_stage", "previs_view", "previs_snapshot", "previs_motion"), ("studio_apply_scene", "studio_board"), ("scene_staging_check", "run_ask", "studio_build_scene", "run_wait")],
    "video_swap": [("world_swap_auto", "world_swap_auto_status", "video_swap_auto", "video_swap_auto_status", "video_swap_plan", "asset_frames", "view_image"), ("studio_accepted_recipes", "franchise_resolve_shots"),
                   ("studio_readiness", "jobs_list"), ("render_enqueue", "run_wait"),
                   ("job_get", "storyboard_concat")],
    "continuation": [("studio_board", "jobs_list"), ("shot_lint", "studio_health"),
                     ("run_wait", "job_get"), ("storyboard_concat", "edl_plan")],
    "qc": [("job_get",), ("job_get",), ("studio_board",), ("run_step",)],
    "edit": [("montage_get",), ("jobs_list", "studio_board"),
             ("montage_update",), ("montage_render", "edl_render_status"),
             ("montage_get", "edl_export")],
    "gpu_recovery": [("studio_readiness", "jobs_list"), ("job_get",),
                     ("studio_api_get", "studio_api_schema"), ("run_wait",)],
}


def h3_language() -> str:
    """Section «0. Язык H3» of docs/guides/H3_PROMPT_GUIDE.md: what H3 executes from a job, measured 06.11.

    Every agent gets it with the playbook, so the rules live in one file and not in each chat's memory."""
    try:
        text = open(os.path.join(REPO, "docs/guides/H3_PROMPT_GUIDE.md"), encoding="utf-8").read()
    except OSError:
        return ""
    start = text.find("## 0. Язык H3")
    end = text.find("\n## 1.", start)
    return text[start:end].strip() if start >= 0 else ""


def t_studio_playbook(a):
    """Small, source-grounded routing; only an explicitly selected repo skill is read in full."""
    workflow = a.get("workflow", "scene")
    if workflow not in PLAYBOOKS:
        raise StudioError(f"Неизвестный маршрут: {workflow}")
    franchise = a.get("franchise", "")
    if FRANCHISE_ID and franchise:
        if task_franchise() != franchise:
            raise StudioError("Маршрут другой франшизы запрещён для этой задачи")
    names = list(PLAYBOOKS[workflow])
    if franchise and workflow in {"idea", "scene", "continuation", "qc", "edit"} and FRANCHISE_SKILLS.get(franchise):
        names.insert(0, FRANCHISE_SKILLS[franchise])
    selected = a.get("skill", "")
    if selected and selected not in names:
        raise StudioError("Навык не относится к выбранному маршруту")
    skills = [{"name": name, "path": f"skills/{name}/SKILL.md"} for name in names]
    result = {"workflow": workflow, "franchise": franchise, "steps": PLAYBOOK_STEPS[workflow],
              **({"franchise_note": "У этой франшизы нет своего стиль-навыка: манеру, тон и правила бери только из её "
                                    "паспорта и слов пользователя; навыки других франшиз не применять"}
                 if franchise and not FRANCHISE_SKILLS.get(franchise) else {}),
              "actions": [{"level": i + 1, "goal": goal, "tools": list(PLAYBOOK_TOOLS[workflow][i])}
                          for i, goal in enumerate(PLAYBOOK_STEPS[workflow])], "skills": skills,
              "execution_policy": {"routine": "Сначала Studio API/MCP и существующие скрипты; не вызывать модель для статуса, декода, хеша или очереди.",
                                   "helper": "run_delegate только для узкой творческой или диагностической задачи; read-only, low effort, без GPU.",
                                   "lead": "Основной агент принимает режиссёрское решение, проверяет результат помощника и фиксирует artifacts.",
                                   "spend": "GPU только после явного gate=gpu с точным числом действий; внешний платный API не запускать без отдельного разрешения."},
              "h3_language": h3_language(),
              "rules": ["Перед любым заданием H3 следуй h3_language (раздел 0 docs/guides/H3_PROMPT_GUIDE.md): склейки только «At», крупность через действия или движение, оптику не писать, эмоции отдельными фразами.",
                        "История и навык — источник метода, не доказательство текущего состояния: сверяй Studio/очередь/MP4.",
                        "Черновик, технический PASS и художественная приёмка — разные статусы.",
                        "Не запускай платный GPU повторно без сверки квитанций и разрешённого объёма."]}
    if selected:
        path = os.path.join(SKILL_ROOT, selected, "SKILL.md")
        with open(path, encoding="utf-8") as handle:
            result["skill_text"] = handle.read(18_001)[:18_000]
    if FRANCHISE_ID:
        cards = request("GET", f"/api/franchises/{q(FRANCHISE_ID)}/entities")
        result["approved_experience"] = [
            {"id": card.get("id"), "title": card.get("title"), "rule": short(card.get("body"), 500)}
            for card in cards if card.get("type") == "rule" and card.get("state") == "approved"
        ][:8]
        result["experience_policy"] = (
            "Проверенный урок записывай карточкой rule в своей франшизе: источник, наблюдение, "
            "изменение метода и проверка. Сначала state=draft; после продюсерской приёмки "
            "state=approved. Только approved попадает в следующие маршруты."
        )
    return result


class StudioError(RuntimeError):
    pass


def request(method: str, path: str, body=None, timeout: float = 60, raw: bool = False):
    if FRANCHISE_ID and path.startswith("/api/franchises/"):
        target = urllib.parse.unquote(path.split("/", 4)[3].split("?", 1)[0])
        if target != FRANCHISE_ID:
            raise StudioError("Задача ограничена франшизой " + FRANCHISE_ID)
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"} if data else {}
    if path.startswith("/api/compute/") and method != "GET":
        headers["Origin"] = STUDIO
    req = urllib.request.Request(STUDIO + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            return (payload, resp.headers.get("Content-Type", "")) if raw else json.loads(payload or b"null")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except (ValueError, AttributeError):
            pass
        raise StudioError(f"Studio HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise StudioError(f"Studio не отвечает на {STUDIO}: {exc}. Запусти H3 Studio.") from exc


def q(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")


# ---------- request schemas straight from the Studio's OpenAPI, so tools never drift from the API

_openapi = None


def openapi() -> dict:
    global _openapi
    if _openapi is None:
        _openapi = request("GET", "/openapi.json", timeout=10)
    return _openapi


def inline(schema, components, refs=0):
    """Resolve $ref in place. Only reference expansion is bounded (cycles); plain nesting may go deep —
    a depth limit once replaced `"type": "number"` deep inside the edit sheet with {} and Claude refused every tool."""
    if isinstance(schema, dict):
        if "$ref" in schema:
            if refs > 8:
                return {"type": "object"}
            return inline(components.get(schema["$ref"].rsplit("/", 1)[-1], {}), components, refs + 1)
        return {k: inline(v, components, refs) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [inline(v, components, refs) for v in schema]
    return schema


def body_schema(method: str, path: str) -> dict:
    try:
        spec = openapi()
        op = spec["paths"][path][method.lower()]
        schema = op["requestBody"]["content"]["application/json"]["schema"]
        return inline(schema, spec.get("components", {}).get("schemas", {}))
    except (StudioError, KeyError, TypeError):
        return {"type": "object", "description": "Studio is offline; call studio_api_schema later."}


# ---------- tools

def obj(props: dict, required=()) -> dict:
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


STR = {"type": "string"}
INT = {"type": "integer"}


def short(text, limit=400):
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f"… (+{len(text) - limit})"


def t_health(_):
    return {"studio": STUDIO, "health": request("GET", "/api/health"), "queue": request("GET", "/api/queue"),
            "gpu_activity": request("GET", "/api/gpu/activity")}


def t_readiness(_):
    """One bounded, read-only preflight; unknown GPU state is never ready."""
    snapshot = t_health({})
    health, queue, activity = snapshot["health"], snapshot["queue"], snapshot["gpu_activity"]
    render = request("GET", "/api/studio/render-options")
    jobs = request("GET", "/api/jobs")
    # Canvas commands still run in a Studio tab. This is independent of GPU readiness.
    try:
        tab_count = request("GET", "/api/ui/tabs").get("open")
        canvas_bridge = "available" if isinstance(tab_count, int) and tab_count > 0 else "unavailable"
    except StudioError:
        canvas_bridge = "unknown"
    counts = {}
    ambiguous = []
    for job in jobs:
        state = str(job.get("state") or "unknown")
        counts[state] = counts.get(state, 0) + 1
        if state == "needs_reconciliation":
            ambiguous.append(str(job.get("job_id") or ""))
    blockers = []
    if health.get("status") != "ok":
        blockers.append("studio_unhealthy")
    if not health.get("gpu_configured"):
        blockers.append("gpu_not_configured")
    elif health.get("gpu_reachable") is not True or activity.get("reachable") is not True:
        blockers.append("gpu_unreachable")
    elif render.get("gpu_reachable") is not True or (render.get("profiles") or {}).get("bf16", {}).get("available") is not True:
        blockers.append("bf16_render_unavailable")
    if queue.get("paused") is True:
        blockers.append("queue_paused")
    if ambiguous:
        blockers.append("ambiguous_receipts")
    return {"ready_for_gpu": not blockers, "blockers": blockers, "jobs_by_state": counts,
            "ambiguous_job_ids": ambiguous[:20], "ambiguous_count": len(ambiguous),
            "gpu_running": len(activity.get("running") or []),
            "gpu_pending": activity.get("pending_count", 0),
            "queue_paused": queue.get("paused"), "gpu_reachable": health.get("gpu_reachable") is True,
            "canvas_bridge": canvas_bridge,
            "canvas_note": "Команды холста требуют открытую вкладку Studio; при неизвестном результате не повторяй запись вслепую.",
            "bf16_available": (render.get("profiles") or {}).get("bf16", {}).get("available") is True,
            "note": "Готовность инфраструктуры не заменяет одобрение сюжета, проверку промпта и gate=gpu."}


def t_franchises(_):
    return request("GET", "/api/franchises")


def t_boards(a):
    return request("GET", f"/api/franchises/{q(a['franchise_id'])}/boards")


def t_board(a):
    return request("GET", f"/api/franchises/{q(a['franchise_id'])}/boards/{q(a['board_id'])}")


def t_entities(a):
    if FRANCHISE_ID and a['franchise_id'] != FRANCHISE_ID:
        raise ValueError("Агент привязан к другой франшизе")
    items = request("GET", f"/api/franchises/{q(a['franchise_id'])}/entities")
    if a.get("type"):
        items = [e for e in items if e.get("type") == a["type"]]
    if a.get("query"):
        needle = a["query"].lower()
        items = [e for e in items if needle in (e.get("title", "") + " " + e.get("body", "")).lower()]
    full = a.get("full_body", False)
    return [{**e, "body": e.get("body", "") if full else short(e.get("body"))} for e in items[: a.get("limit", 50)]]


def t_entity(a):
    if FRANCHISE_ID and a['franchise_id'] != FRANCHISE_ID:
        raise ValueError("Агент привязан к другой франшизе")
    return request("GET", f"/api/franchises/{q(a['franchise_id'])}/entities/{q(a['entity_id'])}")


def t_entity_create(a):
    body = {k: a[k] for k in ("type", "title", "body", "state", "asset_ids") if k in a}
    return request("POST", f"/api/franchises/{q(a['franchise_id'])}/entities", body)


def t_entity_update(a):
    body = {k: a[k] for k in ("type", "title", "body", "state", "asset_ids", "revision") if k in a}
    return request("PUT", f"/api/franchises/{q(a['franchise_id'])}/entities/{q(a['entity_id'])}", body)


def t_asset_import_image(a):
    """Hand a locally saved chat image to Studio; Studio validates and deduplicates the upload."""
    path = Path(a["path"]).expanduser().resolve()
    if not path.is_file():
        raise StudioError("Файл изображения не найден на этом Mac")
    if path.stat().st_size > 25 * 1024 * 1024:
        raise StudioError("Изображение превышает лимит Studio (25 МБ)")
    data = path.read_bytes()
    if not (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff") or
            (data.startswith(b"RIFF") and data[8:12] == b"WEBP")):
        raise StudioError("Нужен PNG, JPEG или WebP")
    boundary = "h3studioimageupload"
    filename = path.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
    payload = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
               'Content-Type: application/octet-stream\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
    role = urllib.parse.quote(a.get("role") or "Окружение")
    req = urllib.request.Request(STUDIO + "/api/assets?role=" + role, data=payload, method="POST",
                                 headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise StudioError(f"Studio HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:500]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise StudioError(f"Studio не отвечает: {exc}") from exc


def t_asset_import_chat_file(a):
    """Receive a host-injected ChatGPT file; never return its signed URL to the model."""
    file = a["image"]
    parsed = urllib.parse.urlsplit(file["download_url"])
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not (host == "oaiusercontent.com" or host.endswith(".oaiusercontent.com"))):
        raise StudioError("Нужна временная HTTPS-ссылка файла ChatGPT на oaiusercontent.com")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise StudioError("Ссылка перенаправляет запрос: передай свежий файл ChatGPT")

    try:
        with urllib.request.build_opener(NoRedirect).open(file["download_url"], timeout=60) as response:
            data = response.read(25 * 1024 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError):
        raise StudioError("Файл ChatGPT недоступен: передай его заново с новой временной ссылкой") from None
    if len(data) > 25 * 1024 * 1024:
        raise StudioError("Изображение превышает лимит Studio (25 МБ)")
    with tempfile.TemporaryDirectory(prefix="h3-chat-image-") as folder:
        name = Path(str(file.get("file_name") or "chat-image.png")).name
        path = Path(folder) / (name if name not in (".", "..", "") else "chat-image.png")
        path.write_bytes(data)
        return t_asset_import_image({"path": str(path), "role": a.get("role") or "Окружение"})


def asset_memberships(franchise_id: str) -> dict[str, set[str]]:
    """asset_id → ids of the franchises whose passport roles or entity cards list it."""
    memberships: dict[str, set[str]] = {}
    franchises = request("GET", "/api/franchises")
    if not any(franchise.get("id") == franchise_id for franchise in franchises):
        raise ValueError("Франшиза не найдена")
    for franchise in franchises:
        fid = franchise["id"]
        entities = request("GET", f"/api/franchises/{q(fid)}/entities")
        profile = request("GET", f"/api/franchises/{q(fid)}/profile")
        linked = {asset_id for entity in entities for asset_id in entity.get("asset_ids", [])}
        for role in profile.get("roles", []):
            linked.update(asset.get("asset_id") for asset in role.get("assets", []))
        for asset_id in linked:
            memberships.setdefault(asset_id, set()).add(fid)
    return memberships


def t_assets(a):
    items = request("GET", "/api/assets")
    franchise_id = FRANCHISE_ID or a.get("franchise_id", "")
    if FRANCHISE_ID and a.get("franchise_id") and a["franchise_id"] != FRANCHISE_ID:
        raise ValueError("Агент привязан к другой франшизе")
    if franchise_id:
        memberships = asset_memberships(franchise_id)
        # Only assets of exactly this franchise: shared files are a data mix-up to resolve in the franchise cards
        # (one hero's face filed under two franchises), not to hand to every owner.
        items = [item for item in items if memberships.get(item.get("asset_id")) == {franchise_id}]
    if a.get("kind"):
        items = [x for x in items if x.get("kind") == a["kind"]]
    if a.get("role"):
        items = [x for x in items if x.get("role") == a["role"]]
    if a.get("query"):
        needle = a["query"].lower()
        items = [x for x in items if needle in " ".join(str(x.get(k) or "") for k in
                                                         ("original_name", "role", "owner", "asset_id")).lower()]
    items = sorted(items, key=lambda x: x.get("created_at") or "", reverse=True)
    keep = ("asset_id", "kind", "role", "owner", "original_name", "mime", "size", "path", "created_at")
    return {"total": len(items), "assets": [{k: x.get(k) for k in keep if k in x} for x in items[: a.get("limit", 40)]]}


def t_motion_reference(a):
    if FRANCHISE_ID and a["asset_id"] not in {item["asset_id"] for item in t_assets({"limit": 100000})["assets"]}:
        raise StudioError("Видео не принадлежит франшизе этого чата")
    return request("POST", f"/api/assets/{q(a['asset_id'])}/motion-reference", {"control": a.get("control", "depth")}, timeout=930)


def t_jobs(a):
    jobs = request("GET", "/api/jobs")
    if a.get("state"):
        jobs = [j for j in jobs if j.get("state") == a["state"]]
    keep = ("job_id", "state", "created_at", "updated_at", "error", "progress", "remote_job_id")
    out = [{**{k: j.get(k) for k in keep if k in j}, "prompt": short(j.get("original_prompt") or j.get("prompt"), 300)}
           for j in jobs[: a.get("limit", 20)]]
    return {"total": len(jobs), "jobs": out}


def t_job(a):
    return request("GET", f"/api/jobs/{q(a['job_id'])}")


def t_job_reproducibility(a):
    return request("GET", f"/api/jobs/{q(a['job_id'])}/reproducibility")


def franchise_key(name: str) -> str:
    """Key of a franchise from its Studio name: upper-case words joined by «_» («My Show · Pilot» → MY_SHOW_PILOT)."""
    import re as _re
    return "_".join(_re.findall(r"[A-ZА-ЯЁ0-9]+", str(name or "").upper()))


def task_franchise() -> str:
    names = {item.get("id"): item.get("name", "") for item in request("GET", "/api/franchises")}
    return franchise_key(names.get(FRANCHISE_ID, ""))


def t_accepted_recipes(a):
    items = request("GET", "/api/recipes/accepted")
    return items


def t_accepted_recipe(a):
    available = t_accepted_recipes({})
    if not any(item.get("id") == a["recipe_id"] for item in available):
        raise StudioError("Принятый рецепт не найден" if available else
                          "Принятых рецептов пока нет: замена и смена мира по видео работают со встроенными настройками")
    return request("GET", f"/api/recipes/accepted/{q(a['recipe_id'])}")


def t_verify_recipe(a):
    # Reuse franchise authorization before accessing the stored recipe files.
    t_accepted_recipe(a)
    return request("GET", f"/api/recipes/accepted/{q(a['recipe_id'])}/verify")


RUN_ID = os.environ.get("H3_RUN_ID", "")  # set for a «Задача агента» chat (local_studio/orchestrator.py)
CHAT_ID = os.environ.get("H3_CHAT_ID", "")  # the Studio agent chat this server serves


def t_story_approval(a):
    if not CHAT_ID:
        raise StudioError("Записать одобрение можно только из чата с агентом в Studio")
    return request("POST", "/api/decisions/story", {**a, "chat_id": CHAT_ID})


def gpu_gate():
    """Consume one approved GPU action atomically; a task cannot reuse an old «Да» forever."""
    if RUN_ID:
        request("POST", f"/api/runs/{q(RUN_ID)}/gate/claim?kind=gpu", {})


def run_id(a) -> str:
    value = a.get("run_id") or RUN_ID
    if not value:
        raise StudioError("Нет run_id: инструменты run_* работают только в «Задаче агента»")
    return value


def run_ack(run: dict) -> dict:
    """The agent already knows what it just sent: echo only the task state, not its log, asks and goal again.
    Every byte here is resent with each later tool call of the session."""
    if not isinstance(run, dict) or "steps" not in run:
        return run
    asks = run.get("asks") or []
    return {"status": run.get("status"),
            "steps": [f"{s.get('id')}:{s.get('status')}" for s in run.get("steps") or []],
            "open_ask": next((a.get("question", "")[:160] for a in asks if not a.get("answer")), None),
            "gates": run.get("gates") or {}, "wait": bool(run.get("wait")),
            **{k: run[k] for k in ("already_authorized", "remaining", "note", "final_artifact") if k in run}}


def run_post(a, action: str):
    return run_ack(request("POST", f"/api/runs/{q(run_id(a))}/{action}", {k: v for k, v in a.items() if k != "run_id"}))


def t_run_plan(a):
    return run_ack(request("POST", f"/api/runs/{q(run_id(a))}/plan", {"steps": a["steps"]}))


def t_run_step(a):
    return run_post(a, "step")


def t_run_ask(a):
    return run_post(a, "ask")


def t_run_wait(a):
    return run_post(a, "wait")


def t_run_done(a):
    return run_post(a, "done")


def t_production_review(a):
    kind = a.get("artifact_kind", "job")
    return request("POST", f"/api/production/artifacts/{q(kind)}/{q(a['job_id'])}/reviews",
                   {k: v for k, v in a.items() if k not in {"job_id", "artifact_kind"}})


def t_production_spec(a):
    payload = a["payload"]
    if FRANCHISE_ID and payload.get("franchise_id") != FRANCHISE_ID:
        raise StudioError("Постановка должна принадлежать франшизе задачи")
    return request("POST", "/api/production/spec/validate", payload)


def t_visual_profiles(a):
    if a.get("profile_id"):
        return request("GET", f"/api/visual-profiles/{q(a['profile_id'])}")
    return [{"id": item["visual_profile"]["id"], "label": item["visual_profile"]["label"],
             "status": item["status"], "sources": item["visual_profile"]["sources"]}
            for item in request("GET", "/api/visual-profiles")]


def t_run_delegate(a):
    return request("POST", f"/api/runs/{q(run_id(a))}/delegate", {k: v for k, v in a.items() if k != "run_id"},
                   timeout=960)


def t_render_enqueue(a):
    if a.get("story_approved") is not True:
        raise StudioError("Рендер серии только после одобренного пользователем сюжета (docs/DECISIONS.md). "
                          "Передай story_approved: true, только если это так или пользователь прямо попросил этот рендер.")
    spec = a["payload"].get("production_spec")
    if spec is not None and FRANCHISE_ID and spec.get("franchise_id") != FRANCHISE_ID:
        raise StudioError("Постановка задания принадлежит другой франшизе")
    if FRANCHISE_ID:
        # A franchise chat renders only its own (or unowned) files: audit 05.11 found no check on asset_ids here.
        memberships = asset_memberships(FRANCHISE_ID)
        foreign = [i for i in a["payload"].get("asset_ids", []) if memberships.get(i) and FRANCHISE_ID not in memberships[i]]
        if foreign:
            raise StudioError("В задании файлы другой франшизы: " + ", ".join(foreign[:5]))
    gpu_gate()
    payload = dict(a["payload"])
    # The agent's prompt is final: no Studio/GPU generic blocks in front of it (A/B 05.11, job 3528f6c8,
    # docs/reference/H3_ENCODER_RESEARCH.md). An explicit settings.prompt_blocks="studio" keeps the old route.
    payload["settings"] = {"prompt_blocks": "none", **(payload.get("settings") or {})}
    return request("POST", "/api/jobs", payload)


def t_job_cancel(a):
    return request("POST", f"/api/jobs/{q(a['job_id'])}/cancel", {})


def t_concat(a):
    body = {"job_ids": a["job_ids"], **{k: a[k] for k in ("trim_frames", "tail_frames") if a.get(k) is not None}}
    return request("POST", "/api/storyboard/concat", body, timeout=600)


def t_job_snap_zoom(a):
    return request("POST", f"/api/jobs/{q(a['job_id'])}/snap-zoom", {}, timeout=900)


def t_video_swap_plan(a):
    if FRANCHISE_ID and a["franchise_id"] != FRANCHISE_ID:
        raise StudioError("Этот чат ведёт другую франшизу")
    return request("POST", "/api/video-swap/plan", {k: a[k] for k in ("asset_id", "franchise_id", "pairs", "audio", "route", "environment_asset_id", "source_short_edge") if k in a},
                   timeout=900)


def t_video_swap_auto(a):
    if a.get("story_approved") is not True:
        raise StudioError("Замена ставит рендеры: передай story_approved: true, только если пользователь прямо попросил "
                          "эту замену или одобрил её сюжет.")
    if FRANCHISE_ID and a["franchise_id"] != FRANCHISE_ID:
        raise StudioError("Этот чат ведёт другую франшизу")
    if a.get("enqueue", False):
        gpu_gate()
    keys = ("asset_id", "franchise_id", "pairs", "audio", "route", "environment_asset_id", "provider", "enqueue",
            "mask", "ab_user_request", "source_short_edge")
    return request("POST", "/api/video-swap/auto", {k: a[k] for k in keys if k in a})


def t_video_swap_auto_status(a):
    return request("GET", f"/api/video-swap/auto/{q(a['run_id'])}")


def t_world_swap_auto(a):
    if a.get("story_approved") is not True:
        raise StudioError("Смена мира ставит рендер: передай story_approved: true, только если пользователь прямо попросил "
                          "эту замену или одобрил её сюжет.")
    if FRANCHISE_ID and a["franchise_id"] != FRANCHISE_ID:
        raise StudioError("Этот чат ведёт другую франшизу")
    if a.get("enqueue", False):
        gpu_gate()
    keys = ("asset_id", "franchise_id", "pairs", "environment_asset_id", "part", "provider", "enqueue")
    return request("POST", "/api/world-swap/auto", {k: a[k] for k in keys if k in a})


def t_world_swap_auto_status(a):
    return request("GET", f"/api/world-swap/auto/{q(a['run_id'])}")


def t_shot_lint(a):
    return request("POST", "/api/shot-lint", a)


def t_edl_from_shots(a):
    return request("POST", "/api/edl/from-shots", a)


def t_edl_plan(a):
    return request("POST", "/api/edl/plan", a, timeout=120)


def t_edl_export(a):
    return request("POST", "/api/edl/exports", a, timeout=900)


def t_edl_export_resolve(a):
    return request("POST", f"/api/edl/exports/{q(a['export_id'])}/resolve", {}, timeout=240)


def t_edl_toolkit(a):
    return request("GET", "/api/edl/toolkit")


def t_edl_render(a):
    return request("POST", "/api/edl/renders", a, timeout=120)


def t_edl_status(a):
    return request("GET", f"/api/edl/renders/{q(a['render_id'])}")


def montage_id(a) -> str:
    """The montage the tool means: the one asked for, else the one open on the «Монтаж» page."""
    chosen = a.get("montage_id") or request("GET", "/api/montages").get("active")
    if not chosen:
        raise StudioError("Монтажей ещё нет — создай montage_create или открой страницу «Монтаж»")
    return chosen


def t_freecut_command(a):
    import time
    def call(path, body=None):
        req = urllib.request.Request("http://127.0.0.1:18189/bridge/" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            raise StudioError(exc.read().decode()) from exc
    _, queued = call("command", a)
    for _ in range(40):
        code, result = call("result/" + queued["id"])
        if code == 200:
            return result
        time.sleep(.5)
    return {"pending": True, "command_id": queued["id"],
            "warning": "Do not resubmit an edit: its result is not yet known."}


def t_montage_list(a):
    return request("GET", "/api/montages")


def t_montage_get(a):
    return request("GET", f"/api/montages/{q(montage_id(a))}")


def t_montage_create(a):
    return request("POST", "/api/montages", {"name": a.get("name") or "Монтаж агента", "edl": a.get("edl")})


def t_montage_update(a):
    chosen = montage_id(a)
    project = request("GET", f"/api/montages/{q(chosen)}")["project"]
    project["edl"] = a["edl"]
    if a.get("name"):
        project["name"] = a["name"]
    return request("PUT", f"/api/montages/{q(chosen)}", {"project": project, "by": "agent", "note": a.get("note", "")})


def t_montage_edit(a):
    return request("POST", f"/api/montages/{q(montage_id(a))}/edit",
                   {"revision": a["revision"], "ops": a["ops"], "note": a["note"]})


def t_montage_render(a):
    edl = request("GET", f"/api/montages/{q(montage_id(a))}")["project"]["edl"]
    if not edl.get("clips"):
        raise StudioError("В монтаже нет клипов")
    return request("POST", f"/api/edl/renders?draft={'1' if a.get('draft', True) else '0'}", edl, timeout=120)


def t_profile_get(a):
    return request("GET", f"/api/franchises/{q(a['franchise_id'])}/profile")


def t_profile_update(a):
    return request("PUT", f"/api/franchises/{q(a['franchise_id'])}/profile", a["profile"])


def idea_path(a):
    if not a.get("franchise_id") or not a.get("idea_id"):
        raise StudioError("Пакет идеи требует franchise_id и idea_id")
    if FRANCHISE_ID and a["franchise_id"] != FRANCHISE_ID:
        raise StudioError("Идея другой франшизы недоступна этой задаче")
    return f"/api/franchises/{q(a['franchise_id'])}/ideas/{q(a['idea_id'])}/pack"


def t_idea_pack_get(a):
    return request("GET", idea_path(a))


def t_idea_pack_build(a):
    body = {key: a[key] for key in ("expected_revision", "mode", "idea_assets", "extra_assets", "add_names") if key in a}
    return request("POST", idea_path(a), body)


def t_resolve_shots(a):
    return request("POST", f"/api/franchises/{q(a['franchise_id'])}/resolve-shots", {"shots": a["shots"]})


def t_franchise_lint(a):
    return request("POST", f"/api/franchises/{q(a['franchise_id'])}/shot-lint", a["plan"])


def t_franchise_edl(a):
    return request("POST", f"/api/franchises/{q(a['franchise_id'])}/edl-from-shots", a["request"])


def t_brain_status(_):
    return request("GET", "/api/brain/status")


def t_brain_ask(a):
    body = {k: a[k] for k in ("provider", "prompt", "schema", "read_repo", "model", "timeout_s") if k in a}
    return request("POST", "/api/brain/ask", body, timeout=body.get("timeout_s", 600) + 30)


def t_api_get(a):
    path = a["path"]
    if not path.startswith("/api/") or ".." in path:
        raise StudioError("Только GET-пути вида /api/…")
    return request("GET", path)


def t_api_schema(a):
    return body_schema(a.get("method", "POST"), a["path"])


IMAGE_PATHS = {"asset": "/api/assets/{}/thumbnail", "job_poster": "/api/jobs/{}/poster"}


def t_job_frames(a):
    got = request("GET", f"/api/jobs/{q(a['job_id'])}/frames?count={int(a.get('count') or 8)}", timeout=120)
    facts = {key: got[key] for key in ("job_id", "technical", "frame_times_s", "hard_cuts_s")}
    facts["review_scope"] = "Sampled images plus technical decode. Does not prove continuous motion or audio listening; do not report full creative acceptance from this sheet alone."
    return {"_image": got["sheet_jpeg_base64"], "_mime": "image/jpeg", "_text": json.dumps(facts, ensure_ascii=False)}


def t_scene_run(a):
    """The scene's build on the Mac (local_studio/scene_runs.py): it goes on without the tab and without you."""
    scene = a.get("scene_id") or SCENE_ID
    if not scene:
        return request("GET", "/api/scene-runs")
    if SCENE_ID and scene != SCENE_ID:
        raise ValueError("Этот чат ведёт только свою сцену")
    action = a.get("action", "status")
    if action == "status":
        run = request("GET", f"/api/scene-runs/{q(scene)}")
        run["cuts"] = [{key: cut.get(key) for key in ("id", "shotJobId", "startFrame", "frameAcceptance", "keyframeJobIds")}
                       for cut in run.get("cuts", [])]
        return run
    return request("POST", f"/api/scene-runs/{q(scene)}/{action}", {})


def t_asset_frames(a):
    params = {"count": int(a.get("count") or 12)}
    if a.get("start_s") is not None:
        params["start_s"] = a["start_s"]
    if a.get("end_s") is not None:
        params["end_s"] = a["end_s"]
    got = request("GET", f"/api/assets/{q(a['asset_id'])}/frames?{urllib.parse.urlencode(params)}", timeout=180)
    facts = {key: got[key] for key in ("asset_id", "name", "duration_s", "has_sound", "frame_times_s", "hard_cuts_s")}
    facts["reviewed_range_s"] = got.get("reviewed_range_s", [0, got["duration_s"]])
    return {"_image": got["sheet_jpeg_base64"], "_mime": "image/jpeg", "_text": json.dumps(facts, ensure_ascii=False)}


def t_view_image(a):
    data, mime = request("GET", IMAGE_PATHS[a["kind"]].format(q(a["id"])), raw=True)
    if not mime.startswith("image/"):
        raise StudioError(f"Studio вернула {mime or 'не картинку'}")
    return {"_image": base64.b64encode(data).decode(), "_mime": mime.split(";")[0]}


# Roles the «Создать видео» form accepts per file kind (preview/h3-director/app-shared.mjs).
COMPOSER_ROLES = {
    "image": ["Лицо героя", "Персонаж целиком", "Костюм", "Предмет", "Стиль", "Свет", "Цвет", "Окружение",
              "Кадр сцены", "Композиция"],
    "video": ["Исходное видео", "Движение", "Движение и камера", "Актёрская игра", "Камера", "Ритм сцены", "Продолжение"],
    "audio": ["Голос", "Музыка", "Звуковой эффект", "Атмосфера"],
}


def t_composer_draft(a):
    """Checked here, applied by the user: the Studio page shows the draft with «Применить в форму»."""
    problems = check_references(a.get("references", []))
    if a.get("recipe_id"):
        try:
            t_accepted_recipe({"recipe_id": a["recipe_id"]})
        except StudioError as exc:
            problems.append(f"рецепт {a['recipe_id']}: {exc}")
    for ref in a.get("references", []):
        if ref.get("performers") and ref.get("role") != "Исходное видео":
            problems.append("исполнителей заменяют только в видео с ролью «Исходное видео»")
    problems.extend(check_swap_draft(a))
    if problems:
        raise StudioError("Черновик не показан: " + "; ".join(problems))
    return {"shown": True, "note": "Черновик показан карточкой под ответом; в форму он попадёт, только когда "
                                   "пользователь нажмёт «Применить в форму». Рендер не поставлен."}


def check_swap_draft(draft: dict) -> list[str]:
    """Prevent an agent's source-video swap card from silently losing a hero, costume or soundtrack."""
    refs = draft.get("references") or []
    sources = [ref for ref in refs if ref.get("role") == "Исходное видео"]
    if not sources:
        return []
    problems = []
    if len(sources) != 1:
        problems.append("для замены укажи одно исходное видео на эту часть")
    pairs = [pair for source in sources for pair in (source.get("performers") or [])]
    if not pairs:
        problems.append("укажи пары: какой исполнитель исходного видео становится каким героем")
    owners = [pair.get("owner", "").strip() for pair in pairs]
    if not all(owners) or len(set(owners)) != len(owners):
        problems.append("у каждой пары должен быть свой герой")
    if any(not pair.get("performer", "").strip() for pair in pairs):
        problems.append("опиши каждого исходного исполнителя по месту и действию (приметы — только чтобы различить двоих)")
    pictured = {ref.get("owner", "").strip() for ref in refs
                if ref.get("role") in {"Лицо героя", "Персонаж целиком", "Костюм"} and ref.get("owner")}
    for extra in sorted(pictured - set(owners)):
        problems.append(f"{extra}: нет своего исходного исполнителя; добавлять героя рядом с заменой нельзя")
    for owner in owners:
        images = [ref for ref in refs if ref.get("owner") == owner and ref.get("role") == "Персонаж целиком"]
        if len(images) != 1:
            problems.append(f"для {owner} нужно ровно одно фото «Персонаж целиком»")
        if any(ref.get("owner") == owner and ref.get("role") in {"Лицо героя", "Костюм"} for ref in refs):
            problems.append(f"для {owner} не смешивай полное фото с отдельными лицом и костюмом")
    if draft.get("mode") != "replace":
        problems.append("для замены исполнителей выбери режим replace")
    if draft.get("use_video_audio") is not True:
        problems.append("для замены сохрани исходный звук: use_video_audio=true")
    if not draft.get("recipe_id"):
        if (draft.get("steps") or 0) < 24 or draft.get("cache_mode") != "safe" or draft.get("ref_image_size") != "max":
            problems.append("для замены нужны 24 шага, cache safe и ref_image_size max")
    if "[Shot " not in draft.get("prompt", ""):
        problems.append("план появлений исполнителей запиши по таймкодам [Shot N] в промпте")
    return problems


# Formats the GPU API accepts (app/contract.py ASPECTS, the list /api/capabilities reports); 9:16 stays the Studio default.
if REPO not in sys.path:
    sys.path.insert(0, REPO)
from app.contract import ASPECTS  # noqa: E402
ASPECT = {"type": "string", "enum": ["9:16", *[a for a in ASPECTS if a != "9:16"]],
          "description": "Video format; 9:16 (vertical) unless the source or the user asks for another."}
SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "director_schema.json")
PRESETS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "preview", "h3-director", "director-presets.mjs")
PLACE_ROLES = ("Окружение", "Кадр сцены", "Композиция")  # storyboard-model.mjs PLACE_ROLES: the keyframe's space
DIRECTOR_CATEGORIES = ["camera", "lens", "shot", "movement", "focus", "lighting", "color", "style", "acting"]
SCENE_MODES = ["realistic", "animation3d", "animation2d", "replace"]


def shot_options(key: str, category: str = "shot") -> list[str]:
    with open(SCHEMA_FILE, encoding="utf-8") as handle:
        field = next(f for f in json.load(handle)["categories"][category] if f["key"] == key)
    return [o["value"] for o in field.get("options", [])]


CAMERA_MOTIONS = shot_options("movement", "movement")


@functools.lru_cache(maxsize=1)
def director_catalog() -> dict:
    """Built-in director presets per category, read from the Studio's own director-presets.mjs (node runs it)."""
    node = shutil.which("node") or next((path for path in (os.path.expanduser("~/.local/bin/node"), "/opt/homebrew/bin/node",
                                                           "/usr/local/bin/node") if os.path.exists(path)), None)
    if not node:
        raise StudioError("node не найден: каталог пресетов режиссуры недоступен")
    script = ("const m = await import(process.argv[1]); console.log(JSON.stringify(m.DIRECTOR_PRESETS.map((p) => "
              "({id: p.id, category: p.category, name: p.name, about: p.descriptionEn || p.description || p.prompt}))))")
    uri = "file://" + urllib.parse.quote(os.path.realpath(PRESETS_FILE))
    done = subprocess.run([node, "--input-type=module", "-e", script, uri], capture_output=True, text=True, timeout=30)
    if done.returncode:
        raise StudioError(f"Каталог пресетов режиссуры не прочитан: {done.stderr.strip()[-400:]}")
    catalog: dict = {category: [] for category in DIRECTOR_CATEGORIES}
    for preset in json.loads(done.stdout):
        catalog.setdefault(preset["category"], []).append(
            {"id": preset["id"], "name": preset["name"], "about": short(preset.get("about") or "", 160)})
    return catalog


def unknown_presets(scene: dict) -> list[str]:
    known = {preset["id"] for presets in director_catalog().values() for preset in presets}
    sets = [("сцена", scene.get("director"))] + [(f"шот {i}", shot.get("director")) for i, shot in enumerate(scene["shots"], 1)]
    return [f"{where}: {category}={preset_id}" for where, chosen in sets for category, preset_id in (chosen or {}).items()
            if preset_id not in known or not preset_id.startswith(f"{category}.")]


def t_director_presets(a):
    catalog = director_catalog()
    return {category: catalog.get(category, []) for category in (a.get("categories") or DIRECTOR_CATEGORIES)}


def check_references(refs) -> list[str]:
    assets = {x["asset_id"]: x for x in request("GET", "/api/assets")}
    problems = []
    for ref in refs:
        asset = assets.get(ref.get("asset_id"))
        if not asset:
            problems.append(f"файла {ref.get('asset_id')} нет в библиотеке")
        elif ref.get("role") not in COMPOSER_ROLES.get(asset.get("kind"), []):
            problems.append(f"роль «{ref.get('role')}» не подходит файлу {asset.get('kind')} №{asset.get('number')}; "
                            f"можно: {', '.join(COMPOSER_ROLES.get(asset.get('kind'), []))}")
    return problems


def check_franchise_characters(franchise_id: str, refs: list[dict]) -> list[str]:
    """Character pictures must come from the selected passport and keep their declared owner."""
    character_refs = [ref for ref in refs if ref.get("role") in {"Лицо героя", "Персонаж целиком", "Костюм"}]
    if not franchise_id or not character_refs:
        return []
    profile = request("GET", f"/api/franchises/{q(franchise_id)}/profile")
    entities = request("GET", f"/api/franchises/{q(franchise_id)}/entities")
    allowed: dict[str, set[str]] = {}
    for role in profile.get("roles", []):
        if role.get("kind") not in {"character", "costume"}:
            continue
        owner = role.get("owner") or role.get("name")
        for asset in role.get("assets", []):
            allowed.setdefault(asset.get("asset_id", ""), set()).add(owner.casefold())
    for entity in entities:
        if entity.get("type") == "character":
            head = re.split(r"\s+[·—|-]\s+|\s*\(", str(entity.get("title", "")), maxsplit=1)[0].strip()
            cyrillic = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
            latin = ("a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya")
            aliases = {head.casefold(), "".join(dict(zip(cyrillic, latin)).get(c, c) for c in head.casefold())}
            for asset_id in entity.get("asset_ids", []):
                allowed.setdefault(asset_id, set()).update(aliases)
    problems = []
    for ref in character_refs:
        asset_id, owner = ref.get("asset_id", ""), str(ref.get("owner", "")).strip()
        if not owner:
            problems.append(f"фото героя {asset_id}: укажи владельца из паспорта франшизы")
        elif owner.casefold() not in allowed.get(asset_id, set()):
            problems.append(f"фото героя {asset_id} не привязано к {owner} в паспорте этой франшизы")
    return problems


SCENE_ACTIONS = {"board", "apply_draft", "apply_scene", "build_scene", "stop_build", "reset_scene", "set_start_frame"}


def ui(action: str, payload=None, seconds: float = 45):
    """Hand a command to the open Studio tab (local_studio/ui_bridge.py) and wait for its result."""
    import time
    if SCENE_ID and action in SCENE_ACTIONS:
        payload = {**(payload or {}), "scene_id": SCENE_ID}
    elif SCENE_ID and action in {"select_scene", "new_scene"}:
        raise StudioError("Этот чат ведёт одну сцену; другая сцена — это другой чат агента")
    command = request("POST", "/api/ui/commands", {"action": action, "payload": payload or {}, "source": "agent"})
    deadline = time.monotonic() + seconds
    while True:
        state = request("GET", f"/api/ui/commands/{command['id']}?wait=15", timeout=30)
        if state["state"] == "done":
            return state["result"]
        if state["state"] == "failed":
            raise StudioError(f"Studio: {state['error']}")
        if state["state"] == "needs_reconciliation":
            raise StudioError(f"Команда {command['id']} требует сверки: {state['error']}. Прочитай studio_board и jobs_list; не повторяй действие автоматически.")
        if time.monotonic() > deadline or (state["state"] == "pending" and not request("GET", "/api/ui/tabs")["open"]):
            request("POST", f"/api/ui/commands/{command['id']}/cancel", {})
            if state["state"] == "pending":
                raise StudioError("Studio не открыта: попроси пользователя открыть Studio (localhost:18187, «Сюжет») и повтори.")
            raise StudioError(f"Команда {command['id']} не подтвердилась; сверь studio_board и jobs_list перед повтором.")


def t_studio_board(a):
    state = ui("board")
    if FRANCHISE_ID and state.get("franchise_id") and state["franchise_id"] != FRANCHISE_ID:
        raise StudioError("Открытый холст принадлежит другой франшизе")
    return state


def t_studio_shot_snapshot(a):
    scene_id = a.get("scene_id") or SCENE_ID
    if SCENE_ID and scene_id != SCENE_ID:
        raise StudioError("Этот чат ведёт другую сцену")
    draft = request("GET", "/api/draft")
    project = draft.get("project") or {}
    scene = next((item for item in project.get("scenes", []) if item.get("id") == scene_id), None)
    if scene is None:
        raise StudioError("Сцена не найдена в сохранённом черновике")
    owner = (scene.get("franchiseSource") or {}).get("franchise_id") or (scene.get("sceneDirector") or {}).get("agentScene", {}).get("franchise_id")
    if FRANCHISE_ID and owner != FRANCHISE_ID:
        raise StudioError("Сцена не принадлежит франшизе этого чата")
    shot = next((item for item in scene.get("cuts", []) if item.get("id") == a["shot_id"]), None)
    if shot is None:
        raise StudioError("Шот не найден в сцене")
    return {"scene_id": scene_id, "shot_id": a["shot_id"], "revision": draft["revision"],
            "prompt": shot.get("prompt", ""), "motivation": shot.get("motivation", ""),
            "has_work": any(shot.get(key) for key in ("shotJobId", "keyframeJobId", "keyframeJobIds", "takes",
                                                       "frameAcceptance"))}  # draft_shot.PROTECTED_WORK


def t_studio_production_state(a):
    owner = FRANCHISE_ID or a.get("franchise_id") or ""
    if FRANCHISE_ID and a.get("franchise_id") not in {None, "", FRANCHISE_ID}:
        raise StudioError("Этот чат ведёт другую франшизу")
    return request("GET", "/api/draft/production" + ("?franchise_id=" + q(owner) if owner else ""))


def t_studio_scene_readiness(a):
    scene_id = a.get("scene_id") or SCENE_ID
    if not scene_id or (SCENE_ID and scene_id != SCENE_ID):
        raise StudioError("Этот чат ведёт другую сцену или ID сцены не задан")
    result = request("GET", f"/api/draft/scenes/{q(scene_id)}/readiness")
    if FRANCHISE_ID and result.get("franchise_id") != FRANCHISE_ID:
        raise StudioError("Сцена не принадлежит франшизе этого чата")
    return result


def t_scene_staging_check(a):
    scene_id = a.get("scene_id") or SCENE_ID or ui("board").get("active_scene")
    if not scene_id or (SCENE_ID and scene_id != SCENE_ID):
        raise StudioError("Этот чат ведёт другую сцену или ID сцены не задан")
    return request("GET", f"/api/previs/scene/plan/{q(scene_id)}")


def t_studio_patch_shot(a):
    snapshot = t_studio_shot_snapshot(a)
    patch = {key: a[key] for key in ("prompt", "motivation") if key in a}
    result = request("POST", f"/api/draft/scenes/{q(snapshot['scene_id'])}/shots/{q(a['shot_id'])}/proposals",
                     {"event_id": a["event_id"], "agent_role": "shot_director",
                      "base_revision": a["base_revision"], "patch": patch})
    if RUN_ID:
        request("POST", f"/api/runs/{q(RUN_ID)}/events",
                {"event_id": a["event_id"], "kind": "shot_updated", "scene_id": snapshot["scene_id"],
                 "shot_id": a["shot_id"], "revision": result["revision"]})
    return result


def t_studio_set_start_frame(a):
    snapshot = t_studio_shot_snapshot(a)
    if snapshot["has_work"]:
        raise StudioError("В шоте уже есть работа: сверь кадры и дубли до замены")
    return ui("set_start_frame", {"scene_id": snapshot["scene_id"], "shot_id": a["shot_id"],
                                  "asset_id": a["asset_id"]})


def t_studio_image_context(a):
    snapshot = t_studio_shot_snapshot(a)
    return request("POST", "/api/chat-images/context", {"scene_id": snapshot["scene_id"],
                   "shot_id": a["shot_id"], "purpose": a.get("purpose", "frame"), "notes": a.get("notes", "")})


def image_request_scope(rid):
    row = request("GET", f"/api/chat-images/{q(rid)}")
    if SCENE_ID and row["scene_id"] != SCENE_ID:
        raise StudioError("Запрос другой сцены запрещён")
    if FRANCHISE_ID and row["franchise_id"] != FRANCHISE_ID:
        raise StudioError("Запрос другой франшизы запрещён")
    return row


def t_image_requests(a):
    params = {"scene_id": SCENE_ID or a.get("scene_id", ""), "franchise_id": FRANCHISE_ID or ""}
    return request("GET", "/api/chat-images?" + urllib.parse.urlencode(params))


def t_image_request_claim(a):
    image_request_scope(a["request_id"])
    return request("POST", f"/api/chat-images/{q(a['request_id'])}/claim", {"claim_id": a["claim_id"]})


def t_image_request_complete(a):
    row = image_request_scope(a["request_id"])
    if row["state"] not in {"claimed", "completed", "stale"}:
        raise StudioError("Запрос не взят чатом; не загружай результат без claim")
    asset = t_asset_import_chat_file({"image": a["image"], "role": "Окружение" if row["purpose"] == "fantasy_world" else "Кадр сцены"})
    return request("POST", f"/api/chat-images/{q(a['request_id'])}/result",
                   {"claim_token": a["claim_token"], "asset_id": asset["asset_id"]})


def t_image_request_fail(a):
    image_request_scope(a["request_id"])
    return request("POST", f"/api/chat-images/{q(a['request_id'])}/result",
                   {"claim_token": a["claim_token"], "error": a["error"]})


def t_studio_apply_draft(a):
    checked = t_composer_draft(a)  # same checks as the card
    applied = ui("apply_draft", {"draft": a}, 90)
    return {**checked, "applied": applied, "note": "Форма «Создать видео» заполнена; рендер не поставлен — "
                                                   "его запускает пользователь (или render_enqueue по его «да»)."}


def t_studio_apply_scene(a):
    checked = t_scene_draft(a)  # same rules and lint as the card
    applied = ui("apply_scene", {"scene": a}, 90)
    if not applied.get("verification", {}).get("ok"):
        return {"applied": True, "verified": False, "differences": applied.get("verification", {}).get("differences", []),
                "board": applied.get("board"), "next": "Сверь холст; не применяй сцену повторно вслепую."}
    return {"lint": checked.get("review", []), **applied}


def t_studio_build(a):
    if FRANCHISE_ID and t_studio_board({}).get("franchise_id") != FRANCHISE_ID:
        raise StudioError("Для сборки открой сцену своей франшизы")
    gpu_gate()
    return ui("build_scene", {}, 120)


def t_studio_stop(a):
    return ui("stop_build")


def t_studio_reset(a):
    return ui("reset_scene")


def t_studio_select(a):
    return ui("select_scene", {"scene_id": a["scene_id"]})


def t_studio_new_scene(a):
    return ui("new_scene", {"title": a.get("title", "")})


def staging_card_problems(plan: dict, staged: dict, shots: list[dict]) -> list[str]:
    """The floor plan and the card must describe one camera: the staging setup's lens and the staged framing are the
    shot's lens_mm and framing. Until 05.11 a 35 mm card staged on an 85 mm setup passed, and the keyframe, the prompt
    and the previz each showed another shot."""
    setups = {str(s.get("id")): s for s in plan.get("setups") or [] if isinstance(s, dict)}
    found = []
    for at, (card, item) in enumerate(zip(shots, plan.get("shots") or []), 1):
        setup = setups.get(str(item.get("setup") or ""))
        lens = card.get("lens_mm")
        if setup and lens and setup.get("lens_mm") and abs(float(setup["lens_mm"]) - float(lens)) > 0.5:
            found.append(f"шот {at}: объектив установки {item['setup']} в постановке {float(setup['lens_mm']):g} мм, а в "
                         f"шоте {float(lens):g} мм — поставь один объектив в обоих местах")
        framing = str(card.get("framing") or "")
        declared = str(item.get("framing") or "")
        computed = next((str(s.get("subject_framing") or "") for s in staged.get("shots") or [] if s.get("shot") == at), "")
        if framing and declared and declared != framing:
            found.append(f"шот {at}: в постановке крупность {declared}, а в шоте {framing} — поставь одну крупность")
        elif framing and not declared and computed and framing in STAGED_FRAMINGS and \
                abs(STAGED_FRAMINGS.index(computed) - STAGED_FRAMINGS.index(framing)) > 1:
            found.append(f"шот {at}: шот заявляет {framing}, а установка {item.get('setup')} на плане даёт {computed} — "
                         "подвинь камеру, смени объектив или крупность")
    return found


STAGED_FRAMINGS = ["extreme_wide", "wide", "full", "medium_full", "medium", "medium_close", "close", "extreme_close"]


def t_scene_draft(a):
    """A whole scene for the story canvas: the same rules as the board (scene-director-model.mjs checkScenePlan)."""
    if FRANCHISE_ID and a.get("franchise_id") != FRANCHISE_ID:
        raise StudioError("Сцена должна принадлежать франшизе этой задачи")
    shots = a["shots"]
    problems, warnings = [], []
    staged = None
    if a.get("staging"):
        if len(a["staging"].get("shots", [])) != len(shots):
            problems.append("в постановке (staging) другое число шотов, чем в сцене")
        else:
            # Before the engine writes the takes: each shot keeps its staging line and the prompt carries it once.
            staged = request("POST", "/api/staging/compile", a["staging"])
            problems += [f"шот {f['shot']}: {f['message']}" for f in staged["findings"] if f["severity"] == "error"]
            problems += staging_card_problems(a["staging"], staged, shots)
            for shot, item in zip(shots, staged.get("shots") or []):
                line = item.get("prompt_line") or ""
                if line:
                    shot["staging_line"] = line
    if any(isinstance(shot.get("take"), dict) for shot in shots):  # the engine writes the prompts from the fields
        written = request("POST", "/api/scene-runs/compose", {"franchise_id": a.get("franchise_id", ""),
                                                              "style": a.get("style", ""), "place": a.get("place", ""),
                                                              # the scene's pictures pick the costume words
                                                              # (shot_contract._passport_words)
                                                              "references": a.get("references") or [], "shots": shots,
                                                              # fantasy shots: environment = the imagined world
                                                              "fantasy_world": (a.get("fantasy_world") or {}).get("description", "")})
        problems += written.get("problems", [])
        warnings += written.get("warnings", [])
        for shot, prompt in zip(shots, written["prompts"]):
            shot["prompt"] = prompt
            if isinstance(shot.get("take"), dict) and shot["take"].get("camera") and not shot.get("motivation"):
                reason = str(shot["take"]["camera"]).strip()  # the take's camera sentence is the move's reason
                shot["motivation"] = reason if len(reason) <= 200 else reason[:200].rsplit(" ", 1)[0]  # Studio limit
    problems += [f"шот {at}: нет ни take, ни prompt" for at, shot in enumerate(shots, 1) if not shot.get("prompt")]
    if a.get("idea_id"):
        # The idea pack is the scene's allow-list: heroes, references, look and mode of this idea only.
        problems += request("POST", idea_path(a) + "/check", a)["problems"]
    problems += check_references(a.get("references", []))
    problems += check_franchise_characters(a.get("franchise_id", ""), a.get("references", []))
    if sum(1 for ref in a.get("references", []) if ref.get("role") in COMPOSER_ROLES["image"]) > 9:
        problems.append("больше 9 картинок в материалах сцены")
    chain = 0
    for index, shot in enumerate(shots, 1):
        spec = shot.get("production_spec")
        if spec is not None:
            if spec.get("franchise_id") != a.get("franchise_id"):
                problems.append(f"шот {index}: ShotSpec другой франшизы")
            result = request("POST", "/api/production/spec/validate", spec)
            if not result.get("valid"):
                problems.append(f"шот {index}: некорректный ShotSpec")
            if spec.get("shot", {}).get("duration") != shot["duration"]:
                problems.append(f"шот {index}: длительность расходится с ShotSpec")
        if index == 1 and shot["start_frame"] != "new_keyframe":
            problems.append("шот 1 начинается с нового ключевого кадра")
        chain = chain + 1 if shot["start_frame"] == "previous_end" else 0
        if chain > 3:
            problems.append(f"шот {index}: больше трёх продолжений подряд — нужен новый ключевой кадр")
    total = sum(shot["duration"] for shot in shots)
    if total > 180:
        problems.append(f"сцена {total} с — больше 180 с")
    if not any(ref.get("role") in PLACE_ROLES for ref in a.get("references", [])):
        problems.append("в сцене нет места действия — добавь фото локации (роль «Окружение», «Кадр сцены» или "
                        "«Композиция»): из локации франшизы (franchise_entities type=location → asset_ids) или из "
                        "библиотеки (assets_list role=Окружение, посмотри view_image); опорные кадры строят "
                        "пространство и композицию от него")
    unknown = unknown_presets(a)
    if unknown:
        problems.append("нет таких пресетов режиссуры (director_presets): " + ", ".join(unknown[:6]))
    # The take contract (local_studio/shot_contract.py): the same check «Собрать сцену» runs, so a scene the agent
    # shows can be built. On 31.10 every scene passed shot_lint and 63 of 82 takes came back broken.
    contract = request("POST", "/api/scene-runs/contract", {"franchise_id": a.get("franchise_id", ""), "mode": a.get("mode"),
                                                            "references": a.get("references", []), "shots": shots})
    problems += contract.get("problems", [])
    if problems:
        raise StudioError("Сцена не показана: " + "; ".join(problems))
    review = request("POST", "/api/shot-lint", {"shots": shots, "target_seconds": total})
    if a.get("franchise_id"):
        review = request("POST", f"/api/franchises/{q(a['franchise_id'])}/shot-lint", {"shots": shots, "target_seconds": total})
    findings = review.get("findings", []) if isinstance(review, dict) else []
    errors = [f for f in findings if f.get("severity") == "error"]
    if errors:
        details = "; ".join(f"шот {f.get('shot', '?')}: {f.get('message', f.get('rule', 'ошибка'))}" for f in errors[:10])
        raise StudioError("Сцена не прошла проверку шотов и не может быть применена: " + details)
    staging = {} if not staged else {
        "staging": [{"shot": item["shot"], "line": item["staging_line"], "axis_side": item["axis_side"]} for item in staged["shots"]],
        "staging_review": [f for f in staged["findings"] if f["severity"] != "error"]}
    camera = [{"severity": "warning", "rule": "camera_text", "message": text} for text in warnings]
    return {"shown": True, "seconds": total, "shots": len(shots), **staging,
            "review": (camera + [f for f in findings if f.get("severity") in {"error", "warning"}])[:30],
            "note": "Сцена показана карточкой под ответом; на холст она попадёт, только когда пользователь нажмёт "
                    "«Применить на холст». Ключевые кадры и рендер запускает пользователь кнопкой «Собрать сцену». "
                    "Если в review есть error/warning — исправь и покажи сцену заново."}


RUN = {"run_id": {"type": "string", "description": "id задачи; по умолчанию — текущая задача этого чата"}}
STEP_STATES = ["pending", "running", "waiting", "done", "failed", "skipped"]
STEP_KINDS = ["plan", "story", "scene", "keyframes", "render", "qc", "edit", "color", "titles", "export", "review", "other"]

DIRECTOR_SET = {**obj({category: {"type": "string", "pattern": f"^{category}\\.[a-z0-9_]+$"} for category in DIRECTOR_CATEGORIES}),
                "description": "Director presets by category, ids from director_presets (camera body, lens, shot, movement, "
                               "focus, lighting, color, style, acting). They reach the render as the shot's director settings."}
NUMBER = {"type": "number"}
# Same fields as local_studio/staging.py (the service validates them; unknown fields are refused).
STAGING_SCHEMA = obj({
    "aspect_ratio": ASPECT,
    "actors": {"type": "array", "minItems": 1, "items": obj({
        "name": {**STR, "description": "Unique name; heroes as in the pack (ALEX, NOVA)"},
        "kind": {"type": "string", "enum": ["hero", "prop", "set"]}, "x": NUMBER, "y": NUMBER,
        "facing": {**NUMBER, "description": "Degrees: 0 = +y, 90 = +x"}, "height_cm": NUMBER, "width_cm": NUMBER,
        "eye_height_cm": NUMBER}, ["name", "x", "y"])},
    "axis": {"type": "array", "items": STR, "maxItems": 2, "description": "Two actor names: the line of action"},
    "setups": {"type": "array", "minItems": 1, "items": obj({
        "id": STR, "x": NUMBER, "y": NUMBER,
        "aim": {"description": "Actor name or [x, y]", "anyOf": [STR, {"type": "array", "items": NUMBER, "minItems": 2, "maxItems": 2}]},
        "height_cm": NUMBER, "lens_mm": NUMBER, "eye_level_of": STR}, ["id", "x", "y", "aim", "height_cm", "lens_mm"])},
    "shots": {"type": "array", "minItems": 1, "items": obj({
        "setup": STR, "characters": {"type": "array", "items": STR}, "subject": STR,
        "framing": {"type": "string", "enum": ["", "extreme_wide", "wide", "full", "medium_full", "medium", "medium_close",
                                                "close", "extreme_close"]},
        "crossing": {"type": "boolean"}}, ["setup"])},
}, ["actors", "setups", "shots"])

SCENE_SCHEMA = obj({"title": STR, "idea": STR, "franchise_id": STR, "style": STR,
                         "fantasy_world": obj({"description": {"type": "string", "description": "Complete English image brief for one consistent empty fantasy world. Write this yourself from the approved story, preserving the franchise look and the transformation of the real place."},
                                               "base_asset_id": STR}, ["description"]),
                         "staging": {**STAGING_SCHEMA, "description": "Optional floor plan from staging_compile (same "
                                     "actors/axis/setups and one staged shot per scene shot). Its geometry errors block "
                                     "the scene; the reply gives each shot's staging line for first_frame and prompt."},
                         "idea_id": {"type": "string", "description": "Idea card id whose pack (idea_pack_build) bounds this "
                                     "scene: references, heroes, style and mode must come from the pack."},
                         "aspect_ratio": ASPECT,
                         "mode": {"type": "string", "enum": SCENE_MODES,
                                  "description": "Render scenario of the Studio form: realistic, animation3d, animation2d, "
                                                 "replace (hero swap over a source video)."},
                         "quality": {"type": "number", "minimum": 0.4, "maximum": 0.8,
                                     "description": "Megapixels per frame. Default 0.8 (9:16 672x1216, user decision 2026-11-04); "
                                                    "0.4 = 480p; another value only when the user asked for it."},
                         "steps": {"type": "integer", "minimum": 20, "maximum": 25},
                         "director": {**DIRECTOR_SET, "description": "Scene-wide director presets for every shot; a shot's "
                                      "own director overrides by category. " + DIRECTOR_SET["description"]},
                         "references": {"type": "array", "maxItems": 12, "items": obj({
                             "asset_id": STR, "owner": STR,
                             "role": {"type": "string", "enum": [r for roles in COMPOSER_ROLES.values() for r in roles]},
                         }, ["asset_id", "role"])},
                         "place": {"type": "string", "description": "The scene's place for every take: layout with "
                                   "screen left/right, what is where, light sources (English)."},
                         "shots": {"type": "array", "minItems": 2, "maxItems": 12, "items": obj({
                             "production_spec": {"type": "object", "description": "Optional complete ProductionSpec from production_spec_validate. Preserved on the canvas and sent to H3; duration and franchise must match."},
                             "title": STR, "beat": STR, "first_frame": {"type": "string", "description": "Complete opening-image brief for this shot. It is automatically sent to image generation; the user must not rewrite it."},
                             "fantasy": {"type": "boolean", "description": "True only for a shot set in the imagined world; supply fantasy_world for that world."},
                             "prompt": {"type": "string", "description": "Only for a scene outside a franchise: a full "
                                        "English six-section H3 prompt. A franchise scene is written by the engine "
                                        "from take; a hand-written prompt there is refused."},
                             "take": obj({
                                 "summary": {"type": "string", "description": "What this 10–15 s chapter does in the "
                                             "story, e.g. 'Chapter 2 of 4: Mira gives the waitress his keys and follows her route.'"},
                                 "cast": {"type": "array", "minItems": 1, "items": obj({
                                     "name": {"type": "string", "description": "Hero name exactly as the reference owner (ALEX, NOVA)."},
                                     "position": {"type": "string", "description": "Where they are at the start: screen side, distance, pose."},
                                     "notices": STR,
                                     "eyes": {"type": "string", "description": "Where they look and when: blinks, a check of the "
                                              "other hero's face before a decision, a glance away; a hero may look once into the lens."},
                                     "decides": STR,
                                     "does": {"type": "string", "description": "The physical action with contacts, weight and timing."},
                                     "answers": {"type": "string", "description": "How they answer the other hero in view: a look, "
                                                 "a gesture, a step back. Needed when two heroes share the take."},
                                     "ends": {"type": "string", "description": "Their pose, face and state when the take ends."},
                                 }, ["name", "position", "eyes", "does", "ends"])},
                                 "world": obj({key: {"type": "string", "description": text} for key, text in (
                                     ("air", "What the air moves in this take: dust in the lamp light, a curtain, fur lifting."),
                                     ("physics", "How touched things react: the tube rolls, wobbles and settles."),
                                     ("light", "What the light does: headlights sweep the ceiling, a monitor flickers."),
                                     ("background", "What lives behind the heroes: traffic in the window, kettle steam."))},
                                     []),
                                 "camera": {"type": "string", "description": "How the camera moves during the take and why."},
                                 "end": {"type": "string", "description": "The final image of the take."},
                                 "lines": {"type": "array", "items": obj({"speaker": STR, "line": STR}, ["speaker", "line"])},
                                 "sound": STR, "music": STR,
                             }, ["summary", "cast", "world", "end"]),
                             "duration": {"type": "integer", "minimum": 10, "maximum": 15},
                             "start_frame": {"type": "string", "enum": ["new_keyframe", "previous_end"]},
                             "framing": {"type": "string", "enum": shot_options("framing")},
                             "angle": {"type": "string", "enum": shot_options("angle")},
                             "lens_mm": {"type": "integer", "minimum": 12, "maximum": 135},
                             "camera_motion": {"type": "string", "enum": CAMERA_MOTIONS},
                             "camera_direction": {"type": "string", "enum": ["", *shot_options("direction", "movement")],
                                                  "description": "Direction of camera_motion: dolly forward = push in, "
                                                                 "backward = pull out; pan/truck left/right; tilt/crane up/down."},
                             "camera_speed": {"type": "string", "enum": ["", *shot_options("speed", "movement")],
                                              "description": "Speed of camera_motion; empty = slow."},
                             "staging_line": {"type": "string", "description": "Set by scene_draft from staging (who is "
                                              "where, screen side, size); the engine puts it into the shot's prompt."},
                             "motivation": STR,
                             "purpose": {"type": "string", "enum": shot_options("purpose")},
                             "transition_in": {"type": "string", "enum": shot_options("transition_in")},
                             "characters": {"type": "array", "items": STR}, "location": STR, "emotion": STR,
                             "key_action": {"type": "boolean"},
                             "screen_direction": {"type": "string", "enum": ["", "left_to_right", "right_to_left", "toward", "away"]},
                             "eyeline": {"type": "string", "enum": ["", "left", "right", "camera", "up", "down"]},
                             "axis_side": {"type": "string", "enum": ["", "A", "B", "crossing"]},
                             "intent": {"type": "string", "maxLength": 200},
                             "state_before": STR, "state_after": STR,
                             "director": DIRECTOR_SET,
                         }, ["title", "beat", "first_frame", "duration", "start_frame", "framing", "lens_mm",
                             "camera_motion", "transition_in", "state_before", "state_after"])},
                         }, ["title", "idea", "references", "shots"])

COMPOSER_SCHEMA = obj({
    "title": STR, "prompt": STR,
    "references": {"type": "array", "maxItems": 9, "items": obj({
        "asset_id": STR, "owner": STR,
        "role": {"type": "string", "enum": [r for roles in COMPOSER_ROLES.values() for r in roles]},
        # «Исходное видео»: which performer in the source becomes which hero; everyone else stays as filmed.
        "performers": {"type": "array", "maxItems": 4, "items": obj({"owner": STR, "performer": {
            "type": "string", "description": "the source performer: place and action first ('the man on the left'); "
                                             "a look word only to tell performers apart; never repeat it in the prompt text"}}, ["owner", "performer"])},
    }, ["asset_id", "role"])},
    "replace_references": {"type": "boolean"},
    "recipe_id": {"type": "string", "description": "An accepted recipe (studio_accepted_recipes) whose proven render "
                                                    "settings the form takes: model, steps, cache, seed, reference size, "
                                                    "sound, resolution and the hero-swap scenario. They win over the fields below."},
    "mode": {"type": "string", "enum": ["realistic", "animation3d", "animation2d", "replace"],
             "description": "replace = hero swap over a source video (Character Swap)."},
    "duration": {"type": "integer", "minimum": 4, "maximum": 15},
    "aspect_ratio": ASPECT,
    "quality": {"type": "number", "minimum": 0.4, "maximum": 0.8, "description": "Megapixels per frame; default 0.8, another value only on the user's request."},
    "steps": {"type": "integer", "minimum": 20, "maximum": 25},
    "seed": {"type": "integer", "minimum": 0},
    "cache_mode": {"type": "string", "enum": ["safe", "fast", "off"]},
    "sampling_profile": {"type": "string", "enum": ["standard", "motion_ab", "workflow_euler", "ref_beta"], "description": "standard unless the user asked for an A/B; ref_beta = res_multistep/beta for reference-heavy jobs (Comfy-Org note), unproven"},
    "swap_lora": {"type": "string", "enum": ["v1", "v1.5"], "description": "Character Swap LoRA for hero replacement: v1 (accepted recipes, default) or v1.5 (A/B only when the user asked; then ab_user_request is required)"},
    "ab_user_request": {"type": "string", "description": "The user's own words asking for an A/B swap option (v1.5, reskin, swap_mask), quoted from the chat. Studio refuses those options without it (night 05→06.11: 22 unrequested A/B takes, all rejected)."},
    "swap_mask": {"type": "object", "description": "A/B masked hero swap, only when the user asked: only the listed source performers are redrawn, everything else stays the source picture. performers: [{name: hero from the pairs, box: [x, y, w, h] 0..1 of the performer on the FIRST frame of this part, from asset_frames}], grow px (default 24)",
                  "properties": {"performers": {"type": "array", "minItems": 1, "maxItems": 4, "items": {"type": "object", "properties": {"name": {"type": "string"}, "box": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}}, "required": ["name", "box"]}},
                                 "grow": {"type": "integer", "minimum": 0, "maximum": 128}, "feather": {"type": "integer", "minimum": 0, "maximum": 64}},
                  "required": ["performers"]},
    "ref_image_size": {"type": "string", "enum": ["match", "max"]},
    "use_video_audio": {"type": "boolean", "description": "Keep the source video's own sound."},
    "note": STR}, ["prompt"])

def t_motion_director_upload(a, media="assets"):
    if FRANCHISE_ID and a["asset_id"] not in {item["asset_id"] for item in t_assets({"limit": 100000})["assets"]}:
        raise StudioError("Медиа не принадлежит франшизе этого агента")
    return request("POST", f"/api/studio/motion-director/{media}/{q(a['asset_id'])}", {})


def t_motion_director_plan(a, save=False):
    scene = a.get("scene_id") or SCENE_ID
    if not scene or (SCENE_ID and scene != SCENE_ID):
        raise StudioError("Нужен ID своей сцены; чат не может менять план другой сцены")
    endpoint = f"/api/scene-runs/{q(scene)}/motion-plan"
    return request("PUT", endpoint, {"plan": a["plan"], "expected_revision": a["expected_revision"]}) if save else request("GET", endpoint)


def t_motion_director_execute(a, status=False):
    scene = a.get("scene_id") or SCENE_ID
    if not scene or (SCENE_ID and scene != SCENE_ID):
        raise StudioError("Этот чат ведёт только свою сцену")
    endpoint = f"/api/studio/motion-director/scenes/{q(scene)}"
    if status:
        return request("GET", endpoint + "/jobs")
    if a.get("story_approved") is not True:
        raise StudioError("Запуск требует одобренного сюжета или явного запроса пользователя на этот рендер")
    return request("POST", endpoint + "/execute", {"expected_revision": a["expected_revision"], "story_approved": True,
                                                   "full_run": a.get("full_run") is True})


def t_motion_director_cancel_waiting(a):
    scene = a.get("scene_id") or SCENE_ID
    if not scene or (SCENE_ID and scene != SCENE_ID):
        raise StudioError("Этот чат ведёт только свою сцену")
    return request("POST", f"/api/studio/motion-director/scenes/{q(scene)}/jobs/{q(a['motion_id'])}/cancel-waiting", {})


TOOLS = {
    "image_companion": (lambda a: {"ready": True, "message": "Open the companion panel in ordinary ChatGPT with ImageGen."},
                        "Open the STUDIO Image Companion panel to launch a conversation for a Studio image request.", obj({})),
    "image_request_status": (lambda a: image_request_scope(a["request_id"]),
                             "Read one image request's state without claiming it or exposing its claim token.",
                             obj({"request_id": STR}, ["request_id"])),
    "image_requests": (t_image_requests, "List durable Studio image requests for an ordinary ChatGPT ImageGen client. "
                       "waiting_chatgpt means no chat has claimed it. Never generate without claiming first.", obj({"scene_id": STR})),
    "image_request_claim": (t_image_request_claim, "Claim one Studio image request exactly once using a unique claim_id "
                            "for this chat/turn; reuse that claim_id only to recover the same claim. Read returned context "
                            "and inspect references before built-in ImageGen. Do not launch Codex, Qwen, or paid APIs. "
                            "Never repeat generation after an ambiguous interruption.", obj({"request_id": STR, "claim_id": STR}, ["request_id", "claim_id"])),
    "image_request_complete": (t_image_request_complete, "Return the generated image file from this ChatGPT conversation "
                               "to the claimed Studio request. Saves the asset and reports completed or stale. "
                               "Does not accept the image or render video.", obj({"request_id": STR, "claim_token": STR,
                                "image": obj({"download_url": STR, "file_id": STR, "mime_type": STR, "file_name": STR},
                                             ["download_url", "file_id"])}, ["request_id", "claim_token", "image"])),
    "image_request_fail": (t_image_request_fail, "Report inability to generate or hand off a file. Never silently retry "
                           "an ambiguous ImageGen operation.", obj({"request_id": STR, "claim_token": STR, "error": STR},
                                                                   ["request_id", "claim_token", "error"])),
    "studio_image_context": (t_studio_image_context,
                             "Read this before generating an image for a Studio shot. Returns the saved story, shot "
                             "order, exact target shot, enabled references, fantasy world and franchise visual passport "
                             "with character sheets, plus draft revision/fingerprint. Does not generate or approve anything.",
                             obj({"scene_id": STR, "shot_id": STR, "purpose": {"type": "string", "enum": ["frame", "fantasy_world"]},
                                  "notes": STR}, ["scene_id", "shot_id"])),
    "studio_set_start_frame": (t_studio_set_start_frame,
                               "Assign a library image (including one imported from ChatGPT) as the unaccepted start "
                               "frame of one explicit saved scene shot. Requires an open Studio tab. Refuses shots "
                               "with existing generation work. Does not accept the frame or render video.",
                               obj({"scene_id": STR, "shot_id": STR, "asset_id": STR},
                                   ["scene_id", "shot_id", "asset_id"])),
    "motion_director_upload_video": (
        lambda a: t_motion_director_upload(a, "videos"),
        "Upload a Studio library video in chunks to the external Motion Director node. Returns confirmed video_file "
        "for source_video segments. Transfers media only; never queues a render.", obj({"asset_id": STR}, ["asset_id"])),
    "motion_director_upload_image": (
        t_motion_director_upload,
        "Upload a Studio library image to connected ComfyUI/input for Motion Director. Returns confirmed image_file "
        "for motion_director_compile. Transfers media only, never queues a render.", obj({"asset_id": STR}, ["asset_id"])),
    "motion_director_compile": (
        lambda a: request("POST", "/api/studio/motion-director/compile", a),
        "Compile segment prompts and explicit image bindings into Motion Director Mixed timeline JSON. "
        "No upload or render. image_file must identify an uploaded file relative to ComfyUI/input; do not invent paths. "
        "Supports T2V (0 pictures), I2V (1 start picture), FL2V (first and last pictures), R2V (1-8 references), "
        "Source Video (uploaded video_file with start/end seconds; identity pictures require explicit replacements mapping owner/performer/body_asset_id, optional face_asset_id/costume_asset_id). "
        "Continuity flags describe the incoming boundary; both must be false for the first segment.",
        obj({"width": {"type": "integer", "minimum": 32, "maximum": 8192, "multipleOf": 32},
             "height": {"type": "integer", "minimum": 32, "maximum": 8192, "multipleOf": 32},
             "segments": {"type": "array", "minItems": 1, "maxItems": 60, "items": obj({
                 "id": STR, "mode": {"type": "string", "enum": ["t2v", "i2v", "fl2v", "r2v", "source_video"]},
                 "prompt": STR, "duration": {"type": "number", "minimum": 1, "maximum": 15},
                 "pictures": {"type": "array", "maxItems": 8, "items": obj({"asset_id": STR, "image_file": STR}, ["asset_id", "image_file"])},
                 "replacements": {"type": "array", "maxItems": 4, "items": obj({
                     "owner": STR, "performer": STR, "body_asset_id": STR,
                     "face_asset_id": STR, "costume_asset_id": STR}, ["owner", "performer", "body_asset_id"])},
                 "source_video": obj({"asset_id": STR, "video_file": STR, "start": {"type": "number", "minimum": 0},
                                      "end": {"type": "number", "exclusiveMinimum": 0}}, ["video_file", "start", "end"]),
                 "visual_continuity": {"type": "boolean"}, "audio_continuity": {"type": "boolean"}},
                 ["id", "mode", "prompt", "duration"])}}, ["segments", "width", "height"])),
    "motion_director_rerun_plan": (
        lambda a: request("POST", "/api/studio/motion-director/rerun-plan", a),
        "Plan which segments become stale after edits. Includes downstream motion, audio and earlier-result dependencies. "
        "No render or scene mutation. Unaffected segments still require matching cache/input revisions before reuse.",
        obj({"segments": {"type": "array", "minItems": 1, "maxItems": 60, "items": obj({
            "id": STR, "mode": {"type": "string", "enum": ["t2v", "i2v", "fl2v", "r2v", "source_video"]},
            "visual_continuity": {"type": "boolean"}, "audio_continuity": {"type": "boolean"},
            "result_sources": {"type": "array", "items": STR}}, ["id", "mode"])},
            "changed_ids": {"type": "array", "items": STR, "minItems": 1}}, ["segments", "changed_ids"])),
    "studio_playbook": (t_studio_playbook,
                        "Route a production task to the relevant existing STUDIO skills and an economical step plan. "
                        "Returns paths first; pass skill=<one listed name> to read that SKILL.md. No shell or GPU work.",
                        obj({"workflow": {"type": "string", "enum": list(PLAYBOOKS)},
                             "franchise": {"type": "string"},
                             "skill": STR}, ["workflow"])),
    "studio_board": (t_studio_board, "The open Studio canvas now: scenes, the active scene's shots (job_id of each finished or "
                                     "running render, start frame, transition), materials and the «Собрать сцену» state.", obj({})),
    "studio_shot_snapshot": (t_studio_shot_snapshot, "Read one saved canvas shot and its draft revision before a bounded edit. "
                            "No GPU or browser tab needed.", obj({"scene_id": STR, "shot_id": STR}, ["shot_id"])),
    "studio_production_state": (t_studio_production_state,
        "Read the saved production revision: scenes, selected/historical takes, job receipts, acceptance and definition drift. "
        "Automatically scoped to this task franchise. Recorded hashes are not a live integrity check; never starts GPU work.",
        obj({"franchise_id": STR})),
    "studio_scene_readiness": (t_studio_scene_readiness,
                               "Read the saved scene or active scene run, each shot's real job and QC receipt, "
                               "and the next safe action. Completed render alone never counts as accepted. No GPU.",
                               obj({"scene_id": STR})),
    "scene_staging_check": (t_scene_staging_check,
                            "Staging check of a saved scene without Blockout or GPU: heroes at passport scale, the camera of "
                            "each shot card (size, angle, lens, move). problems names a hero out of frame, cut by the edge or "
                            "a camera inside a hero. Run it before studio_build_scene; fix the shot card, then check again.",
                            obj({"scene_id": STR})),
    "studio_patch_shot": (t_studio_patch_shot, "Change only prompt/motivation of one unrendered saved shot. "
                         "Use studio_shot_snapshot first; event_id stays the same on a retry. The Studio validates "
                         "revision and stores a durable receipt. No GPU.",
                         obj({"scene_id": STR, "shot_id": STR, "event_id": STR,
                              "base_revision": {"type": "integer", "minimum": 0},
                              "prompt": STR, "motivation": STR}, ["shot_id", "event_id", "base_revision"])),
    "studio_apply_scene": (t_studio_apply_scene, "Put a scene on the open Studio canvas yourself (same input and checks as "
                                                 "scene_draft): a scene with work becomes a new scene, nothing is lost. No GPU. "
                                                 "Check verification.ok and board; if false, inspect rather than reapply.",
                           SCENE_SCHEMA),
    "studio_build_scene": (t_studio_build, "Press «Собрать сцену» on the active scene: keyframes for new_keyframe shots, then the "
                                           "renders in order, continuations from the previous last frame (GPU). With «сначала опорные "
                                           "кадры» on (default) it stops after the keyframes until the user accepts each frame "
                                           "(studio_board: frame_accepted) — show them the frames, never accept for them. In a task needs the "
                                           "user's «Да» (run_ask gate=gpu); outside a task only when the user asked to render. "
                                           "Then follow it with studio_board (job_id per shot) or run_wait(scene_ids=[…]).", obj({})),
    "studio_stop_build": (t_studio_stop, "Stop «Собрать сцену» (renders already sent finish in the queue).", obj({})),
    "studio_reset_scene": (t_studio_reset, "Reset the active scene completely (one empty shot, no materials, style or plan). "
                                           "Only when the user asked for it.", obj({})),
    "studio_select_scene": (t_studio_select, "Make another scene of the canvas active.", obj({"scene_id": STR}, ["scene_id"])),
    "studio_new_scene": (t_studio_new_scene, "Add an empty scene to the canvas and make it active.", obj({"title": STR})),
    "story_approval_record": (t_story_approval,
                              "Record the user's approval of a story in docs/DECISIONS.md (AGENTS.md: storyboard, keyframes "
                              "and renders of an episode need it). user_words = the user's message quoted word for word "
                              "(the Studio checks it against this chat); story = title + one line + doc path. Ask "
                              "«Одобряешь сюжет …?» first if the user has not said it.",
                              obj({"franchise": STR, "story": STR, "user_words": STR}, ["franchise", "story", "user_words"])),
    "run_plan": (t_run_plan, "Task: set or replace the plan (4–10 steps, kept statuses for the same ids).",
                 obj({**RUN, "steps": {"type": "array", "minItems": 1, "maxItems": 20, "items": obj({
                     "id": STR, "title": STR, "kind": {"type": "string", "enum": STEP_KINDS}}, ["id", "title"])}},
                     ["steps"])),
    "run_step": (t_run_step, "Task: mark a step running/done/failed/skipped with a short Russian detail and artifacts "
                             "(scene, job_ids, render_id, export_id, url).",
                 obj({**RUN, "step_id": STR, "status": {"type": "string", "enum": STEP_STATES}, "detail": STR,
                      "artifacts": {"type": "object"}}, ["step_id", "status"])),
    "run_ask": (t_run_ask, "Task: ask only for a real blocker and end your turn. A direct user request to generate "
                           "already grants a bounded task GPU budget; check the run gate and do not ask again for every shot. "
                           "gate=story is still required before storyboarding an unapproved episode. If no GPU grant "
                           "exists, gate=gpu requests the planned task budget once. Options «Да»/«Нет».",
                obj({**RUN, "question": STR, "options": {"type": "array", "items": STR, "maxItems": 6},
                     "gate": {"type": "string", "enum": ["", "gpu", "story"]}, "gpu_uses": {"type": "integer", "minimum": 1, "maximum": 20},
                     "step_id": STR}, ["question"])),
    "run_wait": (t_run_wait, "Task: wait for H3 renders (job_ids) or edit assemblies "
                             "(render_ids) and end your turn; the Studio wakes you when all are finished.",
                 obj({**RUN, "step_id": STR, "job_ids": {"type": "array", "items": STR},
                      "render_ids": {"type": "array", "items": STR},
                      "scene_ids": {"type": "array", "items": STR},
                      "note": STR}, [])),
    "run_delegate": (t_run_delegate, "Task: give a helper a narrow read-only job. Supply a short evidence packet "
                                     "for a payload-only helper with no repo tools and token telemetry; omit evidence "
                                     "only when the helper must inspect the repository (up to 15 min).",
                     obj({**RUN, "role": STR, "task": STR, "evidence": STR,
                          "provider": {"type": "string", "enum": ["", "claude", "codex"]}},
                         ["role", "task"])),
    "run_done": (t_run_done, "Task: finish only after every plan step is done/skipped, waits have ended and referenced "
                             "render job IDs are completed. Summarize the actual scene, renders, edit and export; "
                             "use status=failed with a concrete blocker when work cannot be completed.",
                 obj({**RUN, "summary": STR, "final_job_id": STR, "final_artifact": obj({"kind": {"type": "string", "enum": ["job", "concat", "edl", "motion", "asset"]}, "id": STR}, ["kind", "id"]), "status": {"type": "string", "enum": ["done", "failed"]}}, ["summary"])),
    "production_review_record": (t_production_review, "Record an actual review of a completed MP4, tied to its SHA. artifact_kind defaults to job; use concat, edl, motion or asset with its result ID in job_id for assembled or imported films. This does not watch the video. Creative acceptance needs prior technical_pass, full viewed_ranges and audio_reviewed when audio exists. Never invent viewing evidence.",
        obj({"job_id": STR, "artifact_kind": {"type": "string", "enum": ["job", "concat", "edl", "motion", "asset"]}, "output_sha256": STR, "verdict": {"type": "string", "enum": ["technical_pass", "creative_accepted", "rejected"]},
             "reviewer": STR, "evidence": STR, "viewed_ranges": {"type": "array", "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}},
             "audio_reviewed": {"type": "boolean"}}, ["job_id", "output_sha256", "verdict", "reviewer", "evidence"])),
    "production_spec_validate": (t_production_spec, "Validate versioned character behavior and shot beats before generation. Get exact schema via studio_api_schema(path=/api/production/spec/validate, method=post). Checks timing, causes, actors, references and Director fields; never claims physical correctness or creative approval.",
                                 obj({"payload": {"type": "object"}}, ["payload"])),
    "visual_profiles": (t_visual_profiles, "Source-backed author/studio methods for both frame and video direction. Short list first; profile_id reads one full profile. Not render-verified and not a universal lens/color preset. Copy visual_profile into ProductionSpec only when appropriate.", obj({"profile_id": STR})),
    "edl_toolkit": (t_edl_toolkit,
                    "What the edit can do: seam transitions with their default look and sound, every xfade look "
                    "(transition_effect), picture accents (accent_names: shake, zoom, flash, glitch, chroma, vhs, strobe, blur, bw, invert), looks incl. LUTs, the built-in SFX bank (name, "
                    "length, anchor) and the loudness default. Read it before writing an edit sheet.", obj({})),
    "scene_draft": (t_scene_draft,
                    "Propose a whole scene for the story canvas: shared reference files (franchise passport: faces, "
                    "full bodies, costumes, location; at most 9 images), scene style and 2–12 shots. Each shot: an English "
                    "MiniMax H3 prompt using the scene's <Picture N> tags (numbered per kind in the references order), "
                    "first_frame — the opening frame for its keyframe (who stands where, screen side, framing; name "
                    "the heroes), camera (framing, angle, lens, motion with motivation), transition_in from the previous "
                    "shot, start_frame (previous_end continues from the previous shot's last frame — same place, "
                    "continuous action; new_keyframe for a new angle, place or time), states before/after. Nothing is "
                    "queued: the user applies the scene and starts «Собрать сцену». The result carries shot_lint "
                    "findings to fix. Set up the whole canvas, not only the prompts: scene mode, quality (resolution) "
                    "and a scene-wide director set (camera body, lighting, color, style, acting — ids from "
                    "director_presets), each shot's own director where it differs (lens look, focus, a named camera "
                    "move); framing, angle, lens_mm and camera_motion of a shot win over its presets.",
                    SCENE_SCHEMA),
    "director_presets": (t_director_presets,
                         "The Studio's director presets (the «Режиссура кадра» cards) by category: id, name, what it "
                         "does. Use these ids in scene_draft / studio_apply_scene director sets.",
                         obj({"categories": {"type": "array", "items": {"type": "string", "enum": DIRECTOR_CATEGORIES}}})),
    "composer_draft": (t_composer_draft,
                       "Propose a ready MiniMax H3 clip for the «Создать видео» form: the full English prompt, the "
                       "reference files with their roles and owners, duration, aspect and quality. Nothing is queued: "
                       "the user reviews the draft and applies it with one click. References are numbered per kind in "
                       "the given order (first image = <Picture 1>, first video = <Video 1>, first audio = <Audio 1>); "
                       "use exactly those tags in the prompt. replace_references=false keeps the form's current files "
                       "and appends these after them.",
                       COMPOSER_SCHEMA),
    "studio_apply_draft": (t_studio_apply_draft, "Fill the open «Создать видео» form yourself (same input and checks as "
                           "composer_draft): references with roles, owners and performer pairs, prompt and every render "
                           "setting, or an accepted recipe's settings. No GPU: the render is the user's click.", COMPOSER_SCHEMA),
    "studio_health": (t_health, "Studio state: health, render queue pause, GPU activity.", obj({})),
    "studio_readiness": (t_readiness, "Compact read-only preflight: GPU/queue readiness, counts of local jobs and ambiguous receipts. "
                         "Never queues or resumes anything; a ready result does not approve a story or GPU spend.", obj({})),
    "franchise_list": (t_franchises, "All franchises (id, name).", obj({})),
    "franchise_boards": (t_boards, "Boards of a franchise (world, ideas, episodes).",
                         obj({"franchise_id": STR}, ["franchise_id"])),
    "franchise_board": (t_board, "One board with its content tree.",
                        obj({"franchise_id": STR, "board_id": STR}, ["franchise_id", "board_id"])),
    "franchise_entities": (t_entities,
                           "Franchise bible cards: characters, locations, rules, ideas, scenes, with asset_ids. "
                           "Bodies are shortened unless full_body is true.",
                           obj({"franchise_id": STR,
                                "type": {"type": "string", "enum": ["character", "location", "rule", "idea", "scene"]},
                                "query": STR, "limit": INT, "full_body": {"type": "boolean"}}, ["franchise_id"])),
    "franchise_entity": (t_entity, "One bible card in full, with its revision.",
                         obj({"franchise_id": STR, "entity_id": STR}, ["franchise_id", "entity_id"])),
    "franchise_entity_create": (t_entity_create, "Add a bible card (state draft unless the user approved it).",
                                obj({"franchise_id": STR,
                                     "type": {"type": "string", "enum": ["character", "location", "rule", "idea", "scene"]},
                                     "title": STR, "body": STR,
                                     "state": {"type": "string", "enum": ["draft", "approved"]},
                                     "asset_ids": {"type": "array", "items": STR}},
                                    ["franchise_id", "type", "title"])),
    "franchise_entity_update": (t_entity_update, "Replace a bible card; pass the revision you read (optimistic lock).",
                                obj({"franchise_id": STR, "entity_id": STR,
                                     "type": {"type": "string", "enum": ["character", "location", "rule", "idea", "scene"]},
                                     "title": STR, "body": STR,
                                     "state": {"type": "string", "enum": ["draft", "approved"]},
                                     "asset_ids": {"type": "array", "items": STR}, "revision": INT},
                                    ["franchise_id", "entity_id", "type", "title", "revision"])),
    "assets_list": (t_assets, "Asset library, newest first: id, kind, role, owner, local file path. role filters by the "
                              "library role (e.g. role=«Окружение» for locations, «Кадр сцены» for scene frames); look at "
                              "candidates with view_image before using them.",
                    obj({"kind": {"type": "string", "enum": ["image", "video", "audio"]},
                         "role": {"type": "string", "enum": [r for roles in COMPOSER_ROLES.values() for r in roles]},
                         "query": STR, "limit": INT, "franchise_id": STR})),
    "asset_import_image": (t_asset_import_image,
                           "Import a PNG, JPEG or WebP saved on this Mac by the chat into the Studio library. "
                           "Returns asset_id for scene references. No image generation and no GPU render.",
                           obj({"path": STR, "role": STR}, ["path"])),
    "asset_import_chat_file": (t_asset_import_chat_file,
                              "Use this to send an image file from this ChatGPT conversation to the Studio library. "
                              "The host supplies the file object. Returns asset_id for scene references; no generation or render. "
                              "If the host cannot pass a generated image, ask the user to attach the saved image.",
                              obj({"image": obj({"download_url": STR, "file_id": STR, "mime_type": STR,
                                                  "file_name": STR}, ["download_url", "file_id"]),
                                   "role": STR}, ["image"])),
    "motion_reference_prepare": (t_motion_reference, "Prepare a separate ReShot depth/pose/canny MP4 from one library video, "
                                 "then inspect it with asset_frames and add it to the scene deliberately. This does not enqueue H3. "
                                 "At most 15 s; uses the pinned Apache-2.0 ReShot build and local Mac compute.",
                                 obj({"asset_id": STR, "control": {"type": "string", "enum": ["depth", "pose", "canny"]}}, ["asset_id"])),
    "job_frames": (t_job_frames, "Review a finished shot yourself (this replaces the old Qwen QC): one contact sheet of "
                                 "evenly spaced frames (left to right, top to bottom; frame_times_s) plus the full decode / audio "
                                 "check and the hard cuts found in the clip. Compare with the shot's expected action, start and end "
                                 "state, identities of its heroes (view_image their references) and the seam to the next shot.",
                   obj({"job_id": STR, "count": {"type": "integer", "minimum": 4, "maximum": 12}}, ["job_id"])),
    "asset_frames": (t_asset_frames, "Look at a library video: contact sheet, total duration, original sound and cuts. "
                                     "For a long character swap, inspect the entire source and then use start_s/end_s "
                                     "to inspect every interval containing a performer, including late close-ups, "
                                     "hands, reflections and the ending. frame_times_s are original source times.",
                     obj({"asset_id": STR, "count": {"type": "integer", "minimum": 4, "maximum": 16},
                          "start_s": {"type": "number", "minimum": 0},
                          "end_s": {"type": "number", "exclusiveMinimum": 0}}, ["asset_id"])),
    "scene_run": (t_scene_run, "The build of your scene on the Mac after «Собрать сцену» (studio_ui build): keyframes, the stop "
                               "for the user's «✓ Принять кадр», renders, last frames and the glue run there by themselves, "
                               "without the tab and without you. status: state (running / awaiting — frames wait for the "
                               "user, see awaiting / stopped / done), message, shots. stop halts it (sent shots finish), "
                               "resume continues after a stop. Changed shot text or staging needs a new build instead.",
                  obj({"action": {"type": "string", "enum": ["status", "stop", "resume"]}, "scene_id": STR})),
    "view_image": (t_view_image, "Look at an asset thumbnail (keyframes are image assets) or a render poster.",
                   obj({"kind": {"type": "string", "enum": list(IMAGE_PATHS)}, "id": STR}, ["kind", "id"])),
    "jobs_list": (t_jobs, "Render jobs in Studio order, prompts shortened.",
                  obj({"state": STR, "limit": INT})),
    "job_get": (t_job, "One render job in full (prompt, references, settings, result).",
                obj({"job_id": STR}, ["job_id"])),
    "job_reproducibility": (t_job_reproducibility, "Read-only recipe: exact saved inputs, input fingerprint, verified reference SHA-256 and output SHA-256. Does not rerender.",
                            obj({"job_id": STR}, ["job_id"])),
    "studio_accepted_recipes": (t_accepted_recipes, "Short catalog of the user's own accepted character replacement recipes; excludes drafts and rejected attempts. Empty in a fresh Studio: then video swap and world swap use their built-in settings. Read-only.", obj({})),
    "studio_accepted_recipe": (t_accepted_recipe, "Full accepted recipe with exact request, asset hashes and scope. New source requires its own QC; reading never queues GPU.",
                               obj({"recipe_id": STR}, ["recipe_id"])),
    "studio_verify_recipe": (t_verify_recipe, "Verify actual reference files and accepted MP4 against recorded hashes before replay. Environment match stays unknown until execution provenance is captured. No GPU work.",
                             obj({"recipe_id": STR}, ["recipe_id"])),
    "job_cancel": (t_job_cancel, "Cancel a render job.", obj({"job_id": STR}, ["job_id"])),
    "storyboard_concat": (t_concat, "Join finished render jobs into one MP4 in the given order. For a «Замена по видео» "
                          "plan pass its glue_trim_frames as trim_frames and glue_tail_frames as tail_frames, so the full film keeps the "
                          "source's length and cuts.",
                          obj({"job_ids": {"type": "array", "items": STR, "minItems": 1},
                               "trim_frames": {"type": "array", "items": {"type": "integer", "minimum": 0}},
                               "tail_frames": {"type": "array", "items": {"type": "integer", "minimum": 0}}}, ["job_ids"])),
    "job_snap_zoom": (t_job_snap_zoom,
                      "Crash zoom in/out take: H3 always paces a zoom over 1.7–2.6 s whatever the prompt says, so make the "
                      "snap after the render — this finds the zoom in the finished MP4 and plays it in 3 frames (sound "
                      "sped up with it), saved as a NEW library video; the take itself is not changed. Check the result "
                      "with asset_frames. No GPU.", obj({"job_id": STR}, ["job_id"])),
    "video_swap_plan": (t_video_swap_plan,
                        "«Замена по видео» in one call: a library source video + franchise + who becomes which hero → the "
                        "WHOLE source split on its own cuts into 4–10 s parts (each saved as a library excerpt; 15 s parts render ~2.2x longer), one "
                        "«Персонаж целиком» picture per hero from the passport, and each part's form with the accepted "
                        "Character Swap settings and performer pairs. Then look at every part with asset_frames, write its "
                        "six-section prompt with [Shot N] times (part.source_cuts), and show the plan. No GPU work. "
                        "Own GPU only (MiniMax API removed 04.11): the source room stays; a whole-world change has no route. "
                        "Every part prompt needs a [Shot N] map of WHO does WHAT with which prop and face, written as what is "
                        "in the frame (Studio refuses >14 no/never/not and >5000 compiled characters).",
                        obj({"asset_id": STR, "franchise_id": STR,
                             "pairs": {"type": "array", "minItems": 1, "maxItems": 4, "items": obj(
                                 {"hero": STR, "performer": {"type": "string", "description":
                                  "who in the source becomes the hero: place and action first ('the man on the left', 'the cat at the "
                                  "table'); a look word only to tell two performers apart. Studio already names this label "
                                  "in the prompt: never describe the performer's clothes or hair again in your text "
                                  "(a repeated source beanie lands on the hero)"}},
                                 ["hero", "performer"])},
                             "audio": {"type": "string", "enum": ["source", "hero_voices"], "description":
                                       "source (default, proven): the original soundtrack; hero_voices: H3 makes the "
                                       "sound anew with the heroes' voices — experimental, original music is lost"},
                             "route": {"type": "string", "enum": ["gpu"], "description": "own GPU (ComfyUI Character Swap LoRA); the only route"},
                             "environment_asset_id": {"type": "string", "description":
                                       "minimax_api: the franchise set picture (role «Окружение»)"},
                             "source_short_edge": {"type": "integer", "enum": [480], "description":
                                       "A/B only when the user asked: source excerpts at 480 short edge, ~0.46x attention"}},
                            ["asset_id", "franchise_id", "pairs"])),
    "world_swap_auto": (t_world_swap_auto,
                        "DEFAULT for replacing heroes in a source video: «Смена мира по видео» "
                        "built by Studio. Splits the source on its measured cuts, builds ONE part per call: the brain maps "
                        "only that part's shots, Codex draws a «Кадр сцены» per shot (hero face + full body + franchise "
                        "place + source still), Claude accepts each frame, settings come from the recipe file. "
                        "enqueue=true queues only this part; build part N+1 only after part N's MP4 was accepted. "
                        "Follow it with world_swap_auto_status.",
                        obj({"asset_id": STR, "franchise_id": STR,
                             "pairs": {"type": "array", "minItems": 1, "maxItems": 2, "items": obj(
                                 {"hero": STR, "performer": {"type": "string", "description":
                                  "who in the source becomes the hero: place and action first"}}, ["hero", "performer"])},
                             "environment_asset_id": {"type": "string", "description": "the franchise place (role «Окружение»)"},
                             "part": {"type": "integer", "minimum": 1, "description": "part to build (default 1)"},
                             "provider": {"type": "string", "enum": ["claude", "codex"]},
                             "enqueue": {"type": "boolean", "description": "default false: frames and prompt only"},
                             "story_approved": {"type": "boolean"}},
                            ["asset_id", "franchise_id", "pairs", "environment_asset_id", "story_approved"])),
    "world_swap_auto_status": (t_world_swap_auto_status,
                               "State of a world_swap_auto run: shots, every drawn frame with Claude's verdict, the job, job_id.",
                               obj({"run_id": STR}, ["run_id"])),
    "video_swap_auto": (t_video_swap_auto,
                        "«Замена по видео» end to end, without writing prompts by hand: the plan of video_swap_plan, then "
                        "the Studio brain (subscription Claude/Codex) looks at frames of every shot of every part and maps "
                        "WHO does WHAT; Studio composes each six-section prompt from the passport (hero look), the set "
                        "and the leak guard. By default it prepares prompts only; enqueue=true explicitly queues ALL parts. The source room stays (no world change on the GPU route). Returns run_id at once; follow it with video_swap_auto_status, "
                        "then QC every part's MP4 and glue with storyboard_concat (glue_trim/tail_frames).",
                        obj({"asset_id": STR, "franchise_id": STR,
                             "pairs": {"type": "array", "minItems": 1, "maxItems": 4, "items": obj(
                                 {"hero": STR, "performer": {"type": "string", "description":
                                  "who in the source becomes the hero: place and action first; a look word only to tell two "
                                  "performers apart; never repeated in prompts (≤80 chars is kept)"}},
                                 ["hero", "performer"])},
                             "environment_asset_id": {"type": "string", "description": "the franchise set picture"},
                             "route": {"type": "string", "enum": ["gpu"]},
                             "audio": {"type": "string", "enum": ["source", "hero_voices"]},
                             "provider": {"type": "string", "enum": ["claude", "codex"],
                                          "description": "who maps the shots (default claude)"},
                             "enqueue": {"type": "boolean", "description": "default false: prompts only; true queues all parts after explicit generation request"},
                             "mask": {"type": "boolean", "description": "A/B, only when the user asked: the brain marks each performer on the part's first frame and only they are redrawn (swap_mask); a part whose performer is not visible on frame 0 fails"},
                             "ab_user_request": {"type": "string", "description": "Required with mask: the user's own words asking for this A/B, quoted from the chat. Studio refuses the mask without them."},
                             "source_short_edge": {"type": "integer", "enum": [480], "description": "A/B: source excerpts at 480 short edge"},
                             "story_approved": {"type": "boolean"}},
                            ["asset_id", "franchise_id", "pairs", "story_approved"])),
    "video_swap_auto_status": (t_video_swap_auto_status,
                               "State of a video_swap_auto run: each part's shot map, prompt, job_id; glue frames.",
                               obj({"run_id": STR}, ["run_id"])),
    "brain_status": (t_brain_status, "Whether the Studio brain CLIs (Claude Code, Codex) are installed and logged in.", obj({})),
    "brain_ask": (t_brain_ask,
                  "Ask the Studio brain (the user's Claude Code or Codex subscription, read-only repo access). "
                  "Pass a JSON Schema in `schema` to get structured `data` back.",
                  obj({"provider": {"type": "string", "enum": ["claude", "codex"]}, "prompt": STR,
                       "schema": {"type": "object"}, "read_repo": {"type": "boolean"}, "model": STR,
                       "timeout_s": INT}, ["provider", "prompt"])),
    "studio_api_get": (t_api_get, "Read any other Studio GET endpoint under /api/ (see studio_api_schema).",
                       obj({"path": STR}, ["path"])),
    "franchise_profile": (t_profile_get,
                          "Franchise production profile («паспорт»): look (style, texture, palette, 9:16, English), "
                          "director defaults, camera rules (lens range, allowed moves, signature), edit rules "
                          "(transition palette, shot rhythm, music, fades) and asset roles (costumes, weapons, props, "
                          "fantasies, style plates with owners). Read it with its revision before an update.",
                          obj({"franchise_id": STR}, ["franchise_id"])),
    "staging_compile": (lambda a: request("POST", "/api/staging/compile", a),
                        "Stage a scene on a floor plan before writing shots: actors (heroes, props, set pieces; x/y in "
                        "metres, facing in degrees 0=+y 90=+x, height/width/eye height in cm — cats ~25 cm tall, eyes "
                        "~20 cm), the axis pair, camera setups (position, height_cm, lens_mm, aim at an actor or [x,y], "
                        "eye_level_of) and one entry per shot (setup, characters, subject, framing, crossing). Returns "
                        "per shot what the lens really sees: screen side, depth, shot size, eyeline, who hides whom, an "
                        "English staging line for first_frame/prompt; findings: 180° line, screen swaps, declared vs real "
                        "framing, heroes out of frame, eye level; and a top-down previz SVG. No GPU.",
                        STAGING_SCHEMA),
    "idea_pack_get": (t_idea_pack_get, "The saved pack of an idea: cast, places, references with roles and owners, the "
                      "idea's pictures (split = two-panel sheet, read-only), style_lock, rules, notes; stale=true after the "
                      "idea card changed.", obj({"franchise_id": STR, "idea_id": STR}, ["franchise_id", "idea_id"])),
    "idea_pack_build": (t_idea_pack_build,
                        "Build (or rebuild with the current revision) the one reference set and look of an idea before any "
                        "scene of it: heroes, costumes, props and places named in the idea card of THIS franchise, from its "
                        "passport and bible; franchise style plates; style_lock (style, look lines, director defaults, 9:16, "
                        "English); approved rules. Look at the idea's pictures with view_image first and add the fitting "
                        "ones in idea_assets (never two-panel sheets, never as hero identity); a library picture only in "
                        "extra_assets with a reason. mode is the render look of every scene of the idea. Then pass idea_id "
                        "to scene_draft: it rejects anything outside the pack. No GPU.",
                        obj({"franchise_id": STR, "idea_id": STR, "expected_revision": {
                                 "type": "integer", "minimum": 0, "description": "Revision of the PACK, not of the idea card: "
                                 "0 for the first build, then the revision idea_pack_get returns."},
                             "mode": {"type": "string", "enum": ["realistic", "animation3d", "animation2d", "replace"]},
                             "add_names": {"type": "array", "maxItems": 8, "items": STR, "description": "Heroes or the place the "
                                           "idea implies without naming (it happens at home): passport/bible names of this franchise."},
                             "idea_assets": {"type": "array", "maxItems": 4, "items": obj({"asset_id": STR, "role": {"type": "string", "enum": ["Стиль", "Композиция", "Окружение", "Свет", "Цвет", "Предмет"]}, "reason": STR}, ["asset_id", "role"])}, "extra_assets": {"type": "array", "maxItems": 4, "items": obj({"asset_id": STR, "role": {"type": "string", "enum": ["Стиль", "Композиция", "Окружение", "Свет", "Цвет", "Предмет"]}, "reason": STR}, ["asset_id", "role"])}},
                            ["franchise_id", "idea_id", "expected_revision", "mode"])),
    "franchise_resolve_shots": (t_resolve_shots,
                                "Shot list → per shot the job parts from the franchise: asset_ids, reference_roles, "
                                "reference_owners (faces, bodies, costumes, weapons, locations, style), look lines for "
                                "the prompt, director_config (franchise defaults + the shot's camera fields), missing "
                                "names. Bible characters/locations with pictures resolve on their own (НОВА = NOVA).",
                                obj({"franchise_id": STR, "shots": {"type": "array", "items": {"type": "object"}}},
                                    ["franchise_id", "shots"])),
    "freecut_timeline": (t_freecut_command,
        "Read or edit the OPEN FreeCut timeline. action=timeline_get returns project_id, revision and timeline. "
        "For timeline_edit pass those project_id/revision and exactly one native FreeCut op: moveItem, split, "
        "trimStart, trimEnd, removeItems, addTransition, addText. Times are frames. User changes cause a conflict; "
        "reread first. No generation/export. Open editor must remain visible. Pending response must not be retried.",
        obj({"action": {"type": "string", "enum": ["timeline_get", "timeline_edit"]},
             "project_id": STR, "revision": STR, "ops": {"type": "array", "maxItems": 1,
             "items": {"type": "object"}}}, ["action"])),
    "montage_list": (t_montage_list, "Server-side EDL montages (id, name, clips), separate from the FreeCut workspace. Active EDL "
                                      "(active).", obj({})),
    "montage_get": (t_montage_get, "The montage's edit sheet (edl: clips with in/out, seams, accents, look, music, sfx, "
                                   "graphics…) and revision. Without montage_id — active server EDL, not the open FreeCut project.",
                    obj({"montage_id": STR}, [])),
    "montage_create": (t_montage_create, "New server EDL (optionally from edl_from_shots); it becomes "
                                         "the open one.", obj({"name": STR, "edl": {"type": "object"}}, [])),
    "montage_update": (t_montage_update,
                       "Edit the saved server EDL, not FreeCut: replace its whole edit sheet (same format as "
                       "edl_render, clips may be empty). This does not change the FreeCut timeline; "
                       "prefer montage_edit for small edits. Read montage_get first, change only what was asked, keep the rest.",
                       obj({"montage_id": STR, "edl": {"type": "object"}, "note": STR, "name": STR}, ["edl", "note"])),
    "montage_edit": (t_montage_edit,
                     "Atomic small edits of the current montage. Read montage_get first and pass its revision. "
                     "Ops run in order; indices are zero-based in the evolving list. addClip {index,clip: EDL clip}; "
                     "removeClip {index}; moveClip {index,to: final index}; trimClip {index,source_in?,source_out?} "
                     "(seconds of the source); setTransition {index,transition,seconds?}. A conflict returns 409; "
                     "reread and reconsider, never replay blindly. One batch saves one server EDL revision; FreeCut is not synchronized. "
                     "Does not render or generate.",
                     obj({"montage_id": STR, "revision": STR, "ops": {"type": "array", "minItems": 1,
                          "maxItems": 100, "items": {"type": "object"}}, "note": STR}, ["revision", "ops", "note"])),
    "montage_render": (t_montage_render, "Assemble the montage as it is saved: draft (default) = fast half-size preview, "
                                         "draft false = the master MP4 (−14 LUFS, colour QC). Poll edl_render_status.",
                       obj({"montage_id": STR, "draft": {"type": "boolean"}}, [])),
    "edl_render_status": (t_edl_status, "State of an edit-sheet assembly: running / completed (duration, url, path) / failed.",
                          obj({"render_id": STR}, ["render_id"])),
    "studio_api_schema": (t_api_schema, "Request-body JSON Schema of a Studio endpoint, from its OpenAPI.",
                          obj({"path": STR, "method": STR}, ["path"])),
}


def dynamic_tools() -> dict:
    return {
        "shot_lint": (t_shot_lint,
                      "Cinematographer's check of a shot list before keyframes/render: 30°/two-size rule, 180° axis, "
                      "screen direction, eyelines, establishing shots, motivated and varied camera moves, lens vs "
                      "framing, look room, reaction after key action, transition pairs, rhythm. Fix every error; keep "
                      "a warning only with `intent`. Values follow director_schema.json (shot.*, lighting.scheme).",
                      body_schema("POST", "/api/shot-lint")),
        "edl_from_shots": (t_edl_from_shots,
                           "Editor: shot cards with the job_id/asset_id of their finished MP4 → first edit sheet (EDL). "
                           "Seam transition = incoming transition_in, else outgoing transition_out, else cut.",
                           body_schema("POST", "/api/edl/from-shots")),
        "edl_plan": (t_edl_plan,
                     "Editor: resolve an edit sheet against the real MP4s without rendering — clip timings, overlaps "
                     "of xfades, sound-bridge (J-cut) leads, runtime, warnings and the shot_lint review of the clips' "
                     "shot cards. Iterate until no errors, then edl_render.",
                     body_schema("POST", "/api/edl/plan")),
        "edl_render": (t_edl_render,
                       "Editor: assemble the edit sheet into one checked MP4 on the Mac (FFmpeg, no GPU): cuts, "
                       "xfade transitions, J-cuts, music with ducking, SFX, fades. Poll edl_render_status.",
                       body_schema("POST", "/api/edl/renders")),
        "franchise_profile_update": (t_profile_update,
                                     "Replace the franchise production profile; pass the revision you read "
                                     "(0 for the first save). 409 = someone changed it, re-read.",
                                     obj({"franchise_id": STR, "profile": body_schema("PUT", "/api/franchises/{fid}/profile")},
                                         ["franchise_id", "profile"])),
        "franchise_shot_lint": (t_franchise_lint,
                                "shot_lint plus the franchise rules: lens range, camera language, dutch angles, "
                                "transition palette, shot rhythm.",
                                obj({"franchise_id": STR, "plan": body_schema("POST", "/api/franchises/{fid}/shot-lint")},
                                    ["franchise_id", "plan"])),
        "franchise_edl_from_shots": (t_franchise_edl,
                                     "edl_from_shots with the franchise edit defaults (format, music, fades).",
                                     obj({"franchise_id": STR,
                                          "request": body_schema("POST", "/api/franchises/{fid}/edl-from-shots")},
                                         ["franchise_id", "request"])),
        "edl_export": (t_edl_export,
                       "Export an edit sheet as editable timelines with all sources in one folder: STUDIO.xml for "
                       "Premiere Pro, STUDIO.fcpxml for DaVinci Resolve / Final Cut, media/, look.cube. Same body as "
                       "edl_render. No GPU.", body_schema("POST", "/api/edl/exports")),
        "edl_export_resolve": (t_edl_export_resolve,
                               "Open an export as a new timeline in the running DaVinci Resolve (current project).",
                               obj({"export_id": STR}, ["export_id"])),
        "render_enqueue": (t_render_enqueue,
                           "Queue an H3 render job (GPU, costs money). payload follows JobInput: prompt, asset_ids, "
                           "reference_numbers/roles/owners, settings. Only for an approved story or an explicit user request. "
                           "Your prompt goes to H3 as written (settings.prompt_blocks defaults to \"none\"): write camera, "
                           "lens, light, sound and acting yourself; Studio adds only the reference-role line after it.",
                           obj({"payload": body_schema("POST", "/api/jobs"), "story_approved": {"type": "boolean"}},
                               ["payload", "story_approved"])),
    }


# Share the timeline schema so graph/preflight cannot silently lose segment fields.
for _operation, _description in (
    ("graph", "Build the Motion Director execution graph locally; no upload or render."),
    ("preflight", "Build the graph and check connected ComfyUI node/model schemas. No render. "
                  "Does not verify character identity, media presence or story approval; ready_to_submit remains false."),
):
    _timeline_schema = TOOLS["motion_director_compile"][2]
    TOOLS[f"motion_director_{_operation}"] = (
        lambda a, operation=_operation: request("POST", f"/api/studio/motion-director/{operation}", a),
        _description,
        obj({**_timeline_schema["properties"],
             "seed": {"type": "integer", "minimum": 0, "maximum": 2**53 - 1},
             "steps": {"type": "integer", "minimum": 20, "maximum": 25, "default": 24}},
            [*_timeline_schema["required"], "seed"]),
    )


TOOLS["motion_director_plan_get"] = (
    t_motion_director_plan, "Read this scene's persisted Motion Director plan and revision. No render.", obj({"scene_id": STR}))
TOOLS["motion_director_execute"] = (
    t_motion_director_execute,
    "Queue the saved scene revision for Motion Director execution (GPU, costs compute). Only after story approval "
    "or an explicit request for this render. Same scene/revision returns the original receipt. No automatic regeneration. "
    "Only segments changed since the scene's last completed run (and what continues them) are regenerated; the rest come "
    "from the GPU segment cache. full_run=true regenerates everything (after a server move the cache is gone).",
    obj({"scene_id": STR, "expected_revision": {"type": "integer", "minimum": 1},
         "story_approved": {"type": "boolean"}, "full_run": {"type": "boolean"}}, ["expected_revision", "story_approved"]))
TOOLS["motion_director_jobs"] = (
    lambda a: t_motion_director_execute(a, status=True),
    "Read execution receipts and result paths for this scene; completion does not mean artistic QC passed.", obj({"scene_id": STR}))
TOOLS["motion_director_cancel_waiting"] = (
    t_motion_director_cancel_waiting,
    "Cancel a queued_local Motion Director receipt of this scene before dispatch starts. "
    "Returns conflict once transfer or execution starts; never interrupts shared GPU work.",
    obj({"scene_id": STR, "motion_id": STR}, ["motion_id"]))
TOOLS["motion_director_plan_save"] = (
    lambda a: t_motion_director_plan(a, save=True),
    "Save this scene's Motion Director plan. expected_revision=0 creates; edits require the revision from GET. "
    "Conflicts return 409; reread and merge instead of overwriting. Returns changed/dependent segment IDs. No render or cache reuse.",
    obj({"scene_id": STR, "plan": TOOLS["motion_director_graph"][2],
         "expected_revision": {"type": "integer", "minimum": 0}}, ["plan", "expected_revision"]))

# Studio ↔ ComfyUI (h3-studio-bridge): the agent reads a job's exact graph, makes a schema-checked
# edit (literal inputs only: seed, steps, sampler, shift, prompt…) and renders it after a look at the diff.
GRAPH_NOTE = ("Any model, LoRA or node may be swapped in; an unchanged graph is refused. "
              "Edit only what the A/B or the QC finding asks for. ")
TOOLS["job_graph"] = (lambda a: request("GET", f"/api/jobs/{q(a['job_id'])}/graph"),
    "Exact ComfyUI graph (API format) the job ran: node id → class_type and inputs. Read-only.",
    obj({"job_id": STR}, ["job_id"]))
TOOLS["comfy_node_schema"] = (lambda a: request("GET", f"/api/comfyui/nodes/{q(a['class_type'])}"),
    "Input schema of one ComfyUI node: types, min/max, allowed choices. Read before job_graph_edit.",
    obj({"class_type": STR}, ["class_type"]))
# Operations of app/graph_ops.py, shared by job edits and «Свои воркфлоу»; every one is checked against /object_info.
GRAPH_OPS = {"type": "array", "minItems": 1, "maxItems": 200, "items": {"type": "object", "required": ["op"], "properties": {
    "op": {"type": "string", "enum": ["set", "link", "unset", "add_node", "remove_node", "replace_node", "add_lora", "title"]},
    "node": {"description": "node id, or $ref of a node added earlier in this list"}, "input": STR, "value": {},
    "from": {"type": "array", "description": "[node id or $ref, output slot]"}, "class_type": STR,
    "inputs": {"type": "object", "description": "add_node/replace_node: {input: literal or [node, slot]}"},
    "ref": STR, "title": STR, "bypass": {"type": "boolean"}, "lora_name": STR, "strength_model": NUMBER,
    "strength_clip": NUMBER, "after": {"description": "node whose MODEL output gets the LoRA"}}}}
GRAPH_OPS_HELP = ("ops: set {node,input,value} · link {node,input,from:[node,slot]} · unset {node,input} · "
                  "add_node {class_type,inputs?,ref?} · remove_node {node,bypass?} · replace_node {node,class_type,inputs?} · "
                  "add_lora {lora_name,strength_model?,strength_clip?,after?} · title {node,title}. "
                  "Find classes with comfy_node_search, read inputs with comfy_node_class. ")
TOOLS["job_graph_edit"] = (
    lambda a: request("POST", f"/api/jobs/{q(a['job_id'])}/comfyui-edits",
                      {k: a[k] for k in ("set", "ops") if k in a}),
    GRAPH_NOTE + "Make an edit of the job's graph: set=[{node, input, value}] on literal inputs, and/or ops for "
    "structure (add/remove/replace nodes, rewire, LoRAs, other models). " + GRAPH_OPS_HELP + "Saved like the editor's «⇪ Отправить в Studio»; returns "
    "edit_id and the change list. No render.",
    obj({"job_id": STR, "set": {"type": "array", "items": obj({"node": STR, "input": STR, "value": {}},
                                                                ["node", "input", "value"])}, "ops": GRAPH_OPS}, ["job_id"]))
TOOLS["job_graph_edits"] = (lambda a: request("GET", f"/api/jobs/{q(a['job_id'])}/comfyui-edits"),
    "Edits of the job's graph (from the ComfyUI editor or job_graph_edit), newest first, with changes and problems.",
    obj({"job_id": STR}, ["job_id"]))
def t_job_graph_edit_render(a):
    gpu_gate()
    return request("POST", f"/api/jobs/{q(a['job_id'])}/comfyui-edits/{q(a['edit_id'])}/render", {})


TOOLS["job_graph_edit_render"] = (
    t_job_graph_edit_render,
    GRAPH_NOTE + "Render one edit (GPU time: only when the user asked). Needs an empty, unpaused Mac queue; "
    "the GPU API rechecks the edit and a new Studio job appears.",
    obj({"job_id": STR, "edit_id": STR}, ["job_id", "edit_id"]))

# «🎲 Подобрать seed» (local_studio/seed_hunt.py): small previews with different seeds, the picked one finished.
def t_seed_hunt_start(a):
    gpu_gate()
    return request("POST", f"/api/jobs/{q(a['job_id'])}/seed-hunt", {"count": a.get("count", 3)})


def t_seed_hunt_finish(a):
    gpu_gate()
    return request("POST", f"/api/jobs/{q(a['preview_job_id'])}/seed-hunt/finish", {})


TOOLS["seed_hunt_start"] = (t_seed_hunt_start,
    "Seed hunt for a finished job (GPU time: only when the user asked): queue `count` (2–6, default 3) previews — "
    "pass 1 of the two-pass render at half size, every step, own seed (preview 1 keeps the job's seed). "
    "Look at them (job_frames / view_image), then seed_hunt_finish on the best one.",
    obj({"job_id": STR, "count": INT}, ["job_id"]))
TOOLS["seed_hunt_get"] = (lambda a: request("GET", f"/api/seed-hunts/{q(a['hunt_id'])}"),
    "Previews and finish of one seed hunt: job ids, states, seeds, index.", obj({"hunt_id": STR}, ["hunt_id"]))
TOOLS["seed_hunt_finish"] = (t_seed_hunt_finish,
    "Finish one completed preview at full size: its saved latent is upscaled and pass 2 re-runs the schedule "
    "tail, so the clip is that preview, sharper. Same GPU server as the preview (the package lives there).",
    obj({"preview_job_id": STR}, ["preview_job_id"]))

# «Превиз» (local_studio/previs.py): Blockout on this Mac as the staging stage — scale, marks, lens, camera move.
PREVIS_NOTE = ("Blockout previs (meters; +X screen right, -Z away; heading 0 faces -Z, clockwise from above). ")
TOOLS["previs_status"] = (lambda a: request("GET", "/api/previs/status"),
    "Is the Blockout previs stage running; its current scene and shot.", obj({}))
TOOLS["previs_stage"] = (
    lambda a: request("POST", "/api/previs/call", {"action": a["action"], "params": a.get("params") or {}}),
    PREVIS_NOTE + "Stage a shot before keyframes: call get_state first, then e.g. set_shot {name,duration,aspect:'9:16'}, "
    "list_assets, add_entity {assetId:'person.man'|'animal.cat'…, x, z, rotationDeg} (no label: it is drawn into the "
    "frame), add_actor_mark "
    "{entityId,x,z,time,gait}, add_camera_mark {x,y,z,panDeg,tiltDeg,time,focalLength}, apply_framing, "
    "list_camera_moves / apply_camera_move, set_time. Real scale matters: a house cat beside an adult man. "
    "Look with previs_view; nothing reaches the GPU.",
    obj({"action": {"type": "string", "enum": sorted(
        ["get_state", "list_assets", "add_entity", "move_entity", "delete_entity", "add_actor_mark", "add_camera_mark",
         "clear_camera_marks", "set_shot", "new_shot", "apply_framing", "list_action_presets", "apply_action_preset",
         "list_sequence_styles", "spawn_sequence", "list_camera_moves", "apply_camera_move", "set_track_subject",
         "snap_to_ground", "set_time", "play", "stop", "list_presets", "save_preset", "apply_preset"])},
         "params": {"type": "object"}}, ["action"]))
TOOLS["previs_view"] = (
    lambda a: (lambda got: {"_image": base64.b64encode(got[0]).decode(), "_mime": "image/png"})(
        request("GET", "/api/previs/frame", raw=True)),
    "Look at the staged previs frame (grey figures, real lens). Read-only.", obj({}))
TOOLS["previs_motion"] = (
    lambda a: request("POST", "/api/previs/motion", {k: a[k] for k in ("name", "fps") if k in a}, timeout=600),
    "Render the staged shot's moving camera (2+ camera marks or apply_camera_move; set_shot duration = the take's "
    "seconds, 1–15) as a grey video into the Studio library with the role «Камера»: H3 takes framing, camera movement "
    "and depth from it, never the mannequins' look or gait. Returns asset_id and duration_s.",
    obj({"name": STR, "fps": {"type": "integer", "minimum": 2, "maximum": 12}}))
TOOLS["previs_snapshot"] = (
    lambda a: request("POST", "/api/previs/snapshot", {k: a[k] for k in ("name", "time") if k in a}),
    "Save the staged frame (at `time` s) into the Studio library as «Композиция»: cut to the shot aspect, grey and "
    "blurred — where figures stand, shot size and angle, never faces. Returns asset_id; use it as the layout "
    "reference of that shot's keyframe.", obj({"name": STR, "time": NUMBER}))

TOOLS["compute_instances"] = (
    lambda a: request("GET", "/api/compute/instances"),
    "List Vast instances and their verified SSH endpoints. Match a pasted instance ID before deployment.", obj({}))
TOOLS["compute_deploy"] = (
    lambda a: request("POST", f"/api/compute/instances/{a['instance_id']}/deploy"),
    "Start the existing Mac-local H3 deployment for this Vast instance. Use only when the user asked to deploy that exact instance. Returns an operation ID; do not start duplicates.",
    obj({"instance_id": {"type": "integer", "minimum": 1}}, ["instance_id"]))
TOOLS["compute_operation"] = (
    lambda a: request("GET", f"/api/compute/operations/{a['operation_id']}"),
    "Read deployment state and recent log lines. Report actual progress; ready means state=ready.",
    obj({"operation_id": STR}, ["operation_id"]))

# «Свои воркфлоу» (local_studio/user_workflows.py): any ComfyUI API graph the user imported, run as a form.
def t_workflow_run(a):
    gpu_gate()
    return request("POST", f"/api/workflows/{q(a['workflow_id'])}/run",
                   {"values": a.get("values") or {}, "count": a.get("count", 1)})


TOOLS["workflow_list"] = (lambda a: request("GET", "/api/workflows"),
    "User's own ComfyUI workflows imported into Studio («Свои воркфлоу»): id, name, form inputs, models, "
    "check against the live ComfyUI (missing nodes). Separate from the H3 production path. Read-only.", obj({}))
TOOLS["workflow_get"] = (lambda a: request("GET", f"/api/workflows/{q(a['workflow_id'])}"),
    "One workflow's form: each input's key, kind (image/video/audio = library asset_id, text, seed, int, float), "
    "label and default. Read before workflow_run.", obj({"workflow_id": STR}, ["workflow_id"]))
TOOLS["workflow_import"] = (
    lambda a: request("POST", "/api/workflows", {k: a[k] for k in ("name", "description", "graph", "params") if k in a}),
    "Save a ComfyUI graph in API format (node id → {class_type, inputs}) as a Studio workflow. Studio detects the "
    "inputs (loaders, prompts, seed shown; numbers hidden); params=[{key, exposed, label}] changes that. No render.",
    obj({"name": STR, "description": STR, "graph": {"type": "object"},
         "params": {"type": "array", "items": obj({"key": STR, "exposed": {"type": "boolean"}, "label": STR}, ["key"])}},
        ["name", "graph"]))
TOOLS["workflow_run"] = (
    t_workflow_run,
    "Queue a workflow on the GPU's ComfyUI (GPU time: only when the user asked). values={input key: value} for "
    "the form's inputs only; omitted inputs keep the graph's value, an omitted seed is random; count 1–4 runs "
    "(a given seed goes seed, seed+1…). Returns job ids; follow with workflow_job_get.",
    obj({"workflow_id": STR, "values": {"type": "object"}, "count": {"type": "integer", "minimum": 1, "maximum": 4}},
        ["workflow_id"]))
TOOLS["workflow_jobs"] = (
    lambda a: request("GET", "/api/workflow-jobs" + (f"?workflow_id={q(a['workflow_id'])}" if a.get("workflow_id") else "")),
    "Runs of user workflows, newest first: state, values, output files with URLs. Read-only.", obj({"workflow_id": STR}))
TOOLS["workflow_job_get"] = (lambda a: request("GET", f"/api/workflow-jobs/{q(a['job_id'])}"),
    "One workflow run: state, error, output files (url, kind, sha256). Completed is not QC acceptance.",
    obj({"job_id": STR}, ["job_id"]))

TOOLS["workflow_graph_update"] = (
    lambda a: request("PUT", f"/api/workflows/{q(a['workflow_id'])}/graph",
                      {"expected_revision": a["expected_revision"], "graph": a["graph"]}),
    "Replace a user workflow's ComfyUI API graph (same id, next revision; form choices kept for inputs that still "
    "exist). Read workflow_get first for the revision; 409 = someone changed it meanwhile.",
    obj({"workflow_id": STR, "expected_revision": {"type": "integer", "minimum": 1}, "graph": {"type": "object"}},
        ["workflow_id", "expected_revision", "graph"]))
TOOLS["workflow_graph_edit"] = (
    lambda a: request("POST", f"/api/workflows/{q(a['workflow_id'])}/edit",
                      {k: a[k] for k in ("expected_revision", "ops", "note", "dry_run") if k in a}),
    "Change a user workflow's graph with operations checked against the live ComfyUI /object_info: values "
    "(steps, sampler, scheduler, shift, resolution, LoRA strength, reference file), nodes, LoRAs, links. " + GRAPH_OPS_HELP +
    "dry_run=true returns the new graph, diff and problems without saving; otherwise it is saved as the next "
    "revision (the old one stays in workflow_versions). Any model or LoRA may be used; turbo/quantized weights come back as warnings. No render.",
    obj({"workflow_id": STR, "expected_revision": {"type": "integer", "minimum": 1}, "ops": GRAPH_OPS, "note": STR,
         "dry_run": {"type": "boolean"}}, ["workflow_id", "expected_revision", "ops"]))
TOOLS["workflow_versions"] = (lambda a: request("GET", f"/api/workflows/{q(a['workflow_id'])}/versions"),
    "Revision history of a user workflow: revision, note (what changed), node count, which one is current. Read-only.",
    obj({"workflow_id": STR}, ["workflow_id"]))
TOOLS["workflow_version_get"] = (
    lambda a: request("GET", f"/api/workflows/{q(a['workflow_id'])}/versions/{int(a['revision'])}"),
    "One past revision's graph and its diff to the current graph. Read-only.",
    obj({"workflow_id": STR, "revision": {"type": "integer", "minimum": 1}}, ["workflow_id", "revision"]))
TOOLS["workflow_restore"] = (
    lambda a: request("POST", f"/api/workflows/{q(a['workflow_id'])}/restore",
                      {"expected_revision": a["expected_revision"], "revision": a["revision"]}),
    "Make a past revision current again (saved as a new revision; history is never rewritten). No render.",
    obj({"workflow_id": STR, "expected_revision": {"type": "integer", "minimum": 1},
         "revision": {"type": "integer", "minimum": 1}}, ["workflow_id", "expected_revision", "revision"]))
TOOLS["workflow_from_job"] = (
    lambda a: request("POST", f"/api/workflows/from-job/{q(a['job_id'])}", {k: a[k] for k in ("name",) if k in a}),
    "Copy an H3 job's exact ComfyUI graph into a new user workflow (from the GPU, or the Mac archive once the "
    "server is gone), so nodes, LoRAs and references can be changed and run there. The job and the accepted "
    "recipe are not touched. Returns the workflow with its form.",
    obj({"job_id": STR, "name": STR}, ["job_id"]))
TOOLS["workflow_from_recipe"] = (
    lambda a: request("POST", f"/api/workflows/from-recipe/{q(a['recipe_id'])}", {k: a[k] for k in ("name",) if k in a}),
    "Copy the graph of an accepted recipe's job (studio_accepted_recipes) into a new user workflow. 409 when that "
    "job's graph was neither on a live GPU nor archived on the Mac. The recipe itself never changes.",
    obj({"recipe_id": STR, "name": STR}, ["recipe_id"]))
TOOLS["comfy_node_search"] = (
    lambda a: request("GET", "/api/comfy/classes?" + urllib.parse.urlencode({"q": a.get("query", ""), "limit": a.get("limit", 30)})),
    "Search the node classes of the live ComfyUI (name, display name, category, pack): inputs and output types. "
    "Use before add_node / replace_node. Read-only.", obj({"query": STR, "limit": INT}))
TOOLS["comfy_node_class"] = (lambda a: request("GET", f"/api/comfy/classes/{q(a['class_type'])}"),
    "One node class of the live ComfyUI as a form: every input with type, default, min/max or choices (installed "
    "LoRAs, samplers…), and its outputs. Read-only.", obj({"class_type": STR}, ["class_type"]))
TOOLS["comfy_graph_check"] = (lambda a: request("POST", "/api/comfy/graph/check", {"graph": a["graph"]}),
    "Check any ComfyUI API graph against the live /object_info without running it: unknown nodes, missing or "
    "unknown inputs, values outside choices/ranges, wrong link types, cycles. Read-only.",
    obj({"graph": {"type": "object"}}, ["graph"]))
# «Узлы и модели» (local_studio/comfy_manager.py): GPU inventory + the user's pinned ComfyUI manifest.
TOOLS["comfy_inventory"] = (lambda a: request("GET", "/api/comfy/inventory" + ("?fresh=true" if a.get("fresh") else "")),
    "What the live ComfyUI on the GPU has: node packs with their node classes, model files per folder. Read-only.",
    obj({"fresh": {"type": "boolean"}}))
TOOLS["comfy_manifest"] = (lambda a: request("GET", "/api/comfy/manifest"),
    "The user's ComfyUI manifest: node packs pinned to a git commit and models pinned to a SHA-256, each marked "
    "installed / pending on the current GPU. Read-only.", obj({}))
TOOLS["comfy_manifest_add_node"] = (
    lambda a: request("POST", "/api/comfy/manifest/nodes", {k: a[k] for k in ("repo", "commit", "pip", "note") if k in a}),
    "Add a ComfyUI node pack to the manifest: https git repo, full 40-char commit, optional exact pip pins "
    "(pkg==1.2.3). Installs nothing by itself: the user presses «Применить на GPU» in «Узлы и модели».",
    obj({"repo": STR, "commit": STR, "pip": {"type": "array", "items": STR}, "note": STR}, ["repo", "commit"]))
TOOLS["comfy_manifest_add_model"] = (
    lambda a: request("POST", "/api/comfy/manifest/models", {k: a[k] for k in ("folder", "filename", "url", "sha256", "note") if k in a}),
    "Add a model file to the manifest: ComfyUI folder (checkpoints, loras, vae…), file name, direct https URL, "
    "SHA-256. Never downloads to the Mac; installed on the GPU by «Применить на GPU».",
    obj({"folder": STR, "filename": STR, "url": STR, "sha256": STR, "note": STR}, ["folder", "filename", "url", "sha256"]))
TOOLS["studio_setup_status"] = (lambda a: request("GET", "/api/setup/status"),
    "First-run readiness of this Studio install: Python, data folder, ffmpeg, GPU API, ComfyUI, bridge, current "
    "server, SSH key, MCP hookup — each with the fix. Read-only.", obj({}))

WRITES = {"workflow_import", "workflow_run", "workflow_graph_update", "workflow_graph_edit", "workflow_restore",
          "workflow_from_job", "workflow_from_recipe", "comfy_manifest_add_node", "comfy_manifest_add_model",
          "compute_deploy", "motion_director_cancel_waiting", "motion_director_execute", "motion_director_plan_save", "motion_director_upload_image", "motion_director_upload_video", "production_review_record", "motion_reference_prepare", "franchise_entity_create", "franchise_entity_update", "job_cancel", "storyboard_concat",
          "render_enqueue", "edl_render", "freecut_timeline", "montage_create", "montage_update", "montage_edit", "montage_render", "edl_export", "edl_export_resolve", "story_approval_record",
          "studio_apply_draft", "studio_apply_scene", "studio_patch_shot", "studio_build_scene", "studio_stop_build", "studio_reset_scene", "studio_select_scene", "studio_new_scene",
          "franchise_profile_update", "previs_snapshot", "previs_motion"}


WRITES.update({"job_graph_edit", "job_graph_edit_render", "seed_hunt_start", "seed_hunt_finish",
               "job_snap_zoom", "video_swap_plan", "video_swap_auto", "world_swap_auto", "asset_import_image", "asset_import_chat_file", "studio_set_start_frame",
               "image_request_claim", "image_request_complete", "image_request_fail", "studio_run_code_write"})


def annotations(name: str) -> dict:
    """MCP hints: clients (Codex `default_tools_approval_mode = "writes"`) run reads freely, ask before writes."""
    return {"readOnlyHint": name not in WRITES, "destructiveHint": name == "job_cancel",
            "idempotentHint": name not in WRITES, "openWorldHint": False}


def all_tools() -> dict:
    """Write tools whose payload schema comes from the live OpenAPI are rebuilt on every call."""
    if os.environ.get("H3_IMAGE_COMPANION_ONLY") == "1":
        allowed = {"image_companion", "image_requests", "image_request_status", "image_request_claim",
                   "image_request_complete", "image_request_fail", "view_image"}
        return {name: TOOLS[name] for name in allowed}
    tools = {**TOOLS, **dynamic_tools()}
    return {**tools, **code_tools(tools)}


# ---------- code mode: one sandboxed script calls many tools in one round trip (scripts/h3_code_mode.py)

def _load_code_mode():
    """By path, so scripts/ never shadows a module name on sys.path."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "h3_code_mode", os.path.join(os.path.dirname(os.path.abspath(__file__)), "h3_code_mode.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


h3_code_mode = _load_code_mode()

CODE_TOOLS = {"studio_run_code", "studio_run_code_write", "studio_code_functions"}
# Never from a script: these need the user's own words or eyes on the result in a visible single call.
CODE_DENY = {"compute_deploy",            # GPU server deploy: only on the user's request
             "story_approval_record",     # the user's approval, recorded in their words
             "production_review_record",  # QC acceptance needs the frames seen; code mode returns no images
             "image_companion", "asset_import_chat_file"}  # host UI / chat file params
# Spend GPU time: only from studio_run_code_write with gpu=true, at most one per script, behind the handler's own gates.
CODE_GPU = {"render_enqueue", "job_graph_edit_render", "seed_hunt_start", "seed_hunt_finish", "video_swap_auto",
            "world_swap_auto", "studio_build_scene", "motion_director_execute", "workflow_run"}
CODE_SCHEMA = obj({"code": {"type": "string", "description": "Python body. Tools are functions: jobs_list(limit=5). "
                            "`return x` or a bare last expression is the result; print() output is returned too."},
                   "timeout_s": {"type": "integer", "minimum": 1, "maximum": 300, "default": 60}}, ["code"])


def code_functions(tools: dict) -> list[str]:
    return sorted(name for name in tools if name not in CODE_TOOLS | CODE_DENY)


def code_policy(name: str, args: dict, write: bool, gpu: bool, spent: list) -> None:
    if name in CODE_TOOLS | CODE_DENY:
        raise StudioError(f"{name} недоступен в коде: вызови его отдельным инструментом")
    is_write = name in WRITES or (name == "scene_run" and args.get("action", "status") != "status")
    is_gpu = name in CODE_GPU or (name == "scene_run" and args.get("action") == "resume")
    if is_write and not write:
        raise StudioError(f"{name} меняет Studio: в studio_run_code только чтение, запись — studio_run_code_write")
    if is_gpu and not gpu:
        raise StudioError(f"{name} тратит GPU: только studio_run_code_write с gpu=true и по разрешению пользователя")
    if is_gpu and spent:
        raise StudioError(f"{name}: одно GPU-действие на скрипт (уже: {spent[0]})")
    if is_gpu:
        spent.append(name)


def t_run_code(a, write=False):
    tools, spent = all_tools(), []
    gpu = write and a.get("gpu") is True

    def dispatch(name, args):
        if name not in tools:
            raise StudioError(f"Нет инструмента {name}; список — studio_code_functions")
        code_policy(name, args, write, gpu, spent)
        try:
            result = tools[name][0](args)
        except (KeyError, TypeError, ValueError) as exc:
            raise StudioError(f"{name}: неверные аргументы: {exc}") from exc
        if isinstance(result, dict) and "_image" in result:  # pictures do not travel through code: facts only
            facts = json.loads(result["_text"]) if result.get("_text") else {}
            return {**facts, "image_omitted": "смотри картинку отдельным вызовом " + name}
        return result

    out = h3_code_mode.run(a["code"], code_functions(tools), dispatch,
                           timeout=min(max(int(a.get("timeout_s") or 60), 1), 300))
    if out.get("error"):
        raise StudioError(json.dumps(out, ensure_ascii=False, separators=(",", ":"))[:MAX_TEXT])
    return out


def t_code_functions(a):
    tools, needle = all_tools(), (a.get("query") or "").lower()
    lines = [h3_code_mode.signature(n, tools[n][1], tools[n][2]) for n in code_functions(tools)
             if needle in n or needle in (tools[n][1] or "").lower()]
    marks = {n: ("gpu" if n in CODE_GPU else "write" if n in WRITES else "read") for n in code_functions(tools)}
    return {"functions": [f"[{marks[line.split('(')[0]]}] {line}" for line in lines],
            "denied_in_code": sorted(CODE_DENY), "modules": list(h3_code_mode.SAFE_MODULES)}


def code_tools(tools: dict) -> dict:
    names = ", ".join(code_functions(tools))
    common = ("Code mode: run a short Python script in a sandbox (no network, files or processes; modules: "
              + ", ".join(h3_code_mode.SAFE_MODULES[:8]) + "…) where every Studio tool is a function taking its "
              "inputSchema fields as keyword arguments and returning the parsed result; a refusal raises StudioError. "
              "Only what the script prints and returns comes back, so filter and join results inside. Typed signatures: "
              "studio_code_functions(query). Images are not returned (view them with their own tool). Not callable: "
              + ", ".join(sorted(CODE_DENY)) + ".")
    return {
        "studio_run_code": (t_run_code, "Read-only " + common + " Any write tool is refused here. Functions: " + names,
                            CODE_SCHEMA),
        "studio_run_code_write": (
            lambda a: t_run_code(a, write=True),
            "Same as studio_run_code but write tools work, each behind its own gates (story approval, franchise scope, "
            "run gpu gate, take contract). GPU tools (" + ", ".join(sorted(CODE_GPU)) + ") need gpu=true, the "
            "user's explicit request for that render, and run at most once per script. " + common,
            obj({**CODE_SCHEMA["properties"], "gpu": {"type": "boolean", "default": False}}, ["code"])),
        "studio_code_functions": (t_code_functions, "Typed Python signatures of the functions code mode offers "
                                  "(generated from the tool schemas), marked read/write/gpu. query filters by name or text.",
                                  obj({"query": STR})),
    }


COMPANION_URI = "ui://studio/image-companion"


def companion_resource():
    folder = Path(REPO) / "integrations" / "chatgpt-image-companion"
    html = (folder / "companion.html").read_text(encoding="utf-8")
    script = (folder / "companion.mjs").read_text(encoding="utf-8")
    return {"uri": COMPANION_URI, "mimeType": "text/html;profile=mcp-app",
            "text": html.replace("/*COMPANION_SCRIPT*/", script),
            "_meta": {"ui": {"csp": {"connectDomains": [], "resourceDomains": []}},
                      "openai/widgetCSP": {"connect_domains": [], "resource_domains": []}}}


def call_tool(name: str, args: dict) -> dict:
    tools = all_tools()
    if name not in tools:
        return {"content": [{"type": "text", "text": f"Unknown tool {name}"}], "isError": True}
    try:
        result = tools[name][0](args or {})
    except StudioError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except (KeyError, TypeError, ValueError) as exc:
        return {"content": [{"type": "text", "text": f"Неверные аргументы: {exc}"}], "isError": True}
    if isinstance(result, dict) and "_image" in result:
        image = {"type": "image", "data": result["_image"], "mimeType": result["_mime"]}
        return {"content": [image, *([{"type": "text", "text": result["_text"]}] if result.get("_text") else [])]}
    text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))  # compact: every byte is resent with each later call
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + f"\n… обрезано ({len(text)} знаков); сузь запрос (limit, query)."
    return {"content": [{"type": "text", "text": text}]}


def handle(message: dict):
    method, mid = message.get("method"), message.get("id")
    if mid is None:
        return None  # notification
    if method == "initialize":
        version = (message.get("params") or {}).get("protocolVersion") or "2025-06-18"
        result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
                  "serverInfo": {"name": "h3-studio", "version": VERSION}, "instructions": INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{"name": n, "description": d, "inputSchema": s, "annotations": annotations(n),
                             **({"title": "STUDIO Image Companion", "_meta": {"ui": {"resourceUri": COMPANION_URI,
                                  "visibility": ["model", "app"]}, "openai/ui": {"entrypoints": [{"type": "global"}, {"type": "thread"}]}}}
                                if n == "image_companion" else {}),
                             **({"_meta": {"openai/fileParams": ["image"]}} if n in {"asset_import_chat_file", "image_request_complete"} else {})}
                           for n, (_, d, s) in all_tools().items()]}
    elif method == "resources/list":
        result = {"resources": [{"uri": COMPANION_URI, "name": "STUDIO Image Companion", "mimeType": "text/html;profile=mcp-app"}]}
    elif method == "resources/read":
        if (message.get("params") or {}).get("uri") != COMPANION_URI:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "Unknown resource"}}
        result = {"contents": [companion_resource()]}
    elif method == "tools/call":
        params = message.get("params") or {}
        result = call_tool(params.get("name", ""), params.get("arguments") or {})
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = handle(message)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
