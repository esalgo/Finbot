from dataclasses import dataclass
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


@dataclass
class RunContext:
    """Per-invocation data that must never reach the checkpointer.

    Passed as `context=` to ainvoke. Neither AgentState (saved every superstep) nor
    config["configurable"] (string values are copied into checkpoint metadata) is
    safe for a bank statement screenshot.
    """

    image: str | None = None  # base64 payload, validated, no data URI prefix
    media_type: str | None = None  # detected from the magic bytes, never assumed


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]  # reducer: appends instead of replacing
    turn_start: int  # index of this turn's HumanMessage; set by parse_input
    tools_used: list[str]  # filled by finalize (vision marks itself), never by the model
