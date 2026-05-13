"""Database adapter — asyncpg-compatible interface over Supabase Edge Function.

This is the **public-renderer** variant: talks to db-proxy-public, which
hard-allowlists 9 SELECT templates needed for HTML render and rejects
anything else with HTTP 403. Even if the bearer key leaks, the only
columns reachable are the public-safe ones tracked in
migration/queries_inventory.md.

API surface is identical to asyncpg (kept for drop-in compatibility):
  conn = await db.connect()
  rows  = await conn.fetch(sql, *params)
  row   = await conn.fetchrow(sql, *params)
  val   = await conn.fetchval(sql, *params)
  tag   = await conn.execute(sql, *params)
  await conn.close()

Works on any network with HTTPS (port 443) — including company WiFi
that blocks direct PostgreSQL port 5432.
"""
import os
import re
from datetime import date, datetime, timezone
from typing import Any

import httpx

EDGE_URL = "https://mnseyguxiiditaybpfup.supabase.co/functions/v1/db-proxy-public"

# ISO datetime / date detection for auto-parsing JSON strings back to Python types
_DT_RE  = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _coerce(v: Any) -> Any:
    """Parse ISO datetime/date strings returned by JSON into Python objects."""
    if not isinstance(v, str):
        return v
    if _DT_RE.match(v):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            pass
    if _DATE_RE.match(v):
        try:
            return date.fromisoformat(v)
        except ValueError:
            pass
    return v


class _Row(dict):
    """Dict with attribute access + automatic datetime coercion on read."""

    def __getitem__(self, key: str) -> Any:
        return _coerce(super().__getitem__(key))

    def get(self, key: str, default: Any = None) -> Any:
        v = super().get(key, default)
        return _coerce(v) if v is not default else default

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def values(self):
        return [_coerce(v) for v in super().values()]

    def items(self):
        return [(k, _coerce(v)) for k, v in super().items()]


class AsyncConnection:
    """asyncpg-compatible connection backed by the db-proxy Edge Function."""

    def __init__(self, service_key: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _serialize_param(v: Any) -> Any:
        """Convert Python types that aren't JSON-serializable to strings."""
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, date):
            return v.isoformat()
        return v

    async def _call(self, query: str, params: list) -> tuple[list[_Row], str]:
        serialized = [self._serialize_param(p) for p in params]
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                EDGE_URL,
                headers=self._headers,
                json={"query": query, "params": serialized},
            )
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise RuntimeError(f"DB error: {data['error']}")
            rows = [_Row(row) for row in data.get("rows", [])]
            command = data.get("command", "")
            return rows, command

    async def fetch(self, query: str, *args: Any) -> list[_Row]:
        rows, _ = await self._call(query, list(args))
        return rows

    async def fetchrow(self, query: str, *args: Any) -> _Row | None:
        rows, _ = await self._call(query, list(args))
        return rows[0] if rows else None

    async def fetchval(self, query: str, *args: Any) -> Any:
        row = await self.fetchrow(query, *args)
        if row is None:
            return None
        return next(iter(row.values()))

    async def execute(self, query: str, *args: Any) -> str:
        """Returns asyncpg-style command tag, e.g. 'DELETE 3' or 'INSERT 0 1'."""
        _, command = await self._call(query, list(args))
        return command

    async def close(self) -> None:
        pass  # stateless HTTP — nothing to close


async def connect(*_args: Any, **_kwargs: Any) -> AsyncConnection:
    """Drop-in replacement for asyncpg.connect().

    Reads SUPABASE_ANON_KEY from environment — the public-safe key whose
    surface area is constrained by the db-proxy-public allowlist.
    """
    key = os.environ.get("SUPABASE_ANON_KEY", "")
    if not key:
        raise RuntimeError("SUPABASE_ANON_KEY not set in environment")
    return AsyncConnection(key)
