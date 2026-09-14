"""Build the RAG corpus: scrape + local .txt sources -> chunks -> rag_documents.

Runs from the host, never from the backend image:

    cd scripts && ../.venv/bin/python ingest.py            # full ingest
    cd scripts && ../.venv/bin/python ingest.py --dry-run  # fetch + split only, no DB/OpenAI

Every source is loaded and split BEFORE touching the database, so a single
failing source aborts the run with nothing written.
"""

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

import httpx
import psycopg
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import Column, PGEngine, PGVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter

load_dotenv(Path(__file__).resolve().parent / ".env")

TABLE_NAME = "rag_documents"
SOURCES_DIR = Path(__file__).resolve().parent.parent / "data" / "sources"
REQUIRED_KEYS = ("source_url", "entidad", "fecha_captura")

# ── Path 1: scraped sources (URLs exactly as listed in STACK.md) ────────────
# One CSS selector per site: each CMS wraps the article differently. "strip"
# removes the site's own navigation that lives inside that container.
FOGAFIN = {
    # Acquia Site Studio node body. Class names there are generated ids, so only
    # stable markers are used: the side menu list and the breadcrumb block.
    "selector": "article[data-history-node-id]",
    "strip": "ul.coh-menu-list-container, [class*=breadcrumb]",
}

NU_BLOG = {
    # WordPress block theme: the post body. Outside it live the mega menu, the
    # sign-up form and the footer. Inside it, strip the table of contents, the
    # "apply now" call-to-action buttons (wrapped or bare), media embeds, and the
    # related-links blocks (titles of other posts, no content of their own).
    "selector": ".entry-content",
    "strip": (
        "section.wp-block-directory-full, .nu-shortcode-form-trigger-wrap, .nu-shortcode-form-trigger, figure, "
        ".nubank-link-list, section.articulos-relacionados-slider"
    ),
}

# Fogafín's apex domain serves an expired TLS certificate; only www. answers.
SCRAPED = [
    {"url": "https://www.fogafin.gov.co/preguntas-frecuentes", "entidad": "Fogafín", **FOGAFIN},
    {"url": "https://www.fogafin.gov.co/que-es-el-seguro-de-depositos", "entidad": "Fogafín", **FOGAFIN},
    {"url": "https://blog.nu.com.co/bre-b-paga-con-tus-llaves-en-segundos/", "entidad": "Nu Colombia", **NU_BLOG},
    {"url": "https://blog.nu.com.co/preguntas-frecuentes-sobre-tu-tarjeta-de-credito-nu/", "entidad": "Nu Colombia", **NU_BLOG},
]

# Block-level tags end a paragraph. Inline tags (a, strong, em) must NOT break
# lines, or a bolded phrase ends up as its own "paragraph" in the splitter.
BLOCK_TAGS = ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd", "tr", "blockquote", "div", "section", "summary", "details"]


def html_to_text(node) -> str:
    for block in node.find_all(BLOCK_TAGS):
        block.append("\n\n")
    # <br> separates items inside a single <p> (numbered steps, address lines).
    for br in node.find_all("br"):
        br.replace_with("\n\n")
    raw = node.get_text()
    lines = [" ".join(line.split()) for line in raw.split("\n")]
    # Collapse runs of empty lines into a single paragraph break.
    paragraphs, current = [], []
    for line in lines:
        if line:
            current.append(line)
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        sys.exit(f"Missing environment variable {name} (scripts/.env)")
    return value


def scrape(src: dict, user_agent: str) -> Document:
    resp = httpx.get(
        src["url"],
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": user_agent},
    )
    # Any non-200 final status aborts: an indexed error body is worse than a missing source.
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from {src['url']}")

    soup = BeautifulSoup(resp.text, "html.parser")
    node = soup.select_one(src["selector"])
    # Catches bot walls and JS-rendered pages, both of which return 200 with no content.
    if node is None:
        raise ValueError(f"Selector {src['selector']!r} matched nothing at {src['url']}")

    # No generic "button" here: accordion FAQs put the question inside a <button>.
    # Call-to-action buttons are site-specific and go in each site's "strip".
    for tag in node.select("script, style, noscript, svg, nav, form"):
        tag.decompose()
    if src.get("strip"):
        for tag in node.select(src["strip"]):
            tag.decompose()

    text = html_to_text(node)
    if not text:
        raise ValueError(f"Selector {src['selector']!r} matched an empty node at {src['url']}")

    return Document(
        page_content=text,
        metadata={
            "source_url": src["url"],
            "entidad": src["entidad"],
            "fecha_captura": date.today().isoformat(),
        },
    )


