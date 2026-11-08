"""Story approvals given in the Studio, written to docs/DECISIONS.md in the user's own words.

AGENTS.md: no storyboard, keyframes or render of an episode before the user approved that story and it is
recorded in docs/DECISIONS.md. Studio agents run read-only on the repository, so they could ask but never
record — and stalled. Now an agent records the approval through the Studio: the quote must appear word for
word in one of the user's own messages of that chat (not the Studio's), so an agent cannot invent it. A «Да»
to a story gate of an agent task (orchestrator.py) is recorded the same way.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .agent_brain import REPO

DECISIONS = REPO / "docs" / "DECISIONS.md"
_write = threading.Lock()


class StoryApproval(BaseModel):
    franchise: str = Field(min_length=2, max_length=80)
    story: str = Field(min_length=3, max_length=600)       # which story: title + one line, with the doc link if any
    user_words: str = Field(min_length=2, max_length=600)   # the user's message, verbatim
    chat_id: str = Field(min_length=32, max_length=32, pattern="^[0-9a-f]{32}$")


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def append(franchise: str, story: str, quote: str, via: str, path: Path | None = None) -> str:
    path = path or DECISIONS  # read at call time: tests point DECISIONS at a temp file
    line = (f"- **{franchise.strip()}, одобрение сюжета в Studio ({time.strftime('%d.%m.%Y')}, {via}):** "
            f"{story.strip()} Пользователь: «{quote.strip()}».")
    with _write:
        text = path.read_text(encoding="utf-8") if path.is_file() else "# Решения и запреты пользователя\n"
        path.write_text(text.rstrip("\n") + "\n" + line + "\n", encoding="utf-8")
    return line


def make_router(store, path: Path | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/decisions")

    @router.post("/story")
    async def story(payload: StoryApproval) -> dict:
        chat_file = store.root / "agent_chats" / f"{payload.chat_id}.json"
        if not chat_file.is_file():
            raise HTTPException(404, "Чат не найден")
        chat = json.loads(chat_file.read_text(encoding="utf-8"))
        said = [squash(m["text"]) for m in chat.get("messages", [])[-12:]
                if m.get("role") == "user" and not m.get("from")]
        if not any(squash(payload.user_words) in text for text in said):
            raise HTTPException(422, "Таких слов пользователя нет в этом чате: процитируй его сообщение дословно "
                                     "или спроси «Одобряешь сюжет …?» и дождись ответа")
        line = append(payload.franchise, payload.story, payload.user_words, "чат с агентом", path)
        return {"recorded": line, "file": "docs/DECISIONS.md"}

    return router
