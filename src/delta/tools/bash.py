import asyncio
from typing import Type

from langchain_core.callbacks import CallbackManagerForToolRun
from pydantic import BaseModel, Field

from delta.tools.base import BaseTool
from delta.tools.registry import register


class BashInput(BaseModel):
    command: str = Field(description="Shell command to execute")
    timeout: int = Field(default=30, description="Timeout in seconds")


class BashTool(BaseTool):
    name: str = "bash"
    description: str = "Run a shell command and return stdout + stderr"
    args_schema: Type[BaseModel] = BashInput

    def _run(self, command: str, timeout: int = 30, run_manager: CallbackManagerForToolRun | None = None) -> str:
        raise NotImplementedError("Use async")

    async def _arun(self, command: str, timeout: int = 30, run_manager: CallbackManagerForToolRun | None = None) -> str:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return stdout.decode()
        except asyncio.TimeoutError:
            return f"[timeout after {timeout}s]"
        except Exception as e:
            return f"[error: {e}]"


register(BashTool())
