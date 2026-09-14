"""Semantic cache over the semantic_cache table and the match_cache function.

The cache is a curated FAQ: it is written ONLY by the pre-population below, never
from live conversations. A live answer can depend on the thread (a name, an
earlier turn) and would be served to someone else out of context.

Threshold measured with real questions (table in STACK.md, "Umbral de similitud"):
no value below 0.90 is safe. Different questions on the same topic ("¿qué NO
cubre el seguro?" vs "¿qué cubre?") score higher than genuine paraphrases, so
lowering it returns the answer to another question.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from langchain_core.embeddings import Embeddings
from psycopg_pool import AsyncConnectionPool

import settings

logger = logging.getLogger(__name__)

EXPIRY_DAYS = 30
REFRESH_INTERVAL_SECONDS = 24 * 60 * 60
# Arbitrary constant: serializes pre-population across uvicorn workers.
PREPOPULATE_LOCK_ID = 7_310_001

# The five FAQ topics listed in STACK.md, phrased as a user would ask them.
FAQ_QUESTIONS = (
    "¿Qué cubre el seguro de depósitos?",
    "¿Cuál es el monto asegurado por el seguro de depósitos?",
    "¿Qué es una llave de Bre-B?",
    "¿Cuáles son los límites de una transferencia inmediata?",
    "¿Qué es la tasa de usura?",
)

# (answer, tools_used) for a question asked in a clean, context-free thread.
AnswerFn = Callable[[str], Awaitable[tuple[str, list[str]]]]

# Tools whose output depends on the moment or on numbers in the question.
# An answer that used any of them is never stored, even during pre-population.
VOLATILE_TOOLS = frozenset({"get_usd_rate", "get_stock_quote", "get_crypto_price", "calculate_interest", "imagen"})


def _vector_literal(vector: list[float]) -> str:
    # pgvector accepts the text form; verified to cast correctly through match_cache with psycopg3.
    return "[" + ",".join(repr(x) for x in vector) + "]"


class SemanticCache:
    def __init__(self, pool: AsyncConnectionPool, embeddings: Embeddings):
        self._pool = pool
        self._embeddings = embeddings

    async def lookup(self, question: str) -> str | None:
        vector = await self._embeddings.aembed_query(question)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT m.response, m.similarity
                FROM match_cache(%s, %s, 5) AS m
                JOIN semantic_cache c ON c.id = m.id
                WHERE c.created_at >= NOW() - make_interval(days => %s)
                ORDER BY m.similarity DESC
                LIMIT 1
                """,
                (_vector_literal(vector), settings.CACHE_THRESHOLD, EXPIRY_DAYS),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        logger.info("Cache hit (similarity=%.3f)", row[1])
        return row[0]

    async def prepopulate(self, answer: AnswerFn) -> None:
        """Expire old rows and generate the FAQ entries that are missing."""
        async with self._pool.connection() as conn:
            cur = await conn.execute("SELECT pg_try_advisory_lock(%s)", (PREPOPULATE_LOCK_ID,))
            if not (await cur.fetchone())[0]:
                logger.info("Cache pre-population already running in another worker; skipping")
                return
            try:
                await conn.execute(
                    "DELETE FROM semantic_cache WHERE created_at < NOW() - make_interval(days => %s)",
                    (EXPIRY_DAYS,),
                )
                cur = await conn.execute("SELECT query_text FROM semantic_cache WHERE query_text = ANY(%s)", (list(FAQ_QUESTIONS),))
                present = {r[0] for r in await cur.fetchall()}
                await conn.commit()

                for question in (q for q in FAQ_QUESTIONS if q not in present):
                    try:
                        text, tools_used = await answer(question)
                    except Exception:
                        logger.exception("Could not generate cached answer for %r", question)
                        continue
                    if VOLATILE_TOOLS.intersection(tools_used) or not text.strip():
                        logger.warning("Not caching %r: tools_used=%s", question, tools_used)
                        continue
                    vector = await self._embeddings.aembed_query(question)
                    await conn.execute(
                        "INSERT INTO semantic_cache (query_text, query_embedding, response) VALUES (%s, %s, %s)",
                        (question, _vector_literal(vector), text),
                    )
                    await conn.commit()
                    logger.info("Cached FAQ answer for %r (tools_used=%s)", question, tools_used)
            finally:
                await conn.execute("SELECT pg_advisory_unlock(%s)", (PREPOPULATE_LOCK_ID,))
                await conn.commit()

    async def refresh_forever(self, answer: AnswerFn) -> None:
        # Entries are only written here, so a daily pass is what keeps expired
        # FAQ answers from lingering in a long-running process.
        while True:
            try:
                await self.prepopulate(answer)
            except Exception:
                logger.exception("Cache pre-population failed")
            await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
