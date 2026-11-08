"""Transport-neutral contract for a proposed Studio state change."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class AgentTaskRequest:
    event_id: str
    agent_role: str
    project_id: str
    shot_id: str | None = None
    base_revision: int = 0
    action: Literal["update_shot", "inspect", "review"] = "update_shot"
    patch: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentTaskResult:
    task_id: str
    agent_role: str
    status: Literal["completed", "failed", "requires_review"]
    input_revision: int
    patch: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def validate_agent_contract(request: AgentTaskRequest) -> list[str]:
    """Validate shape only; domain fields and permissions belong to the state owner."""
    errors: list[str] = []
    for name in ("event_id", "agent_role", "project_id"):
        value = getattr(request, name)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"Missing {name}")
    if type(request.base_revision) is not int or request.base_revision < 0:
        errors.append("Invalid base_revision")
    if request.action not in {"update_shot", "inspect", "review"}:
        errors.append("Unsupported action")
    if not isinstance(request.patch, dict):
        errors.append("Patch must be an object")
    elif request.action == "update_shot":
        if not request.shot_id:
            errors.append("Missing shot_id")
        if not request.patch:
            errors.append("Empty mutation patch")
    elif request.patch:
        errors.append("Read-only action cannot include a patch")
    return errors
