"""跨 CI run 的歷史大表增量快取(Supabase Disk IO 治本 B)。

`ticker_close_history` / `ticker_net_inst_history` / `theme_history` 三張大表,
「昨天以前」的列永不改變(唯一例外:除權息 / 減資的回溯改寫,由「每週全量重建」
安全網吸收)。此模組讓 `generate_html` 每次 deploy 只向 DB 撈「比快取最新日更新的
列」,把 ~99% 的重複讀砍掉 —— 這是吃光 Supabase Disk IO budget、db-proxy 回 500 的元兇。

**職責邊界**:本模組純資料操作(load / 決定 fetch 範圍 / merge / evict),不碰 DB。
DB 讀取仍留在 `generate_html`(需 `conn` + 分批 helper)。快取檔在 CI 由
`actions/cache` 跨 run 持久化(`.cache/` 目錄),本機亦落地同目錄。

**快取檔格式**(`.cache/<name>.json`):
    {
      "rows": { <entity>: [ {<date_key>: "YYYY-MM-DD", ...其他欄位}, ... ], ... },
      "meta": { "last_full_rebuild": "YYYY-MM-DD" }
    }
`entity` = ticker(kline / net_inst)或 "main||sub" 複合鍵(theme_history)。
同一 entity 內 `date_key` 唯一(對齊三表的 PK),故 merge 以 `date_key` 去重即可。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

# 與 db-proxy-public allowlist Q13/Q17/Q11 的 retention 對齊。
WINDOW_DAYS = 400
# 增量重疊窗:每次多撈這麼多天,吸收「晚到 / 隔日更正」的列。
OVERLAP_DAYS = 5
# 安全網:距上次全量重建 ≥ 此天數 → 強制全量,把「除權息 / 減資回溯改寫」
# 與快取不同步的最長延遲鎖在 < 1 週(split 事件罕見,代價可接受)。
FULL_REBUILD_INTERVAL_DAYS = 7

# `.cache/` 相對 repo root(CI 與本機皆從 repo root 跑 generate_html)。
CACHE_DIR = Path(".cache")


def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.json"


def load_cache(name: str) -> dict:
    """讀 `.cache/<name>.json`。回傳 {"rows": {...}, "meta": {...}}。

    缺檔 / 壞檔 / 格式不符 → 一律回空殼(交由 `need_full_rebuild` 判為全量重建),
    確保「缺快取絕不產出殘缺輸出」。
    """
    p = _cache_path(name)
    if not p.exists():
        return {"rows": {}, "meta": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"rows": {}, "meta": {}}
    if not isinstance(data, dict) or not isinstance(data.get("rows"), dict):
        return {"rows": {}, "meta": {}}
    if not isinstance(data.get("meta"), dict):
        data["meta"] = {}
    return data


def save_cache(name: str, rows: dict[str, list], meta: dict) -> None:
    """把合併後的 rows + meta 寫回 `.cache/<name>.json`(compact JSON)。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(name).write_text(
        json.dumps({"rows": rows, "meta": meta},
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def need_full_rebuild(cache: dict, today: date) -> bool:
    """判定是否忽略快取、走全量重建。

    觸發條件(任一):
      * 快取空(首跑 / cache miss)
      * 缺 last_full_rebuild 或格式壞
      * 距上次全量重建 ≥ FULL_REBUILD_INTERVAL_DAYS(除權息回溯改寫安全網)
    """
    if not cache.get("rows"):
        return True
    lfr = (cache.get("meta") or {}).get("last_full_rebuild")
    if not lfr:
        return True
    try:
        last = date.fromisoformat(lfr)
    except (TypeError, ValueError):
        return True
    return (today - last).days >= FULL_REBUILD_INTERVAL_DAYS


def _entity_max_date(rows: list, date_key: str) -> str | None:
    return max((r.get(date_key) for r in rows if r.get(date_key)), default=None)


def _global_max_date(cache_rows: dict[str, list], date_key: str) -> str | None:
    mx = None
    for lst in cache_rows.values():
        m = _entity_max_date(lst, date_key)
        if m and (mx is None or m > mx):
            mx = m
    return mx


def plan_fetch(cache_rows: dict[str, list], entities: list[str], *,
               date_key: str, today: date) -> tuple[list[str], list[str], int, int]:
    """決定增量 fetch 的兩桶 entity + 兩個「days-back」下限。

    回傳 `(incr_entities, full_entities, incr_days_back, full_days_back)`:
      * `full_entities`  = 不在快取、或自身最新日落後全域 incr_cutoff 的 entity。
        這些若只走增量會漏掉「快取空窗 ~ 今天」中間那段 → 必須重撈完整視窗。
        cutoff = `current_date - full_days_back`(= WINDOW_DAYS)。
      * `incr_entities`  = 快取新鮮的 entity → 只撈全域最新日往前 OVERLAP_DAYS。
        cutoff = `current_date - incr_days_back`。

    days-back 以「今天」與快取全域最新日的差 + OVERLAP 計得,對應 SQL
    `rank_date >= (current_date - $N::int)`(沿用 allowlist Q37 的參數化寫法)。
    """
    full_days_back = WINDOW_DAYS
    gmax = _global_max_date(cache_rows, date_key)
    if gmax is None:
        # 快取實質為空 → 全部 entity 走全量。
        return [], list(entities), full_days_back, full_days_back

    gmax_date = date.fromisoformat(gmax)
    incr_days_back = (today - gmax_date).days + OVERLAP_DAYS
    # 對「離群」情形(理論上快取日 > 今天)夾住下限,至少涵蓋 overlap。
    if incr_days_back < OVERLAP_DAYS:
        incr_days_back = OVERLAP_DAYS
    incr_cutoff = (gmax_date - timedelta(days=OVERLAP_DAYS)).isoformat()

    incr, full = [], []
    for e in entities:
        lst = cache_rows.get(e)
        emax = _entity_max_date(lst, date_key) if lst else None
        if emax is None or emax < incr_cutoff:
            full.append(e)          # 新 entity 或自身落後 → 補完整視窗
        else:
            incr.append(e)
    return incr, full, incr_days_back, full_days_back


def merge_entity(cached: list[dict], fresh: list[dict], *, date_key: str,
                 window_cutoff: str) -> list[dict]:
    """以 `date_key` 去重合併單一 entity(fresh 覆蓋同日 cached),
    裁掉 < window_cutoff 的舊列,回傳日期升冪 list。
    """
    by_date: dict[str, dict] = {}
    for r in cached:
        d = r.get(date_key)
        if d and d >= window_cutoff:
            by_date[d] = r
    for r in fresh:                 # fresh 後寫 → 同日覆蓋 cached(吸收更正)
        d = r.get(date_key)
        if d and d >= window_cutoff:
            by_date[d] = r
    return [by_date[d] for d in sorted(by_date)]


def window_cutoff(today: date) -> str:
    """merge 時裁掉更舊列的視窗下限(YYYY-MM-DD)。"""
    return (today - timedelta(days=WINDOW_DAYS)).isoformat()


async def incremental_load(name: str, entities: list[str], *,
                           date_key: str, today: date, fetch_rows):
    """歷史大表的增量讀取骨架(load → 決定範圍 → fetch → merge → 寫回)。

    Args:
        name:      快取檔名(`.cache/<name>.json`)。
        entities:  本次需要的 entity 清單(ticker 或 "main||sub" 鍵)。
        date_key:  每列的日期欄鍵(kline/net_inst="d";theme_history="rank_date")。
        today:     基準日曆日(UTC)。
        fetch_rows: async(bucket_entities: list[str], days_back: int) ->
                    dict[entity, list[cache_row]]。呼叫端負責把 DB row 轉成
                    可 JSON 序列化的 cache row(含 date_key)並按 entity 分組。

    Returns:
        (merged_rows, stats):merged_rows = 合併後全 entity 的 {entity:[row,...]}
        (呼叫端據此重建下游結構,只取自己要的 entities);stats 供日誌。

    DB 失敗:fetch_rows 內拋例外會直接往上傳(在 save_cache 之前)→ 快取不被
    半寫,呼叫端沿用既有 try/except 決定 fatal 與否。
    """
    cache = load_cache(name)
    cache_rows: dict[str, list] = cache["rows"]
    full_rebuild = need_full_rebuild(cache, today)

    if full_rebuild:
        cache_rows = {}
        incr, full = [], list(entities)
        incr_back = full_back = WINDOW_DAYS
    else:
        incr, full, incr_back, full_back = plan_fetch(
            cache_rows, entities, date_key=date_key, today=today)

    fresh: dict[str, list] = {}
    rows_read = 0
    for bucket, days_back in ((full, full_back), (incr, incr_back)):
        if not bucket:
            continue
        grouped = await fetch_rows(bucket, days_back)
        for e, lst in grouped.items():
            fresh.setdefault(e, []).extend(lst)
            rows_read += len(lst)

    wc = window_cutoff(today)
    for e, lst in fresh.items():
        cache_rows[e] = merge_entity(cache_rows.get(e, []), lst,
                                     date_key=date_key, window_cutoff=wc)
    # 全域 window evict:不再被 fetch 的舊 entity 也裁到視窗內,防快取無限膨脹。
    for e in list(cache_rows.keys()):
        pruned = [r for r in cache_rows[e]
                  if r.get(date_key) and r.get(date_key) >= wc]
        if pruned:
            cache_rows[e] = pruned
        else:
            del cache_rows[e]

    meta = dict(cache.get("meta") or {})
    if full_rebuild:
        meta["last_full_rebuild"] = today.isoformat()
    save_cache(name, cache_rows, meta)

    stats = {
        "mode": "full" if full_rebuild else "incremental",
        "rows_read": rows_read,
        "full_entities": len(full),
        "incr_entities": len(incr),
    }
    return cache_rows, stats
