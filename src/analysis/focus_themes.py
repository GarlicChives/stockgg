"""Industry clustering — 熱門題材.

2026-05 改版:從「主題 keyword 字典(themes 陣列)」改為「statementdog
階層產業字典(stocks 物件,ticker-centric)」。台股 only(美股題材路徑
2026-05 移除,因為新 source 只覆蓋 TWSE/TPEX)。

演算法
------
對台股 top-N 成交值股票,每檔股票讀 `stocks[ticker].industries[]`,
累加 trading_value 到:
  - `main_tv[main]`            每個主產業桶
  - `sub_tv[(main, sub)]`      每個 (主, 子) 桶
同 ticker 在多個 main / sub 都會被累加(各桶各算一次,但同桶內每檔
ticker 只 +1 次 TV 不會重複)。

entries 標 `disabled=True` 略過(人工標「這檔 × 這個 main 別在公開
站顯示」)。`locked=True` 不過濾(只凍結 admin 編輯,不影響顯示)。

ETF(代號 00 開頭 / 名稱含 ETF)在累加迴圈直接跳過,與 ingest 端
build script 一致。

對應 ingest commit: GarlicChives/StockGG-ingest@1660b8c
(整套舊機制 build_theme_dictionary / src/theme / theme_classifier
2026-05 清除)
"""
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

DICT_FILE = Path(__file__).resolve().parents[2] / "data" / "theme_dictionary.json"
MIN_VOLUME = 2  # minimum top-N members for a cluster to qualify (保留舊門檻)

_ETF_TW_RE = re.compile(r"^00\d")


def _is_etf(ticker: str, name: str = "") -> bool:
    if _ETF_TW_RE.match(ticker):
        return True
    return "ETF" in (name or "").upper()


@dataclass
class FocalStock:
    ticker: str
    name: str
    change_pct: float | None
    trading_value: float
    rank: int
    limit_up: bool


@dataclass
class WatchStock:
    code: str
    name: str
    change_pct: float | None = None


@dataclass
class IndustryCluster:
    cluster_id: str        # "main::PCB" or "sub::PCB|硬板..."
    level: str             # "main" | "sub"
    name: str              # main name (level=main) or sub name (level=sub)
    main: str              # parent main industry (==name for level=main)
    focal: list[FocalStock]
    watch: list[WatchStock]
    trading_value: float   # sum of focal stocks' trading_value (TWD)


def _load_dict() -> dict:
    if not DICT_FILE.exists():
        return {"stocks": {}}
    with DICT_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def _focal_from(ticker: str, info: dict) -> FocalStock:
    return FocalStock(
        ticker=ticker,
        name=info.get("name", ticker),
        change_pct=info.get("change_pct"),
        trading_value=float(info.get("trading_value") or 0),
        rank=int(info.get("rank") or 99),
        limit_up=bool(info.get("limit_up", False)),
    )


