from pathlib import Path
from typing import Type

from langchain_core.callbacks import CallbackManagerForToolRun
from pydantic import BaseModel, Field

from delta.tools.base import BaseTool
from delta.tools.registry import register


class ReadFileInput(BaseModel):
    path: str = Field(description="Absolute or relative file path to read")


class ReadFileTool(BaseTool):
    name: str = "read_file"
    description: str = "Read the contents of a file at the given path"
    args_schema: Type[BaseModel] = ReadFileInput

    def _run(self, path: str, run_manager: CallbackManagerForToolRun | None = None) -> str:
        try:
            return Path(path).read_text()
        except Exception as e:
            return f"[error: {e}]"

    async def _arun(self, path: str, run_manager: CallbackManagerForToolRun | None = None) -> str:
        return self._run(path)


register(ReadFileTool())
