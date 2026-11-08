"""Apply a bounded agent proposal to one saved canvas shot, without starting GPU work."""
from __future__ import annotations

import json
import time

from .agents.contracts import AgentTaskRequest, validate_agent_contract
from .core.idempotency import StateTransactionManager


class DraftShotError(ValueError):
    pass


SHOT_TEXT_LIMITS = {"prompt": 12000, "motivation": 200}
PROTECTED_WORK = {"shotJobId", "keyframeJobId", "keyframeJobIds", "takes", "frameAcceptance"}


def apply_draft_shot_proposal(store, request: AgentTaskRequest) -> tuple[int, bool]:
    """The draft row is the state owner; its revision also protects open browser tabs."""
    errors = validate_agent_contract(request)
    if errors:
        raise DraftShotError("; ".join(errors))
    if request.action != "update_shot":
        raise DraftShotError("Допускается только правка шота")
    if set(request.patch) - set(SHOT_TEXT_LIMITS):
        raise DraftShotError("Агент может менять только prompt и motivation")
    for key, value in request.patch.items():
        if not isinstance(value, str) or not value.strip() or len(value) > SHOT_TEXT_LIMITS[key]:
            raise DraftShotError(f"Некорректное поле {key}")

    def read_revision(db, _request):
        row = db.execute("SELECT revision FROM draft WHERE id=1").fetchone()
        if not row:
            raise DraftShotError("Черновик отсутствует")
        return row[0]

    def mutate(db, proposal):
        row = db.execute("SELECT payload,revision FROM draft WHERE id=1").fetchone()
        project = json.loads(row[0])
        scenes = project.get("scenes")
        if not isinstance(scenes, list):
            raise DraftShotError("Черновик без списка сцен: открой его в Studio")
        scene = next((item for item in scenes if isinstance(item, dict) and item.get("id") == proposal.project_id), None)
        if scene is None:
            raise DraftShotError("Сцена не найдена")
        cuts = scene.get("cuts")
        if not isinstance(cuts, list):
            raise DraftShotError("В сцене нет шотов")
        matches = [cut for cut in cuts if isinstance(cut, dict) and cut.get("id") == proposal.shot_id]
        if len(matches) != 1:
            raise DraftShotError("Шот не найден или его ID неоднозначен")
        cut = matches[0]
        if any(cut.get(key) for key in PROTECTED_WORK):
            raise DraftShotError("У шота уже есть производственная работа; нужна отдельная процедура правки")
        cut.update(proposal.patch)
        if project.get("activeSceneId") == proposal.project_id:
            project["cuts"] = cuts
        revision = row[1] + 1
        db.execute("INSERT OR REPLACE INTO draft_history(revision,payload,saved_at) VALUES (?,?,?)",
                   (row[1], row[0], time.time()))  # store.save_draft keeps every replaced canvas the same way
        db.execute("UPDATE draft SET payload=?, revision=? WHERE id=1",
                   (json.dumps(project, ensure_ascii=False), revision))
        return revision

    return StateTransactionManager(str(store.db_path)).apply(
        request, allowed_roles={"director", "shot_director"},
        read_revision=read_revision, mutate=mutate)
