"""LLM classifier interface + Gemini implementation.

To swap provider: subclass ClassifierProvider and pass instance to build_theme_dictionary.run().

Uses the same urllib-based REST pattern as src/utils/refine.py — no SDK dependency.
"""
import json
import os
import re
import urllib.request
from abc import ABC, abstractmethod

from src.prompts import render as render_prompt
from src.utils.api_logger import log_usage

GEMINI_MODEL = "gemini-2.5-flash-lite"
GEMINI_BASE  = "https://generativelanguage.googleapis.com/v1beta/models"


class ClassifierProvider(ABC):
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def classify(self, snippets_text: str, themes: list[dict]) -> dict:
        """
        themes: list of {"id": str, "name": str, "keyword": str}
        Returns:
          {
            "matched":    [theme_id, ...],                    # IDs from input list
            "new_themes": [{"id","name","keyword"}, ...],     # concepts not in dict
          }
        Caller is responsible for deduping new_themes against the live dict
        (LLM may miss near-matches) and for actually creating dictionary entries.
        """
        ...


class GeminiClassifier(ClassifierProvider):
    """Gemini Flash-Lite — cheap, reliable JSON output via response_mime_type."""

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")

    def available(self) -> bool:
        return bool(self._api_key)

    def _build_prompt(self, snippets_text: str, themes: list[dict]) -> str:
        theme_lines = "\n".join(
            f"- {t['id']}: {t['keyword']} ({t['name']})"
            for t in themes
            if t.get("keyword")
        )
        return render_prompt(
            "theme_classifier",
            snippets=snippets_text,
            theme_lines=theme_lines,
        )

    def classify(self, snippets_text: str, themes: list[dict]) -> dict:
        empty = {"matched": [], "new_themes": []}
        if not self.available() or not snippets_text.strip():
            return empty

        prompt = self._build_prompt(snippets_text, themes)
        url = f"{GEMINI_BASE}/{GEMINI_MODEL}:generateContent?key={self._api_key}"
        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 1024,
                "response_mime_type": "application/json",
            },
        }).encode()
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
            usage = data.get("usageMetadata", {})
            log_usage(
                "gemini", GEMINI_MODEL, "theme_classify",
                usage.get("promptTokenCount", 0),
                usage.get("candidatesTokenCount", 0),
            )
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(
                p["text"] for p in parts
                if "text" in p and not p.get("thought", False)
            ).strip()
            text = re.sub(r"^```[a-z]*\n?", "", text).rstrip("`").strip()
            result = json.loads(text)
            if isinstance(result, dict):
                matched = [s for s in result.get("matched", []) if isinstance(s, str)]
                new_themes = []
                for t in result.get("new_themes", []):
                    if not isinstance(t, dict):
                        continue
                    tid   = t.get("id", "")
                    tname = t.get("name", "")
                    tkw   = t.get("keyword", tname)
                    if tid and tname and re.match(r"^[a-z][a-z0-9_]{2,40}$", tid):
                        new_themes.append({"id": tid, "name": tname, "keyword": tkw})
                return {"matched": matched, "new_themes": new_themes}
            # Backwards compat: if model returns a bare list, treat as matched.
            if isinstance(result, list):
                return {"matched": [s for s in result if isinstance(s, str)],
                        "new_themes": []}
        except Exception:
            pass
        return empty
