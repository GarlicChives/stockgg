"""Theme classification cache — JSON file, 30-day TTL.

Swap entire class for a SQLite / Redis implementation without changing callers.
"""
import json
from datetime import date
from pathlib import Path

CACHE_FILE = Path(__file__).resolve().parents[2] / "data" / "search_cache.json"
TTL_DAYS = 30


class CacheManager:
    def __init__(self, path: Path = CACHE_FILE):
        self._path = path
        self._data: dict = self._load()

    # ── I/O ──────────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def save(self) -> None:
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def is_fresh(self, ticker: str) -> bool:
        """True if ticker was classified within TTL_DAYS."""
        entry = self._data.get(ticker, {})
        raw = entry.get("updated_at")
        if not raw:
            return False
        try:
            return (date.today() - date.fromisoformat(raw)).days < TTL_DAYS
        except ValueError:
            return False

    def get(self, ticker: str) -> dict | None:
        return self._data.get(ticker)

    def set(self, ticker: str, name: str, market: str, theme_ids: list[str]) -> None:
        self._data[ticker] = {
            "name": name,
            "market": market,
            "updated_at": date.today().isoformat(),
            "theme_ids": theme_ids,
        }

    def reset(self) -> None:
        """Clear all cache entries (force full re-classification)."""
        self._data = {}
