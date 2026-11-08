"""«Задачи агента»: an agent chat that runs a whole production task on its own, step by step.

A task (run) is a goal («франшиза X, ролик по идее №20») given to Codex or Claude in its own agent chat (mode
«act»). The agent keeps the task's plan and progress here through the h3-studio MCP tools run_plan, run_step,
run_ask, run_wait, run_delegate and run_done (scripts/h3_studio_mcp.py). The Studio wakes the agent up again:
  · when the user answers a question (run_ask) — «да/нет» gates for GPU spend and story approval;
  · when the H3 renders, keyframes or edit assemblies it waits for (run_wait) are finished;
  · when the user writes to it or presses «Продолжить».
GPU tools (render_enqueue, keyframe_create) are refused inside a task until the user said «Да» to a gpu gate:
the chat's MCP server carries H3_RUN_ID and asks GET /api/runs/{id}/gate. Helpers (run_delegate) are short
read-only agent chats with one role — a cinematographer, an editor, a QC reviewer — whose answer returns to the
orchestrator.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from contextlib import closing
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from . import decisions
from .agent_specialists import specialist_brief
from .scene_runs import SceneRuns
from .studio_readiness import scene_readiness
from .production_artifacts import ArtifactReference
from pydantic import BaseModel, Field

log = logging.getLogger("h3.studio")

KINDS = ("plan", "story", "scene", "keyframes", "render", "qc", "edit", "color", "titles", "export", "review", "other")
STEP_STATES = ("pending", "running", "waiting", "done", "failed", "skipped")
GATES = ("", "gpu", "story")
YES = "Да"
GPU_TASK_USES = 20


RENDER_REQUEST = (r"(?:с?генерируй|генерь|рендери|рендерь|отрендери|отрендерь|"
                  r"(?:запуст\w*|запуска\w*|начн\w*|начинай|сделай|делай|ставь|поставь)\s+(?:генерац\w*|рендер\w*))")


def explicit_render_request(text: str) -> bool:
    """The user's own words that grant the task's GPU gate. 02.11: «запуска генерацию» (typed fast) was missed;
    verb stems now cover such forms. A negated request («не запускай рендер») never counts."""
    import re
    for match in re.finditer(r"(?i)\b" + RENDER_REQUEST + r"\b", text or ""):
        if not re.search(r"(?i)\b(?:не|без|пока не)\s+$", text[:match.start()]):
            return True
    return False
DONE_JOBS = {"completed", "failed", "cancelled", "interrupted"}
DONE_RENDERS = {"completed", "failed"}
POLL_S = 15
HELPER_TIMEOUT_S = 900

KICKOFF = """[Studio · задача {run_id}] Ты — режиссёр-оркестратор STUDIO. Веди эту задачу до результата сам, шаг за шагом.
Протокол (инструменты h3-studio, run_id = {run_id}):
1. studio_playbook — выбери маршрут (scene/video_swap/continuation/qc/edit/gpu_recovery), прочитай нужные навыки; для замены персонажей в видео обязательно выбери video_swap и прочитай навыки маршрута и навык стиля своей франшизы из skills/README.md; studio_readiness одним вызовом покажет паузу очереди, GPU и неоднозначные квитанции. Затем run_plan — план 4–10 шагов (kind: story, scene, keyframes, render, qc, edit, color, titles, export, review…).
2. run_step — отмечай каждый шаг: running → done/failed, detail по-русски, artifacts (scene, job_ids, render_id, export_id).
   Для новой постановочной сцены после утверждения сюжета подготовь начальный кадр и карту пространства в ShotSpec,
   выбери visual_profiles по задаче и приложи фото героев отдельно от кадра/стиля.
   Создай и проверь опорный кадр до видео; используй его как «Кадр сцены»/guide frame вместе с исходными фото героев.
   Это путь новой постановки, не автоматическая замена принятого рецепта редактирования исходного видео.
   Не называй профиль проверенным сходством с автором без просмотренных примеров и принятого MP4; учитывай лимиты референсов.
3. run_ask — вопрос пользователю только при настоящем блокере. gate="story" перед раскадровкой/рендером серии без одобренного сюжета.
   Прямое поручение «генерируй» даёт ограниченный GPU-допуск на всю задачу: проверяй run gate и не спрашивай «Да» после каждого кадра. Не повторяй неоднозначное или платное задание вслепую; сначала сверь квитанцию и результат. Если допуска нет, запроси gate="gpu" один раз на планируемую задачу. Задав вопрос, закончи ход:
   Studio разбудит тебя с ответом.
