"""Content refinement + embedding pipeline.

Refinement priority (podcast): Gemini 2.5 Flash (GOOGLE_API_KEY) → Ollama qwen2.5:7b fallback
Refinement priority (articles): Ollama qwen2.5:7b → Gemini fallback if Ollama down
Embedding: sentence-transformers (local) — for pgvector similarity search.
"""
import json
import os
import re
import urllib.request
from typing import Optional
from urllib.request import urlopen

from src.prompts import load as load_prompt
from src.utils.api_logger import log_usage

_embed_model = None

_VALID_TAGS = {"macro", "international", "stock", "supply_chain"}
OLLAMA_MODEL    = "qwen2.5:7b"
GEMINI_MODEL    = "gemini-2.5-flash-lite"   # refinement: lite is cheaper & sufficient
GEMINI_BASE     = "https://generativelanguage.googleapis.com/v1beta/models"
CONTENT_TRUNCATE = 4000
PODCAST_TRUNCATE = 16000


def _ollama_running() -> bool:
    try:
        urlopen("http://localhost:11434/api/tags", timeout=2)
        return True
    except Exception:
        return False


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            token = os.environ.get("HF_TOKEN") or None
            _embed_model = SentenceTransformer(
                "paraphrase-multilingual-mpnet-base-v2",
                token=token,
            )
        except ImportError:
            pass
    return _embed_model


def _s2tw(text: str) -> str:
    """Convert simplified Chinese to traditional Chinese (best-effort)."""
    try:
        import opencc
        return opencc.OpenCC('s2twp').convert(text)
    except Exception:
        return text


def _parse_refine_response(response: str) -> tuple[str, list[str]]:
    """Extract (refined_text, tags) from TAGS:/CONTENT: formatted response."""
    if not response:
        return "", []
    stripped_upper = response.strip().upper()
    # NONE variants: bare NONE, or TAGS: NONE (model forgot the format)
    if stripped_upper == "NONE" or stripped_upper.startswith("NONE\n") or \
            re.match(r"TAGS:\s*NONE", response, re.IGNORECASE):
        return "", []
    tags: list[str] = []
    refined = response
    tag_match = re.match(r"TAGS:\s*(.+)", response, re.IGNORECASE)
    if tag_match:
        raw_tags = [t.strip().lower() for t in tag_match.group(1).split(",")]
        tags = [t for t in raw_tags if t in _VALID_TAGS]
        content_match = re.search(r"CONTENT:\s*\n(.*)", response, re.IGNORECASE | re.DOTALL)
        refined = content_match.group(1).strip() if content_match else response
    return refined, tags


def _gemini_refine(api_key: str, title: str, raw: str,
                   is_podcast: bool = False) -> tuple[str, list[str]] | None:
    """Refine via Gemini 2.5 Flash. Returns (refined, tags) or None on error."""
    system_prompt = load_prompt("refine_podcast" if is_podcast else "refine_article")
    truncate = PODCAST_TRUNCATE if is_podcast else CONTENT_TRUNCATE
    full_prompt = f"{system_prompt}\n\n標題：{title}\n\n{raw[:truncate]}"

    url = f"{GEMINI_BASE}/{GEMINI_MODEL}:generateContent?key={api_key}"
    payload = json.dumps({
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 8000,
        },
    }).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        usage = data.get("usageMetadata", {})
        log_usage(
            "gemini", GEMINI_MODEL, "refine",
            usage.get("promptTokenCount", 0),
            usage.get("candidatesTokenCount", 0),
            usage.get("thoughtsTokenCount", 0),
        )
        parts = data["candidates"][0]["content"]["parts"]
        response = "".join(
            p["text"] for p in parts if "text" in p and not p.get("thought", False)
        ).strip()
        if not response:
            response = "".join(p.get("text", "") for p in parts).strip()
    except Exception as e:
        print(f"    [refine/gemini] {e}")
        log_usage("gemini", GEMINI_MODEL, "refine", 0, 0, success=False)
        return None

    return _parse_refine_response(response)


def _refine_ollama(raw: str, title: str,
                   is_podcast: bool = False) -> tuple[str, list[str]] | None:
    """Refine via local Ollama. Returns None on error or unavailable."""
    try:
        import ollama
    except ImportError:
        return None
    system_prompt = load_prompt("refine_podcast" if is_podcast else "refine_article")
    truncate = PODCAST_TRUNCATE if is_podcast else CONTENT_TRUNCATE
    text_input = f"標題：{title}\n\n{raw[:truncate]}"
    try:
        resp = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": text_input},
            ],
            options={"temperature": 0},
        )
        response = _s2tw(resp["message"]["content"].strip())
    except Exception as e:
        print(f"    [refine/ollama] {e}")
        return None
    return _parse_refine_response(response)


def refine_content(raw: str, title: str = "",
                   is_podcast: bool = False) -> tuple[str, list[str]] | None:
    """Return (refined_text, tags) or None if no backend available.

    Podcast: Gemini only (qwen2.5:7b cannot follow the structured format reliably)
    Article: Ollama preferred (local, fast) → Gemini fallback
    """
    api_key = os.environ.get("GOOGLE_API_KEY")

    if is_podcast:
        if api_key:
            return _gemini_refine(api_key, title, raw, is_podcast=True)
        # No Ollama fallback for podcasts — format adherence is critical
        return None
    else:
        if _ollama_running():
            return _refine_ollama(raw, title, is_podcast=False)
        if api_key:
            return _gemini_refine(api_key, title, raw, is_podcast=False)
        return None


def embed_text(text: str) -> Optional[list[float]]:
    """Return 768-dim embedding vector, or None if model unavailable."""
    model = _get_embed_model()
    if model is None or not text:
        return None
    try:
        vec = model.encode(text[:2000], normalize_embeddings=True)
        return vec.tolist()
    except Exception as e:
        print(f"    [embed] {e}")
        return None


async def refine_and_store(conn, article_id: int, title: str, content: str,
                           is_podcast: bool = False) -> bool:
    """Refine + embed an article and persist to DB. Returns True if anything was written."""
    if not content:
        return False

    result = refine_content(content, title, is_podcast=is_podcast)
    has_refined = result is not None
    refined, tags = result if has_refined else ("", [])

    embed_src = refined if (has_refined and refined) else content
    embedding = embed_text(embed_src)

    if has_refined and embedding is not None:
        vec_str = "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"
        await conn.execute(
            """UPDATE articles
               SET refined_content=$1, content_tags=$2,
                   embedding=CAST($3 AS vector), updated_at=NOW()
               WHERE id=$4""",
            refined, tags, vec_str, article_id,
        )
    elif has_refined:
        await conn.execute(
            """UPDATE articles SET refined_content=$1, content_tags=$2, updated_at=NOW()
               WHERE id=$3""",
            refined, tags, article_id,
        )
    elif embedding is not None:
        vec_str = "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"
        await conn.execute(
            "UPDATE articles SET embedding=CAST($1 AS vector), updated_at=NOW() WHERE id=$2",
            vec_str, article_id,
        )
    else:
        return False
    return True
