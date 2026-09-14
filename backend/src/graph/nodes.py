from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

import settings
from graph.prompts import SYSTEM_PROMPT
from graph.state import AgentState, RunContext

VISION_TOOL_MARK = "imagen"


def parse_input(state: AgentState) -> dict:
    # The input reducer already appended this turn's HumanMessage, so it is the
    # last message. Everything from here on belongs to the current turn.
    return {"turn_start": len(state["messages"]) - 1, "tools_used": []}


def route_by_image(state: AgentState, runtime: Runtime[RunContext]) -> Literal["vision", "agent"]:
    # The image travels in the run context, never in the state. The context is
    # None when a caller invokes the graph without one: that is a text turn.
    context = runtime.context
    return "vision" if context is not None and context.image else "agent"


def trim_history(messages: list, limit: int) -> list:
    # Read-time window. A cut that starts on a ToolMessage (or an AIMessage with
    # tool_calls) sends an orphan tool result, which OpenAI-compatible APIs reject,
    # so the window always starts at a HumanMessage.
    recent = messages[-limit:]
    for i, message in enumerate(recent):
        if isinstance(message, HumanMessage):
            return recent[i:]
    return messages[-1:]


def make_agent_node(react_agent: CompiledStateGraph):
    async def agent(state: AgentState) -> dict:
        recent = trim_history(state["messages"], settings.HISTORY_LIMIT)
        result = await react_agent.ainvoke({"messages": recent})
        # Only what the agent produced this turn; the window is already in the state.
        return {"messages": result["messages"][len(recent):]}

    return agent


def make_vision_node(vision_llm: BaseChatModel):
    async def vision(state: AgentState, runtime: Runtime[RunContext]) -> dict:
        image, media_type = runtime.context.image, runtime.context.media_type
        window = trim_history(state["messages"], settings.HISTORY_LIMIT_WITH_IMAGE)
        *history, current = window

        # The vision model has no tools bound, so prior tool-call plumbing is
        # dropped: only user turns and the assistant's final text answers remain.
        history = [
            m for m in history
            if isinstance(m, HumanMessage) or (isinstance(m, AIMessage) and not m.tool_calls and m.text)
        ]

        # The image goes in a user message, built here and never returned: only
        # the model's text answer is written back to the state.
        multimodal = HumanMessage(content=[
            {"type": "text", "text": current.text},
            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image}"}},
        ])
        response = await vision_llm.ainvoke([SystemMessage(content=SYSTEM_PROMPT), *history, multimodal])
        return {"messages": [response], "tools_used": [VISION_TOOL_MARK]}

    return vision


def finalize(state: AgentState) -> dict:
    # Start from what a node already marked (vision has no tool_calls to read);
    # parse_input resets it every turn, so nothing leaks from earlier turns.
    used = list(state.get("tools_used") or [])
    seen = set(used)
    # Only this turn's messages: the checkpointer accumulates across turns, so
    # walking the whole state would surface badges from earlier questions.
    for message in state["messages"][state["turn_start"]:]:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls or []:
            if call["name"] not in seen:
                seen.add(call["name"])
                used.append(call["name"])  # dedup, preserving call order
    return {"tools_used": used}  # always a list, possibly empty
