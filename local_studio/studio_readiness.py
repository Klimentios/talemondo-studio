"""Read-only next-step projection of a saved scene and its production receipts."""
from __future__ import annotations

from pathlib import Path
import sqlite3

from fastapi import APIRouter, HTTPException

from .take_acceptance import take_is_accepted
from .scene_versions import definition_drift, context_drift


def _owner(scene: dict) -> str:
    return ((scene.get("franchiseSource") or {}).get("franchise_id")
            or ((scene.get("sceneDirector") or {}).get("agentScene") or {}).get("franchise_id")
            or "")


def _shot_state(cut: dict, store) -> dict:
    job_id = cut.get("shotJobId") or ""
    result = {"shot_id": cut.get("id"), "job_id": job_id, "job_state": None,
              "state": "draft", "next_action": "prepare_shot", "blockers": []}
    if not job_id:
        if not str(cut.get("prompt") or "").strip():
            result["blockers"].append("У шота нет промпта")
        elif cut.get("keyframeJobId") or cut.get("keyframeJobIds"):
            result.update(state="keyframe_pending", next_action="check_keyframe")
        elif (cut.get("startFrame") or {}).get("source") == "keyframe" and not cut.get("frameAcceptance"):
            result.update(state="frame_review", next_action="review_frame")
        else:
            result.update(state="ready_for_preflight", next_action="preflight_and_request_gpu_gate")
        return result

    job = store.get_job(job_id)
    if not job:
        result.update(state="unreconciled", next_action="reconcile_job",
                      blockers=["ID задания отсутствует в локальной базе; проверь очередь и квитанцию"])
        return result
    state = job.get("state") or "unknown"
    result["job_state"] = state
    if state != "completed":
        if state in {"failed", "cancelled", "interrupted"}:
            result.update(state="unreconciled", next_action="reconcile_job",
                          blockers=[f"Задание имеет состояние {state}; повтор запрещён до сверки"])
        else:
            result.update(state="in_progress", next_action="wait_for_job")
        return result
    path = job.get("output_path")
    output = Path(path) if path else None
    results_dir = getattr(store, "results_dir", None)
    if (output is None or not output.is_file()
            or (results_dir is not None and not output.resolve().is_relative_to(Path(results_dir).resolve()))):
        result.update(state="unreconciled", next_action="reconcile_result",
                      blockers=["MP4 завершённого задания недоступен через Studio"])
        return result
    qc = cut.get("shotQc") or {}
    accepted = take_is_accepted(cut)
    if accepted:
        result.update(state="accepted", next_action="continue_scene")
    elif cut.get("shotQcReview") in {"rejected", "failed"} or qc.get("decision") == "reject":
        result.update(state="rejected", next_action="revise_shot",
                      blockers=["Дубль отклонён; исправление требует отдельного решения"])
    else:
        result.update(state="needs_review", next_action="review_full_mp4",
                      blockers=["Технический статус completed не подтверждает качество MP4"])
    return result


