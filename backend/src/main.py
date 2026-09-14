import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from langchain_postgres import PGEngine, PGVectorStore
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

import settings
from graph.builder import build_graph
from graph.factories import make_chat_model, make_checkpointer, make_embeddings, make_vision_model
from graph.market_tools import MARKET_TOOLS, load_fiat_codes
from graph.state import RunContext
from graph.tools import calculate_interest, make_search_docs
from services.cache import SemanticCache
from services.images import ImageValidationError, validate_image
from services.limits import IMAGE_REQUEST_UNITS, LIMIT_MESSAGE, TEXT_REQUEST_UNITS, consume_global_quota

logger = logging.getLogger("finbot")

RAG_TABLE = "rag_documents"

# Used when an image arrives without text: with no text there is no language
# signal, so the default is Spanish.
DEFAULT_IMAGE_PROMPT = "Analiza la imagen adjunta y explícame la información financiera relevante que contiene."
GRAPH_ERROR = "No pude procesar tu mensaje en este momento. Intenta de nuevo en unos segundos."


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = PGEngine.from_connection_string(url=settings.DATABASE_URL)
    pool = AsyncConnectionPool(settings.DATABASE_DSN, min_size=1, max_size=5, open=False)
    refresh_task: asyncio.Task | None = None
    try:
        await pool.open(wait=True)
        embeddings = make_embeddings()
        try:
            store = await PGVectorStore.create(
                engine=engine,
                table_name=RAG_TABLE,
                embedding_service=embeddings,
                metadata_columns=["source_url"],
            )
        except ValueError as exc:
            raise RuntimeError(f"{RAG_TABLE} is not ready — run scripts/ingest.py first ({exc})") from exc

        retriever = store.as_retriever(search_kwargs={"k": settings.RETRIEVER_TOP_K})
        tools = [calculate_interest, make_search_docs(retriever), *MARKET_TOOLS]
        # Best effort: a Coinbase outage must not take the whole agent down.
        await load_fiat_codes()

        llm, vision_llm = make_chat_model(settings.LLM_MODEL), make_vision_model()
        cache = SemanticCache(pool, embeddings)

        # FAQ answers come from the same agent, in a graph with no checkpointer:
        # a clean thread with no history, and nothing left behind in the database.
        faq_graph = build_graph(llm, vision_llm, tools, checkpointer=None)

        async def answer_faq(question: str) -> tuple[str, list[str]]:
            state = await faq_graph.ainvoke({"messages": [HumanMessage(content=question)]}, context=RunContext())
            return state["messages"][-1].text, state["tools_used"]

        async with make_checkpointer() as checkpointer:
            app.state.graph = build_graph(llm, vision_llm, tools, checkpointer)
            app.state.pool = pool
            app.state.cache = cache
            # In the background so a slow or failing model never blocks startup.
            refresh_task = asyncio.create_task(cache.refresh_forever(answer_faq))
            logger.info(
                "Graph ready: model=%s vision_model=%s checkpointer=%s",
                settings.LLM_MODEL, settings.VISION_MODEL, settings.CHECKPOINTER,
            )
            yield
    finally:
        if refresh_task is not None:
            refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await refresh_task
        await pool.close()
        await engine.close()


app = FastAPI(title="FinBot", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=4000)
    thread_id: str = Field(min_length=1, max_length=128)
    # Base64 (a data URI prefix is tolerated). Length is checked against
    # MAX_IMAGE_MB after decoding, in services/images.py.
    image: str | None = None


class ChatResponse(BaseModel):
    response: str
    tools_used: list[str]
    cached: bool
    audio: str | None


@dataclass
class PreparedTurn:
    message: str
    context: RunContext
    config: dict
    cached: str | None  # the cached answer on a hit, otherwise None


