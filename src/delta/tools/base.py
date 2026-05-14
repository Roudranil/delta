from typing import Literal

from langchain_core.tools import BaseTool as LangChainBaseTool


class BaseTool(LangChainBaseTool):
    execution_mode: Literal["sequential", "parallel"] = "sequential"
