import json
from dataclasses import asdict
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from delta.config import settings
from delta.session.models import SessionEntry


class SessionStore:
    def __init__(self, session_dir: Path | None = None):
        self.session_dir = session_dir or settings.session_dir
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.session_dir / f"{session_id}.jsonl"

    def load(self, session_id: str) -> list[BaseMessage]:
        path = self._path(session_id)
        if not path.exists():
            return []
        messages: list[BaseMessage] = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            entry = SessionEntry(**json.loads(line))
            if entry.role == "human":
                messages.append(HumanMessage(content=entry.content or ""))
            elif entry.role == "ai":
                if entry.tool_calls:
                    messages.append(AIMessage(content="", tool_calls=entry.tool_calls))
                else:
                    messages.append(AIMessage(content=entry.content or ""))
            elif entry.role == "tool":
                messages.append(
                    ToolMessage(
                        content=entry.content or "",
                        tool_call_id=entry.tool_call_id or "",
                    )
                )
        return messages

    def load_raw(self, session_id: str) -> list[SessionEntry]:
        path = self._path(session_id)
        if not path.exists():
            return []
        entries = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            entries.append(SessionEntry(**json.loads(line)))
        return entries

    def append(self, entry: SessionEntry) -> None:
        path = self._path(entry.session_id)
        with path.open("a") as f:
            f.write(json.dumps(asdict(entry)) + "\n")

    def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False
