"""Search provider interface + Tavily implementation.

To swap provider: subclass SearchProvider and pass instance to build_theme_dictionary.run().

Required env vars:
  TavilyProvider — TAVILY_API_KEY (app.tavily.com, 1000 free calls/month)
"""
import os
import re
from abc import ABC, abstractmethod


class SearchProvider(ABC):
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def search(self, query: str, num_results: int = 3) -> list[str]:
        """Return list of snippet strings (HTML-stripped)."""
        ...


class TavilyProvider(SearchProvider):
    """Tavily Search API — designed for LLM RAG, better snippet quality."""

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("TAVILY_API_KEY", "")

    def available(self) -> bool:
        return bool(self._api_key)

    def search(self, query: str, num_results: int = 3) -> list[str]:
        if not self.available():
            return []
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=self._api_key)
            resp = client.search(query, max_results=num_results, search_depth="basic")
            snippets = []
            for r in resp.get("results", [])[:num_results]:
                text = r.get("content", "") or r.get("snippet", "")
                cleaned = re.sub(r"<[^>]+>", "", text).replace("\n", " ").strip()
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
