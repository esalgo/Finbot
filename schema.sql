-- Hand-written schema: semantic cache + daily usage limits.
-- rag_documents is NOT here: PGEngine.ainit_vectorstore_table() creates it (scripts/ingest.py).
-- checkpoints* tables are created by AsyncPostgresSaver.setup().
-- VECTOR(1536) must match EMBEDDING_DIM.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS semantic_cache (
  id              BIGSERIAL PRIMARY KEY,
  query_text      TEXT NOT NULL,
  query_embedding VECTOR(1536) NOT NULL,
  response        TEXT NOT NULL,
  created_at      TIMESTAMP DEFAULT NOW()
);

-- One row per identifier per day; a fixed '__global__' row holds the global counter.
-- Increment atomically with INSERT ... ON CONFLICT ... RETURNING count.
CREATE TABLE IF NOT EXISTS usage_daily (
  identifier TEXT NOT NULL,
  day        DATE NOT NULL,
  count      INT  NOT NULL DEFAULT 0,
  PRIMARY KEY (identifier, day)
);

CREATE OR REPLACE FUNCTION match_cache(
  query_embedding_input VECTOR(1536),
  match_threshold       FLOAT DEFAULT 0.90,
  match_count           INT   DEFAULT 1
)
RETURNS TABLE (id BIGINT, query_text TEXT, response TEXT, similarity FLOAT)
LANGUAGE SQL STABLE AS $$
  SELECT id, query_text, response,
    1 - (semantic_cache.query_embedding <=> query_embedding_input) AS similarity
  FROM semantic_cache
  WHERE 1 - (semantic_cache.query_embedding <=> query_embedding_input) > match_threshold
  ORDER BY similarity DESC LIMIT match_count;
$$;

-- No index on purpose: ivfflat computes its centroids at creation time, so
-- building it on an empty table degrades recall. Create it after the cache has
-- real volume, with lists ≈ rows/1000. Below a few thousand rows a sequential
-- scan is faster and exact.
--
-- CREATE INDEX semantic_cache_embedding_idx
--   ON semantic_cache USING ivfflat (query_embedding vector_cosine_ops)
--   WITH (lists = 10);