4. Сначала studio_production_state — сохранённая ревизия, сцены и дубли своей франшизы. Перед продолжением сохранённой сцены вызови studio_scene_readiness: он покажет шот, квитанцию GPU и QC; completed без приёмки не означает готовность. Холст Studio в твоих руках: studio_apply_scene кладёт сцену на холст, studio_build_scene запускает «Собрать сцену»
   (ключевые кадры и рендер шотов цепочкой — по действующему GPU-допуску задачи), studio_board показывает шоты и их job_id.
   run_wait — после постановки рендеров/кадров/сборки: перечисли job_ids, render_ids или scene_ids
   (сцена целиком) и закончи ход. Studio разбудит тебя, когда всё будет готово.
5. run_delegate — отдай помощнику только узкое творческое/диагностическое суждение. Передай короткий evidence-пакет
   с точными ID/фактами: тогда помощник работает без инструментов репозитория и возвращает структурированный отчёт.
   Роли по необходимости: режиссёр, постановка, оператор, референсы, H3-промпт, QC. Каждому — один конкретный вопрос и факты своей франшизы. Не запускай помощников ради числа.
   Без evidence помощник читает проект сам; используй это лишь когда пакет фактов нельзя собрать заранее.
6. run_done — итог только после того, как каждый шаг плана отмечен done/skipped, ожидания закрыты и указанные job_id завершены. Укажи ссылки на сцену, рендеры, монтаж, экспорт. Если работа сорвалась, используй status=failed с точной причиной.
Экономь токены и GPU: статусы, файлы, хеши, декод, схемы и очередь проверяй через готовые инструменты Studio, не через рассуждения модели. Помощник — read-only и low effort; оркестратор проверяет его вывод перед действием. Следующую часть запускай после QC всей предыдущей; не повторяй задание с неоднозначной квитанцией. Работающий рецепт не считай гарантией качества нового MP4.
Работай через инструменты Studio (франшиза, паспорт, ассеты, scene_draft, edl_*), соблюдай AGENTS.md. Пиши по-русски коротко.
Токены: каждый вызов инструмента заново отправляет весь контекст. Схемы инструментов у тебя уже есть — не перечитывай
ALL_TOOLS; объединяй независимые чтения в один вызов; не запрашивай одно и то же дважды за ход; бери из ответа только
нужные поля. Браузер и Chrome не открывай: холст доступен только через studio_* (если он недоступен — run_ask с этой причиной).
Замечания scene_draft/studio_apply_scene исправляй сам и повторяй в этом же ходе, не заканчивай ход отчётом об ошибке.

