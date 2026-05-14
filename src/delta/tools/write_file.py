from pathlib import Path
from typing import Type

from langchain_core.callbacks import CallbackManagerForToolRun
from pydantic import BaseModel, Field

from delta.tools.base import BaseTool
from delta.tools.registry import register


class WriteFileInput(BaseModel):
    path: str = Field(description="File path to write to")
    content: str = Field(description="Content to write")


class WriteFileTool(BaseTool):
    name: str = "write_file"
    description: str = "Write content to a file, creating it if it doesn't exist"
    args_schema: Type[BaseModel] = WriteFileInput

    def _run(self, path: str, content: str, run_manager: CallbackManagerForToolRun | None = None) -> str:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            return f"Written {len(content)} bytes to {path}"
        except Exception as e:
            return f"[error: {e}]"

    async def _arun(self, path: str, content: str, run_manager: CallbackManagerForToolRun | None = None) -> str:
        return self._run(path, content)


register(WriteFileTool())