# ── Path 2: manually captured .txt files ────────────────────────────────────
# Three "key: value" lines, a blank line, then the pasted body.
def load_file(path: Path) -> Document:
    header, _, body = path.read_text(encoding="utf-8").partition("\n\n")

    meta = {}
    for line in header.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()

    missing = [k for k in REQUIRED_KEYS if not meta.get(k)]
    if missing:
        raise ValueError(f"{path.name}: header missing {missing}")
    if not body.strip():
        raise ValueError(f"{path.name}: empty body — is there a blank line after the header?")

    return Document(
        page_content=body.strip(),
        metadata={k: meta[k] for k in REQUIRED_KEYS},
    )


def load_and_split() -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=int(require_env("CHUNK_SIZE")),
        chunk_overlap=int(require_env("CHUNK_OVERLAP")),
        separators=["\n\n", "\n", ". ", " ", ""],  # paragraph → line → sentence → word
    )
    user_agent = require_env("USER_AGENT")

    docs = [scrape(src, user_agent) for src in SCRAPED]
    files = sorted(SOURCES_DIR.glob("*.txt"))
    docs += [load_file(p) for p in files]
    if not docs:
        raise RuntimeError("No sources to ingest")

    print(f"Sources: {len(SCRAPED)} scraped, {len(files)} local files")
    all_chunks: list[Document] = []
    for doc in docs:
        chunks = splitter.split_documents([doc])
        if not chunks:
            raise ValueError(f"Zero chunks from {doc.metadata['source_url']}")
        print(f"{len(chunks):>4} chunks — {doc.metadata['entidad']} — {doc.metadata['source_url']}")
        all_chunks.extend(chunks)

    print(f"Total: {len(all_chunks)} chunks from {len(docs)} sources")
    return all_chunks


async def ensure_table(engine: PGEngine, vector_size: int) -> None:
    try:
        await engine.ainit_vectorstore_table(
            table_name=TABLE_NAME,
            vector_size=vector_size,
            metadata_columns=[Column("source_url", "TEXT")],  # real column, not JSON
        )
        print(f"Created table {TABLE_NAME}")
    except Exception as exc:
        # SQLAlchemy wraps the driver error; only "already exists" is expected.
        if isinstance(getattr(exc, "orig", None), psycopg.errors.DuplicateTable):
            return
        raise


async def store(chunks: list[Document]) -> None:
    # Embeddings are always OpenAI: explicit key, never LLM_BASE_URL.
    embeddings = OpenAIEmbeddings(
        model=require_env("EMBEDDING_MODEL"),
        dimensions=int(require_env("EMBEDDING_DIM")),
        api_key=require_env("OPENAI_API_KEY"),
        base_url="https://api.openai.com/v1",
    )
    engine = PGEngine.from_connection_string(url=require_env("DATABASE_URL"))
    try:
        await ensure_table(engine, int(require_env("EMBEDDING_DIM")))
        vector_store = await PGVectorStore.create(
            engine=engine,
            table_name=TABLE_NAME,
            embedding_service=embeddings,
            metadata_columns=["source_url"],
        )
        # Re-ingest per source: delete its previous chunks before inserting.
        for url in sorted({c.metadata["source_url"] for c in chunks}):
            await vector_store.adelete(filter={"source_url": url})
        await vector_store.aadd_documents(chunks)
        print(f"Stored {len(chunks)} chunks in {TABLE_NAME}")
    finally:
        await engine.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and split every source, print counts, write nothing",
    )
    args = parser.parse_args()

    try:
        chunks = load_and_split()
    except Exception as exc:
        sys.exit(f"Ingest aborted, nothing written: {exc}")

    if args.dry_run:
        print("Dry run: database untouched")
        return
    asyncio.run(store(chunks))


if __name__ == "__main__":
    main()
