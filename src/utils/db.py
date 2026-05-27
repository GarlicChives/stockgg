"""Database adapter — asyncpg-compatible interface over Supabase Edge Function.

This is the **public-renderer** variant: talks to db-proxy-public, which
hard-allowlists a fixed set of SELECT templates needed for HTML render and
rejects anything else with HTTP 403. Even if the bearer key leaks, the only
columns reachable are the public-safe ones defined in the ALLOWED set of
supabase/functions/db-proxy-public/index.ts.

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
import asyncio
import os
import re
from datetime import date, datetime, timezone
from typing import Any

import httpx

EDGE_URL = "https://mnseyguxiiditaybpfup.supabase.co/functions/v1/db-proxy-public"

# Supabase Edge Function 偶發 5xx(isolate cold start / CPU 上限 / 連線池),
# 這些 retry 一次幾乎都會通 → 在 client 層攔下,免每個 caller 自己包 try/except。
# 5xx + httpx.RequestError (timeout / DNS / connection reset) 都會觸發 retry。
_RETRYABLE_HTTP_CODES = {500, 502, 503, 504, 522, 524, 546, 548}
_RETRY_BACKOFF_S = (0.5, 1.5, 3.0)  # 3 次 retry,total 0.5 + 1.5 + 3 = 5s 上限

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
        last_exc: Exception | None = None
        # 第 0 輪 = 首次嘗試;1/2/3 輪是 retry。
        for attempt in range(len(_RETRY_BACKOFF_S) + 1):
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    r = await client.post(
                        EDGE_URL,
                        headers=self._headers,
                        json={"query": query, "params": serialized},
                    )
                    # 5xx → 進 retry(若還有額度);403/400/4xx 是 caller 真錯,
                    # 不 retry,直接讓上層看見。
                    if r.status_code in _RETRYABLE_HTTP_CODES:
                        last_exc = httpx.HTTPStatusError(
                            f"Edge {r.status_code} (retryable)", request=r.request, response=r,
                        )
                        if attempt < len(_RETRY_BACKOFF_S):
                            await asyncio.sleep(_RETRY_BACKOFF_S[attempt])
                            continue
                        r.raise_for_status()  # exhausted retries
                    r.raise_for_status()
                    data = r.json()
                    if "error" in data:
                        raise RuntimeError(f"DB error: {data['error']}")
                    rows = [_Row(row) for row in data.get("rows", [])]
                    command = data.get("command", "")
                    return rows, command
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                # 連線層問題也 retry —— DNS / TCP reset / TLS handshake fail。
                last_exc = exc
                if attempt < len(_RETRY_BACKOFF_S):
                    await asyncio.sleep(_RETRY_BACKOFF_S[attempt])
                    continue
                raise
        # 不會到這裡;保底。
        if last_exc:
            raise last_exc
        raise RuntimeError("_call: unreachable")

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
