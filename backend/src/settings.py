"""Environment configuration, read once at startup.

backend/.env is loaded here and only here, before any other module reads the
environment. In a container there is no .env file: variables come from env_file.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing environment variable {name} (backend/.env)")
    return value


# Chat model: swappable, OpenAI-compatible provider.
LLM_BASE_URL = _require("LLM_BASE_URL")
LLM_API_KEY = _require("LLM_API_KEY")
LLM_MODEL = _require("LLM_MODEL")

# Vision: same factory, own provider settings. Empty means "inherit the chat
# ones", so vision can move to another provider without dragging the chat along.
VISION_MODEL = _require("VISION_MODEL")
VISION_BASE_URL = os.getenv("VISION_BASE_URL") or LLM_BASE_URL
VISION_API_KEY = os.getenv("VISION_API_KEY") or LLM_API_KEY
MAX_IMAGE_MB = float(_require("MAX_IMAGE_MB"))

# Embeddings: always OpenAI, never routed through LLM_BASE_URL.
OPENAI_API_KEY = _require("OPENAI_API_KEY")
EMBEDDING_MODEL = _require("EMBEDDING_MODEL")
EMBEDDING_DIM = int(_require("EMBEDDING_DIM"))

# Market tools. FX (open.er-api.com) and crypto (Coinbase) need no key.
FINNHUB_API_KEY = _require("FINNHUB_API_KEY")

# SQLAlchemy form (langchain-postgres). The checkpointer uses CHECKPOINT_DSN.
DATABASE_URL = _require("DATABASE_URL")
RETRIEVER_TOP_K = int(_require("RETRIEVER_TOP_K"))
HISTORY_LIMIT = int(_require("HISTORY_LIMIT"))
HISTORY_LIMIT_WITH_IMAGE = int(_require("HISTORY_LIMIT_WITH_IMAGE"))
# Plain psycopg form of the same database, for the cache and the usage counter.
DATABASE_DSN = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)

# Backend guards. Read once at startup; a restart is needed to change them.
CACHE_THRESHOLD = float(_require("CACHE_THRESHOLD"))
DAILY_GLOBAL_LIMIT = int(_require("DAILY_GLOBAL_LIMIT"))

# "memory" in development, "postgres" on the VPS.
CHECKPOINTER = os.getenv("CHECKPOINTER", "memory")
CHECKPOINT_DSN = os.getenv("CHECKPOINT_DSN")
