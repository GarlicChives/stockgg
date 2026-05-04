"""Search provider interface + Google Custom Search Engine implementation.

To swap provider: subclass SearchProvider and pass instance to build_theme_dictionary.run().

Required env vars for GoogleCSEProvider:
  GOOGLE_CSE_API_KEY  — Google API key with Custom Search API enabled
  GOOGLE_CSE_CX       — Search engine ID (from cse.google.com)
"""
import json
import os
import re
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod


class SearchProvider(ABC):
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def search(self, query: str, num_results: int = 3) -> list[str]:
        """Return list of snippet strings (HTML-stripped)."""
        ...


class GoogleCSEProvider(SearchProvider):
    """Google Custom Search Engine API — returns top-N result snippets."""

    _BASE = "https://www.googleapis.com/customsearch/v1"

    def __init__(
        self,
        api_key: str | None = None,
        cx: str | None = None,
    ):
        self._api_key = api_key or os.environ.get("GOOGLE_CSE_API_KEY", "")
        self._cx      = cx      or os.environ.get("GOOGLE_CSE_CX", "")

    def available(self) -> bool:
        return bool(self._api_key and self._cx)

    def search(self, query: str, num_results: int = 3) -> list[str]:
        if not self.available():
            return []
        url = f"{self._BASE}?" + urllib.parse.urlencode({
            "key": self._api_key,
            "cx":  self._cx,
            "q":   query,
            "num": num_results,
        })
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            snippets = []
            for item in data.get("items", [])[:num_results]:
                raw = item.get("snippet", "")
                cleaned = re.sub(r"<[^>]+>", "", raw).replace("\n", " ").strip()
                if cleaned:
                    snippets.append(cleaned)
            return snippets
        except Exception:
            return []


def build_query(name: str, ticker: str, market: str) -> str:
    """Construct search query optimised per market."""
    if market == "TW":
        return f"{name} 法說會 產品 營收比重"
    return f"{name} {ticker} investor day products revenue breakdown"
