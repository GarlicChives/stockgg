"""Industry clustering — 熱門題材.

2026-05-18 加 `detect_focus_clusters`(種子驅動的「焦點 tab」cluster detection)。
跟 `detect_industry_clusters`(TV 累加,普適)用途不同;hl_sub 走前者,pan_sub
走後者。

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
from dataclasses import dataclass, field
from pathlib import Path

DICT_FILE = Path(__file__).resolve().parents[2] / "data" / "theme_dictionary.json"
MIN_VOLUME = 2  # minimum top-N members for a cluster to qualify (保留舊門檻)
HIGHLIGHT_MAIN = "近一年焦點"  # 焦點 cluster detection 限定 main

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
    level: str             # "main" | "sub" | "hl_sub" | "pan_sub"
    name: str              # main name (level=main) or sub name (level=sub)
    main: str              # parent main industry (==name for level=main)
    focal: list[FocalStock]
    watch: list[WatchStock]
    trading_value: float   # sum of focal stocks' trading_value (TWD)
    # 對應原始 theme_history 表的 (main, sub) keys。
    # - 未合併 sub cluster:單一 [(main, name)]
    # - 合併後 sub cluster:多個 [(m1, s1), (m2, s2), ...] 對應每個成員
    # - main cluster:空 list(主產業層級不查 theme_history)
    members: list[tuple[str, str]] = field(default_factory=list)
    # 前哨標的(2026-05-18 焦點 cluster 用):題材內 universe 內、下跌的標的。
    # 只有 `detect_focus_clusters`(種子驅動)輸出會填這個欄。
    # 既有 `detect_industry_clusters` 輸出 sentinel=[](所有 pan_sub cluster)。
    sentinel: list[FocalStock] = field(default_factory=list)


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
            members=[(main, sub)],
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
        merged_members: list[tuple[str, str]] = []
        for m in members:
            merged_members.extend(m.members)
        # sentinel 合併:union 各 member 的 sentinel(focal set 同表示題材重複,
        # 但 sentinel 是「題材內下跌標的」可能各 sub 不同),依 ticker 去重保留
        # 第一次出現順序
        seen_snt: set[str] = set()
        merged_sentinel: list[FocalStock] = []
        for m in members:
            for s in m.sentinel:
                if s.ticker in seen_snt:
                    continue
                seen_snt.add(s.ticker)
                merged_sentinel.append(s)
        merged.append(IndustryCluster(
            cluster_id=f"merged::{joined_id}",
            level=first.level,
            name=joined_name,
            main=joined_main,
            focal=first.focal,
            watch=[],  # 合併後 watch 不再有意義(可能各 sub 不同),且公開頁面已不顯示
            trading_value=first.trading_value,
            members=merged_members,
            sentinel=merged_sentinel,
        ))
    return merged


# ── 焦點 cluster detection(2026-05-18 加,種子驅動) ──────────────────────────
#
# 跟 detect_industry_clusters(普適 TV 累加)不同:此函式只處理 main='近一年焦點',
# 從 ingest 端 pre-compute 的 hot_seed flag(成交值前 15 上漲 或 前 50 漲停)
# 反推所屬 sub,題材內 universe(top-50 ∪ special ∪ volume_universe)有上漲標的
# 即族群成立,上漲入 focal、下跌入 sentinel。
#
# Universe filter「成交金額 ≥ 大盤總TV / 1000」由 ingest 端 store_tw_rankings
# 寫入時就 filter 好(is_volume_universe 只標符合的);stockgg 端讀 trading_rankings
# 取 row 即自然符合,不用再算動態門檻。
#
# 對應 ingest commit: bd85f1d


def detect_focus_clusters(
    tw_universe: dict[str, dict],
) -> list[IndustryCluster]:
    """從 hot-seed flag 反推「近一年焦點」sub clusters。

    Args:
        tw_universe: ticker -> {name, change_pct, trading_value, rank,
                                limit_up, is_hot_seed, is_limit_hot_seed,
                                is_volume_universe, is_special}
                     必須涵蓋 top-50 ∪ special ∪ volume_universe(stockgg 端
                     從 Q6 ∪ Q14 ∪ Q15 合併;ETF 已 filter)。

    Returns:
        list[IndustryCluster] (level='hl_sub')。dedup 過(同 focal set 合併),
        按 trading_value desc 排序。空列表代表今天沒有任何符合的 seed。
    """
    data = _load_dict()
    all_stocks = data.get("stocks", {})
    if not all_stocks:
        return []

    seeds = {
        t for t, info in tw_universe.items()
        if (info.get("is_hot_seed") or info.get("is_limit_hot_seed"))
        and not _is_etf(t, info.get("name", ""))
    }
    if not seeds:
        return []

    # 1) seeds → 候選 sub(只看 main='近一年焦點' 的 entries)
    candidate_subs: set[str] = set()
    for seed in seeds:
        info = all_stocks.get(seed)
        if not info:
            continue
        for entry in info.get("industries", []):
            if entry.get("disabled"):
                continue
            if (entry.get("main") or "").strip() != HIGHLIGHT_MAIN:
                continue
            for sub in entry.get("subs", []) or []:
                sub = (sub or "").strip()
                if sub:
                    candidate_subs.add(sub)

    if not candidate_subs:
        return []

    # 2) 對每候選 sub,從 theme_dictionary 取題材內全 ticker;與 tw_universe 取交集
    #    上漲 → focal,下跌 → sentinel。族群性條件:至少 1 檔上漲。
    clusters: list[IndustryCluster] = []
    for sub in candidate_subs:
        members: list[str] = []
        for ticker, info in all_stocks.items():
            if _is_etf(ticker, info.get("name", "")):
                continue
            for entry in info.get("industries", []):
                if entry.get("disabled"):
                    continue
                if (entry.get("main") or "").strip() != HIGHLIGHT_MAIN:
                    continue
                if sub in (entry.get("subs", []) or []):
                    members.append(ticker)
                    break  # 單檔在同 (main, sub) 只算一次

        focal_stocks: list[FocalStock] = []
        sentinel_stocks: list[FocalStock] = []
        for tk in members:
            row = tw_universe.get(tk)
            if not row:
                continue
            chg = row.get("change_pct")
            if chg is None:
                continue  # 缺漲跌資料 ignore(極少見;avoid mis-bucket)
            stk = _focal_from(tk, row)
            if chg > 0:
                focal_stocks.append(stk)
            elif chg < 0:
                sentinel_stocks.append(stk)
            # chg == 0 平盤:不入 focal 也不入 sentinel(語意上「題材內無動向」)

        if not focal_stocks:
            continue  # 族群性不成立

        focal_stocks.sort(key=lambda s: -s.trading_value)
        sentinel_stocks.sort(key=lambda s: -s.trading_value)
        total_tv = sum(s.trading_value for s in focal_stocks)

        clusters.append(IndustryCluster(
            cluster_id=f"hl::{sub}",
            level="hl_sub",
            name=sub,
            main=HIGHLIGHT_MAIN,
            focal=focal_stocks,
            watch=[],
            trading_value=total_tv,
            members=[(HIGHLIGHT_MAIN, sub)],
            sentinel=sentinel_stocks,
        ))

    clusters.sort(key=lambda c: -c.trading_value)
    clusters = _dedup_by_name(clusters)
    return _merge_identical_focal(clusters)
