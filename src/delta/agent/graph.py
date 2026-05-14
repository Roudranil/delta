from langchain_core.messages import AIMessage
from langchain_community.chat_models import ChatLiteLLM
from langgraph.graph import END, START, StateGraph

from delta.agent.state import AgentState
from delta.config import settings
from delta.tools import registry


def _llm(model: str) -> ChatLiteLLM:
    return ChatLiteLLM(model=model).bind_tools(registry.get_all())


async def call_llm(state: AgentState) -> dict:
    if state["iteration"] >= settings.max_iterations:
        return {
            "messages": [AIMessage(content="[max iterations reached]")],
            "iteration": state["iteration"],
        }
    llm = _llm(state["model"])
    response = await llm.ainvoke(state["messages"])
    return {"messages": [response], "iteration": state["iteration"] + 1}


async def execute_tools(state: AgentState) -> dict:
    last = state["messages"][-1]
    tool_messages = []
    for tc in last.tool_calls:
        tool = registry.get_by_name(tc["name"])
        if tool is None:
            result = f"[unknown tool: {tc['name']}]"
        else:
            result = await tool.arun(tc["args"])
        from langchain_core.messages import ToolMessage
        tool_messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
    return {"messages": tool_messages}


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "execute_tools"
    return END


def build_graph() -> StateGraph:
    builder = StateGraph(AgentState)
    builder.add_node("call_llm", call_llm)
    builder.add_node("execute_tools", execute_tools)
    builder.add_edge(START, "call_llm")
    builder.add_conditional_edges("call_llm", should_continue)
    builder.add_edge("execute_tools", "call_llm")
    return builder.compile()


graph = build_graph()