async def prepare_turn(body: ChatRequest, request: Request) -> PreparedTurn:
    """Everything that happens before the graph, shared by /chat and /chat/stream.

    Raises HTTPException (422, 429) before any streaming starts, so both endpoints
    report those with a real HTTP status.
    """
    message = body.message.strip()
    context = RunContext()
    if body.image:
        try:
            context.image, context.media_type = validate_image(body.image)
        except ImageValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        message = message or DEFAULT_IMAGE_PROMPT
    elif not message:
        raise HTTPException(status_code=422, detail="El mensaje no puede estar vacío.")

    graph = request.app.state.graph
    config = {"configurable": {"thread_id": body.thread_id}}

    # 1) Semantic cache, before the graph. Never for images: the answer depends on the picture.
    if context.image is None:
        try:
            cached = await request.app.state.cache.lookup(message)
        except Exception:
            logger.exception("Cache lookup failed; continuing without cache")
            cached = None
        if cached is not None:
            # Record the exchange in the thread without calling the model, so a
            # follow-up question keeps its context.
            await graph.aupdate_state(
                config,
                {"messages": [HumanMessage(content=message), AIMessage(content=cached)], "tools_used": []},
                as_node="finalize",
            )
            return PreparedTurn(message, context, config, cached)

    # 2) Daily limit: only cache misses count, because only they reach the model.
    # An image turn weighs more: it sends ~1,000 image tokens on top of the text.
    units = IMAGE_REQUEST_UNITS if context.image else TEXT_REQUEST_UNITS
    if not await consume_global_quota(request.app.state.pool, units):
        raise HTTPException(status_code=429, detail=LIMIT_MESSAGE)

    return PreparedTurn(message, context, config, None)


def response_from_state(state: dict) -> ChatResponse:
    return ChatResponse(
        response=state["messages"][-1].text,
        tools_used=state["tools_used"],
        cached=False,
        audio=None,  # voice (Fase 4) not implemented
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    turn = await prepare_turn(body, request)
    if turn.cached is not None:
        return ChatResponse(response=turn.cached, tools_used=[], cached=True, audio=None)

    try:
        # The image goes in the run context, never in the input state or in
        # config["configurable"]: both end up in the checkpointer.
        state = await request.app.state.graph.ainvoke(
            {"messages": [HumanMessage(content=turn.message)]}, turn.config, context=turn.context
        )
    except Exception:
        logger.exception("Graph invocation failed for thread %s", body.thread_id)
        raise HTTPException(status_code=502, detail=GRAPH_ERROR)
    return response_from_state(state)


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/chat/stream")
async def chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
    """Same turn as /chat, reported as Server-Sent Events while the graph runs.

    Events:
      status  {"stage": "thinking" | "vision" | "tool" | "writing", "tool"?: name}
      final   ChatResponse — the exact /chat contract
      error   {"status": 502, "detail": str}
    Stage codes are technical; their user-facing wording lives in the frontend.
    422 and 429 are returned as plain HTTP errors before the stream opens.
    """
    turn = await prepare_turn(body, request)
    graph = request.app.state.graph

    async def events() -> AsyncIterator[str]:
        if turn.cached is not None:
            yield sse("final", ChatResponse(response=turn.cached, tools_used=[], cached=True, audio=None).model_dump())
            return
        try:
            async for ev in graph.astream_events(
                {"messages": [HumanMessage(content=turn.message)]},
                turn.config,
                context=turn.context,
                version="v2",
            ):
                kind, name = ev["event"], ev.get("name")
                node = ev.get("metadata", {}).get("langgraph_node")
                if kind == "on_chain_start" and name in ("agent", "vision") and node == name:
                    yield sse("status", {"stage": "thinking" if name == "agent" else "vision"})
                elif kind == "on_tool_start":
                    yield sse("status", {"stage": "tool", "tool": name})
                elif kind == "on_tool_end":
                    # Tool results are back: the model is now composing the answer.
                    yield sse("status", {"stage": "writing"})
            state = (await graph.aget_state(turn.config)).values
            yield sse("final", response_from_state(state).model_dump())
        except asyncio.CancelledError:
            raise  # client disconnected
        except Exception:
            logger.exception("Graph stream failed for thread %s", body.thread_id)
            yield sse("error", {"status": 502, "detail": GRAPH_ERROR})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # no-transform and X-Accel-Buffering keep proxies from buffering the stream.
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
