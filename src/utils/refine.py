"""Content refinement + embedding pipeline.

Refinement: Ollama (local Qwen2.5:7b) — free, no API key, runs on M1.
Embedding:  sentence-transformers (local) — for pgvector similarity search.

If Ollama is not running, refined_content stays NULL (embedding still generated).
Run `ollama serve` before using refinement.
"""
import os
import re
from typing import Optional
from urllib.request import urlopen

_embed_model = None

# Concise prompt keeps inference fast on local hardware
_REFINE_SYSTEM = """\
你是投資篩選器。從內容截取投資相關段落，過濾閒聊廣告個人軼事。
投資相關：總經(利率/通膨/GDP/聯準會)、國際股市、個股動向、供應鏈產業。
與投資完全無關→回覆 NONE。

輸出格式（嚴格遵守，不要加其他文字）：
TAGS: <從 macro/international/stock/supply_chain 選，逗號分隔>
CONTENT:
<條列式重點，保留數字和標的名稱>"""

_PODCAST_SYSTEM = """\
你是投資分析整理員。將 Podcast 逐字稿整理成結構化投資筆記。

必須過濾（完全刪除，不保留）：
- 開場白、結語、感謝詞、廣告、訂閱推廣
- 主持人間的閒聊、趣事、日常話題
- 與投資市場完全無關的內容

必須保留並結構化（以下每類各用編號列點）：
1. 總經觀點：利率、通膨、GDP、聯準會動向、景氣循環判斷
2. 市場判斷：指數看法（多/空/區間）、資金輪動方向
3. 產業/題材：具體看好或看壞的產業，說明理由
4. 標的分析：提到的具體股票（台股代號/美股TICKER），給出看法與邏輯
5. 風險提示：提到的下行風險、注意事項

格式規定（嚴格遵守）：
TAGS: <從 macro/international/stock/supply_chain 選，逗號分隔>
CONTENT:
【總經觀點】
1. ...（保留數字、時間點）
【市場判斷】
1. ...
【產業/題材】
1. ...
【標的分析】
1. 股名(代號)：...（說明看法與邏輯，保留目標價/估值）
【風險提示】
1. ...

若某類別完全沒有內容，略去該類別。若整段無投資內容→回覆 NONE。"""

_VALID_TAGS = {"macro", "international", "stock", "supply_chain"}
OLLAMA_MODEL = "qwen2.5:7b"
CONTENT_TRUNCATE = 4000      # chars for regular articles
PODCAST_TRUNCATE = 12000     # chars for podcast transcripts (longer)


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


def refine_content(raw: str, title: str = "",
                   is_podcast: bool = False) -> tuple[str, list[str]] | None:
    """Return (refined_text, tags) via Ollama, or None if Ollama unavailable."""
    if not _ollama_running():
        return None

    import ollama  # imported here to avoid error when package missing
    system_prompt = _PODCAST_SYSTEM if is_podcast else _REFINE_SYSTEM
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
        response = resp["message"]["content"].strip()
    except Exception as e:
        print(f"    [refine/ollama] {e}")
        return None

    if response.upper().startswith("NONE") or not response:
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
