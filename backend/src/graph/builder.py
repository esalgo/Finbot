from collections.abc import Sequence

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from graph.nodes import finalize, make_agent_node, make_vision_node, parse_input, route_by_image
from graph.prompts import SYSTEM_PROMPT
from graph.state import AgentState, RunContext


def build_graph(
    llm: BaseChatModel,
    vision_llm: BaseChatModel,
    tools: Sequence[BaseTool],
    checkpointer: BaseCheckpointSaver | None,
) -> CompiledStateGraph:
    # No checkpointer here: the outer graph owns memory; this subgraph runs per turn.
    react_agent = create_agent(model=llm, tools=list(tools), system_prompt=SYSTEM_PROMPT)

    builder = StateGraph(AgentState, context_schema=RunContext)
    builder.add_node("parse_input", parse_input)
    builder.add_node("vision", make_vision_node(vision_llm))
    builder.add_node("agent", make_agent_node(react_agent))
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "parse_input")
    builder.add_conditional_edges(
        "parse_input",
        route_by_image,
        {"vision": "vision", "agent": "agent"},
    )
    builder.add_edge("vision", "finalize")
    builder.add_edge("agent", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)
