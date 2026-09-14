"""Swappable infrastructure behind factories, so changing it never touches the graph."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

import settings

OPENAI_BASE_URL = "https://api.openai.com/v1"


def make_chat_model(model: str, base_url: str | None = None, api_key: str | None = None) -> ChatOpenAI:
    # The provider lives in .env: DeepSeek and OpenAI share the OpenAI-compatible API.
    return ChatOpenAI(
        model=model,
        temperature=0.2,
        base_url=base_url or settings.LLM_BASE_URL,
        api_key=api_key or settings.LLM_API_KEY,
    )


def make_vision_model() -> ChatOpenAI:
    return make_chat_model(settings.VISION_MODEL, settings.VISION_BASE_URL, settings.VISION_API_KEY)


def make_embeddings() -> OpenAIEmbeddings:
    # Always OpenAI. Explicit key and base_url so neither LLM_* nor an ambient
    # OPENAI_BASE_URL can reroute it (DeepSeek has no embeddings endpoint).
    return OpenAIEmbeddings(
        model=settings.EMBEDDING_MODEL,
        dimensions=settings.EMBEDDING_DIM,
        api_key=settings.OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
    )


@asynccontextmanager
async def make_checkpointer() -> AsyncIterator[BaseCheckpointSaver]:
    if settings.CHECKPOINTER == "memory":
        # In-process only: lost on restart and not shared between uvicorn workers.
        yield MemorySaver()
    elif settings.CHECKPOINTER == "postgres":
        if not settings.CHECKPOINT_DSN:
            raise RuntimeError("CHECKPOINTER=postgres requires CHECKPOINT_DSN (plain postgresql://)")
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(settings.CHECKPOINT_DSN) as saver:
            await saver.setup()
            yield saver
    else:
        raise RuntimeError(f"Unknown CHECKPOINTER {settings.CHECKPOINTER!r}: use 'memory' or 'postgres'")