def scene_readiness(store, runs, scene_id: str, *, snapshot=None) -> dict:
    project, revision = snapshot if snapshot is not None else store.load_draft_snapshot()
    scenes = (project or {}).get("scenes") or []
    scene = next((item for item in scenes if isinstance(item, dict) and item.get("id") == scene_id), None)
    if scene is None:
        raise ValueError("Сцена не найдена в сохранённом черновике")
    run = runs.load(scene_id)
    owner = _owner(scene)
    cuts = (run or scene).get("cuts") or []
    if not isinstance(cuts, list):
        cuts = []
    shots = [_shot_state(cut, store) for cut in cuts if isinstance(cut, dict)]
    blockers = []
    drift = definition_drift(scene.get("cuts") or [], run) if run else None
    context_version = context_drift(scene, run) if run else None
    if context_version and context_version["matches"] is False:
        blockers.append("Материалы или настройки сцены изменились после запуска сборки; требуется сверка входов")
    if drift and not drift["matches"]:
        blockers.append("Постановка или состав шотов изменились после запуска сборки; требуется сверка версий")
    if not shots:
        blockers.append("В сцене нет шотов")
    if not owner:
        blockers.append("У сцены не указана франшиза")
    if run and (run.get("options") or {}).get("franchise_id") not in {None, "", owner}:
        blockers.append("Франшиза сборки не совпадает с сохранённой сценой")
    if run and run.get("pending_submission"):
        blockers.append("Отправка задания требует сверки квитанции; не запускай повторно")
    if run and run.get("state") in {"stopped", "failed", "interrupted"}:
        blockers.append(f"Сборка сцены: {run['state']}; требуется разбор перед продолжением")
    attention = next((shot for shot in shots if shot["state"] != "accepted"), None)
    if blockers:
        next_action = "reconcile_scene" if run else "prepare_scene"
    elif attention:
        next_action = attention["next_action"]
    else:
        next_action = "review_scene_assembly" if run else "preflight_and_request_gpu_gate"
    return {"scene_id": scene_id, "franchise_id": owner, "draft_revision": revision,
            "definition_version": drift, "context_version": context_version,
            "source": "scene_run" if run else "draft", "run_state": run.get("state") if run else None,
            "next_action": next_action, "blockers": blockers, "shots": shots,
            "accepted_shots": sum(shot["state"] == "accepted" for shot in shots),
            "total_shots": len(shots)}


def production_state(store, runs, franchise_id: str = "") -> dict:
    """One saved revision projected through the existing scene/job state owners."""
    snapshot = store.load_draft_snapshot()
    project, revision = snapshot
    scenes = [scene for scene in (project or {}).get("scenes", []) if isinstance(scene, dict)
              and scene.get("id") and (not franchise_id or _owner(scene) == franchise_id)]
    rows = []
    for scene in scenes:
        state = scene_readiness(store, runs, scene["id"], snapshot=snapshot)
        run = runs.load(scene["id"])
        current = (run or scene).get("cuts") or []
        takes = []
        for cut in current:
            ids = list(dict.fromkeys([take.get("jobId") for take in cut.get("takes", [])
                                      if take.get("jobId")] + ([cut["shotJobId"]] if cut.get("shotJobId") else [])))
            for job_id in ids:
                job = store.get_job(job_id)
                takes.append({"shot_id": cut.get("id"), "job_id": job_id,
                              "selected": cut.get("shotJobId") == job_id,
                              "state": job.get("state") if job else "missing",
                              "recorded_output_sha256": job.get("output_sha256") if job else None})
        motion = []
        if hasattr(store, 'connect'):
            try:
                with store.connect() as db:
                    motion = [dict(row) for row in db.execute(
                        'SELECT motion_id,revision,state,remote_job_id,output_sha256 FROM motion_jobs '
                        'WHERE scene_id=? ORDER BY revision', (scene['id'],))]
            except sqlite3.OperationalError as exc:
                if 'no such table' not in str(exc):
                    raise
        rows.append({**state, "takes": takes, "motion_results": motion})
    return {"schema_version": 1, "draft_revision": revision, "franchise_id": franchise_id or None,
            "scenes": rows, "scene_count": len(rows),
            "blocked_scene_count": sum(bool(row["blockers"]) for row in rows),
            "scope": "saved_production_state", "file_integrity_verified": False,
            "automatic_generation": False}


def make_router(ctx, runs) -> APIRouter:
    router = APIRouter()

    @router.get("/api/draft/production")
    async def get_production_state(franchise_id: str = "") -> dict:
        return production_state(ctx.store, runs, franchise_id)


    @router.get("/api/draft/scenes/{scene_id}/readiness")
    async def get_scene_readiness(scene_id: str) -> dict:
        try:
            return scene_readiness(ctx.store, runs, scene_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    return router
