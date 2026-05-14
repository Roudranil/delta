from delta.tools.base import BaseTool

_tools: dict[str, BaseTool] = {}


def register(tool: BaseTool) -> None:
    _tools[tool.name] = tool


def get_all() -> list[BaseTool]:
    return list(_tools.values())


def get_by_name(name: str) -> BaseTool | None:
    return _tools.get(name)
