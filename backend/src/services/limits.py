"""Daily request limit, stored in usage_daily.

A single global counter (row '__global__'): it protects the API budget. It lives
in Postgres, never in process memory, so N uvicorn workers share one count.
Only cache misses consume it: a hit never reaches the model.

Requests are weighted by cost: an image turn sends ~1,000 image tokens (small
images are upscaled in the client) on top of the text, so it counts as
IMAGE_REQUEST_UNITS units instead of one.
"""

from psycopg_pool import AsyncConnectionPool

import settings

GLOBAL_IDENTIFIER = "__global__"
TEXT_REQUEST_UNITS = 1
IMAGE_REQUEST_UNITS = 3

# There is no language signal before the graph runs, so the message is bilingual.
LIMIT_MESSAGE = (
    "El servicio alcanzó su capacidad de consultas por hoy. No es un problema de tu parte: "
    "vuelve a intentarlo mañana. / "
    "The service has reached its daily query capacity. Nothing is wrong on your side: "
    "please try again tomorrow."
)


async def consume_global_quota(pool: AsyncConnectionPool, units: int = TEXT_REQUEST_UNITS) -> bool:
    """Atomically add `units` to today's counter. Returns False once the daily limit is exceeded.

    A request is only let through if all its units fit: with 49 of 50 used, an
    image (3 units) is rejected even though one unit is left.
    """
    async with pool.connection() as conn:
        # Read and increment in one statement: a read-then-write would let two
        # concurrent requests see the same count and both pass.
        cur = await conn.execute(
            """
            INSERT INTO usage_daily (identifier, day, count)
            VALUES (%s, CURRENT_DATE, %s)
            ON CONFLICT (identifier, day) DO UPDATE SET count = usage_daily.count + EXCLUDED.count
            RETURNING count
            """,
            (GLOBAL_IDENTIFIER, units),
        )
        count = (await cur.fetchone())[0]
    return count <= settings.DAILY_GLOBAL_LIMIT
