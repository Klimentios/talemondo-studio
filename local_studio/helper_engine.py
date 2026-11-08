"""One bounded, read-only specialist check from a supplied evidence packet.

The task orchestrator owns lifecycle and permissions; this module owns the model
request and the structured result. No repository, MCP, GPU, or project writes.
"""
from __future__ import annotations

import json

from . import agent_brain
from .agent_specialists import specialist_brief
from .helper_context import parse_helper_response


REPORT_SCHEMA = {"type": "object", "properties": {
    "verdict": {"type": "string", "enum": ["pass", "fail", "uncertain"]},
    "findings": {"type": "array", "items": {"type": "string"}},
    "evidence": {"type": "array", "items": {"type": "string"}},
    "uncertainties": {"type": "array", "items": {"type": "string"}},
    "suggested_fix": {"type": "string"}},
    "required": ["verdict", "findings", "evidence", "uncertainties", "suggested_fix"],
    "additionalProperties": False}


async def check(*, provider: str, franchise_id: str, role: str, task: str,
                evidence: str, timeout_s: int) -> dict:
    if not evidence.strip():
        raise ValueError("Помощнику нужен пакет фактов")
    prompt = (f"Ты помощник Studio: {role}. Франшиза: {franchise_id}. "
              "Работай только с пакетом фактов ниже. Не утверждай, что видел исходный файл или MP4, "
              "если в пакете нет доказательства. При недостатке данных верни uncertain. "
              "Ответь кратко по JSON-схеме.\n"
              f"Задача: {task}\nПакет фактов:\n{evidence}")
    focus = specialist_brief(role)
    if focus:
        prompt += f"\nПрофиль роли: {focus}"
    reply = await agent_brain.ask(agent_brain.BrainRequest(
        provider=provider, prompt=prompt, schema=REPORT_SCHEMA, read_repo=False,
        effort="low", timeout_s=timeout_s))
    report = parse_helper_response(json.dumps(reply.get("data"), ensure_ascii=False))
    if not report["structured"]:
        raise agent_brain.BrainError("Помощник не вернул структурированный отчёт")
    return {"report": report, "usage": reply.get("usage"),
            "input_summary": reply.get("input_summary"), "seconds": reply.get("seconds")}