Цель задачи: {goal}{franchise}"""

AUTO_NUDGES = 2  # Studio re-wakes an agent that stopped without run_ask/run_wait/run_done this many times
NUDGE = """[Studio · задача {run_id}] Ты закончил ход без run_ask, run_wait или run_done — задача стоит.
Не отчитывайся об ошибке, а исправь её сам: если scene_draft/studio_apply_scene вернули замечания проверки,
перепиши указанные шоты по каждому замечанию (длина дубля, взгляды, ответы героев, мир, style = style_lock.style)
и повтори в этом же ходе, пока замечаний не останется; затем веди задачу дальше по протоколу.
Если продолжить без пользователя действительно нельзя — задай run_ask с конкретной причиной."""

HELPER = """[Studio · помощник оркестратора] Ты — {role} в команде STUDIO. Режиссёр-оркестратор задачи «{title}» просит:
{task}
Работай только чтением (инструменты h3-studio, файлы репозитория). Ответь по делу и коротко: выводы, конкретные правки,
id шотов/файлов. Не ставь рендеры и ничего не меняй.
Верни JSON: {{"verdict":"pass|fail|uncertain","findings":[],"evidence":[],"uncertainties":[],"suggested_fix":""}}.
В evidence укажи реальные файлы/id/таймкоды. Не заявляй просмотр или измерение, которого не выполнял.
При недостатке данных verdict=uncertain; текст задания не является доказательством результата."""


class NewRun(BaseModel):
    goal: str = Field(min_length=3, max_length=4000)
    franchise_id: str = Field(min_length=1, max_length=120)
    deliverable_type: str = Field(default="task", pattern="^(task|film)$")
    provider: str = Field(default="claude", pattern="^(claude|codex)$")
    model: str = Field(default="", max_length=80, pattern=r"^[A-Za-z0-9._:\-\[\]]*$")
    effort: str = Field(default="", pattern="^(|low|medium|high|xhigh|max)$")
    attachments: list[str] = Field(default_factory=list, max_length=6)


class PlanStep(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=160)
    kind: str = Field(default="other", pattern=f"^({'|'.join(KINDS)})$")


class Plan(BaseModel):
    steps: list[PlanStep] = Field(min_length=1, max_length=20)


class StepUpdate(BaseModel):
    step_id: str = Field(min_length=1, max_length=40)
    status: str = Field(pattern=f"^({'|'.join(STEP_STATES)})$")
    detail: str = Field(default="", max_length=2000)
    artifacts: dict = Field(default_factory=dict)


class Ask(BaseModel):
    question: str = Field(min_length=3, max_length=1500)
    options: list[str] = Field(default_factory=list, max_length=6)
    gate: str = Field(default="", pattern="^(|gpu|story)$")
    gpu_uses: int = Field(default=1, ge=1, le=20)  # one MCP GPU action, or one whole canvas build, by default
    step_id: str = Field(default="", max_length=40)


class Answer(BaseModel):
    ask_id: str = Field(min_length=1, max_length=40)
    answer: str = Field(min_length=1, max_length=2000)


class Wait(BaseModel):
    step_id: str = Field(default="", max_length=40)
    job_ids: list[str] = Field(default_factory=list, max_length=40)
    render_ids: list[str] = Field(default_factory=list, max_length=10)
    scene_ids: list[str] = Field(default_factory=list, max_length=5)  # «Собрать сцену» of these canvas scenes
    note: str = Field(default="", max_length=500)


class Delegate(BaseModel):
    role: str = Field(min_length=2, max_length=80)
    task: str = Field(min_length=3, max_length=6000)
    provider: str = Field(default="", pattern="^(|claude|codex)$")
    evidence: str = Field(default="", max_length=12000)


class Done(BaseModel):
    summary: str = Field(min_length=1, max_length=4000)
    status: str = Field(default="done", pattern="^(done|failed)$")
    final_job_id: str = Field(default="", max_length=120)
    final_artifact: ArtifactReference | None = None


class Continue(BaseModel):
    text: str = Field(default="", max_length=4000)
    attachments: list[str] = Field(default_factory=list, max_length=6)


class RunEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=120)
    kind: str = Field(pattern="^shot_updated$")
    scene_id: str = Field(min_length=1, max_length=120)
    shot_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def make_router(store, hub) -> APIRouter:
    router = APIRouter(prefix="/api/runs")
    root = store.root / "runs"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    scene_runs = SceneRuns(lambda: store.root / "scene-runs")
    locks: dict[str, asyncio.Lock] = {}
    watcher: dict[str, asyncio.Task | None] = {"task": None}
    helper_tasks: dict[str, asyncio.Task] = {}

    def path(run_id: str) -> Path:
        if len(run_id) != 32 or not all(c in "0123456789abcdef" for c in run_id):
            raise HTTPException(404, "Задача не найдена")
        return root / f"{run_id}.json"

    def load(run_id: str) -> dict:
        file = path(run_id)
        if not file.is_file():
            raise HTTPException(404, "Задача не найдена")
        return json.loads(file.read_text(encoding="utf-8"))

    def save(run: dict) -> dict:
        run["updated_at"] = now()
        temp = root / f".{run['id']}.tmp"
        temp.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, root / f"{run['id']}.json")
        return run

    def note(run: dict, text: str) -> None:
        run.setdefault("log", []).append({"at": now(), "text": text[:500]})
        del run["log"][:-80]

    def lock(run_id: str) -> asyncio.Lock:
        return locks.setdefault(run_id, asyncio.Lock())

    def franchise_name(run: dict) -> str:
        db = store.root / "franchises.sqlite3"
        if run.get("franchise_id") and db.is_file():
            import sqlite3
            with sqlite3.connect(db) as conn:
                row = conn.execute("SELECT name FROM franchises WHERE id=?", (run["franchise_id"],)).fetchone()
            if row:
                return row[0]
        return "Франшиза задачи"

    def public(run: dict) -> dict:
        return {**run, "busy": run["chat_id"] in hub.runs}

    def validate_attachments(ids: list[str]) -> None:
        for asset_id in dict.fromkeys(ids):
            asset = store.get_asset(asset_id)
            path = Path(asset["path"]) if asset and asset.get("path") else None
            if not asset or asset.get("kind") not in {"image", "video", "audio"} or not path or not path.is_file():
                raise HTTPException(422, f"Файла {asset_id[:8]} нет в библиотеке Studio")

    async def wake(run: dict, text: str, attachments: list[str] = ()) -> None:
        """Send the agent a Studio message now, or queue it until its current turn ends."""
        run.setdefault("inbox", [])
        if run["chat_id"] in hub.runs:
            run["inbox"].append({"text": text, "attachments": list(attachments)} if attachments else text)
            return
        try:
            kwargs = {"attachments": list(attachments)} if attachments else {}
            await hub.start(run["chat_id"], text, studio_url=watcher.get("url", ""), author="studio", **kwargs)
            run["status"] = "running"
        except HTTPException as exc:
            run["status"] = "idle"
            note(run, f"Агент не запустился: {exc.detail}")

    def turn_ended(chat: dict, reply: dict) -> None:
        """agent_chat hook: the orchestrator's turn is over — keep waiting, deliver the inbox or mark it idle."""
        run_id = chat.get("run_id")
        if not run_id or not path(run_id).is_file():
            return

        async def settle():
            async with lock(run_id):
                run = load(run_id)
                if reply.get("error"):
                    note(run, f"Ошибка агента: {reply['error'][:300]}")
                if run.get("inbox"):
                    item = run["inbox"].pop(0)
                    if isinstance(item, str):
                        await wake(run, item)
                    else:
                        await wake(run, item["text"], item.get("attachments", []))
                elif run["status"] == "running" and run.get("auto_nudges", 0) < AUTO_NUDGES and not reply.get("error"):
                    run["auto_nudges"] = run.get("auto_nudges", 0) + 1
                    note(run, f"Агент остановился без вопроса, ожидания или итога — Studio продолжает его сама "
                              f"({run['auto_nudges']}/{AUTO_NUDGES})")
                    await wake(run, NUDGE.format(run_id=run_id))
                elif run["status"] == "running":
                    run["status"] = "idle"
                    note(run, "Агент закончил ход без вопроса, ожидания или итога — напиши ему или нажми «Продолжить»")
                save(run)

        try:
            asyncio.get_running_loop().create_task(settle())
        except RuntimeError:  # called from another thread: hand it to the Studio's loop
            loop = watcher.get("loop")
            if loop:
                loop.call_soon_threadsafe(lambda: loop.create_task(settle()))

    hub.on_done.append(turn_ended)

    # ---------- the watcher: wake the agent when what it waits for is finished

    def job_state(kind: str, item_id: str) -> tuple[str, str]:
        if kind == "job":
            job = store.get_job(item_id) or {}
            return job.get("state", "missing"), job.get("error") or ""
        if kind == "scene":
            return scene_state(item_id)
        state_file = store.root / "edl_renders" / f"{item_id}.state"
        if not state_file.is_file():
            return "missing", ""
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return data.get("state", "missing"), data.get("detail") or ""

    def scene_state(scene_id: str) -> tuple[str, str]:
        """A canvas scene from the saved project draft: still building, or every shot's render finished."""
        production = scene_runs.load(scene_id)
        if production:
            state = production.get("state")
            if state == "done":
                return "completed", production.get("message") or ""
            if state in {"stopped", "failed", "interrupted"}:
                return "stopped", production.get("message") or ""
            return "running", production.get("message") or ""
        draft = store.load_draft() or {}
        project = draft.get("project", draft)
        if project.get("activeSceneId") == scene_id:
            scene = {"cuts": project.get("cuts", []), "storyboard": project.get("storyboard", {})}
        else:
            scene = next((s for s in project.get("scenes", []) if s.get("id") == scene_id), None)
        if not scene:
            return "missing", "сцены нет на холсте"
        if (scene.get("storyboard") or {}).get("running"):
            return "running", ""
        states = [(store.get_job(c["shotJobId"]) or {}).get("state", "missing") if c.get("shotJobId") else "none"
                  for c in scene.get("cuts", [])]
        detail = ", ".join(f"шот {i + 1}: {st}" for i, st in enumerate(states))
        if all(st in DONE_JOBS for st in states):
            return ("failed" if any(st != "completed" for st in states) else "completed"), detail
        if any(st not in DONE_JOBS | {"none", "missing"} for st in states):
            return "running", detail
        message = (scene.get("storyboard") or {}).get("message", "")
        return "stopped", f"{detail}. {message}".strip()

    def finished(kind: str, state: str) -> bool:
        if kind == "scene":
            return state in {"completed", "failed", "stopped", "missing"}
        return state == "missing" or state in (DONE_RENDERS if kind == "render" else DONE_JOBS)

    async def check_waits() -> None:
        for file in root.glob("*.json"):
            try:
                run = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if run.get("status") != "waiting_jobs" or not run.get("wait"):
                continue
            wait = run["wait"]
            items = [("job", i) for i in wait.get("job_ids", [])] \
                + [("render", i) for i in wait.get("render_ids", [])] + [("scene", i) for i in wait.get("scene_ids", [])]
            states = [(kind, item_id, *job_state(kind, item_id)) for kind, item_id in items]
            if not all(finished(kind, state) for kind, _, state, _ in states):
                continue
            async with lock(run["id"]):
                run = load(run["id"])
                if run.get("status") != "waiting_jobs":
                    continue
                lines = [f"- {kind} {item_id}: {state}" + (f" — {error[:200]}" if error else "")
                         for kind, item_id, state, error in states]
                run["wait"] = None
                note(run, "Ожидание закончилось: " + ", ".join(f"{s}" for _, _, s, _ in states))
                await wake(run, f"[Studio · задача {run['id']}] Готово то, чего ты ждал:\n" + "\n".join(lines)
                           + "\nПроверь результат и продолжай задачу.")
                save(run)

    async def watch() -> None:
        while True:
            try:
                await check_waits()
            except Exception:  # noqa: BLE001 — the watcher must survive one bad file
                log.exception("Задачи агента: проверка ожиданий не удалась")
            await asyncio.sleep(POLL_S)

    def seen(request: Request) -> None:
        """The Studio's own address for the agent's MCP server (a test Studio runs on another port)."""
        watcher["url"] = f"http://127.0.0.1:{request.url.port or 18187}"

    def ensure_watcher() -> None:
        watcher["loop"] = asyncio.get_running_loop()
        task = watcher["task"]
        if task is None or task.done():
            watcher["task"] = asyncio.get_running_loop().create_task(watch())

    # ---------- user side

    @router.get("")
    async def list_runs(request: Request) -> list[dict]:
        seen(request)
        ensure_watcher()
        out = []
        for file in root.glob("*.json"):
            try:
                run = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            steps = run.get("steps", [])
            out.append({k: run.get(k) for k in ("id", "title", "status", "provider", "updated_at", "franchise_id", "chat_id")}
                       | {"steps": len(steps), "done": sum(s["status"] == "done" for s in steps),
                          "asking": any(not a.get("answer") for a in run.get("asks", [])),
                          "busy": run.get("chat_id") in hub.runs})
        return sorted(out, key=lambda r: r.get("updated_at") or "", reverse=True)

    @router.post("", status_code=201)
    async def create(payload: NewRun, request: Request) -> dict:
        seen(request)
        ensure_watcher()
        validate_attachments(payload.attachments)
        run_id = uuid.uuid4().hex
        title = payload.goal.strip().splitlines()[0][:80]
        chat = hub.new_chat(payload.provider, payload.model, "act", f"Задача: {title[:60]}", payload.effort,
                            run_id=run_id, franchise_id=payload.franchise_id)
        run = {"id": run_id, "title": title, "goal": payload.goal, "franchise_id": payload.franchise_id,
               "deliverable_type": payload.deliverable_type,
               "provider": payload.provider, "model": payload.model, "effort": payload.effort, "chat_id": chat["id"],
               "status": "running",
               "steps": [], "asks": [], "helpers": [], "wait": None,
               "gates": ({"gpu": {"remaining": GPU_TASK_USES, "approved_at": now(), "source": "user_goal"}}
                         if explicit_render_request(payload.goal) else {}), "inbox": [], "log": [],
               "created_at": now()}
        note(run, "Задача создана")
        save(run)
        franchise = f"\nФраншиза: id {payload.franchise_id}" if payload.franchise_id else ""
        if payload.deliverable_type == "film":
            franchise += ("\nРезультат: готовый фильм. run_done требует final_artifact {kind: job|concat|edl|motion|asset, id} или final_job_id и действующую приёмку MP4. "
                          "После реального QC записывай production_review_record: technical_pass, затем creative_accepted "
                          "с полным диапазоном просмотра и прослушиванием. Не выдумывай доказательства просмотра.")
        try:
            await hub.start(chat["id"], KICKOFF.format(run_id=run_id, goal=payload.goal, franchise=franchise),
                            studio_url=watcher.get("url", ""), author="studio",
                            **({"attachments": payload.attachments} if payload.attachments else {}))
        except HTTPException as exc:
            run["status"] = "idle"
            note(run, f"Агент не запустился: {exc.detail}")
            save(run)
        return public(run)

    @router.get("/{run_id}")
    async def get(run_id: str, request: Request) -> dict:
        seen(request)
        ensure_watcher()
        return public(load(run_id))

    @router.post("/{run_id}/events")
    async def record_event(run_id: str, payload: RunEvent) -> dict:
        """Attach a committed Studio event to the existing task without a model call."""
        async with lock(run_id):
            run = load(run_id)
            try:
                with closing(sqlite3.connect(store.db_path)) as db:
                    receipt = db.execute("SELECT project_id,shot_id,revision FROM agent_event_receipts WHERE event_id=?",
                                         (payload.event_id,)).fetchone()
            except sqlite3.OperationalError:  # no agent edit was ever applied: the receipt table is not there yet
                receipt = None
            if receipt != (payload.scene_id, payload.shot_id, payload.revision):
                raise HTTPException(409, "Событие не подтверждено сохранённой квитанцией Studio")
            draft = store.load_draft() or {}
            scene = next((item for item in draft.get("scenes", []) if item.get("id") == payload.scene_id), None)
            owner = ((scene or {}).get("franchiseSource") or {}).get("franchise_id") or \
                    ((scene or {}).get("sceneDirector") or {}).get("agentScene", {}).get("franchise_id")
            if not scene or owner != run["franchise_id"]:
                raise HTTPException(403, "Событие относится к другой франшизе или отсутствующей сцене")
            events = run.setdefault("events", [])
            if not any(item["event_id"] == payload.event_id for item in events):
                events.append({**payload.model_dump(), "at": now()})
                note(run, f"Шот {payload.shot_id} обновлён: ревизия {payload.revision}")
                save(run)
            return {"recorded": True, "event_id": payload.event_id}

    @router.post("/{run_id}/answer")
    async def answer(run_id: str, payload: Answer, request: Request) -> dict:
        seen(request)
        async with lock(run_id):
            run = load(run_id)
            ask = next((a for a in run["asks"] if a["id"] == payload.ask_id), None)
            if not ask:
                raise HTTPException(404, "Вопрос не найден")
            if ask.get("answer"):
                raise HTTPException(409, "На этот вопрос уже ответили")
            ask["answer"], ask["answered_at"] = payload.answer, now()
            if ask.get("gate"):
                if ask["gate"] == "gpu":
                    run["gates"]["gpu"] = {"remaining": ask.get("gpu_uses", 1) if payload.answer.strip() == YES else 0,
                                           "approved_at": now()}
                else:
                    run["gates"][ask["gate"]] = payload.answer.strip() == YES
                if ask["gate"] == "story" and payload.answer.strip() == YES:
                    line = decisions.append(franchise_name(run), ask["question"][:600], YES, "кнопка в задаче агента")
                    note(run, f"Одобрение сюжета записано в DECISIONS.md: {line[:160]}")
            note(run, f"Ответ пользователя: {payload.answer[:120]}")
            await wake(run, f"[Studio · задача {run_id}] Пользователь ответил на «{ask['question'][:300]}»: "
                            f"{payload.answer}\nПродолжай задачу.")
            return public(save(run))

    @router.post("/{run_id}/continue")
    async def resume(run_id: str, payload: Continue, request: Request) -> dict:
        seen(request)
        async with lock(run_id):
            run = load(run_id)
            if run["status"] in {"done", "stopped"} and not payload.text:
                raise HTTPException(409, "Задача завершена — напиши, что сделать дальше")
            validate_attachments(payload.attachments)
            text = payload.text.strip() or "Продолжай задачу с того места, где остановился."
            run["auto_nudges"] = 0
            if explicit_render_request(text):
                run["gates"]["gpu"] = {"remaining": GPU_TASK_USES, "approved_at": now(), "source": "user_continue"}
            await wake(run, f"[Пользователь · задача {run_id}] {text}", payload.attachments)
            note(run, f"Продолжить: {text[:120]}")
            return public(save(run))

    @router.post("/{run_id}/stop")
    async def halt(run_id: str) -> dict:
        async with lock(run_id):
            run = load(run_id)
            hub.stop(run["chat_id"])
            for helper in run.get("helpers", []):
                if helper.get("chat_id"):
                    hub.stop(helper["chat_id"])
                if helper.get("id") in helper_tasks:
                    helper_tasks[helper["id"]].cancel()
            run["status"], run["wait"], run["inbox"] = "stopped", None, []
            note(run, "Остановлено пользователем")
            return public(save(run))

    # ---------- agent side (MCP tools)

    @router.post("/{run_id}/plan")
    async def plan(run_id: str, payload: Plan) -> dict:
        async with lock(run_id):
            run = load(run_id)
            old = {s["id"]: s for s in run["steps"]}
            run["steps"] = [{**old.get(step.id, {"status": "pending", "detail": "", "artifacts": {}}),
                             "id": step.id, "title": step.title, "kind": step.kind} for step in payload.steps]
            note(run, f"План: {len(run['steps'])} шагов")
            return public(save(run))

    @router.post("/{run_id}/step")
    async def step(run_id: str, payload: StepUpdate) -> dict:
        async with lock(run_id):
            run = load(run_id)
            item = next((s for s in run["steps"] if s["id"] == payload.step_id), None)
            if not item:
                raise HTTPException(404, f"Нет шага «{payload.step_id}» в плане — сначала run_plan")
            item["status"] = payload.status
            if payload.detail:
                item["detail"] = payload.detail
            item["artifacts"] = {**item.get("artifacts", {}), **payload.artifacts}
            item["updated_at"] = now()
            note(run, f"{item['title']}: {payload.status}")
            return public(save(run))

    @router.post("/{run_id}/ask")
    async def ask(run_id: str, payload: Ask) -> dict:
        async with lock(run_id):
            run = load(run_id)
            if payload.gate == "gpu" and run.get("gates", {}).get("gpu", {}).get("remaining", 0) > 0:
                return {"already_authorized": True, "remaining": run["gates"]["gpu"]["remaining"],
                        "note": "GPU-допуск уже дан. Продолжай задачу без повторного вопроса."}
            if payload.gate == "gpu":
                run["gates"]["gpu"] = {"remaining": 0, "approved_at": ""}
            options = [YES, "Нет"] if payload.gate and not payload.options else payload.options
            item = {"id": uuid.uuid4().hex[:8], "question": payload.question, "options": options, "gate": payload.gate,
                    "step_id": payload.step_id, "gpu_uses": payload.gpu_uses if payload.gate == "gpu" else 0,
                    "asked_at": now(), "answer": ""}
            run["asks"].append(item)
            run["status"] = "waiting_user"
            note(run, f"Вопрос: {payload.question[:120]}")
            save(run)
            return {"ask_id": item["id"], "note": "Вопрос показан пользователю. Закончи ход — Studio разбудит тебя с ответом."}

    @router.post("/{run_id}/wait")
    async def wait(run_id: str, payload: Wait) -> dict:
        if not (payload.job_ids or payload.render_ids or payload.scene_ids):
            raise HTTPException(422, "Нечего ждать: передай job_ids, render_ids или scene_ids")
        async with lock(run_id):
            run = load(run_id)
            run["wait"] = payload.model_dump()
            run["status"] = "waiting_jobs"
            note(run, f"Ждёт: {len(payload.job_ids)} рендеров, "
                      f"{len(payload.render_ids)} сборок, {len(payload.scene_ids)} сцен")
            save(run)
        ensure_watcher()
        return {"note": "Studio разбудит тебя, когда всё будет готово (очередь Mac может стоять на паузе). Закончи ход."}

    @router.post("/{run_id}/done")
    async def done(run_id: str, payload: Done) -> dict:
        async with lock(run_id):
            run = load(run_id)
            if payload.status == "done":
                steps = run.get("steps") or []
                if not steps:
                    raise HTTPException(409, "Перед завершением составь план через run_plan")
                pending = [step["title"] for step in steps if step["status"] not in {"done", "skipped"}]
                if pending or run.get("wait"):
                    raise HTTPException(409, "Задача не завершена: " + ", ".join(pending[:4] or ["есть ожидание результата"]))
                for step in steps:
                    artifacts = step.get("artifacts") or {}
                    scene_id = artifacts.get("scene") or artifacts.get("scene_id")
                    if scene_id and scene_runs.load(str(scene_id)):
                        try:
                            readiness = scene_readiness(store, scene_runs, str(scene_id))
                        except ValueError as exc:
                            raise HTTPException(409, str(exc)) from exc
                        if (readiness["run_state"] != "done" or readiness["blockers"]
                                or readiness["accepted_shots"] != readiness["total_shots"]):
                            raise HTTPException(409, f"Сцена {scene_id}: сборка или приёмка шотов не завершена")
                    ids = artifacts.get("job_ids") or artifacts.get("job_id") or []
                    if step.get("kind") == "render" and step["status"] == "done" and not ids:
                        raise HTTPException(409, "Шаг рендера требует job_id готового задания")
                    for job_id in ([ids] if isinstance(ids, str) else ids):
                        job = store.get_job(job_id)
                        if not job or job.get("state") != "completed":
                            raise HTTPException(409, f"Рендер {str(job_id)[:12]} не завершён — проверь очередь")
                if run.get("deliverable_type", "task") == "film":
                    from .production_review import require_creative_acceptance
                    if payload.final_artifact and payload.final_job_id:
                        raise HTTPException(409, "Укажи один финальный результат")
                    target = payload.final_artifact or (ArtifactReference(kind="job", id=payload.final_job_id)
                                                        if payload.final_job_id else None)
                    if target is None:
                        raise HTTPException(409, "Для готового фильма укажи final_artifact принятого MP4")
                    try:
                        review = await asyncio.to_thread(require_creative_acceptance, store, target.id, target.kind)
                    except (ValueError, OSError) as exc:
                        raise HTTPException(409, str(exc)) from exc
                    run["final_artifact"] = target.model_dump()
                    if target.kind == "job":
                        run["final_job_id"] = target.id
                    run["final_review_id"] = review["id"]
            run["status"], run["summary"] = payload.status, payload.summary
            note(run, "Задача завершена" if payload.status == "done" else "Задача провалена")
            return public(save(run))

    @router.get("/{run_id}/gate")
    async def gate(run_id: str, kind: str = "gpu") -> dict:
        run = load(run_id)
        value = run.get("gates", {}).get(kind)
        remaining = value.get("remaining", 0) if isinstance(value, dict) else int(bool(value))
        return {"allowed": remaining > 0, "kind": kind, "remaining": remaining}

    @router.post("/{run_id}/gate/claim")
    async def claim_gate(run_id: str, kind: str = "gpu") -> dict:
        if kind != "gpu":
            raise HTTPException(422, "Расходуемое разрешение есть только для GPU")
        async with lock(run_id):
            run = load(run_id)
            if run["status"] in {"done", "failed", "stopped"}:
                raise HTTPException(409, "Задача уже завершена или остановлена")
            value = run.get("gates", {}).get("gpu")
            remaining = value.get("remaining", 0) if isinstance(value, dict) else int(bool(value))
            if remaining <= 0:
                raise HTTPException(409, "Лимит действий GPU исчерпан — запроси новое разрешение через run_ask")
            run["gates"]["gpu"] = {"remaining": remaining - 1, "approved_at": value.get("approved_at", "") if isinstance(value, dict) else ""}
            note(run, f"Разрешение GPU использовано: осталось {remaining - 1}")
            save(run)
            return {"allowed": True, "remaining": remaining - 1}

    @router.post("/{run_id}/delegate")
    async def delegate(run_id: str, payload: Delegate, request: Request) -> dict:
        seen(request)
        run = load(run_id)
        provider = payload.provider or run["provider"]
        if payload.evidence.strip():
            from .helper_engine import check as check_helper
            helper_id = uuid.uuid4().hex
            async with lock(run_id):
                fresh = load(run_id)
                fresh["helpers"].append({"id": helper_id, "role": payload.role, "task": payload.task[:300],
                                         "status": "running", "at": now(), "mode": "evidence_only"})
                save(fresh)
            try:
                task = asyncio.create_task(check_helper(provider=provider, franchise_id=run["franchise_id"],
                                                        role=payload.role, task=payload.task,
                                                        evidence=payload.evidence, timeout_s=HELPER_TIMEOUT_S))
                helper_tasks[helper_id] = task
                reply = await task
            except asyncio.CancelledError as exc:
                async with lock(run_id):
                    fresh = load(run_id)
                    for helper in fresh["helpers"]:
                        if helper.get("id") == helper_id:
                            helper.update(status="stopped")
                    save(fresh)
                raise HTTPException(409, "Помощник остановлен вместе с задачей") from exc
            except Exception as exc:
                async with lock(run_id):
                    fresh = load(run_id)
                    for helper in fresh["helpers"]:
                        if helper.get("id") == helper_id:
                            helper.update(status="failed", error=str(exc)[:400])
                    save(fresh)
                raise HTTPException(502, f"Помощник «{payload.role}»: {str(exc)[:400]}") from exc
            finally:
                helper_tasks.pop(helper_id, None)
            async with lock(run_id):
                fresh = load(run_id)
                for helper in fresh["helpers"]:
                    if helper.get("id") == helper_id:
                        helper.update(status="done", report=reply["report"], usage=reply.get("usage"),
                                      input_summary=reply.get("input_summary"), seconds=reply.get("seconds"))
                save(fresh)
            return {"helper_id": helper_id, "role": payload.role, "report": reply["report"],
                    "usage": reply.get("usage"), "input_summary": reply.get("input_summary"),
                    "seconds": reply.get("seconds"), "cache_used": False}
        # Helpers do one narrow read-only job: light effort unless the task itself runs lighter.
        chat = hub.new_chat(provider, "", "read", f"Помощник · {payload.role}"[:60], "low",
                            parent_run=run_id, role=payload.role, franchise_id=run["franchise_id"])
        async with lock(run_id):
            fresh = load(run_id)
            fresh["helpers"].append({"chat_id": chat["id"], "role": payload.role, "task": payload.task[:300],
                                     "status": "running", "at": now()})
            note(fresh, f"Помощник «{payload.role}» получил задание")
            save(fresh)
        turn = await hub.start(chat["id"], HELPER.format(role=payload.role, title=run["title"], task=payload.task) +
                               (f"\nПрофиль роли: {specialist_brief(payload.role)}" if specialist_brief(payload.role) else ""),
                               studio_url=watcher.get("url", ""), author="studio")
        deadline = time.monotonic() + HELPER_TIMEOUT_S
        while not turn.done and time.monotonic() < deadline:
            turn.changed.clear()
            try:
                await asyncio.wait_for(turn.changed.wait(), 15)
            except TimeoutError:
                pass
        if not turn.done:
            hub.stop(chat["id"])
        reply = hub.load(chat["id"])["messages"][-1] if turn.done else {"error": "Помощник не уложился в 15 минут"}
        from .helper_context import parse_helper_response
        report = parse_helper_response(reply.get("text", ""))
        async with lock(run_id):
            fresh = load(run_id)
            for helper in fresh["helpers"]:
                if helper["chat_id"] == chat["id"]:
                    helper["status"] = "failed" if reply.get("error") else "done"
                    helper["answer"] = (reply.get("text") or reply.get("error") or "")[:1500]
                    helper["report"] = report
            save(fresh)
        if reply.get("error"):
            raise HTTPException(502, f"Помощник «{payload.role}»: {reply['error'][:400]}")
        return {"helper_chat_id": chat["id"], "role": payload.role, "answer": reply.get("text", ""),
                "report": report, "cache_used": False}

    return router