def detect_industry_clusters(
    tw_top_volume: dict[str, dict],
) -> tuple[list[IndustryCluster], list[IndustryCluster]]:
    """Aggregate TW top-N trading value into main + (main, sub) buckets.

    Args:
        tw_top_volume: ticker -> {name, change_pct, trading_value, rank, limit_up}
                  caller should have already filtered ETFs but we re-guard.

    Returns:
        (main_clusters, sub_clusters), each sorted by trading_value desc and
        gated by MIN_VOLUME (≥ 2 top-N members per cluster).
    """
    data = _load_dict()
    all_stocks = data.get("stocks", {})
    if not all_stocks:
        return [], []

    # ETF guard on top-N (defensive — caller usually filters too)
    top30 = {t: info for t, info in tw_top_volume.items() if not _is_etf(t, info.get("name", ""))}
    top30_set = set(top30)

    # Aggregation buckets
    main_focal: dict[str, list[str]] = defaultdict(list)
    sub_focal: dict[tuple[str, str], list[str]] = defaultdict(list)
    main_tv: dict[str, float] = defaultdict(float)
    sub_tv: dict[tuple[str, str], float] = defaultdict(float)

    for ticker, top_info in top30.items():
        dict_info = all_stocks.get(ticker)
        if not dict_info:
            continue
        tv = float(top_info.get("trading_value") or 0)
        for entry in dict_info.get("industries", []):
            if entry.get("disabled"):
                continue
            main = (entry.get("main") or "").strip()
            if not main:
                continue
            # Same ticker may have multiple entries with same main — guard.
            if ticker not in main_focal[main]:
                main_focal[main].append(ticker)
                main_tv[main] += tv
            for sub in entry.get("subs", []) or []:
                sub = (sub or "").strip()
                if not sub:
                    continue
                key = (main, sub)
                if ticker not in sub_focal[key]:
                    sub_focal[key].append(ticker)
                    sub_tv[key] += tv

    # Watch stocks: dictionary stocks NOT in top-N for each touched main / sub.
    main_watch: dict[str, list[WatchStock]] = defaultdict(list)
    sub_watch: dict[tuple[str, str], list[WatchStock]] = defaultdict(list)

    for ticker, info in all_stocks.items():
        if ticker in top30_set or _is_etf(ticker, info.get("name", "")):
            continue
        seen_main: set[str] = set()
        seen_sub: set[tuple[str, str]] = set()
        for entry in info.get("industries", []):
            if entry.get("disabled"):
                continue
            main = (entry.get("main") or "").strip()
            if not main:
                continue
            if main_focal.get(main) and main not in seen_main:
                main_watch[main].append(WatchStock(ticker, info.get("name", ticker)))
                seen_main.add(main)
            for sub in entry.get("subs", []) or []:
                sub = (sub or "").strip()
                if not sub:
                    continue
                key = (main, sub)
                if sub_focal.get(key) and key not in seen_sub:
                    sub_watch[key].append(WatchStock(ticker, info.get("name", ticker)))
                    seen_sub.add(key)

    # Assemble clusters with MIN_VOLUME gate
    main_clusters: list[IndustryCluster] = []
    for name, tickers in main_focal.items():
        if len(tickers) < MIN_VOLUME:
            continue
        focal = [_focal_from(t, top30[t]) for t in tickers]
        focal.sort(key=lambda s: -s.trading_value)
        watch = main_watch[name][:15]
        main_clusters.append(IndustryCluster(
            cluster_id=f"main::{name}",
            level="main",
            name=name,
            main=name,
            focal=focal,
            watch=watch,
            trading_value=main_tv[name],
        ))
    main_clusters.sort(key=lambda c: -c.trading_value)

    sub_clusters: list[IndustryCluster] = []
    for (main, sub), tickers in sub_focal.items():
        if len(tickers) < MIN_VOLUME:
            continue
        focal = [_focal_from(t, top30[t]) for t in tickers]
        focal.sort(key=lambda s: -s.trading_value)
        watch = sub_watch[(main, sub)][:12]
        sub_clusters.append(IndustryCluster(
            cluster_id=f"sub::{main}|{sub}",
            level="sub",
            name=sub,
            main=main,
            focal=focal,
            watch=watch,
            trading_value=sub_tv[(main, sub)],
        ))
    sub_clusters.sort(key=lambda c: -c.trading_value)

    # 同名子產業可能掛在多個主產業下(例如「記憶體」同時屬於 電腦及週邊
    # 設備 與 通信網路)。頁面只呈現 TV 較大的那個,小的丟掉。
    sub_clusters = _dedup_by_name(sub_clusters)
    return main_clusters, _merge_identical_focal(sub_clusters)


def _dedup_by_name(clusters: list[IndustryCluster]) -> list[IndustryCluster]:
    """同名 cluster 只保留 TV 最大的(輸入已按 TV desc 排序,first wins)。"""
    seen: set[str] = set()
    result: list[IndustryCluster] = []
    for c in clusters:
        if c.name in seen:
            continue
        seen.add(c.name)
        result.append(c)
    return result


def _merge_identical_focal(clusters: list[IndustryCluster]) -> list[IndustryCluster]:
    """合併 focal ticker set 完全相同的 cluster(用於子產業聚合)。

    範例:
      電阻器: 國巨、台達電
      電容器: 國巨、台達電
      電感器: 國巨、台達電
    → 電阻器 & 電容器 & 電感器: 國巨、台達電
    """
    from collections import OrderedDict
    groups: "OrderedDict[frozenset[str], list[IndustryCluster]]" = OrderedDict()
    for c in clusters:
        key = frozenset(s.ticker for s in c.focal)
        groups.setdefault(key, []).append(c)

    merged: list[IndustryCluster] = []
    for members in groups.values():
        if len(members) == 1:
            merged.append(members[0])
            continue
        first = members[0]
        # 名稱以 " & " 串接,維持原順序;parent main 也聚合(去重後串接)
        joined_name = " & ".join(c.name for c in members)
        unique_mains = list(dict.fromkeys(c.main for c in members))
        joined_main = " & ".join(unique_mains)
        joined_id = "|".join(c.cluster_id for c in members)
        merged.append(IndustryCluster(
            cluster_id=f"merged::{joined_id}",
            level=first.level,
            name=joined_name,
            main=joined_main,
            focal=first.focal,
            watch=[],  # 合併後 watch 不再有意義(可能各 sub 不同),且公開頁面已不顯示
            trading_value=first.trading_value,
        ))
    return merged
