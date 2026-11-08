from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .keychain import KeychainError
from .manager import ComputeConflict, ComputeManager
from .operator import make_router as make_operator_router
from .vast import (VastAuthError, VastError, VastOfferUnavailable, VastRateLimitError,
                   VastUnavailableError)


class ProviderKeyInput(BaseModel):
    api_key: str = Field(min_length=8, max_length=8192)


class RentInput(BaseModel):
    offer_id: int = Field(gt=0)
    max_hourly_rate: float = Field(gt=0, le=1000)


class DestroyInput(BaseModel):
    confirmation_id: int = Field(gt=0)


def make_router(manager: ComputeManager) -> APIRouter:
    router = APIRouter(prefix="/api/compute", tags=["compute"])
    router.include_router(make_operator_router(manager))

    def write_origin(request: Request) -> None:
        origin = request.headers.get("origin")
        host = request.headers.get("host", "").lower()
        if host not in {"127.0.0.1:18187", "localhost:18187", "127.0.0.1", "localhost"}:
            raise HTTPException(403, "Недопустимый адрес Studio")
        if not origin or origin.lower() not in {f"http://{host}", "http://127.0.0.1:18187", "http://localhost:18187"}:
            raise HTTPException(403, "Требуется запрос с этой страницы Studio")

    def safe_provider_error(exc: Exception) -> HTTPException:
        if isinstance(exc, VastAuthError):
            return HTTPException(401, {"code": "provider_auth", "message": "Vast не принял ключ или его разрешения."})
        if isinstance(exc, VastRateLimitError):
            return HTTPException(429, {"code": "provider_rate_limit", "message": "Vast временно ограничил запросы."})
        if isinstance(exc, VastOfferUnavailable):
            return HTTPException(409, {"code": "offer_unavailable", "message": "Предложение изменилось или больше недоступно."})
        if isinstance(exc, VastUnavailableError):
            return HTTPException(503, {"code": "provider_unavailable", "message": "Сервис Vast временно недоступен."})
        if isinstance(exc, ComputeConflict):
            code = str(exc)
            if code == "provider_not_connected":
                return HTTPException(409, {"code": code, "message": "Сначала подключи API key Vast."})
            if code == "studio_jobs_active":
                return HTTPException(409, {"code": code, "message": "Сначала дождись завершения заданий Studio."})
            messages = {
                "active_gpu_instance": "Этот сервер сейчас отмечен как активный для Studio. Сначала разверни и проверь другой сервер.",
                "instance_not_found": "Сервер не найден в аккаунте Vast. Обнови список.",
                "instance_not_stopped": "Vast сообщает, что этот сервер не остановлен; обнови его состояние.",
                "instance_not_running": "Сначала запусти сервер и дождись статуса running в Vast.",
                "deploy_current_server": "Для безопасной установки выбери другой сервер, не тот, что указан в текущем туннеле Studio.",
                "ssh_endpoint_not_ready": "Vast ещё не выдал SSH-адрес. Подожди и обнови серверы.",
                "ssh_identity_missing": "Не найден SSH-ключ ~/.ssh/id_ed25519: создайте его на «Первом запуске».",
                "hf_token_missing": "Нет токена Hugging Face: добавьте его на «Первом запуске» — сервер скачает веса MiniMax H3.",
                "deploy_script_missing": "В этой установке Studio отсутствует сценарий установки H3.",
                "deployment_already_running": "Установка H3 уже идёт — дождись её завершения.",
                "deployment_in_progress": "Не останавливай и не удаляй сервер, пока идёт установка H3.",
                "deploy_start_failed": "Не удалось запустить установку H3 на этом Mac.",
            }
            if code in messages:
                return HTTPException(409, {"code": code, "message": messages[code]})
            if code == "stop_current_before_destroy":
                return HTTPException(409, {"code": code, "message": "Сначала останови текущий сервер Studio. После подтверждения статуса stopped его можно удалить."})
            return HTTPException(409, {"code": code, "message": "Действие требует сверки с Vast. Обнови список серверов."})
        if isinstance(exc, (KeychainError, VastError)):
            return HTTPException(502, {"code": "compute_provider_error", "message": "Не удалось выполнить запрос. Проверь соединение и повтори."})
        if isinstance(exc, ValueError):
            return HTTPException(422, {"code": "invalid_request", "message": str(exc)})
        return HTTPException(502, {"code": "compute_error", "message": "Операция не завершена. Обнови состояние и попробуй снова."})

    async def run(call):
        try:
            return await call
        except HTTPException:
            raise
        except Exception as exc:
            raise safe_provider_error(exc) from None

    @router.get("/status")
    async def status():
        try:
            connection = await manager.connection()
        except Exception as exc:
            raise safe_provider_error(exc) from None
        return {"provider": "vast", **connection, "current_server": manager.current_server(), "requirements": {
            "gpu_vram_mb": 94372, "cpu_ram_mb": 256000, "disk_gb": 500, "cuda": 12.8,
        }, "deployment": manager.deployment_status()}

    @router.post("/provider-key")
    async def save_key(payload: ProviderKeyInput, request: Request):
        write_origin(request)
        return await run(manager.save_key(payload.api_key))

    @router.delete("/provider-key")
    async def delete_key(request: Request):
        write_origin(request)
        try:
            manager.delete_key()
            return {"connected": False}
        except Exception as exc:
            raise safe_provider_error(exc) from None

    @router.get("/offers")
    async def offers(max_hourly_rate: float | None = None):
        if max_hourly_rate is not None and (not math.isfinite(max_hourly_rate) or max_hourly_rate <= 0):
            raise HTTPException(422, "Укажи положительную цену в час")
        return {"offers": await run(manager.offers(max_hourly_rate=max_hourly_rate)),
                "checked_at": datetime.now(timezone.utc).isoformat()}

    @router.get("/instances")
    async def instances():
        return {"instances": await run(manager.instances())}

    @router.post("/rent")
    async def rent(payload: RentInput, request: Request):
        write_origin(request)
        return await run(manager.rent(payload.offer_id, payload.max_hourly_rate))

    @router.post("/instances/{instance_id}/stop")
    async def stop(instance_id: int, request: Request):
        write_origin(request)
        return await run(manager.stop(instance_id))

    @router.post("/instances/{instance_id}/start")
    async def start(instance_id: int, request: Request):
        write_origin(request)
        return await run(manager.start(instance_id))

    @router.delete("/instances/{instance_id}")
    async def destroy(instance_id: int, payload: DestroyInput, request: Request):
        write_origin(request)
        return await run(manager.destroy(instance_id, payload.confirmation_id))

    @router.post("/instances/{instance_id}/deploy")
    async def deploy(instance_id: int, request: Request):
        write_origin(request)
        return await run(manager.start_deploy(instance_id))

    @router.get("/operations/{operation_id}")
    async def operation(operation_id: str):
        if not re.fullmatch(r"[a-f0-9]{32}", operation_id):
            raise HTTPException(422, "Недопустимый номер операции")
        item = manager.operation(operation_id)
        if item is None:
            raise HTTPException(404, "Операция не найдена")
        return item

    return router
