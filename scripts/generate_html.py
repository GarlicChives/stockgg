#!/usr/bin/env python3
"""Generate docs/index.html from latest DB data for GitHub Pages.

Three-tab layout:
  市場行情 — Full AI report + US/TW rankings
  焦點股   — TW/US sub-tabs, article-matched stocks + popup modal
  股市筆記  — Cross-source topic intersection + podcast notes (collapsible)

Fixed elements:
  - Direction badge (fixed top-right: short/mid term + report date)
"""
import asyncio
import collections
import hashlib
import html as html_lib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import db
from dotenv import load_dotenv

load_dotenv()

from src.analysis.focus_themes import (
    detect_industry_clusters,
    detect_focus_clusters,
    hot_subs_from_seeds,
    _sub_prefix as _focus_sub_prefix,
    FOCUS_SENTINEL_THRESHOLD,
    IndustryCluster,
)
from src.utils.config import RANKINGS_TOP_N

OUT_FILE = Path(__file__).resolve().parents[1] / "docs" / "index.html"
_THEME_DICT_PATH = Path(__file__).resolve().parents[1] / "data" / "theme_dictionary.json"
HIGHLIGHT_MAIN = "近一年焦點"  # main industry 名稱(ingest 端 commit 254e47e 起)

_ETF_TW_RE = re.compile(r'^00\d')


def _load_highlight_subs() -> dict[str, list[tuple[str, str]]]:
    """讀 theme_dictionary.json,回 main='近一年焦點' 的 {sub: [(ticker, name), ...]}。
    sub 名稱通常為「前綴·後綴」形式(例「AI 伺服器/資料中心·散熱」),
    前綴用於前端分群展示。disabled 條目跳過。
    """
    try:
        d = json.loads(_THEME_DICT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    sub_to_tickers: dict[str, list[tuple[str, str]]] = {}
    for t, info in (d.get("stocks") or {}).items():
        if not isinstance(info, dict):
            continue
        for ind in info.get("industries", []):
            if ind.get("main") != HIGHLIGHT_MAIN or ind.get("disabled"):
                continue
            for s in ind.get("subs", []):
                sub_to_tickers.setdefault(s, []).append((t, info.get("name") or t))
    return sub_to_tickers

def _is_etf(ticker: str, name: str = "") -> bool:
    if _ETF_TW_RE.match(ticker):
        return True
    return "ETF" in (name or "").upper()

SOURCE_NAMES = {
    "macromicro":             "財經M平方",
    "vocus":                  "韭菜王",
    "statementdog":           "財報狗",
    "investanchors":          "投資錨點",
    "pressplay":              "財經捕手",
    "podcast_gooaye":         "股癌 Gooaye",
    "podcast_macromicro":     "財經M平方",
    "podcast_chives_grad":    "韭菜畢業班",
    "podcast_stock_barrel":   "股海飯桶",
    "podcast_zhaohua":        "兆華與股惑仔",
    "podcast_statementdog":   "財報狗 podcast",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt_pct(v) -> tuple[str, str]:
    """格式化漲跌% (亞洲慣例:紅=漲 綠=跌 白=平盤)。返回 (顯示字串, CSS class)。"""
    if v is None:
        return "—", "neutral"
    if v > 0:
        return f"+{v:.2f}%", "up"
    if v < 0:
        return f"{v:.2f}%", "down"
    return "0.00%", "flat"


def strip_preamble(text: str) -> str:
    m = re.search(r'^(##\s)', text, re.MULTILINE)
    return text[m.start():] if m else text


def parse_directions(text: str) -> dict:
    result = {"short": "中立", "mid": "中立"}
    if not text:
        return result
    m = re.search(r'短期[（(][^)）]*[）)][：:]\s*(偏多|中立|偏空)', text)
    if m:
        result["short"] = m.group(1)
    m = re.search(r'中期[（(][^)）]*[）)][：:]\s*(偏多|中立|偏空)', text)
    if m:
        result["mid"] = m.group(1)
    return result


def md_to_html(text: str) -> str:
    for section in ("動能股彙整", "今日焦點股分析", "明日觀察重點"):
        text = re.sub(rf'## {section}.*?(?=\n## |\Z)', '', text, flags=re.DOTALL)
    text = strip_preamble(text)
    text = html_lib.escape(text)
    text = re.sub(r'^### (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    def wrap_list(m):
        items = re.sub(r'^[\*\-]\s+(.+)$', r'<li>\1</li>', m.group(0), flags=re.MULTILINE)
        return f'<ul>{items}</ul>'
    text = re.sub(r'(?m)(^[\*\-] .+\n?)+', wrap_list, text)
    blocks = re.split(r'\n{2,}', text)
    result = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if re.match(r'^<(h[1-3]|ul)', block):
            result.append(block)
        else:
            result.append(f'<p>{block.replace(chr(10), "<br>")}</p>')
    return '\n'.join(result)


def md_to_html_simple(text: str) -> str:
    """Catalyst preview 用簡版 markdown → HTML。
    與 md_to_html 不同:不做 strip_preamble、不移除特定 section
    (那些針對日報設計的邏輯會誤殺 catalyst preview 的開頭段)。
    處理:### / ## heading、**bold**、* / - bullets、段落 wrap。
    """
    if not text:
        return ""
    text = html_lib.escape(text)
    text = re.sub(r'^### (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    def wrap_list(m):
        items = re.sub(r'^[\*\-]\s+(.+)$', r'<li>\1</li>', m.group(0), flags=re.MULTILINE)
        return f'<ul>{items}</ul>'
    text = re.sub(r'(?m)(^[\*\-] .+\n?)+', wrap_list, text)
    blocks = re.split(r'\n{2,}', text)
    result = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if re.match(r'^<(h[1-3]|ul)', block):
            result.append(block)
        else:
            result.append(f'<p>{block.replace(chr(10), "<br>")}</p>')
    return '\n'.join(result)


# ── Unified stock pill (全站統一顯示模組) ─────────────────────────────────────

def _flag_chips(info: dict) -> str:
    """共用 chip 渲染:依 extra flag 顯小 tag。pill / rankings table 都用。
    ingest 5a172be 起 trading_rankings.extra 寫入這些 flag。"""
    chips: list[str] = []
    if info.get("is_punish"):
        ptype = info.get("punish_type")
        if ptype == "strict":
            chips.append('<span class="sp-tag tag-strict" title="嚴格處置">嚴處</span>')
        else:
            chips.append('<span class="sp-tag tag-punish" title="處置股">處</span>')
    if info.get("limit_up"):
        chips.append('<span class="sp-tag tag-limit-up" title="漲停">漲</span>')
    if info.get("is_limit_down"):
        chips.append('<span class="sp-tag tag-limit-down" title="跌停">跌</span>')
    return "".join(chips)


def _disp_ticker(ticker: str) -> str:
    """顯示用 ticker:台股拿掉 .TW / .TWO 後綴。

    市場別已由 mkt-badge / board badge 標示,代號旁再掛「.TW」純屬冗餘。
    僅用於畫面顯示文字 —— ticker 原值仍須保留供 showArtModal / DB 查詢 /
    history.json series key 比對(那些都吃帶後綴的完整 symbol)。
    """
    up = (ticker or "").upper()
    for suf in (".TWO", ".TW"):
        if up.endswith(suf):
            return ticker[: -len(suf)]
    return ticker


def _stk_pill(ticker: str, stocks_info: dict, clickable: bool = True, extra_attrs: str = "") -> str:
    """Unified stock chip: ticker + market badge + name + "price(chg%)" 報價。

    報價 span 用 fmt_pct 的 css class (up=紅 down=綠 flat=白 neutral=灰),
    全站股票標的(報告段末 pill / 題材卡 / 跨來源議題 / rankings 表) 共用。
    """
    info = stocks_info.get(ticker, {})
    _core = ticker.split(".")[0]
    market = info.get("market") or ("TW" if _core.isdigit() else "US")
    disp_ticker = _disp_ticker(ticker)
    name = info.get("name", "")
    chg = info.get("change_pct")
    close = info.get("close_price")
    mkt_cls = "mkt-tw" if market == "TW" else "mkt-us"
    # 市場別 badge:台股全站皆 TW,標一次「TW」純屬冗餘 noise → 只對美股(US)顯示
    mkt_badge_html = "" if market == "TW" else f'<span class="mkt-badge {mkt_cls}">{market}</span>'
    pct_str, pct_cls = fmt_pct(chg)
    if close is not None:
        price_str = f"{close:.2f}"
        # chg=None(如 TPEX 除權息股 ingest 存 NULL)→ pct_str 已是「—」,照樣顯
        # 「價(—)」而非省略,讓「無漲跌資料」明確可見、不被誤認平盤(2026-06-08)
        quote = f"{price_str}({pct_str})"
    else:
        quote = pct_str
    name_span = f'<span class="sp-name">{html_lib.escape(name[:8])}</span>' if name else ""
    click = f" onclick='showArtModal({json.dumps(ticker)},{json.dumps(name[:12])},event)'" if clickable else ""
    extra = f" {extra_attrs}" if extra_attrs else ""

    # 處置 / 漲跌停 flag tag(ingest 5a172be 起 extra 帶進來):
    #   punish_type='strict'  → 「嚴處」紅底
    #   punish_type='normal'  → 「處」橘底
    #   is_limit_up           → 「漲」紅底
    #   is_limit_down         → 「跌」綠底
    # 全部 1-2 字小 chip,避免擠 pill。
    tags_html = _flag_chips(info)

    return (
        f'<div class="stk-pill"{click}{extra}>'
        f'<span class="sp-ticker">{html_lib.escape(disp_ticker)}</span>'
        f'{mkt_badge_html}'
        f'{name_span}'
        f'<span class="sp-quote {pct_cls}">{quote}</span>'
        f'{tags_html}'
        f'</div>'
    )


def _pillify_in_html(html: str, stocks_info: dict) -> str:
    """Append a stock-pill row to the end of each <p>/<li> block in the report.

    Body text is left untouched — Gemini's original wording (e.g. "台積電(2330)")
    stays as plain text, with no inline code/change% styling. Instead, every
    ticker or known Chinese stock name mentioned inside a block is collected,
    de-duplicated by resolved ticker, and rendered as a single pill row at the
    end of that block. Only tickers present in stocks_info produce a pill —
    unknown tokens (VIX, AI, foreign names without ranking data) are silently
    ignored, which gives us a free false-positive filter.
    """
    if not html or not stocks_info:
        return html

    name_to_ticker: dict[str, str] = {}
    for tk, info in stocks_info.items():
        nm = (info.get("name") or "").strip()
        if nm and len(nm) >= 2:
            name_to_ticker[nm] = tk

    names_sorted = sorted(name_to_ticker.keys(), key=len, reverse=True)
    name_alt = "|".join(re.escape(n) for n in names_sorted)
    if name_alt:
        token_re = re.compile(rf"({name_alt})|\b(\d{{4}}|[A-Z]{{2,5}})\b")
    else:
        token_re = re.compile(r"\b(\d{4}|[A-Z]{2,5})\b")

    def _collect_tickers(text: str, acc: list[str]) -> None:
        for m in token_re.finditer(text):
            matched = m.group(0)
            tk = name_to_ticker.get(matched) or (matched if matched in stocks_info else None)
            if tk and tk in stocks_info and tk not in acc:
                acc.append(tk)

    def _process_block(m) -> str:
        tag, inner = m.group(1), m.group(2)
        tickers: list[str] = []
        # Scan text segments only — skip nested tags (e.g. <strong>).
        for i, seg in enumerate(re.split(r"(<[^>]+>)", inner)):
            if i % 2 == 0 and seg:
                _collect_tickers(seg, tickers)
        if not tickers:
            return m.group(0)
        row = '<div class="report-stocks">' + "".join(
            _stk_pill(tk, stocks_info) for tk in tickers
        ) + '</div>'
        # A <div> inside <p> is invalid HTML — place the row after </p>.
        if tag == "li":
            return f"<li>{inner}{row}</li>"
        return f"<p>{inner}</p>{row}"

    return re.sub(r"<(p|li)>(.*?)</\1>", _process_block, html, flags=re.DOTALL)


_TICKER_PAREN_RE = re.compile(r'\(([^)]+)\)$')

def _normalize_ticker(raw: str) -> str:
    """Normalize Gemini-formatted tickers.
    '台積電(2330)' -> '2330'
    'MU(US)' -> 'MU'
    '2330.TW' -> '2330'
    'NVDA' -> 'NVDA'
    """
    s = raw.strip()
    m = _TICKER_PAREN_RE.search(s)
    if m:
        inner = m.group(1).strip()
        outer = s[:m.start()].strip()
        if inner.upper() in ("US", "TW", "HK", "JP", "KR"):
            return outer   # "MU(US)" -> "MU"
        if re.match(r'^[A-Z0-9]{2,8}$', inner, re.IGNORECASE):
            return inner   # "台積電(2330)" -> "2330"
    # Strip .TW suffix (keep just code for DB queries; yfinance re-adds it)
    if re.match(r'^[0-9]{4,6}\.(TW|TWO)$', s, re.IGNORECASE):
        return s.split(".")[0]
    return s


_REC_LABEL: dict[str, tuple[str, str]] = {
    "strong_buy":   ("強力買入", "#22c55e"),
    "buy":          ("買入",     "#4ade80"),
    "hold":         ("持有",     "#f59e0b"),
    "underperform": ("落後",     "#f97316"),
    "sell":         ("賣出",     "#ef4444"),
}


# ── Ranking rows HTML ─────────────────────────────────────────────────────────

def rank_rows_html(ranks, market: str) -> str:
    """Render rows for the rankings table. 股價與漲跌% 合併為單欄
    "price(chg%)",CSS class 由 fmt_pct 決定(up/down/flat/neutral)。
    無單位前綴(NT$/$ 拿掉);Asia 慣例:紅漲綠跌白平。
    """
    rows = []
    for r in ranks:
        chg = float(r["change_pct"]) if r["change_pct"] is not None else None
        close = float(r["close_price"]) if r.get("close_price") is not None else None
        pct_str, pct_cls = fmt_pct(chg)
        if market == "US":
            val = f"${float(r['trading_value'] or 0)/1e9:.1f}B"
        else:
            val = f"{float(r['trading_value'] or 0)/1e8:.0f}億"
            if r.get("is_limit_up_30m"):
                val += " ⬆"
        if close is not None:
            price_str = f"{close:.2f}"
            # chg=None → 顯「價 (—)」(pct_str 已是「—」),不省略、不誤認平盤
            quote = f"{price_str} ({pct_str})"
        else:
            quote = pct_str
        board = ""
        flag_chips = ""
        if market == "TW":
            extra = json.loads(r.get("extra") or "{}") if isinstance(r.get("extra"), str) else (r.get("extra") or {})
            b = extra.get("board", "TWSE")
            board = f'<span class="board-badge {b.lower()}">{b}</span>'
            # 處置 / 漲跌停 chip(同 _stk_pill 規格)
            flag_info = {
                "is_punish": bool(extra.get("is_punish")),
                "punish_type": extra.get("punish_type"),
                "limit_up": bool(extra.get("is_limit_up") or r.get("is_limit_up_30m")),
                "is_limit_down": bool(extra.get("is_limit_down")),
            }
            flag_chips = _flag_chips(flag_info)
        rank_disp = r["rank"] if r["rank"] is not None else "—"
        rows.append(
            f'<tr><td class="rank">{rank_disp}</td>'
            f'<td class="ticker">{html_lib.escape(r["ticker"])}</td>'
            f'<td class="name">{html_lib.escape((r["name"] or "")[:10])}{board}{flag_chips}</td>'
            f'<td class="num {pct_cls}">{quote}</td>'
            f'<td class="num">{val}</td></tr>'
        )
    if not rows:
        return '<tr><td colspan="5" style="color:var(--muted);text-align:center">尚無資料</td></tr>'
    return ''.join(rows)


# ── Focus stocks tab ──────────────────────────────────────────────────────────


def _jc(v, nd: int | None = None):
    """JSON payload 數值壓縮:round 到 nd 位(None=取整),整數值收斂成 int。

    DB 回來的價格常帶 float32→float64 雜訊(7.650000095367432),原樣
    json.dumps 一個數字就 17+ 字元;round(2) 後 4 字元、且才是真實 tick 價
    (台股最小 tick 0.01)。整數值再去掉 `.0` 尾巴(51.0→51)。
    history.json / kline.json 靠這支從 16MB/12MB 壓回約一半。
    """
    if v is None:
        return None
    f = float(v)
    if nd is None:
        return int(round(f))
    r = round(f, nd)
    i = int(r)
    return i if i == r else r


def _svgn(v: float) -> str:
    """SVG path 座標:2 位小數,去尾零(8.20→8.2、3.00→3)。"""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s and s != "-0" else "0"


def _sparkline_bars_svg(values: list[float], width: int = 84, height: int = 22) -> str:
    """Histogram sparkline:每天一根 bar,紅(正/買)綠(負/賣)。
    values 是 daily 三大法人淨流入金額(億 TWD),正買負賣。

    2026-06-11 起 up/down 各合併成單一 <path>(原本每天一個 <rect>,
    210 張卡 × ~42 天 ≈ 8,700 個 rect ≈ 600KB HTML;path 同樣吃
    .spark-up/.spark-down 的 fill,視覺不變,省 ~75% sparkline bytes)。
    """
    if not values or all(v == 0 for v in values):
        return ""
    abs_max = max(abs(v) for v in values)
    if abs_max <= 0:
        return ""
    n = len(values)
    bar_w = width / n
    w = max(bar_w - 0.4, 0.5)
    mid = height / 2
    up_d: list[str] = []
    down_d: list[str] = []
    for i, v in enumerate(values):
        x = i * bar_w
        h = abs(v) / abs_max * (height / 2 - 1)
        if h < 0.5:
            h = 0.5
        y = mid - h if v >= 0 else mid
        (up_d if v >= 0 else down_d).append(
            f'M{_svgn(x)} {_svgn(y)}h{_svgn(w)}v{_svgn(h)}h-{_svgn(w)}z'
        )
    paths = ""
    if up_d:
        paths += f'<path class="spark-up" d="{"".join(up_d)}"/>'
    if down_d:
        paths += f'<path class="spark-down" d="{"".join(down_d)}"/>'
    return (
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" preserveAspectRatio="none">'
        + paths
        + f'<line class="spark-mid" x1="0" y1="{mid}" x2="{width}" y2="{mid}" />'
        + '</svg>'
    )


def _aggregate_history_net(member_keys: list[str], history_payload: dict) -> list[float]:
    """合併 member_keys 對應的 daily 三大法人淨流入金額(億 TWD)。
    payload value 是 4-tuple [tv, chg, close, net_inst],這裡只用 idx 3。
    """
    daily: dict[str, float] = {}
    for k in member_keys:
        for row in history_payload.get(k, []):
            d = row.get("d")
            stocks = row.get("s", {})
            day_net = sum(
                (v[3] or 0) for v in stocks.values()
                if v and len(v) >= 4 and v[3] is not None
            )
            daily[d] = daily.get(d, 0) + day_net
    return [daily[d] / 1e8 for d in sorted(daily.keys())]  # 換成億單位


def _aggregate_ticker_net_inst(focal_tickers: list[str],
                                ticker_net_inst: dict[str, dict[str, float]],
                                n_days: int = 180) -> list[float]:
    """合併 focal_tickers 跨 main 的 daily net_inst(億 TWD)。
    用於 hl_sub cluster sparkline:member_keys (近一年焦點||...) 沒 theme_history
    row,但其 focal ticker 在「其他 main」row 內出現過,net_inst 是 ticker-level
    transaction 跨 (m,s) 同值,可由 ticker_net_inst 反向索引拿。
    回 list[float] 億 TWD,最後 n_days 個 trading day。
    """
    daily: dict[str, float] = {}
    for tk in focal_tickers:
        for d, v in (ticker_net_inst or {}).get(tk, {}).items():
            if v is not None:
                daily[d] = daily.get(d, 0) + v
    sorted_days = sorted(daily.keys())[-n_days:]
    return [daily[d] / 1e8 for d in sorted_days]


# ── 焦點 cluster header「連續上榜天數 / 近 20 日上榜率」+ 趨勢圖序列 ────────
# Q24 提供 main='近一年焦點' 過去 180 天每個交易日的 hl_sub list。

def _cluster_streak_rate20(cluster_subs: list[str],
                            sorted_dates: list[str],
                            daily_subs: dict[str, set[str]],
                            window: int = 20) -> tuple[int, float]:
    """Merged cluster 視為「任一成員 sub 上榜 = cluster 上榜」。
    回 (連續上榜天數含今日, 近 N 個交易日上榜率 0-1)。
    sorted_dates 由舊到新;若 cluster 今日沒上榜,streak=0、rate20 仍計入過去 N 天。
    """
    if not cluster_subs or not sorted_dates:
        return 0, 0.0
    subset = set(cluster_subs)

    streak = 0
    for d in reversed(sorted_dates):
        if subset & daily_subs.get(d, set()):
            streak += 1
        else:
            break

    wind = sorted_dates[-window:]
    hits = sum(1 for d in wind if subset & daily_subs.get(d, set()))
    rate20 = hits / len(wind) if wind else 0.0
    return streak, rate20


def _focus_dynamics_chip(streak: int | None, rate20: float | None) -> str:
    """cluster header 兩個小 chip:連續上榜天數 + 近 20 日上榜率。
    streak / rate20 為 None 時 chip 不渲(該 cluster 半年內無 history)。"""
    if streak is None and rate20 is None:
        return ""
    parts = []
    if streak is not None and streak > 0:
        # 顏色:≥5 強(實心)、2-4 中(框線)、1 灰
        cls = "fdyn-streak-strong" if streak >= 5 else ("fdyn-streak-mid" if streak >= 2 else "fdyn-streak-low")
        parts.append(
            f'<span class="fdyn-chip {cls}" '
            f'title="連續上榜天數(含今日)— 近 180 天 hl_sub history">連 {streak} 天</span>'
        )
    if rate20 is not None:
        pct = round(rate20 * 100)
        cls = "fdyn-rate-high" if pct >= 70 else ("fdyn-rate-mid" if pct >= 40 else "fdyn-rate-low")
        parts.append(
            f'<span class="fdyn-chip {cls}" '
            f'title="近 20 個交易日上榜率 = 該題材出現天數 / 20">20 日 {pct}%</span>'
        )
    return "".join(parts)




def _industry_section_html(
    clusters: list[IndustryCluster],
    all_stocks: dict,
    level: str,
    history_payload: dict | None = None,
    highlight_subs: dict[str, list[tuple[str, str]]] | None = None,
    stock_meta: dict | None = None,
    ticker_net_inst: dict[str, dict[str, float]] | None = None,
    topics_by_ticker: dict[str, str] | None = None,
    topics_by_focus_theme: dict[str, list] | None = None,
    topics_stocks_info: dict | None = None,
    cluster_dynamics: dict[str, dict] | None = None,
) -> str:
    """Render industry cluster cards. level = "main" | "sub" | "hl_sub" | "pan_sub"。
    前哨觀察(watch)已從顯示移除(2026-05-16),只保留今日焦點。
    sub level:加廣泛概念股 panel(>3 個 cluster 出現的 ticker 可點擊濾除,
    觸發 FLIP 動畫重排 + TV 重算)。每張卡內嵌 sparkline(過去 180 天
    TV trend);點擊彈出 modal 大圖。

    2026-05-17 加:level='hl_sub' 時,從 highlight_subs(theme_dictionary
    的「近一年焦點」main 結構)查每個 cluster 對應 sub 的完整 ticker list,
    扣掉 focal 顯示為「前哨」chip(.snt-pill,虛線淡色,顯 PE)。
    """
    stock_meta = stock_meta or {}
    if history_payload is None:
        history_payload = {}
    if not clusters:
        label = "主產業" if level == "main" else "子產業"
        return f'<p class="muted-note">今日尚無{label}熱門產業</p>'

    # 廣泛概念股(sub-only):同 ticker 在 N 個 sub-cluster 出現 → 變成可濾除 chip。
    # threshold 綁「層級語意」而非原始 cluster 數:
    #   hl_sub(焦點,人工編彙、focal 小 1-4 檔)→ >1(≥2 題材)
    #   pan_sub / sub(statementdog 稠密分類)→ >3(≥4 題材)避免 noise
    # 舊版用 `len(clusters) > 20` 當「是否稠密層」的代理,但焦點在廣度大的日子
    # 也會超過 20(2026-06-03 焦點 23 條),代理失準把門檻頂成 >3 → 焦點當天
    # 跨題材最多僅 3 → universal 空 → 多題材股 panel 消失。改綁 level 根治。
    universal: dict[str, str] = {}
    if level in ("sub", "hl_sub", "pan_sub"):
        from collections import Counter
        counts: Counter = Counter()
        for c in clusters:
            for s in c.focal:
                counts[s.ticker] += 1
        threshold = 1 if level == "hl_sub" else 3
        for t, n in counts.items():
            if n > threshold:
                info = all_stocks.get(t, {})
                universal[t] = (info.get("name") or t)[:8]

    # sort chip row(sub level only):換維度看 cluster 排序。預設 TV desc。
    sort_html = ""
    if level in ("sub", "hl_sub", "pan_sub"):
        # 指標說明 tooltip(2026-05-19 從 details 改 hover tooltip)。
        # 文案要跟 MA20 乖離率計算(Q13 close 歷史 simple-mean)邏輯對齊;
        # 改公式必須同步改這段。
        explainer_html = (
            '<span class="metric-tooltip" tabindex="0">'
            '<span class="mt-trigger">ⓘ 指標計算說明</span>'
            '<span class="mt-body">'
            '<ul>'
            '<li><b>漲跌</b>：cluster 焦點股「當日收盤漲跌%」的<b>簡單算術平均</b>'
            '(skip 缺值)。例：3 檔焦點 +2% / -1% / +5% → 平均 +2.00%。</li>'
            '<li><b>乖離</b>：焦點股「20MA 乖離率%」的簡單平均;'
            '每檔乖離 = (今日收盤 − 過去 20 日收盤均線)÷ 20MA × 100;'
            '數值越正越「過熱」、越負越「超賣」。</li>'
            '<li><b>PE</b>：焦點股 <b>PE (TTM)</b> 簡單平均;'
            'skip 虧損股(PE ≤ 0)避免拉低均值。</li>'
            '</ul>'
            '<p class="metric-note">⚠ 三項皆為<b>簡單算術平均</b>(每檔等權重),'
            '與點開 chart modal 內的「焦點股加權指數」(用市值 × shares 加權) <b>不同</b>。'
            '小型股對 cluster header 的影響與大型股相同。</p>'
            '</span>'
            '</span>'
        )
        # data-level 讓 _refreshSortUi / setClusterSort 知道這個 chip 屬於哪個 sub-tab,
        # state per level(_clusterSort[level] / _clusterSortDir[level]),兩 tab 各管自己。
        sort_html = (
            '<div class="sort-explainer-row">'
            '<div class="sort-row">'
            '<span class="cluster-count">共 <b>__NCLUSTER__</b> 個題材</span>'
            '<span class="sort-sep">/</span>'
            '<span class="sort-label">排序：</span>'
            f'<button class="sort-chip"        data-sort="tv"    data-level="{level}" type="button" onclick="setClusterSort(\'tv\',\'{level}\')">成交金額</button>'
            f'<button class="sort-chip active" data-sort="chg"   data-level="{level}" data-dir="desc" type="button" onclick="setClusterSort(\'chg\',\'{level}\')">平均漲跌</button>'
            f'<button class="sort-chip"        data-sort="bias"  data-level="{level}" type="button" onclick="setClusterSort(\'bias\',\'{level}\')">平均乖離</button>'
            f'<button class="sort-chip"        data-sort="pe"    data-level="{level}" type="button" onclick="setClusterSort(\'pe\',\'{level}\')">平均 PE</button>'
            f'<button class="sort-chip"        data-sort="peg"   data-level="{level}" type="button" onclick="setClusterSort(\'peg\',\'{level}\')">平均 PEG</button>'
            '</div>'
            + explainer_html
            + '</div>'
        )

    univ_html = ""
    if universal:
        # 「多題材股」chip:同 ticker 在 N 個 sub-cluster 出現。點 chip → 該
        # sub-tab 內只留含此 ticker 的 cluster,其餘 collapse 動畫隱藏;再點
        # 取消。single-select(state per level)。
        chips = "".join(
            f'<button class="univ-chip" data-ticker="{html_lib.escape(t)}" '
            f'data-level="{level}" type="button"'
            f" onclick='toggleMultiTheme({json.dumps(t)},{json.dumps(level)})'>"
            f"{html_lib.escape(t)}&nbsp;{html_lib.escape(n)}</button>"
            for t, n in universal.items()
        )
        # chip 數超過門檻(泛分類常 100+)→ 預設摺疊成約兩列 + 「展開全部」
        # (2026-06-12:整面 chip 牆佔掉半個首屏,訪客還沒看到題材卡先被嚇到)
        _UNIV_COLLAPSE_N = 24
        _collapsed = len(universal) > _UNIV_COLLAPSE_N
        _panel_id = f"univ-{level}"
        univ_html = (
            f'<div class="univ-panel{" univ-collapsed" if _collapsed else ""}" id="{_panel_id}">'
            '<span class="univ-label">多題材股:</span>'
            f'{chips}'
            '</div>'
            + (
                f'<div class="univ-more-row"><button class="univ-more" type="button" '
                f'data-full="展開全部 {len(universal)} 檔 ▾" '
                f"onclick=\"toggleUnivExpand('{_panel_id}',this)\">"
                f'展開全部 {len(universal)} 檔 ▾</button></div>'
                if _collapsed else ""
            )
        )

    # badges = per-cluster 焦點股排序觸發(只動該題材內的 pill 順序,不影響外層 cluster 排序)。
    # 預設每個 cluster 內 focal 都依 乖離(bias)desc。
    def _metric_badge(label: str, value: float | None, title: str, sort_key: str,
                      card_id: str, is_default_sort: bool = False) -> str:
        """指標 badge(可點擊觸發 setFocalSort):正紅 / 負綠 / 平盤白 / None 灰。"""
        onclick = f"onclick=\"setFocalSort('{card_id}','{sort_key}')\""
        active = " is-active-sort" if is_default_sort else ""
        ddir = ' data-dir="desc"' if is_default_sort else ""
        common = (f'class="cluster-metric metric-btn {{cls}}{active}" data-sort="{sort_key}"{ddir} '
                  f'role="button" tabindex="0" title="{title}" {onclick}')
        if value is None:
            return f'<span {common.format(cls="neutral")}>{label} —</span>'
        pct_str, cls = fmt_pct(value)
        return f'<span {common.format(cls=cls)}>{label} {pct_str}</span>'

    # 2026-05-19 起預設依「平均漲跌」desc 排序(改前是 tv desc):跟 JS
    # _getSortKey 預設 'chg' 一致,首次 _recalcClusters 不觸發 FLIP 動畫
    # (dy≈0)→ 無視覺跳動。None 排尾段(用 -inf 讓 desc 把 None 推後)。
    def _cluster_avg_chg(c):
        chgs = [s.change_pct for s in c.focal if s.change_pct is not None]
        return sum(chgs) / len(chgs) if chgs else float("-inf")
    clusters = sorted(clusters, key=lambda c: -_cluster_avg_chg(c))

    # sub-level 判斷:hl_sub / pan_sub 都視同 sub(顯 sparkline / subtitle 等),
    # 提到 for-loop 外避免每 iter 重算 + 解決前向使用 UnboundLocalError
    is_sub_level = level in ("sub", "hl_sub", "pan_sub")
    _topics_by_ticker = topics_by_ticker or {}
    _topics_by_focus_theme = topics_by_focus_theme or {}
    _topics_stocks_info = topics_stocks_info or {}
    cluster_topic_payload: dict[str, str] = {}  # card_id -> rendered topic_card HTML(s)
    cards = []
    cluster_json: list[dict] = []
    for idx, c in enumerate(clusters):
        n_focal = len(c.focal)
        # 焦點股平均漲跌幅
        chgs = [s.change_pct for s in c.focal if s.change_pct is not None]
        avg_chg = sum(chgs) / len(chgs) if chgs else None
        # 焦點股平均 20MA 乖離率(ma20_bias 由 Q13 close 歷史算入 stocks_info)
        ma20s = [all_stocks.get(s.ticker, {}).get("ma20_bias") for s in c.focal]
        ma20s = [m for m in ma20s if m is not None]
        avg_ma20 = sum(ma20s) / len(ma20s) if ma20s else None
        # F2: cluster stock_meta 平均 — PE 只(殖利/Beta 2026-05-18 起移除全站)
        def _mean(lst):
            xs = [x for x in lst if x is not None]
            return sum(xs) / len(xs) if xs else None
        avg_pe = _mean([all_stocks.get(s.ticker, {}).get("pe_ttm")
                        for s in c.focal if (all_stocks.get(s.ticker, {}).get("pe_ttm") or 0) > 0])
        # PEG 只計入 status='ok_*' 且 > 0 的 ticker(eps_declining / low_growth /
        # insufficient_history 不計入平均)
        def _peg_of(t):
            inf = all_stocks.get(t, {})
            st = inf.get("peg_status")
            pg = inf.get("peg_ratio")
            return pg if (st and st.startswith("ok_") and pg is not None and pg > 0) else None
        avg_peg = _mean([_peg_of(s.ticker) for s in c.focal])

        def _plain_badge(label: str, value: float | None, title: str, sort_key: str,
                         card_id: str, fmt: str = "{:.2f}") -> str:
            """中性 badge(無顏色,可點擊觸發 setFocalSort)。value=None 仍可點(用 — 顯示)。"""
            onclick = f"onclick=\"setFocalSort('{card_id}','{sort_key}')\""
            common = (f'class="cluster-metric metric-btn neutral" data-sort="{sort_key}" '
                      f'role="button" tabindex="0" title="{title}" {onclick}')
            val_str = "—" if value is None else fmt.format(value)
            return f'<span {common}>{label} {val_str}</span>'

        # 順序:成交 / 漲跌 / 乖離 / PE(2026-05-19 對齊外層 cluster sort chip
        # 順序「成交金額、平均漲跌、平均乖離、平均 PE」)。
        # 點 badge → setFocalSort(card_id, key):只動該題材內 focal pill 順序
        card_id = f"cc-{level}-{idx}"
        _tv_billion = (c.trading_value or 0) / 1e8
        metric_html = (
            _plain_badge("成交", _tv_billion, "點擊依此題材內個股成交金額排序", "tv", card_id, "{:.0f}億")
            + _metric_badge("漲跌", avg_chg, "點擊依此題材內個股漲跌幅排序", "chg", card_id, is_default_sort=True)
            + _metric_badge("乖離", avg_ma20, "點擊依此題材內個股 20MA 乖離率排序", "bias", card_id)
            + _plain_badge("PE", avg_pe, "點擊依此題材內個股 PE (TTM)排序", "pe", card_id, "{:.1f}")
            + _plain_badge("PEG", avg_peg, "點擊依此題材內個股 PEG 排序(<1 低估、≈1 合理、>1 偏貴)", "peg", card_id, "{:.2f}")
        )

        member_keys = [f"{m}||{s}" for m, s in (c.members or [])]
        # focal entries 帶 6 維 metric,供前端 sort chip / modal chip 用。
        # toggle universal 後前端依 _univDis 重算。
        def _focal_entry(s):
            info = all_stocks.get(s.ticker, {})
            mkt = info.get("market") or ("TW" if s.ticker.split(".")[0].isdigit() else "US")
            # peg 只在 status='ok_*' 時帶值;其他狀態 None → 排序排尾
            _ps = info.get("peg_status")
            _pg = info.get("peg_ratio") if (_ps and _ps.startswith("ok_")) else None
            return {
                "ticker": s.ticker,
                "n":     (info.get("name") or "")[:10],
                "mkt":   mkt,
                "tv":    s.trading_value,
                "chg":   info.get("change_pct"),
                "close": info.get("close_price"),
                "bias":  info.get("ma20_bias"),
                "pe":    info.get("pe_ttm"),
                "peg":   _pg,
            }
        cluster_json.append({
            "cardId": card_id,
            "memberKeys": member_keys,
            "name": c.name,
            "focal": [_focal_entry(s) for s in c.focal],
            # sentinel(2026-05-24 起進 modal):同題材內今日 chg < -3 的成員。
            # modal 端 ticker 列表 + 加權指數 + 三大法人計算皆納入 sentinel,
            # 讓 user 看見題材完整面貌(原本只顯 focal,sentinel 只在卡片
            # 「前哨」toggle 摺疊區段,modal 不可見)。cluster 頁卡片 metric
            # 仍維持 focal-only(代表題材「熱度」基線)。
            "sentinel": [_focal_entry(s) for s in (getattr(c, "sentinel", None) or [])],
            "baseTv": c.trading_value,
        })

        # Sparkline (server-side SVG):過去 N 天三大法人淨流入(億)柱狀圖。
        # 紅買綠賣亞洲慣例。
        #   pan_sub:走 member_keys → theme_history_payload (現有路徑)
        #   hl_sub:走 focal tickers → ticker_net_inst 反向索引(跨 main 拿,
        #     因為「近一年焦點||...」自己沒 theme_history row,但 focal ticker
        #     在其他 main 的 row 內有 net_inst,值跨 (m,s) 同 day 相同可共用)
        spark_html = ""
        if is_sub_level:
            if level == "hl_sub" and ticker_net_inst:
                focal_tks = [s.ticker for s in c.focal]
                spark_values = _aggregate_ticker_net_inst(focal_tks, ticker_net_inst)
            elif member_keys:
                spark_values = _aggregate_history_net(member_keys, history_payload)
            else:
                spark_values = []
            if len(spark_values) >= 2:
                spark_svg = _sparkline_bars_svg(spark_values)
                if spark_svg:
                    spark_html = (
                        f'<button class="spark-btn" type="button" '
                        f"onclick=\"openThemeChart('{card_id}')\" "
                        f'title="點擊看 6 個月資金淨流入 / 平均股價大圖">'
                        f'{spark_svg}'
                        f'<span class="spark-label">{len(spark_values)}d</span>'
                        f'</button>'
                    )
            # NOTE: 不對 spark_html 空時補 fallback chart icon —
            # net_inst 缺資料是 ingest 端應補的 root cause(對「純近一年焦點」
            # ticker:從未進 top-50 → theme_history.focal_breakdown 永遠缺席
            # → 反向索引 ticker_net_inst 拿不到)。stockgg 端維持單一 sparkline
            # path,缺就缺(該 cluster 暫無 chart 入口),強迫 ingest 補資料
            # 才會恢復。歷史踩雷:2026-05-19 曾加 📈 icon fallback 被 user 否決,
            # 因為「icon 與其他 cluster 不一致」+「掩蓋上游 bug」。
        # focal pills 預設依該股當日漲跌 desc 排(對齊 cluster header 預設 active 的 漲跌 badge);
        # None 排尾段。JS setFocalSort 點擊後會 re-order DOM。
        def _focal_chg_key(s):
            v = s.change_pct
            return (1, 0) if v is None else (0, -v)
        focal_sorted = sorted(c.focal, key=_focal_chg_key)
        focal_pills = "".join(
            _stk_pill(
                s.ticker, all_stocks,
                extra_attrs=f'data-cluster-ticker="{html_lib.escape(s.ticker)}" data-tv="{int(s.trading_value)}"',
            )
            for s in focal_sorted
        )

        # cluster-meta 文字 2026-05-19 起拿掉(「N 檔焦點 · 694億」多餘 —
        # focal 數一目了然、TV 已變成 metric badge)。span 保留為 spacer 給
        # cluster-hdr flex 的 margin-left:auto hook 把 spark-btn 推到最右。
        meta_text = ""

        icon = "🔷" if level == "main" else "🔸"
        # 近一年焦點 sub-tab 內所有 cluster 的 main 都是「近一年焦點」,顯
        # subtitle 是 redundant noise(每張都一樣),拿掉。泛分類維持原樣。
        subtitle = (
            f'<div class="cluster-subtitle">屬於 {html_lib.escape(c.main)}</div>'
            if is_sub_level and level != "hl_sub" else ""
        )

        # 前哨 section:
        # - hl_sub (2026-05-18 起):由 detect_focus_clusters 提供 cluster.sentinel
        #   (題材內、universe 內、下跌的標的);chip 顯漲跌%
        # - 其他 level + 有 highlight_subs 傳入(舊兼容路徑):從 theme_dictionary
        #   完整 ticker list 扣 focal,顯 PE
        # toggle 按鈕直接 append 到 focal_pills 末段,panel 在下方獨立 block,
        # JS toggleSentinelInline 透過 data-target 找 panel 動畫展開/收合。
        sentinel_toggle = ""  # inline button(append to focal_pills)
        sentinel_panel = ""   # panel block(在 focal-stocks div 下方)

        new_sentinel = list(getattr(c, "sentinel", None) or [])
        if level == "hl_sub" and new_sentinel:
            # 新版:cluster.sentinel 已是 FocalStock list(題材內下跌標的)
            # 重用 _stk_pill 顯漲跌(跟 focal pill 樣式統一),加 data 屬性區隔
            snt_html = "".join(
                _stk_pill(
                    s.ticker, all_stocks,
                    extra_attrs=f'data-cluster-ticker="{html_lib.escape(s.ticker)}" '
                                f'data-tv="{int(s.trading_value)}" data-sentinel="1"',
                )
                for s in new_sentinel
            )
            panel_id = f"{card_id}-sntl"
            sentinel_toggle = (
                f'<button class="sntl-toggle-inline" type="button" '
                f'data-target="{panel_id}" '
                f'onclick="toggleSentinelInline(this)" '
                f'title="展開 {len(new_sentinel)} 檔同題材下跌前哨">'
                f'<span class="sntl-arrow">▾</span>'
                f'<span class="sntl-count">前哨 {len(new_sentinel)}</span>'
                f'</button>'
            )
            sentinel_panel = (
                f'<div class="cluster-sentinel-stocks anim-panel" '
                f'id="{panel_id}" hidden>{snt_html}</div>'
            )
        elif level != "hl_sub" and highlight_subs:
            # 舊版(其他 level 兼容):從 theme_dictionary 全 ticker 扣 focal
            focal_tk_set = {s.ticker for s in c.focal}
            sentinel_pool: dict[str, str] = {}
            for m, s in (c.members or []):
                if m != HIGHLIGHT_MAIN:
                    continue
                for tk, nm in highlight_subs.get(s, []):
                    if tk not in focal_tk_set:
                        sentinel_pool.setdefault(tk, nm)
            if sentinel_pool:
                def _snt_pill(tk, nm):
                    meta = stock_meta.get(tk, {})
                    try:
                        pe = float(meta["pe_ttm"]) if meta.get("pe_ttm") is not None else None
                    except (TypeError, ValueError):
                        pe = None
                    pe_html = (f'<span class="snt-pe">PE {pe:.1f}</span>'
                               if pe is not None and pe > 0 else "")
                    return (
                        f'<div class="snt-pill" data-ticker="{html_lib.escape(tk)}" '
                        f'title="今日未進 top-50;PE 來自 stock_meta">'
                        f'<span class="sp-ticker">{html_lib.escape(_disp_ticker(tk))}</span>'
                        f'<span class="sp-name">{html_lib.escape((nm or tk)[:10])}</span>'
                        f'{pe_html}'
                        f'</div>'
                    )
                items = sorted(
                    sentinel_pool.items(),
                    key=lambda x: (
                        1 if stock_meta.get(x[0], {}).get("pe_ttm") in (None, 0) else 0,
                        -float(stock_meta.get(x[0], {}).get("pe_ttm") or 0),
                    ),
                )
                snt_html = "".join(_snt_pill(tk, nm) for tk, nm in items)
                panel_id = f"{card_id}-sntl"
                sentinel_toggle = (
                    f'<button class="sntl-toggle-inline" type="button" '
                    f'data-target="{panel_id}" '
                    f'onclick="toggleSentinelInline(this)" '
                    f'title="展開 {len(items)} 檔同題材未進 top-50 的前哨">'
                    f'<span class="sntl-arrow">▾</span>'
                    f'<span class="sntl-count">前哨 {len(items)}</span>'
                    f'</button>'
                )
                sentinel_panel = (
                    f'<div class="cluster-sentinel-stocks anim-panel" '
                    f'id="{panel_id}" hidden>{snt_html}</div>'
                )

        # Cluster name:用 CSS 寬度判斷自動 ellipsis(改自之前 30 字硬閾值)。
        # 標題永遠完整 render,cluster-hdr 是 nowrap → 標題用 flex-grow + overflow
        # ellipsis 自動吃可用空間,當其他 chip / sparkline 擠不下就把標題截尾段。
        # 點標題切 .expanded → 解掉 nowrap 允許多行展開(配 cursor:pointer 暗示)。
        # cn-merged(focal 完全相同的合併 cluster)仍保留 +▾ button mobile 收合機制。
        title_attr = f' title="{html_lib.escape(c.name)}(點擊展開全名)"'
        if " & " in c.name:
            parts = c.name.split(" & ")
            parts_html_pieces = []
            for i, p in enumerate(parts):
                if i > 0:
                    parts_html_pieces.append('<span class="cn-sep"> &amp; </span>')
                parts_html_pieces.append(
                    f'<span class="cn-part">{html_lib.escape(p)}</span>'
                )
            name_html = (
                f'<span class="cluster-name cn-merged" data-parts="{len(parts)}"'
                f' onclick="toggleNameExpand(this)"{title_attr}>'
                f'{icon} {"".join(parts_html_pieces)}'
                f'<button class="cn-toggle" type="button" '
                f'onclick="event.stopPropagation();toggleClusterName(this)">+ ▾</button>'
                f'</span>'
            )
        else:
            name_html = (
                f'<span class="cluster-name"'
                f' onclick="toggleNameExpand(this)"{title_attr}>'
                f'{icon} {html_lib.escape(c.name)}</span>'
            )

        # Cluster info ⓘ button:關聯跨來源議題。hl_sub(焦點)走 focus_themes
        # 題材名比對、其他 level 走龍頭股 ticker 反查(見 _resolve_cluster_topics)。
        # 有 match → render ⓘ button(onclick → showClusterTopicModal)+ 把
        # topic HTML 加進 cluster_topic_payload[card_id];沒 match → 不渲。
        info_btn_html = ""
        _topics_html = _resolve_cluster_topics(
            c, level, _topics_by_ticker, _topics_by_focus_theme, _topics_stocks_info)
        if _topics_html:
            cluster_topic_payload[card_id] = _topics_html
            info_btn_html = (
                f'<button class="cluster-info-btn" type="button" '
                f"onclick=\"showClusterTopicModal('{card_id}')\" "
                f'title="點擊查看此題材關聯議題">ⓘ</button>'
            )

        # 焦點 cluster header 兩個 chip(連續上榜 / 20 日上榜率)。
        # cluster_dynamics keyed by cluster_id;merged cluster 在 outer 已合算 max.
        dyn_chip_html = ""
        if cluster_dynamics:
            dyn = cluster_dynamics.get(c.cluster_id)
            if dyn:
                dyn_chip_html = _focus_dynamics_chip(dyn.get("streak"), dyn.get("rate20"))

        cards.append(f"""
<div class="cluster-card" id="{card_id}">
  <div class="cluster-hdr">
    <span class="cluster-name-wrap">{name_html}{dyn_chip_html}{info_btn_html}</span>
    {metric_html}
    <span class="cluster-meta">{meta_text}</span>
    {spark_html}
  </div>
  {subtitle}
  <div class="cluster-focal-stocks">{focal_pills}{sentinel_toggle}</div>
  {sentinel_panel}
</div>""")

    cluster_json_str = json.dumps(cluster_json, ensure_ascii=False, separators=(",", ":"))
    # cluster topic payload — keyed by card_id,跨 sub-tab merge 進
    # window.IIA_CLUSTER_TOPICS(每個 _industry_section_html call 共用此 obj,
    # Object.assign 累積)
    topics_json_str = json.dumps(cluster_topic_payload, ensure_ascii=False, separators=(",", ":"))
    return (
        sort_html.replace("__NCLUSTER__", str(len(cards)))
        + univ_html
        + f'<div id="cluster-container-{level}" class="focus-clusters">'
        + "".join(cards)
        + "</div>"
        + f"<script>if(!window.IIA_CLUSTERS)window.IIA_CLUSTERS={{}};"
          f"window.IIA_CLUSTERS.{level}={cluster_json_str};"
          f"if(!window.IIA_CLUSTER_TOPICS)window.IIA_CLUSTER_TOPICS={{}};"
          f"Object.assign(window.IIA_CLUSTER_TOPICS,{topics_json_str});</script>"
    )


_WEEKDAY_TW = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]


# ── 🛡️ 風控儀錶板組別 / 觸發訊號 / 燈號中文對照 ──────────────────────
# 給「終端使用者」看的文案:絕不出現 G1-G4 代號。每組 = (名稱, 它看什麼, 偏高代表什麼)
_RISK_GROUP = {
    "G1": ("大盤價量趨勢", "綜合大盤漲速、與年線/季線的乖離、波動度",
           "偏高代表大盤過熱、短線拉回風險升高"),
    "G2": ("市場槓桿與避險", "融資餘額、三大法人台指期、Put/Call 避險比",
           "偏高代表散戶槓桿過熱或法人轉趨保守"),
    "G3": ("題材輪動速度", "熱門題材每天換手的快慢",
           "偏高代表輪動過快、行情沒有主流、追高難度大"),
    "G4": ("高檔出貨跡象", "強勢股忽漲忽跌、大戶減碼、量價背離",
           "偏高代表高檔可能有人倒貨"),
}
# trigger name → (短標籤, 白話「代表什麼」)。讓使用者不必懂 z-score 也看得懂含意。
_RISK_TRIG = {
    "roc60": ("大盤漲速過快", "近 3 個月漲幅偏高,過熱回檔風險上升"),
    "intersect_hi": ("強勢股過度集中", "同時符合多項強勢條件的股票異常多,常見於行情過熱末段"),
    "put_call": ("避險情緒升高", "選擇權 Put/Call 比偏高,資金轉趨防禦"),
    "ma200_dev": ("偏離年線過遠", "大盤距年線太遠,均值回歸(拉回)壓力大"),
    "ma60_slope": ("季線轉弱", "季線斜率走平/下彎,中期上升動能減退"),
    "realized_vol": ("波動明顯放大", "近期上沖下洗加劇,常見於變盤前後"),
    "margin": ("融資槓桿過熱", "市場融資餘額快速堆高,籌碼面偏脆弱"),
    "margin_hi": ("融資槓桿過熱", "市場融資餘額快速堆高,籌碼面偏脆弱"),
    "futures_oi": ("法人期貨偏空", "三大法人台指期淨部位轉空,大戶看法保守"),
    "churn": ("題材輪動加速", "熱門題材每天快速換手,行情缺乏主流、追高易套"),
    "churn_hi": ("題材輪動加速", "熱門題材每天快速換手,行情缺乏主流、追高易套"),
    "reversal": ("強勢股忽漲忽跌", "熱門股單日反轉頻繁,高檔換手、追價套牢風險高"),
    "holder_dist": ("大戶持股下降", "集保大戶持股比週減,疑似高檔出貨"),
    "breadth_div": ("量價背離", "指數創高但上漲家數縮減,上攻動能虛弱"),
    "nh_count": ("創新高家數萎縮", "指數仍高但能創新高的股票變少,常是轉弱前兆"),
    # ingest 端模型特徵常帶視窗天數後綴(vol20 / churn5 / xsec_disp5…)。
    # 下面是「去掉尾碼數字後」的 base 名映射(_risk_trig_label 先查完整名、
    # 再查 base 名),新視窗變體不用逐一補
    "vol": ("波動明顯放大", "近期大盤上沖下洗加劇,常見於變盤前後"),
    "xsec_disp": ("個股漲跌分歧擴大", "個股之間漲跌差距拉大、齊漲結構鬆動,資金集中少數標的,行情末段常見"),
    "disp": ("個股漲跌分歧擴大", "個股之間漲跌差距拉大、齊漲結構鬆動,資金集中少數標的,行情末段常見"),
}


def _risk_trig_label(raw: str | None) -> tuple[str, str]:
    """模型訊號內部代號 → 訪客可讀的中文標籤。

    查找順序:完整名(churn5 若有專屬條目)→ 去尾碼數字的 base 名
    (churn5→churn、vol20→vol、xsec_disp5→xsec_disp)→ 通用 fallback。
    **絕不把內部代號原樣丟到公開 UI**(2026-06-12 修:風控頁警示卡曾直接
    顯示 vol20 / churn5 / xsec_disp5,違反「公開 UI 不外洩開發者視角」鐵則)。
    """
    raw = raw or ""
    entry = _RISK_TRIG.get(raw)
    if not entry:
        base = re.sub(r"_?\d+$", "", raw)
        entry = _RISK_TRIG.get(base)
    return entry if entry else ("其他過熱訊號", "模型偵測到的市場過熱跡象")
_RISK_LEVEL = {
    "safe": ("安全", "risk-safe", "☀"),
    "warn": ("警戒", "risk-warn", "⚠"),
    "danger": ("危險", "risk-danger", "🔥"),
}


def build_risk_page(snapshot: dict | None, history: list[dict]) -> str:
    """🛡️ 風控儀錶板(取代舊趨勢頁)— 建議曝險部位 % + 4 組訊號拆解 + 回測誠實背書。
    資料全來自 ingest 寫入的 risk_dashboard_snapshot / _history(Q36/Q37),stockgg
    端不重算。誠實定位:OOS 未打贏 buy&hold(僅 3 次崩跌樣本),明確標註為風險監控參考。"""
    if not snapshot:
        return ('<p class="muted-note">風控資料載入失敗(Q36 無資料,'
                'ingest 風控 cron 可能尚未跑)。</p>')

    pos = snapshot["position"]
    score = snapshot["score"]
    level = snapshot["level"]
    lbl, lvl_cls, icon = _RISK_LEVEL.get(level, ("未知", "risk-unknown", "—"))
    comp = snapshot["components"]
    trig = snapshot["triggers"]
    meta = snapshot["meta"]
    oos = meta.get("oos", {})
    caveat = meta.get("caveat", "")

    def _pct(x):
        return f"{x * 100:+.1f}%" if isinstance(x, (int, float)) else "—"

    def _num(x, n=2):
        return f"{x:.{n}f}" if isinstance(x, (int, float)) else "—"

    H = []
    # ── 頂部誠實 banner ──
    H.append(
        '<div class="risk-caveat">'
        '<b>⚠ 這是「風險監控訊號」,不是穩賺策略。</b>'
        '回測樣本內僅 3 次崩跌事件(統計上偏弱),樣本外(OOS)並未打敗買進持有。'
        '請當作「目前該謹慎或可進取」的溫度計,而非進出場保證。'
        + (f'<span class="risk-caveat-src">{caveat}</span>' if caveat else '')
        + '</div>'
    )

    # ── 主儀錶:建議部位 + 燈號 ──
    H.append(
        f'<div class="risk-gauge risk-gauge-{lvl_cls}">'
        f'<div class="risk-gauge-pos"><span class="risk-gauge-num">{pos}</span>'
        f'<span class="risk-gauge-unit">%</span>'
        f'<span class="risk-gauge-cap">建議曝險部位</span></div>'
        f'<div class="risk-gauge-state">'
        f'<span class="risk-state-badge {lvl_cls}">{icon} {lbl}</span>'
        f'<span class="risk-gauge-date">資料日 {snapshot["d"]}</span>'
        f'</div></div>'
    )

    # ── 訊號拆解:只列「實際參與風險分數」的面向(權重>0);去除內部代號 /
    #    回測機制 / 裸 z-score,改用使用者語言 + 狀態文字。權重 0 的面向不顯示
    #    (對使用者是噪音),改在底部用一句話交代「模型涵蓋四面向、目前以這些為主」。
    active_rows = []
    all_names = []
    for g in ("G1", "G2", "G3", "G4"):
        gi = comp.get(g) or {}
        name, desc, hi_means = _RISK_GROUP[g]
        all_names.append(name)
        w = gi.get("w")
        if not (isinstance(w, (int, float)) and w > 0):
            continue  # 權重 0 = 回測未選中 → 不顯示
        z = gi.get("z")
        zz = z if isinstance(z, (int, float)) else 0.0
        # 溫度計:指針位置 = z 映射到 -3σ~+3σ,中間(50%)= 中性基準。z≈0(中性)
        # 指針落正中央而非空條 —— 解決「中性看起來像沒資料」。左綠(偏冷/降風險)、
        # 右紅(偏熱/升風險);狀態文字也用同一 z,三者一致。
        if zz >= 1.0:
            st_txt, st_cls, pin_cls = "目前偏熱,推升風險", "risk-st-hot", "risk-pin-hot"
        elif zz <= -1.0:
            st_txt, st_cls, pin_cls = "目前偏冷,壓低風險", "risk-st-cold", "risk-pin-cold"
        else:
            st_txt, st_cls, pin_cls = "目前中性", "risk-st-neutral", "risk-pin-neutral"
        pin = max(0.0, min((zz + 3) / 6 * 100, 100))  # z∈[-3,3] → 0~100%,z=0 → 50%
        active_rows.append(
            f'<div class="risk-grp">'
            f'<div class="risk-grp-head"><b>{name}</b>'
            f'<span class="risk-grp-state {st_cls}">{st_txt}</span></div>'
            f'<div class="risk-grp-desc">{desc} —— {hi_means}</div>'
            f'<div class="risk-gauge-bar"><span class="risk-gauge-mid"></span>'
            f'<span class="risk-gauge-pin {pin_cls}" style="left:{pin:.0f}%"></span></div>'
            f'<div class="risk-gauge-scale"><span>偏冷</span><span>中性</span>'
            f'<span>偏熱</span></div>'
            f'</div>'
        )
    H.append(
        '<div class="risk-grp-list"><h3 class="risk-sec-h">風險分數來自哪些訊號</h3>'
        + "".join(active_rows)
        + f'<p class="risk-grp-foot">模型評估「{"、".join(all_names)}」四大面向,'
          '經 3 年回測校準後,目前主要由上列訊號驅動風險分數。</p>'
        + '</div>'
    )

    # ── 過熱訊號:白話含意 + 定性程度 + 刻度條(標出警戒線基準),不 dump 裸 z ──
    if trig:
        tl = []
        # 程度直接採用 ingest 判定的 status(danger=警戒 / warn=注意),不自己用
        # value 套門檻(各訊號亮燈門檻不同、value 尺度未必可比 —— 之前臆測「>1.5
        # 才亮」與實際 put_call=1.39 亮燈矛盾)。強度條只做「相對本批最大」的視覺
        # 暗示,不標絕對數字、不畫固定門檻線。
        _vals = [abs(x.get("value")) for x in trig
                 if isinstance(x.get("value"), (int, float))]
        max_v = max(_vals) if _vals else 1.0
        for t in trig:
            short, mean = _risk_trig_label(t.get("name"))
            st = t.get("status", "warn")
            deg = "警戒" if st == "danger" else "注意"
            val = t.get("value")
            v = abs(val) if isinstance(val, (int, float)) else 0.0
            fill_pct = min(v / max_v * 100, 100) if max_v else 0
            tl.append(
                f'<div class="risk-trig risk-trig-{st}">'
                f'<div class="risk-trig-top"><b>{short}</b>'
                f'<span class="risk-trig-deg risk-deg-{st}">{deg}</span></div>'
                f'<div class="risk-trig-mean">{mean}</div>'
                f'<div class="risk-trig-scale" title="相對強度(本日警示訊號間比較)">'
                f'<span class="risk-trig-fill" style="width:{fill_pct:.0f}%"></span></div>'
                f'</div>'
            )
        H.append(
            '<div class="risk-trig-box"><h3 class="risk-sec-h">⚡ 目前觸發的警示訊號'
            '<span class="risk-sec-sub">模型偵測到的過熱跡象,長條為彼此間的相對強度</span></h3>'
            '<div class="risk-trig-list">' + "".join(tl) + '</div></div>'
        )
    else:
        H.append(
            '<div class="risk-trig-box"><h3 class="risk-sec-h">⚡ 過熱訊號</h3>'
            '<p class="muted-note">目前沒有任何單一訊號進入警戒區,市場情緒相對平穩。</p></div>'
        )

    # ── 回測背書(誠實對照表)──
    H.append(
        '<div class="risk-bt"><h3 class="risk-sec-h">3 年樣本外(OOS)回測 — 誠實對照</h3>'
        '<table class="risk-bt-tbl"><thead><tr><th>指標</th>'
        '<th>依建議部位調倉</th><th>買進持有</th></tr></thead><tbody>'
        f'<tr><td>總報酬</td><td>{_pct(oos.get("strat_return"))}</td>'
        f'<td>{_pct(oos.get("bh_return"))}</td></tr>'
        f'<tr><td>最大回撤(MDD)</td><td>{_pct(oos.get("strat_mdd"))}</td>'
        f'<td>{_pct(oos.get("bh_mdd"))}</td></tr>'
        f'<tr><td>Calmar(報酬/回撤)</td><td>{_num(oos.get("strat_calmar"))}</td>'
        f'<td>{_num(oos.get("bh_calmar"))}</td></tr>'
        f'<tr><td>Sharpe</td><td>{_num(oos.get("strat_sharpe"))}</td>'
        f'<td>{_num(oos.get("bh_sharpe"))}</td></tr>'
        '</tbody></table>'
        f'<p class="risk-bt-note">崩跌預警 AUC <b>{_num(oos.get("auc"))}</b>'
        f'(0.5 = 無預測力)、警報精確率 {_pct(oos.get("precision_at_theta"))}、'
        f'OOS 樣本 {oos.get("oos_days", "—")} 日。'
        '<b>策略小幅輸給買進持有</b> —— 它的價值在「壓低回撤、提醒高檔減碼」,'
        '不在拉高報酬。</p></div>'
    )

    # ── 淨值雙線圖(app.js _initRiskChart lazy render)──
    H.append(
        '<div class="risk-chart-box"><h3 class="risk-sec-h">淨值走勢:依建議部位 vs 買進持有</h3>'
        '<div id="risk-nav-chart" class="risk-chart"></div>'
        '<div class="risk-chart-legend">'
        '<span><i style="background:#60a5fa"></i>依建議部位調倉</span>'
        '<span><i style="background:#9aa4ad"></i>買進持有</span></div></div>'
    )

    payload = json.dumps({"history": history}, ensure_ascii=False, separators=(",", ":"))
    H.append(f'<script>window.IIA_RISK={payload};</script>')

    return '<div class="risk-page">' + "".join(H) + '</div>'


# ── 📈 策略模擬(動能策略 paper trading;ingest 195ac88,Q40/Q41)──────────────
# ingest 每晚 22:05 cron 全量重算 trade_sim_nav / trade_sim_trades 後觸發 deploy。
# 對照數字(報酬% / MaxDD)一律由三條序列即時算,不寫死。

def _sim_reason_label(r: str | None) -> str:
    """交易理由 → 訪客可讀文字(內部代號不外洩:entry/add1/add2/trail…)。"""
    if not r:
        return "—"
    m = {
        "entry": "首次進場",
        "add1": "加碼(第 1 段)",
        "add2": "加碼(第 2 段)",
        "驗收未過": "尾盤驗收未過,出場",
        "停損出": "觸及停損,出場",
        "線下出": "大盤跌破月線,出場",
    }
    if r in m:
        return m[r]
    if r.startswith("trail"):
        return r.replace("trail", "移動停利", 1)
    return r


def _sim_max_dd(vals: list[float]) -> float:
    """最大回撤 %(序列為 rebase 後淨值);回傳正數,如 12.3 = -12.3%。"""
    peak = float("-inf")
    mdd = 0.0
    for v in vals:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak * 100
            if dd > mdd:
                mdd = dd
    return mdd


def build_trade_sim_page(nav_rows: list[dict], trades: list[dict]) -> str:
    """📈 策略模擬頁:NAV vs 加權指數 vs 00981A vs 00991A 四線圖 + 當前持股
    + 近 20 筆交易 + 誠實方法論註記。nav_rows = Q40(sim_date desc),trades = Q41。
    四條序列(nav / twii / etf=00981A / etf2=00991A)都在 trade_sim_nav 同列,
    ingest 每晚同基準日對齊寫入(etf2 = ingest d8edc6e 加,取代原 Q13 暫解)。

    對照「報酬%」一律取**最後一個非空 rebase 值**(不是序列末筆)——trade_sim_nav
    的 etf 欄最新一天偶為 NULL,直接讀末筆會讓報酬顯「—」(2026-06-14 修)。"""
    if not nav_rows:
        return ('<p class="muted-note">策略模擬資料尚未生成(每晚 22:05 後更新,'
                '首次上線前此頁為空)。</p>')

    esc = html_lib.escape
    rows = sorted((dict(r) for r in nav_rows),
                  key=lambda r: str(r["sim_date"])[:10])
    series = []          # [{d, nav, twii, etf, etf991}] rebase=100(起點 = 序列首日)
    base = None
    base991 = None       # 00991A 在基準日的收盤
    for r in rows:
        d = str(r["sim_date"])[:10]
        nav, twii, etf, etf2 = r.get("nav"), r.get("twii"), r.get("etf"), r.get("etf2")
        if nav is None:
            continue
        if base is None:
            if not (twii and etf):
                continue   # 起點需三值齊備才能公平 rebase
            base = (float(nav), float(twii), float(etf))
            base991 = float(etf2) if etf2 else None   # 同基準日的 00991A 收盤
        series.append({
            "d": d,
            "nav": _jc(float(nav) / base[0] * 100, 2),
            "twii": _jc(float(twii) / base[1] * 100, 2) if twii else None,
            "etf": _jc(float(etf) / base[2] * 100, 2) if etf else None,
            "etf991": (_jc(float(etf2) / base991 * 100, 2)
                       if (etf2 and base991) else None),
        })
    if not series:
        return ('<p class="muted-note">策略模擬資料尚未生成(每晚 22:05 後更新)。</p>')

    start_d, end_d = series[0]["d"], series[-1]["d"]
    latest = rows[-1]

    # ── 對照數字(由序列即時算)──
    def _last_val(key):
        """最後一個非空 rebase 值(序列末筆可能因 NULL 缺值)。"""
        for p in reversed(series):
            v = p.get(key)
            if isinstance(v, (int, float)):
                return v
        return None

    def _ret(key):
        v = _last_val(key)
        return (v - 100) if v is not None else None

    def _dd(key):
        vals = [p[key] for p in series if isinstance(p.get(key), (int, float))]
        return _sim_max_dd(vals) if vals else None

    def _stat_card(label, color, ret, dd):
        rs, rcls = fmt_pct(ret)
        dds = f"-{dd:.1f}%" if dd is not None else "—"
        return (f'<div class="sim-stat"><span class="sim-stat-name">'
                f'<i style="background:{color}"></i>{esc(label)}</span>'
                f'<span class="sim-stat-ret {rcls}">{rs}</span>'
                f'<span class="sim-stat-dd">最大回撤 {dds}</span></div>')

    _has991 = any(isinstance(p.get("etf991"), (int, float)) for p in series)
    # 00991A 最後有資料日(可能落後 nav 末日:非焦點 ETF、ticker_close 更新較慢)
    _last991_d = next((p["d"] for p in reversed(series)
                       if isinstance(p.get("etf991"), (int, float))), None)
    _991_lag = bool(_last991_d and _last991_d < end_d)
    stats_html = (
        '<div class="sim-stats">'
        + _stat_card("策略淨值", "#60a5fa", _ret("nav"), _dd("nav"))
        + _stat_card("加權指數", "#f59e0b", _ret("twii"), _dd("twii"))
        + _stat_card("00981A(主動式 ETF)", "#10b981", _ret("etf"), _dd("etf"))
        + (_stat_card("00991A(主動式 ETF)", "#c084fc", _ret("etf991"), _dd("etf991"))
           if _has991 else "")
        + '</div>'
    )

    # ── 當前持股 ──
    pos = latest.get("positions")
    if isinstance(pos, str):
        try:
            pos = json.loads(pos)
        except Exception:
            pos = []
    pos = pos or []
    cash = latest.get("cash")
    nav_now = latest.get("nav")
    cash_s = f"{int(float(cash)):,}" if cash is not None else "—"
    nav_s = f"{int(float(nav_now)):,}" if nav_now is not None else "—"
    if pos:
        cards = []
        for p in pos:
            pnl = p.get("pnl_pct")
            ps, pcls = fmt_pct(pnl)
            stop = p.get("stop")
            adds = p.get("adds") or 0
            half = p.get("half")
            since = p.get("since") or ""
            tags = []
            if adds:
                tags.append(f"已加碼 {adds} 段")
            if half:
                tags.append("已減半(移動停利)")
            tag_html = "".join(f'<span class="sim-pos-tag">{esc(t)}</span>' for t in tags)
            cards.append(
                '<div class="sim-pos-card">'
                f'<div class="sim-pos-head"><b>{esc(str(p.get("t") or ""))} '
                f'{esc(str(p.get("name") or ""))}</b>'
                f'<span class="sim-pos-pnl {pcls}">{ps}</span></div>'
                f'<div class="sim-pos-row">{int(p.get("shares") or 0) // 1000} 張 · '
                f'進場 {_jc(p.get("entry"), 2) if p.get("entry") is not None else "—"} · '
                f'現價 {_jc(p.get("close"), 2) if p.get("close") is not None else "—"} · '
                f'停損 {_jc(stop, 2) if stop is not None else "—"}</div>'
                f'<div class="sim-pos-row sim-pos-sub">持有自 {esc(str(since)[:10])}'
                f'{tag_html}</div>'
                '</div>'
            )
        pos_html = ('<div class="sim-pos-list">' + "".join(cards) + '</div>'
                    f'<p class="sim-cash">現金 {cash_s} 元 / 總淨值 {nav_s} 元</p>')
    else:
        pos_html = (f'<p class="muted-note">目前空手(全現金 {cash_s} 元)—— '
                    '可能是大盤趨勢開關關閉,或近日無符合進場條件的標的。</p>')

    # ── 交易明細(全數列出,前端每 20 筆一分頁;00981A 停泊交易可 toggle 隱藏)──
    _PARK_TK = "00981A"   # 現金停泊 ETF;非個股,出手頻率統計排除
    _trades = list(trades)
    tr_rows = []
    for i, t in enumerate(_trades):
        d = str(t.get("sim_date") or "")[:10]
        side = (t.get("side") or "").lower()
        side_s, side_cls = ("買進", "up") if side == "buy" else ("賣出", "down")
        shares = t.get("shares") or 0
        price = _jc(t.get("price"), 2)
        pnl = t.get("pnl")
        if pnl is None:
            pnl_s, pnl_cls = "—", "neutral"
        else:
            pnl_v = int(float(pnl))
            pnl_s = f"{pnl_v:+,}"
            pnl_cls = "up" if pnl_v > 0 else ("down" if pnl_v < 0 else "flat")
        # 初始只顯前 20 列(no-JS 友善);JS simRenderTrades 取代後依 filter 重算。
        hidden = "" if i < 20 else " hidden"
        _is_park = str(t.get("ticker") or "") == _PARK_TK
        park_attr = ' data-etf981="1"' if _is_park else ''
        # 持有天數:賣出列才有(買入列 NULL → 顯「—」)
        hd = t.get("hold_days")
        hd_s = f"{int(hd)}" if hd is not None else "—"
        tr_rows.append(
            f'<tr class="sim-tr-row"{park_attr}{hidden}><td>{esc(d)}</td>'
            f'<td>{esc(str(t.get("ticker") or ""))} {esc(str(t.get("name") or ""))}</td>'
            f'<td class="{side_cls}">{side_s}</td>'
            f'<td class="r">{int(shares) // 1000}</td>'
            f'<td class="r">{price if price is not None else "—"}</td>'
            f'<td class="r">{hd_s}</td>'
            f'<td>{esc(_sim_reason_label(t.get("reason")))}</td>'
            f'<td class="r {pnl_cls}">{pnl_s}</td></tr>'
        )
    _n_trades = len(_trades)
    _n_pages = (_n_trades + 19) // 20
    # 出手頻率(周轉率):個股出手次數(買+賣,排除停泊 ETF)/ 交易日數。
    _n_indiv = sum(1 for t in _trades if str(t.get("ticker") or "") != _PARK_TK)
    _n_days = len(series)
    _freq = (_n_indiv / _n_days) if _n_days else 0
    _freq_txt = (f'共 {_n_trades} 筆 · 個股 {_n_indiv} 筆 · '
                 f'平均每交易日 {_freq:.2f} 次出手')

    trades_html = (
        '<table class="sim-tr-tbl"><thead><tr><th>日期</th><th>標的</th><th>動作</th>'
        '<th class="r">張數</th><th class="r">價格</th><th class="r">持有天數</th>'
        '<th>理由</th><th class="r">損益(元)</th></tr></thead><tbody>'
        + ("".join(tr_rows) or '<tr><td colspan="8" class="muted-note">尚無交易</td></tr>')
        + '</tbody></table>'
    )
    # 00981A 停泊交易 toggle(預設 ON=顯示全部;OFF=只秀個股交易)
    park_toggle = (
        '<button type="button" class="sim-park-btn active" id="sim-park-btn" '
        'onclick="simToggle981(this)" '
        'title="關閉後只顯示個股交易,隱藏現金停泊 ETF(00981A)的進出">'
        '含 00981A 停泊交易</button>'
    )
    # 分頁列(總筆數 >20 才渲;前端 simRenderTrades 依 filter 動態算頁數)
    pager_html = ""
    if _n_pages > 1:
        pager_html = (
            '<div class="sim-pager" id="sim-pager">'
            '<button class="sim-pg-btn" type="button" data-dir="-1" '
            'onclick="simStepTradePage(-1)" disabled>‹ 上一頁</button>'
            '<span class="sim-pg-info" id="sim-pg-info">第 1 / ' + str(_n_pages)
            + ' 頁(共 ' + str(_n_trades) + ' 筆)</span>'
            '<button class="sim-pg-btn" type="button" data-dir="1" '
            'onclick="simStepTradePage(1)">下一頁 ›</button>'
            '</div>'
        )

    # ── 前五賺最多 / 前五賠最多個股(依 ticker 彙總 sell 的 pnl)──
    _by_ticker: dict[str, dict] = {}
    for t in _trades:
        if (t.get("side") or "").lower() != "sell":
            continue
        pnl = t.get("pnl")
        if pnl is None:
            continue
        tk = str(t.get("ticker") or "")
        if not tk:
            continue
        slot = _by_ticker.setdefault(tk, {"name": str(t.get("name") or ""), "pnl": 0.0, "n": 0})
        slot["pnl"] += float(pnl)
        slot["n"] += 1
    _ranked = sorted(_by_ticker.items(), key=lambda kv: kv[1]["pnl"], reverse=True)

    def _rank_rows(items):
        out = []
        for tk, v in items:
            pv = int(v["pnl"])
            cls = "up" if pv > 0 else ("down" if pv < 0 else "flat")
            out.append(
                f'<li class="sim-rank-row"><span class="sim-rank-tk">{esc(tk)} '
                f'{esc(v["name"])}</span>'
                f'<span class="sim-rank-pnl {cls}">{pv:+,}</span>'
                f'<span class="sim-rank-n">{v["n"]} 筆賣出</span></li>'
            )
        return "".join(out)

    winloss_html = ""
    if _ranked:
        winners = [kv for kv in _ranked[:5] if kv[1]["pnl"] > 0]
        losers = [kv for kv in _ranked[::-1][:5] if kv[1]["pnl"] < 0]
        winloss_html = (
            '<div class="card"><div class="sec">已實現損益排行 '
            '<span class="sim-daterange">依標的累計賣出損益(含現金停泊 ETF)</span></div>'
            '<div class="sim-winloss">'
            '<div class="sim-wl-col"><div class="sim-wl-h sim-wl-win">🏆 賺最多 Top 5</div>'
            '<ul class="sim-rank-list">'
            + (_rank_rows(winners) or '<li class="muted-note">尚無獲利了結</li>')
            + '</ul></div>'
            '<div class="sim-wl-col"><div class="sim-wl-h sim-wl-lose">💧 賠最多 Top 5</div>'
            '<ul class="sim-rank-list">'
            + (_rank_rows(losers) or '<li class="muted-note">尚無虧損了結</li>')
            + '</ul></div>'
            '</div></div>'
        )

    # ── 誠實方法論註記(必放;規則描述與 ingest 引擎「版本 C」同步,2026-06-14
    #    ingest 2596edb)──
    method_html = (
        '<div class="sim-method"><span class="crash-banner-icon">⚠️</span>'
        '<div class="crash-banner-txt">'
        '<b>這是模擬,不是實盤。</b>'
        '初始資金 300 萬元(現股),已計手續費 0.1425% 與證交稅 0.3%;'
        '成交採「觸價限價」模型 —— 開盤跳空超過掛價就視為買不到,不會用想像價成交。'
        '策略規則:選股雷達「交集股」(命中 ≥2 條件)再篩出「有成長(月營收/EPS '
        'YoY 正)、未過度延伸(離月線 &lt;10%)、非追高(當日漲幅 &lt;3%)、非爆量」者為'
        '候選,離一年高點最遠者優先;前一日收盤 ~ +3% 區間掛單進場(分批,跳空追高'
        '只進 1/3);獲利達 +3%/+6% 分段加碼並上移停損;停損 -7%(收盤跌破隔日出);'
        '獲利單峰值回落 10% 先出一半、剩餘跌破停損線或月線出場;大盤(^TWII)跌破'
        '月線(MA20)即停止新進場,閒置現金停泊主動式 ETF 00981A、站回月線隔日賣回'
        '轉個股(不強制出清現有部位)。'
        f'回測段(自 {esc(start_d)} 起)與每日更新段用同一套引擎計算,無事後挑選;'
        '數字含已實現 + 未實現損益。本頁僅為策略研究紀錄,不構成投資建議。'
        '</div></div>'
    )

    payload = json.dumps({"series": series}, ensure_ascii=False, separators=(",", ":"))
    return (
        '<div class="sim-page">'
        '<div class="card">'
        f'<div class="sec">📈 動能策略模擬器 <span class="sim-daterange">'
        f'{esc(start_d)} ~ {esc(end_d)} · 每晚 22:05 後更新</span></div>'
        + stats_html
        + '<div id="sim-nav-chart" class="sim-chart"></div>'
        '<div class="risk-chart-legend">'
        '<span><i style="background:#60a5fa"></i>策略淨值</span>'
        '<span><i style="background:#f59e0b"></i>加權指數</span>'
        '<span><i style="background:#10b981"></i>00981A(主動式 ETF)</span>'
        + ('<span><i style="background:#c084fc"></i>00991A(主動式 ETF)</span>'
           if _has991 else "")
        + f'<span class="sim-rebase-note">皆以 {esc(start_d)} = 100 重設基期'
        + (f'；00991A 資料截至 {esc(_last991_d)}' if _991_lag else "")
        + '</span>'
        '</div></div>'
        '<div class="card"><div class="sec">目前持股</div>' + pos_html + '</div>'
        + winloss_html
        + f'<div class="card"><div class="sec">交易明細 '
        f'<span class="sim-daterange">{esc(_freq_txt)}</span></div>'
        '<div class="sim-tr-ctrl">' + park_toggle + '</div>'
        '<div class="sim-tr-wrap">' + trades_html + '</div>' + pager_html + '</div>'
        + method_html
        + f'<script>window.IIA_TRADESIM={payload};</script>'
        '</div>'
    )


def build_industry_map_page(rows: list[dict],
                            stocks_info: dict | None = None,
                            supply_edges: list[dict] | None = None) -> str:
    """🗺️ 產業地圖(Q38 + Q39)— 焦點產業供應鏈「蜘蛛網」圖。
      - 節點 = 焦點產業;連線 = **焦點間供應鏈有向邊**(Q39 `industry_supply_edges`,
        ingest Gemini 推導;from=上游供應端 → to=下游需求端,箭頭指下游)。**非交集股**。
      - 節點發亮 = 該焦點今日成分股的「成交值加權平均漲跌幅」(紅=今日強 / 綠=弱 /
        灰空心=今日無成交資料);加權漲幅 ≥ hot_threshold 的焦點脈動光暈。
      - 點節點 → modal 展開該焦點 **同產業上中下游**(axis)→ 子產業 → 個股 + 星級。
    當日漲跌來自記憶體 `stocks_info`,用 ticker join,不另查 DB。供應鏈邊來自 Q39。"""
    if not rows:
        return ('<p class="muted-note">產業地圖資料載入失敗(Q38 無資料,'
                'ingest 產業地圖 cron 可能尚未跑)。</p>')

    esc = html_lib.escape
    si = stocks_info or {}
    HOT_THRESHOLD = 2.0          # 加權漲幅 ≥ 此值(且覆蓋足夠)→ 脈動「熱門」

    # ── 1. group rows → focus → axis(欄)→ sub_industry → companies ──
    # rows 已由 SQL 排好序(focus_name, axis_order, sub_order, rating_rank desc, ticker)。
    focuses: list[dict] = []
    fidx: dict[str, dict] = {}          # focus_tag → focus dict
    cross: dict[str, dict] = {}         # ticker → {name, hits:[{f, s}]}(跨產業 modal)

    for r in rows:
        ftag = r.get("focus_tag") or ""
        fname = r.get("focus_name") or ftag or "（未命名焦點）"
        axis = (r.get("axis") or "").strip() or "其他"
        axis_order = r.get("axis_order")
        try:
            axis_order = int(axis_order) if axis_order is not None else 9
        except (TypeError, ValueError):
            axis_order = 9
        axis_kind = r.get("axis_kind") or "other"
        sub = (r.get("sub_industry") or "").strip()
        desc = (r.get("description") or "").strip()
        ticker = (r.get("ticker") or "").strip()
        name = (r.get("stock_name") or "").strip()
        market = (r.get("market") or "TW").strip().upper()
        rr = r.get("rating_rank")
        try:
            rr = int(rr) if rr is not None else None
        except (TypeError, ValueError):
            rr = None

        f = fidx.get(ftag)
        if f is None:
            f = {"tag": ftag, "name": fname, "kind": axis_kind,
                 "cols": [], "_colidx": {}, "tks": {}}   # tks: ticker→market(去重)
            fidx[ftag] = f
            focuses.append(f)
        col = f["_colidx"].get(axis_order)
        if col is None:
            col = {"axis": axis, "order": axis_order, "kind": axis_kind,
                   "subs": [], "_subidx": {}}
            f["_colidx"][axis_order] = col
            f["cols"].append(col)
        srow = col["_subidx"].get(sub)
        if srow is None:
            srow = {"sub": sub, "desc": desc, "cos": []}
            col["_subidx"][sub] = srow
            col["subs"].append(srow)
        if ticker:
            srow["cos"].append({"t": ticker, "n": name, "m": market, "rr": rr})
            f["tks"][ticker] = market
            ce = cross.get(ticker)
            if ce is None:
                ce = {"n": name, "hits": [], "_seen": set()}
                cross[ticker] = ce
            if ftag not in ce["_seen"]:
                ce["_seen"].add(ftag)
                ce["hits"].append({"f": fname, "s": sub})

    for f in focuses:
        f["cols"].sort(key=lambda c: c["order"])

    # ── 2. per-focus 當日聚合(join stocks_info)──
    # chg_w = 成交值加權平均漲跌;cov = 有當日資料的成分股比例;tv = 總成交值;
    # movers = 今日漲幅前 3(tooltip)。沒對到任何今日資料 → chg=None(UI 顯空心灰,
    # 不假裝是冷區/下跌 —— 中性≠無資料)。
    def _focus_agg(f: dict):
        num = den = tv_sum = 0.0
        matched = 0
        movers = []
        for tk in f["tks"]:
            s = si.get(tk)
            if not s:
                continue
            chg = s.get("change_pct")
            if chg is None:
                continue
            tv = float(s.get("trading_value") or 0)
            matched += 1
            tv_sum += tv
            if tv > 0:
                num += chg * tv
                den += tv
            movers.append((tk, s.get("name") or tk, chg))
        total = len(f["tks"])
        if den > 0:
            chg_w = num / den
        elif matched:
            chg_w = sum(m[2] for m in movers) / matched
        else:
            chg_w = None
        movers.sort(key=lambda x: -x[2])
        return chg_w, (matched / total if total else 0.0), tv_sum, total, movers[:3]

    nodes = []
    for i, f in enumerate(focuses):
        chg_w, cov, tv_sum, total, movers = _focus_agg(f)
        nodes.append({
            "i": i,
            "name": f["name"],
            "kind": f["kind"],
            "chg": round(chg_w, 2) if chg_w is not None else None,
            "cov": round(cov, 2),
            "tv": round(tv_sum / 1e8, 1),     # 億元
            "n": total,
            "mv": [{"t": t, "n": n, "c": round(c, 1)} for t, n, c in movers],
        })

    # ── 3. 邊:焦點間供應鏈有向邊(Q39;from=上游 → to=下游,箭頭指下游)──
    # 取代舊的「交集股連線」—— 交集股只是「同一檔出現在兩題材」,不是供應鏈關係。
    # 邊由 ingest Gemini 從 industry_focus_map 階層 + 描述語意推導(industry_supply_edges)。
    tag2idx = {f["tag"]: i for i, f in enumerate(focuses)}
    edges = []      # [from_idx, to_idx, strength, relation]
    for e in (supply_edges or []):
        try:
            fa = int(e.get("from_focus_tag"))
            tb = int(e.get("to_focus_tag"))
        except (TypeError, ValueError):
            continue
        ia, ib = tag2idx.get(fa), tag2idx.get(tb)
        if ia is None or ib is None or ia == ib:
            continue
        try:
            st = int(e.get("strength"))
        except (TypeError, ValueError):
            st = 2
        st = max(1, min(3, st))
        rel = (e.get("relation") or "").strip()
        edges.append([ia, ib, st, rel])
    edges.sort(key=lambda e: e[2])   # 弱邊先畫,強邊壓上(視覺層次)

    # ── 4. 跨產業關聯 payload(modal 內個股點擊用;頁面層級「明星股」chip 已移除）──
    cross_payload = {}
    for tk, ce in cross.items():
        cross_payload[tk] = {"n": ce["n"], "h": ce["hits"]}

    n_focus = len(focuses)
    n_rows = len(rows)
    n_hot = sum(1 for nd in nodes
                if nd["chg"] is not None and nd["chg"] >= HOT_THRESHOLD
                and nd["cov"] >= 0.2)

    # ── 5. 每焦點階層 HTML → 隱藏 detail store(點節點才進 modal)──
    KIND_LABEL = {"supply_chain": "上下游", "benefit": "受惠層", "other": ""}
    detail_blocks = []
    subs_payload: dict[int, dict] = {}   # focus i → {name, all:[tw tk], subs:[{name,tickers}]}
    for i, f in enumerate(focuses):
        kind_lbl = KIND_LABEL.get(f["kind"], "")
        kind_html = (f'<span class="im-kind">{kind_lbl}</span>' if kind_lbl else "")
        cols_html = []
        sub_idx = 0                       # 該焦點內的子產業流水序(對應 subs_payload.subs)
        f_subs = []                       # 子產業趨勢圖用:[{name, tickers}]
        f_all: dict[str, dict] = {}       # ticker → enriched obj(去重,焦點全部)

        def _tw_obj(co):
            """TW 個股 → {t,n,chg,close,tv}(今日報價來自 stocks_info,缺則 None)"""
            sinfo = si.get(co["t"]) or {}
            _c = sinfo.get("change_pct")
            _p = sinfo.get("close_price")
            _v = sinfo.get("trading_value")
            return {
                "t": co["t"], "n": co["n"],
                "chg": round(_c, 2) if isinstance(_c, (int, float)) else None,
                "close": round(_p, 2) if isinstance(_p, (int, float)) else None,
                "tv": round(float(_v) / 1e8, 1) if _v else 0.0,
            }

        for col in f["cols"]:
            ax_cls = f'ax-{col["order"]}' if 1 <= col["order"] <= 6 else "ax-9"
            subs_html = []
            for s in col["subs"]:
                cos_html = []
                tw_objs = []
                for co in s["cos"]:
                    rr = co["rr"]
                    star = ('<span class="im-star">' + ("★" * (rr + 1)) + '</span>'
                            if isinstance(rr, int) and rr >= 0 else "")
                    us = " us" if co["m"] == "US" else ""
                    us_tag = '<span class="im-us">美</span>' if co["m"] == "US" else ""
                    cos_html.append(
                        f'<button type="button" class="im-co{us}" '
                        f'onclick="imShowCross(\'{esc(co["t"])}\')" '
                        f'title="看 {esc(co["n"])} 出現的所有焦點">'
                        f'{us_tag}<span class="im-tk">{esc(co["t"])}</span> '
                        f'{esc(co["n"])}{star}</button>'
                    )
                    if co["m"] != "US" and str(co["t"]).isdigit():
                        o = _tw_obj(co)
                        tw_objs.append(o)
                        f_all.setdefault(o["t"], o)
                desc_html = (f'<div class="im-desc">{esc(s["desc"])}</div>'
                             if s["desc"] else "")
                sub_name = esc(s["sub"]) if s["sub"] else "—"
                # 子產業標題可點 → 上方趨勢圖切到該子產業(只在有 TW 個股時可點)
                if tw_objs:
                    head = (f'<button type="button" class="im-subname im-sub-pick" '
                            f'data-sub="{sub_idx}" onclick="imPickSub({i},{sub_idx})">'
                            f'{sub_name}<span class="im-sub-go">📈</span></button>')
                    f_subs.append({"name": s["sub"] or "—", "tickers": tw_objs})
                    sub_idx += 1
                else:
                    head = f'<div class="im-subname im-subname-flat">{sub_name}</div>'
                subs_html.append(
                    f'<div class="im-sub">{head}'
                    f'{desc_html}<div class="im-cos">{"".join(cos_html)}</div></div>'
                )
            cols_html.append(
                f'<div class="im-axiscol"><span class="im-axislabel {ax_cls}">'
                f'{esc(col["axis"])}</span>{"".join(subs_html)}</div>'
            )
        kind_head = (f'<div class="im-focus-meta">{kind_html}</div>'
                     if kind_html else "")
        detail_blocks.append(
            f'<div class="im-focus" id="imf-{i}" data-name="{esc(f["name"])}">'
            f'{kind_head}<div class="im-axes">' + "".join(cols_html) + '</div></div>'
        )
        subs_payload[i] = {
            "name": f["name"],
            "all": sorted(f_all.values(), key=lambda o: -(o["tv"] or 0)),
            "subs": f_subs,
        }

    n_edges = len(edges)
    H = []
    # ── 說明 banner ──
    H.append(
        '<div class="im-intro">'
        '<b>🗺️ 產業地圖</b>'
        f'<span class="im-intro-meta">{n_focus} 個焦點產業 · {n_edges} 條供應鏈關係 · '
        f'今日 <b>{n_hot}</b> 個焦點明顯走強</span>'
        '<p class="im-intro-desc">每個圓點是一個焦點產業,圓點之間的<b>箭頭 = 供應鏈上下游</b>'
        '(箭頭由<b>上游供應端</b>指向<b>下游需求端</b>,如「ABF 載板 → 先進封裝」);滑過線看關係說明。'
        '圓點<b>今天愈紅愈亮 = 該題材今日資金愈強</b>(成交值加權漲跌幅),灰色空心 = 今日無成交資料。'
        '<b>點圓點</b>展開該焦點<b>內部</b>的上中下游 / 受惠層與成分股。</p>'
        '</div>'
    )
    # ── 圖例(標尺,不 dump 裸值)──
    H.append(
        '<div class="im-legend">'
        '<span class="im-leg-item"><span class="im-leg-arrow">→</span>供應鏈:上游指向下游</span>'
        '<span class="im-leg-item"><i class="im-leg-dot im-leg-up"></i>今日走強(愈紅愈亮)</span>'
        '<span class="im-leg-item"><i class="im-leg-dot im-leg-flat"></i>今日持平</span>'
        '<span class="im-leg-item"><i class="im-leg-dot im-leg-down"></i>今日走弱(綠)</span>'
        '<span class="im-leg-item"><i class="im-leg-dot im-leg-na"></i>今日無成交資料</span>'
        '<span class="im-leg-item im-leg-size">圓點大小 = 成交熱度</span>'
        f'<span class="im-leg-item im-leg-hot">脈動光暈 = 加權漲幅 ≥ {HOT_THRESHOLD:.0f}%</span>'
        '</div>'
    )
    # ── 蜘蛛網圖容器(app.js _initIndmapGraph lazy render)──
    H.append('<div id="im-graph" class="im-graph"><div class="im-graph-hint">'
             '載入關聯圖中…</div></div>')

    # ── 隱藏 detail store(點節點 → imOpenFocus 取 innerHTML 進 modal)──
    H.append('<div id="im-detail-store" hidden>' + "".join(detail_blocks) + '</div>')

    graph_payload = json.dumps(
        {"nodes": nodes, "edges": edges, "hot": HOT_THRESHOLD},
        ensure_ascii=False, separators=(",", ":"))
    cross_json = json.dumps(cross_payload, ensure_ascii=False, separators=(",", ":"))
    subs_json = json.dumps(subs_payload, ensure_ascii=False, separators=(",", ":"))
    H.append(f'<script>window.IIA_INDMAP_GRAPH={graph_payload};'
             f'window.IIA_INDMAP_CROSS={cross_json};'
             f'window.IIA_INDMAP_SUBS={subs_json};</script>')

    return '<div class="im-page">' + "".join(H) + '</div>'


def build_catalyst_html(events: list[dict], stocks_info: dict | None = None) -> str:
    if not events:
        return ('<div class="cal-empty">'
                '前 2 週 ~ 後 3 週區間無已知重要事件（每日 07:30 自動更新）</div>')

    from datetime import date as _date_cls, datetime as _dt_cls

    def _to_date(v):
        if isinstance(v, _date_cls) and not isinstance(v, _dt_cls):
            return v
        if isinstance(v, _dt_cls):
            return v.date()
        if isinstance(v, str):
            return _dt_cls.fromisoformat(v.replace("Z", "+00:00")).date()
        return None

    by_date: dict = collections.OrderedDict()
    for ev in events:
        d = _to_date(ev["event_date"])
        if d is None:
            continue
        by_date.setdefault(d, []).append(ev)

    today = _date_cls.today()
    day_html = []
    for d, evs in by_date.items():
        date_label = f"{d.month}/{d.day} {_WEEKDAY_TW[d.weekday()]}"
        day_cls = "cal-day"
        if d < today:
            day_cls += " past"
        elif d == today:
            day_cls += " today"
            date_label += " · 今天"

        chips = []
        for ev in evs:
            imp = ev.get("importance", 2)
            typ = ev["event_type"]
            cls = f"cal-ev cal-{typ}"
            if imp >= 3:
                cls += " imp-3"
            tk = ev.get("ticker") or ""
            has_preview = bool((ev.get("preview_text") or "").strip())

            if typ == "earnings" and tk:
                name = ""
                if stocks_info:
                    info = stocks_info.get(tk) or {}
                    name = (info.get("name") or "").strip()
                label = f"{tk} {name}".strip() + " 法說"
            else:
                label = ev["title"]

            data_attr = f' data-ticker="{html_lib.escape(tk)}"' if tk else ""
            if has_preview:
                cls += " has-preview"
                chips.append(
                    f'<span class="{cls}"{data_attr} '
                    f'onclick="showCatalystModal({int(ev["id"])})">'
                    f'{html_lib.escape(label)} 📝</span>'
                )
            else:
                chips.append(f'<span class="{cls}"{data_attr}>{html_lib.escape(label)}</span>')

        # preview_text 從 inline 展開改為 art-modal 彈窗(2026-05-19):
        # 點 chip → showCatalystModal(id) → 拿 catalystModalData[id]/Titles[id] 渲染。
        # inline expandable div 已廢,留 has-preview class 給 chip 視覺提示(📝)。
        day_html.append(
            f'<div class="{day_cls}"><div class="cal-date">{date_label}</div>'
            f'<div class="cal-events">{"".join(chips)}</div></div>'
        )
    return '<div class="cal-list">' + "".join(day_html) + "</div>"


# ── 主動式 ETF tab(2026-05-20 對應 ingest f5faa21) ──────────────────────────

_AETF_ACTION_MAP = {
    "add":    ("加碼", "aetf-chip-add"),
    "reduce": ("減碼", "aetf-chip-reduce"),
    "new":    ("新增", "aetf-chip-new"),
    "exit":   ("清倉", "aetf-chip-exit"),
}


def _aetf_action_chip(action: str | None) -> str:
    if not action:
        return ""
    cfg = _AETF_ACTION_MAP.get(action)
    if not cfg:
        return ""
    label, cls = cfg
    return f'<span class="aetf-chip {cls}">{label}</span>'


def _aetf_lots_chg_html(lots_chg: float | int | None, has_baseline: bool = True) -> str:
    """渲染張數變化:
    - has_baseline=False(該 ETF DB 只有 1 day holdings) → 顯「—」灰字
    - lots_chg=0 / None 且 has baseline → 顯空字串
    - +N / -N 紅綠
    """
    if not has_baseline:
        return '<span class="aetf-chg-na">—</span>'
    if not lots_chg:
        return ""
    if lots_chg > 0:
        return f'<span class="aetf-chg-up">+{int(lots_chg):,} 張</span>'
    return f'<span class="aetf-chg-down">{int(lots_chg):,} 張</span>'


def _aetf_render_modal_body(etf_rows: list, stock_meta_entry: dict | None) -> str:
    """個股 modal body:持股主動式 ETF 表(2026-05-20 取代既有 intro + analyst)。
    etf_rows: 已過濾的 list[dict],含 etf_code/short_name/issuer/aum_ntd/lots/lots_chg/
              market_value_ntd/action(從 reverse-index of Q19 而來)
    stock_meta_entry: stock_meta[ticker] 或 None,用來算 pct_of_float
    """
    if not etf_rows:
        return '<p class="muted-note">本檔目前無主動 ETF 持有</p>'

    shares_out = None
    if stock_meta_entry and stock_meta_entry.get("shares_outstanding"):
        try:
            shares_out = float(stock_meta_entry["shares_outstanding"])
        except (TypeError, ValueError):
            shares_out = None

    # 統計 bar
    total_count = len(etf_rows)
    total_mv = sum(float(r.get("market_value_ntd") or 0) for r in etf_rows)
    sum_pct = 0.0
    for r in etf_rows:
        lots = r.get("lots") or 0
        if shares_out and lots:
            sum_pct += (lots * 1000.0) / shares_out * 100

    def _pct_for_row(r):
        lots = r.get("lots") or 0
        if not shares_out or not lots:
            return None
        return (lots * 1000.0) / shares_out * 100

    body_rows = []
    all_no_baseline = True
    for r in etf_rows:
        row_baseline = bool(r.get("has_baseline"))
        if row_baseline:
            all_no_baseline = False
        etf_label = r.get("short_name") or r["etf_code"]
        issuer = r.get("issuer") or ""
        lots = int(r.get("lots") or 0)
        mv = float(r.get("market_value_ntd") or 0)
        pct = _pct_for_row(r)
        pct_str = f"{pct:.3f}%" if pct is not None else "—"
        mv_str = f"{mv/1e8:.2f} 億" if mv else "—"
        chg_html = _aetf_lots_chg_html(r.get("lots_chg"), has_baseline=row_baseline)
        chip = _aetf_action_chip(r.get("action")) if row_baseline else ""
        body_rows.append(
            "<tr>"
            f'<td class="aetf-etf-cell"><span class="aetf-etf-code">{html_lib.escape(str(etf_label))}</span>'
            f' <span class="aetf-etf-issuer">{html_lib.escape(issuer)}</span></td>'
            f'<td class="r">{mv_str} <span class="aetf-lots-sub">({lots:,} 張)</span> {chg_html}</td>'
            f'<td class="r">{pct_str}</td>'
            f'<td class="c">{chip}</td>'
            "</tr>"
        )

    # 若全部 row 都沒 baseline,modal 頂部加警示
    baseline_warn = (
        '<p class="aetf-no-baseline-note">⚠ 各 ETF 目前只有 1 天 holdings,'
        '無前一交易日 baseline 可比較動作。等下次 cron 跑後才會顯示。</p>'
        if all_no_baseline else ""
    )

    # 各 ETF 的 data_date 可能不同(極少數情況某 ETF 當日 cron 失敗,前日資料殘留)
    # → 取 max。row 內 data_date 來自 Q19 latest CTE。
    _dates = [d for d in (_aetf_date_fmt(r.get("data_date")) for r in etf_rows) if d]
    latest_data_date = max(_dates) if _dates else None
    date_line = (
        f'<p class="aetf-modal-date"><span class="muted">持股更新</span> {latest_data_date}</p>'
        if latest_data_date else ""
    )

    return (
        '<div class="aetf-section">'
        '<h3 class="aetf-modal-hdr">持股主動式 ETF</h3>'
        + date_line
        + baseline_warn +
        '<div class="aetf-stats">'
        f'<div><span class="muted">總檔數</span> <b>{total_count}</b> 檔</div>'
        f'<div><span class="muted">總持股市值</span> <b>{total_mv/1e8:.2f}</b> 億</div>'
        f'<div><span class="muted">佔個股流通</span> <b>{sum_pct:.3f}</b>%</div>'
        '</div>'
        '<table class="aetf-table">'
        '<thead><tr><th>ETF</th><th class="r">持股市值(張數變化)</th>'
        '<th class="r">佔流通</th><th class="c">動作</th></tr></thead>'
        f"<tbody>{''.join(body_rows)}</tbody>"
        '</table>'
        '</div>'
    )


def _aetf_f(v):
    """DB NUMERIC 經 db-proxy JSON 反序列化可能是 str / Decimal,統一轉 float / None。"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _aetf_date_fmt(v):
    """db-proxy (npm:postgres) 把 DATE 序列化成 ISO datetime string
    `2026-05-27T00:00:00.000Z`;切前 10 碼回 YYYY-MM-DD。同時兼容 date 物件。"""
    if not v:
        return None
    if hasattr(v, "isoformat"):
        s = v.isoformat()
    else:
        s = str(v)
    return s[:10] if len(s) >= 10 else s


_TAIPEI_TZ = timezone(timedelta(hours=8))


def _fmt_data_stamp(dt) -> str | None:
    """把 db-proxy 回來的 timestamptz(已被 db._coerce 轉成 tz-aware datetime,
    UTC)換算台北時間並格式化成 YYYY/MM/DD HH:MM:SS。非 datetime 回 None。"""
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_TAIPEI_TZ).strftime("%Y/%m/%d %H:%M:%S")


def _taipei_date(dt) -> str | None:
    """timestamptz → 台北日期 YYYY-MM-DD(供主動 ETF「資料已更新 n/total」badge
    判定「該檔今天有沒有公布」用 updated_at,而非持股截止日 holding_date —— 部分
    發行商結構性標 T-1 持股日,用 holding_date 比對會永遠少算一檔)。"""
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_TAIPEI_TZ).strftime("%Y-%m-%d")


def _stamp_badge(ts_str: str | None, label: str = "資料更新") -> str:
    """各頁右上「資料最後更新時間」badge。ts_str 為 None(查不到 / 空表)時不渲染。"""
    return _stamp_badge_multi([(label, ts_str)])


def _stamp_badge_multi(pairs: list[tuple[str, str | None]]) -> str:
    """多組 (label, 時間) 併成同一行右上 badge(用 · 分隔);全空則不渲染。"""
    items = [(lbl, ts) for lbl, ts in pairs if ts]
    if not items:
        return ""
    inner = '<span class="ds-sep">·</span>'.join(
        f'<span class="ds-label">{lbl}</span><time>{ts}</time>' for lbl, ts in items)
    return f'<div class="data-stamp" title="資料寫入時間（台北時間）">{inner}</div>'


def _aetf_money(v) -> str:
    """NTD 金額簡記:≥1億 顯 X.X億、≥1萬 顯 X 萬、否則整數。帶正負號。"""
    if not v:
        return "0"
    sign = "+" if v > 0 else "−"
    a = abs(v)
    if a >= 1e8:
        return f"{sign}{a/1e8:.1f}億"
    if a >= 1e4:
        return f"{sign}{a/1e4:.0f}萬"
    return f"{sign}{a:.0f}"


def _build_aetf_trend(trend: list[dict], update_badge: str = "") -> str:
    """每日跨 ETF 加減碼金額趨勢:server-render CSS 長條圖(上紅=加碼、下綠=減碼)。
    標題行右側內嵌「資料已更新 n/total」badge。retention ~14 天,延長後累積到一個月。"""
    hdr = (f'<div class="aetf-section-hdr aetf-trend-hdr">'
           f'<span>每日加減碼趨勢(上紅加碼 / 下綠減碼)</span>{update_badge}</div>')
    if not trend:
        return ('<div class="aetf-trend">' + hdr
                + '<p class="muted-note">尚無足夠多日持股資料(需 ≥2 個交易日 baseline)。</p></div>')
    _mx = max((max(abs(d["add"]), abs(d["red"])) for d in trend), default=1) or 1
    cols = []
    for d in trend:
        up = d["add"] / _mx * 100
        dn = abs(d["red"]) / _mx * 100
        mmdd = d["d"][5:].replace("-", "/") if len(d["d"]) >= 10 else d["d"]
        # data-add/-red(原始 NTD)供 app.js hover:畫對應 Y 軸的虛線 + 顯實際金額
        cols.append(
            f'<div class="atr-col" data-d="{mmdd}" '
            f'data-add="{int(d["add"])}" data-red="{int(d["red"])}">'
            f'<div class="atr-up"><i style="height:{up:.1f}%"></i></div>'
            f'<div class="atr-dn"><i style="height:{dn:.1f}%"></i></div>'
            f'<div class="atr-d">{mmdd}</div></div>')
    # Y 軸刻度(億元):bars 上下各 46px(共 92px)對應 ±_mx,零線在中段。刻度 5 格
    # +max / +max/2 / 0 / −max/2 / −max,億元值即時從 _mx 算(調參或資料變動自動跟著)。
    def _axe(v: float) -> str:
        if abs(v) < 5e6:          # < 0.05 億 一律當 0(避免 +0.0億 噪音)
            return "0"
        return f"{'+' if v > 0 else '−'}{abs(v) / 1e8:.1f}億"
    _ticks = [_mx, _mx / 2, 0.0, -_mx / 2, -_mx]
    axis = ('<div class="atr-axis"><div class="atr-scale">'
            + "".join(f'<span>{_axe(v)}</span>' for v in _ticks)
            + '</div></div>')
    # guide(對應 Y 軸的虛線,加碼紅/減碼綠)+ vtip(實際金額)由 app.js `_initAetfTrend`
    # 於 hover 時定位顯示;放在 .atr-plot(不隨 bars 橫向捲動)層級。
    overlay = ('<div class="atr-guide atr-guide-add" hidden></div>'
               '<div class="atr-guide atr-guide-red" hidden></div>'
               '<div class="atr-vtip" hidden></div>')
    return (
        '<div class="aetf-trend">' + hdr
        + f'<div class="atr-plot">{axis}<div class="atr-bars">{"".join(cols)}</div>{overlay}</div>'
        '</div>'
    )


def build_active_etf_page(etf_list: list, holdings_by_etf: dict[str, list],
                          trend: list[dict] | None = None) -> str:
    """主動式 ETF 頁:每日加減碼趨勢圖(含資料已更新 badge)→ 橫排 sub-tab(按 AUM
    desc 一檔一 tab)+ 各 tab content:ETF 資訊 bar / 今日異動 / 全持股。
    """
    if not etf_list:
        return '<p class="muted-note">尚無主動式 ETF 資料</p>'

    panes = []
    for i, etf in enumerate(etf_list):
        code = etf["etf_code"]
        active = " active" if i == 0 else ""
        aum_b = float(etf.get("aum_ntd") or 0) / 1e8

        holdings = holdings_by_etf.get(code, [])
        # Normalize Decimal/str → float once (DB NUMERIC 經 JSON 變 str)
        for h in holdings:
            for k in ("lots", "prev_lots", "lots_chg", "weight_pct", "market_value_ntd"):
                if k in h:
                    h[k] = _aetf_f(h[k])
        # has_baseline:Q19 v2 每 row 同值,任取 first;空 holdings 視為無 baseline
        etf_has_baseline = bool(holdings and holdings[0].get("has_baseline"))
        # Today 仍持有(lots > 0);其他 action=exit 走異動 row
        today_holds = [h for h in holdings if (h.get("lots") or 0) > 0]
        today_holds.sort(key=lambda h: -(h.get("weight_pct") or 0))
        adds    = [h for h in holdings if h.get("action") == "add"]
        reduces = [h for h in holdings if h.get("action") == "reduce"]
        news    = [h for h in holdings if h.get("action") == "new"]
        exits   = [h for h in holdings if h.get("action") == "exit"]

        # 頂部 bar
        nav_per = etf.get("nav_per_unit")
        listing = etf.get("listing_date")
        if listing and hasattr(listing, "isoformat"):
            listing = listing.isoformat()
        # 持股更新時間:優先用該 ETF 自己的 updated_at 時間戳(各家公布時間不同),
        # 無則回退持股日期。
        updated_ts = etf.get("updated_ts")
        data_date = _aetf_date_fmt(etf.get("data_date"))
        _upd = updated_ts or data_date
        bar_html = (
            '<div class="aetf-info">'
            f'<span class="aetf-name">{html_lib.escape(etf.get("etf_name") or code)}</span>'
            f'<span class="aetf-meta"><span class="muted">AUM</span> <b>{aum_b:.0f} 億</b></span>'
            + (f'<span class="aetf-meta"><span class="muted">NAV</span> <b>{float(nav_per):.2f}</b></span>' if nav_per else '')
            + (f'<span class="aetf-meta"><span class="muted">上市</span> {listing}</span>' if listing else '')
            + (f'<span class="aetf-meta aetf-data-date"><span class="muted">持股更新</span> <b>{_upd}</b></span>' if _upd else '')
            + '</div>'
        )

        # 異動 4 區
        def _chg_chip(h, css):
            tk = h.get("ticker") or ""
            nm = (h.get("name") or "")[:10]
            chg = _aetf_lots_chg_html(h.get("lots_chg"))
            # 外層 attribute 用 ' 包,內層 json.dumps 用 " 避免引號嵌套撞 SyntaxError
            return (
                f'<span class="aetf-chg-pill {css}" '
                f"onclick='showArtModal({json.dumps(tk)},{json.dumps(nm)},event)' "
                f'role="button" tabindex="0">'
                f'<span class="aetf-cp-tk">{html_lib.escape(_disp_ticker(tk))}</span>'
                f'<span class="aetf-cp-nm">{html_lib.escape(nm)}</span>'
                f'{chg}'
                f'</span>'
            )

        def _chg_row(title, items, css):
            if not items:
                return ""
            chips = "".join(_chg_chip(h, css) for h in items[:30])
            return (
                '<div class="aetf-chg-row">'
                f'<span class="aetf-chg-label">{title} ({len(items)})</span>'
                f'{chips}'
                '</div>'
            )

        if etf_has_baseline:
            chg_inner = (
                _chg_row("🔼 加碼", adds, "add")
                + _chg_row("🔽 減碼", reduces, "reduce")
                + _chg_row("🆕 新增", news, "new")
                + _chg_row("🚪 清倉", exits, "exit")
            )
            chg_html = (
                f'<div class="aetf-changes">{chg_inner}</div>'
                if chg_inner else '<p class="muted-note">最近一個交易日無持股異動</p>'
            )
        else:
            # 無 baseline:DB 內該 ETF 只有 1 day holdings(首次 cron 寫入),
            # 沒前一天可比較動作 → 4 異動分區跳過 + tab 頂部警示。
            chg_html = (
                '<p class="aetf-no-baseline-note">⚠ 該 ETF 只有 1 天持股 snapshot,'
                '無前一交易日 baseline 可比較動作。等下次 cron 跑後才會顯示加碼/減碼/新增/清倉。</p>'
            )

        # 全持股 table — 無 baseline 時 chip 不渲、lots_chg 跳過
        hold_rows = []
        for h in today_holds:
            tk = h.get("ticker") or ""
            nm = (h.get("name") or "")[:12]
            chip = _aetf_action_chip(h.get("action")) if etf_has_baseline else ""
            lots = int(h.get("lots") or 0)
            weight = float(h.get("weight_pct") or 0)
            # 外層 attribute 用 ' 包,內層 json.dumps 用 " 避免雙引號嵌套
            click = f"showArtModal({json.dumps(tk)},{json.dumps(nm)},event)"
            hold_rows.append(
                f"<tr class=\"aetf-hold-row\" onclick='{click}'>"
                f'<td><span class="aetf-h-tk">{html_lib.escape(_disp_ticker(tk))}</span> '
                f'<span class="aetf-h-nm">{html_lib.escape(nm)}</span></td>'
                f'<td class="r">{lots:,} 張</td>'
                f'<td class="r">{weight:.2f}%</td>'
                f'<td class="c">{chip}</td>'
                f'</tr>'
            )
        hold_table = (
            '<table class="aetf-table aetf-hold-table">'
            '<thead><tr><th>持股</th><th class="r">張數</th>'
            '<th class="r">權重</th><th class="c">動作</th></tr></thead>'
            f'<tbody>{"".join(hold_rows) or "<tr><td colspan=4 class=\"muted-note\">尚無持股資料</td></tr>"}</tbody>'
            '</table>'
        )

        panes.append(
            f'<div class="aetf-pane{active}" data-aetf-pane="{code}">'
            + bar_html
            + '<div class="aetf-section-hdr">今日異動</div>'
            + chg_html
            + '<div class="aetf-section-hdr">全持股</div>'
            + hold_table
            + '</div>'
        )

    # 「資料已更新 n/total」:client-side 即時算(每交易日 13:30 收盤後歸零,
    # 假日 / 週末不歸零 —— 見 app.js aetfUpdateBadge + IIA_TW_HOLIDAYS)。
    # server 端嵌每檔日期 payload + 初始值(JS 載入後依台北現在時間覆寫)。
    #
    # **用 updated_at 的台北日期(「今天公布了沒」),不用 holding_date(持股截止日)**
    # (2026-06-08 修):部分發行商結構性標 T-1 持股日(如 00988A 週一公布卻標週五
    # 持股),用 holding_date 比對全體 max 會讓它永遠少算一檔 → badge 卡死 9/10。
    # updated_at 才是「該檔今天有無刷新」的正確訊號(00988A 今日 updated_at=當日)。
    _dd_list = [d for d in (e.get("updated_date") for e in etf_list) if d]
    _total = len(etf_list)
    if _dd_list:
        _latest = max(_dd_list)
        _n_done = sum(1 for d in _dd_list if d == _latest)
        _done_cls = "aetf-done-full" if _n_done >= _total else "aetf-done-partial"
        _payload = json.dumps({"dates": _dd_list, "total": _total, "latest": _latest})
        update_badge = (
            f'<span class="aetf-update-badge {_done_cls}" id="aetf-update-badge" '
            f'title="持股日達當前交易日的 ETF 檔數;每交易日 13:30 收盤後歸零、隨各家公布回補">'
            f'資料已更新 <b>{_n_done}/{_total}</b>'
            + ("" if _n_done >= _total else f' · 尚有 {_total - _n_done} 檔待今日資料')
            + '</span>'
            + f'<script>window.IIA_AETF_UPDATE={_payload};'
            'if(window.aetfUpdateBadge)window.aetfUpdateBadge();</script>')
    else:
        update_badge = ""

    # 橫排 sub-tab(取代下拉選單;JS showAetfTab 已支援 .aetf-tab-btn[data-aetf])
    tab_btns = "".join(
        f'<button class="aetf-tab-btn{" active" if i == 0 else ""}" type="button" '
        f'data-aetf="{e["etf_code"]}" onclick="showAetfTab(\'{e["etf_code"]}\')">'
        f'{html_lib.escape(str(e.get("short_name") or e["etf_code"]))}</button>'
        for i, e in enumerate(etf_list)
    )
    return (
        _build_aetf_trend(trend or [], update_badge)
        + '<div class="aetf-section-hdr">各 ETF 持股明細</div>'
        + f'<div class="aetf-tabs">{tab_btns}</div>'
        + "".join(panes)
    )


# ── 焦點股 tab(2026-05-20)— 出量股 / 潛力股 ──────────────────────────────────

def _focus_stock_etf_cell(etf_rows: list) -> str:
    """個股的主動 ETF 動作 cell:持有檔數 + 加碼/減碼/清倉 count chip。"""
    held = [r for r in etf_rows if (r.get("lots") or 0) > 0]
    if not held and not etf_rows:
        return '<span class="muted">—</span>'
    n = len(held)
    adds    = sum(1 for r in etf_rows if r.get("action") == "add")
    reduces = sum(1 for r in etf_rows if r.get("action") == "reduce")
    exits   = sum(1 for r in etf_rows if r.get("action") == "exit")
    parts = [f'<span class="fs-etf-held">{n} 檔持有</span>']
    if adds:
        parts.append(f'<span class="aetf-chip aetf-chip-add">加碼 {adds}</span>')
    if reduces:
        parts.append(f'<span class="aetf-chip aetf-chip-reduce">減碼 {reduces}</span>')
    if exits:
        parts.append(f'<span class="aetf-chip aetf-chip-exit">清倉 {exits}</span>')
    return " ".join(parts)


def _is_growth_meta(meta: dict) -> bool:
    """成長股判定:月營收連 3 月 YoY > 0 + 近一季 4 損益科目金額 YoY 皆 > 0。
    NULL 視為不符合(缺資料不誤判)。今日 / 昨日重算共用。"""
    def _g(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    gp, oi = _g(meta.get("gross_profit_yoy")), _g(meta.get("operating_income_yoy"))
    pt, ni = _g(meta.get("pretax_income_yoy")), _g(meta.get("net_income_yoy"))
    return bool(
        meta.get("revenue_yoy_3m_all_positive") is True
        and gp is not None and gp > 0
        and oi is not None and oi > 0
        and pt is not None and pt > 0
        and ni is not None and ni > 0
    )


def _was_intersect_stock(hist: list[dict], meta: dict, as_of_date: str) -> bool:
    """某焦點股在 as_of_date(交易日)是否入選「交集股」= 站上季線且符合
    ≥ 2 條件(出量 / 潛力 / 新高 / 成長)。供潛力股 condition C 的「前一
    交易日入選交集股」判定。條件以 ticker_close_full 歷史 + stock_meta 快照
    重算(成長條件無逐日歷史 → 用現有快照近似);潛力用 A 或 B(C 恆為前哨
    且 matched 只記「潛力」→ 永不入交集股,故 A/B-only 即精確、無遞迴)。
    籌碼條件 (chip_signals) 不重算 → 此處不含 chip → 是 actual intersect 的
    下界 (under-estimate;一個 ticker 只靠 chip 跨 ≥2 的會被漏掉)。"""
    if not hist or not as_of_date:
        return False
    rows = [h for h in hist if h.get("d") and h["d"] <= as_of_date]
    day_row = next((h for h in rows if h["d"] == as_of_date), None)
    if not day_row or day_row.get("c") is None:
        return False
    day_close = day_row["c"]
    closes = [h["c"] for h in rows if h.get("c") is not None]
    if len(closes) < 60:
        return False  # 季線算不出 → 未確認站上,不算交集股
    ma60 = sum(closes[-60:]) / 60
    if not day_close > ma60:
        return False  # 全域季線過濾
    ma5  = sum(closes[-5:])  / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    prev = [h for h in rows if h["d"] != as_of_date and h.get("c") and h.get("v")]
    prev5, prev30 = prev[-5:], prev[-30:]
    avg5_tv = (sum(h["c"] * h["v"] for h in prev5) / 5) if len(prev5) == 5 else None
    avg30_tv = (sum(h["c"] * h["v"] for h in prev30) / 30) if len(prev30) == 30 else None
    day_tv = day_close * day_row["v"] if day_row.get("v") else None
    vol_mult = (day_tv / avg5_tv) if (day_tv and avg5_tv) else None
    is_volume = bool(vol_mult and vol_mult > 3)
    # 潛力 A:MA5 > MA10 > MA20 且 close < MA20 × 1.15
    is_potential_a = bool(ma5 > ma10 > ma20 and day_close < ma20 * 1.15)
    # 潛力 B:三均線糾結 + close > all MAs 但距離不太遠 + 近 5 日量 > 近 30 日 × 2
    _ma_set = [ma5, ma10, ma20]
    _ma_converged = ((max(_ma_set) - min(_ma_set)) / (sum(_ma_set) / 3)) < 0.025
    is_potential_b = bool(
        _ma_converged
        and day_close > max(_ma_set)
        and day_close <= ma20 * 1.05
        and avg5_tv and avg30_tv and avg5_tv > avg30_tv * 2
    )
    is_potential = is_potential_a or is_potential_b
    # 新高:as_of_date 盤中高 ≥ 過去(不含當日)252 日盤中高;high NULL 安全
    day_high = day_row.get("high")
    past52_high = [h["high"] for h in rows
                   if h["d"] != as_of_date and h.get("high") is not None][-252:]
    is_new_high = bool(day_high and past52_high and day_high >= max(past52_high))
    is_growth = _is_growth_meta(meta)
    return (is_volume + is_potential + is_new_high + is_growth) >= 2


async def _compute_yesterday_intersect(
    conn,
    ticker_close_full: dict[str, list[dict]],
    stock_meta: dict,
    today_str: str,
) -> set[str]:
    """重算「前一交易日」的交集股名單,供潛力股 condition B 判定。

    流程:從 ticker_close_full 推前一交易日 → 抓昨日 Q15 / Q16(沿用既有
    allowlist 模板,只換 rank_date 參數,免改 allowlist)→ detect_focus_clusters
    得昨日 focal union → 對每檔焦點股以歷史重算條件 → 符合交集股者入集合。
    任一步失敗回空集合(潛力股退化為純 condition A)。"""
    try:
        all_dates = sorted({h["d"] for hist in ticker_close_full.values()
                             for h in hist if h.get("d")})
        prev_days = [d for d in all_dates if d < today_str]
        if not prev_days:
            return set()
        prev_trading_day = prev_days[-1]

        seed_rows = await conn.fetch(
            "SELECT ticker FROM trading_rankings WHERE rank_date=$1 "
            "AND market='TW' AND extra->>'is_focus_seed' = 'true' ORDER BY ticker",
            prev_trading_day,
        )
        yest_seeds = [r["ticker"] for r in seed_rows]

        member_rows = await conn.fetch(
            "SELECT ticker, name, trading_value, change_pct, close_price, high, open, low, "
            "is_limit_up_30m, extra "
            "FROM trading_rankings WHERE rank_date=$1 AND market='TW' "
            "AND extra->>'is_focus_member' = 'true' ORDER BY ticker",
            prev_trading_day,
        )
        yest_members: dict[str, dict] = {}
        for r in member_rows:
            tk = r["ticker"]
            if _is_etf(tk, r["name"] or ""):
                continue
            extra = (json.loads(r["extra"]) if isinstance(r.get("extra"), str)
                     else (r.get("extra") or {}))
            yest_members[tk] = {
                "name": r["name"] or tk,
                "change_pct": (float(r["change_pct"])
                               if r["change_pct"] is not None else None),
                "trading_value": float(r["trading_value"] or 0),
                "rank": None,
                "limit_up": bool(extra.get("is_limit_up") or r.get("is_limit_up_30m")),
            }

        yest_clusters = detect_focus_clusters(yest_seeds, yest_members)
        # 2026-05-24 起昨日 intersect 也納入 sentinel(對齊新設計:sentinel
        # 等同評估全條件)。一檔昨日跌但符合 ≥2 條件的也是昨日交集股。
        yest_all = {s.ticker for c in yest_clusters
                    for s in list(c.focal) + list(getattr(c, 'sentinel', None) or [])}

        result: set[str] = set()
        for tk in yest_all:
            hist = ticker_close_full.get(tk)
            if hist and _was_intersect_stock(hist, stock_meta.get(tk, {}),
                                             prev_trading_day):
                result.add(tk)
        print(f"  yesterday intersect set ({prev_trading_day}): {len(result)} stocks")
        return result
    except Exception as exc:
        print(f"  ⚠ compute yesterday intersect failed: {exc}")
        return set()


def _is_bowl_breakout(hist, today_str, today_close, today_tv,
                      win=120, r2min=0.35, depth_min=0.13, volx=3.0) -> bool:
    """看高做低股(碗型底)— 回測最佳參數 win120·R²≥0.35·深≥0.13·量×3.0
    (Sharpe 0.39 全場最佳、大賺小賠 R≈11,1336 組密格交叉回測)。
    判定「今日」是否帶量突破碗型底頸線(左緣阻力):
      近 win 日收盤(今日在末根)二次擬合開口向上(a>0)、底部落在中段 30~70%、
      R²≥r2min(夠平滑像碗)、碗深(左緣-谷底)/左緣 ≥ depth_min;今日收盤由左緣
      下翻上,且今日成交金額 > 視窗內(不含今日)均成交金額 × volx。
    量 gate 用「成交金額」(close×volume)而非股數量 —— 對齊站上 出量股 定義、
    且回測 share-volume 版差異甚微。資料源 = ticker_close_full(Q13)+ 今日 stocks_info。
    """
    if not today_close or not today_tv:
        return False
    prev = [h for h in hist if h.get("d") != today_str
            and h.get("c") is not None and h.get("v") is not None]
    if len(prev) < win - 1:
        return False
    pw = prev[-(win - 1):]
    closes = [h["c"] for h in pw] + [today_close]
    n = len(closes)
    prev_close = closes[-2]
    xs = list(range(n))
    sx = sum(xs); sx2 = sum(x * x for x in xs); sx3 = sum(x ** 3 for x in xs); sx4 = sum(x ** 4 for x in xs)
    sy = sum(closes); sxy = sum(x * y for x, y in zip(xs, closes)); sx2y = sum(x * x * y for x, y in zip(xs, closes))
    A = [[sx4, sx3, sx2, sx2y], [sx3, sx2, sx, sxy], [sx2, sx, n, sy]]
    for ci in range(3):
        piv = A[ci][ci]
        if abs(piv) < 1e-9:
            return False
        A[ci] = [val / piv for val in A[ci]]
        for r in range(3):
            if r != ci:
                f = A[r][ci]; A[r] = [A[r][k] - f * A[ci][k] for k in range(4)]
    a, b, c0 = A[0][3], A[1][3], A[2][3]
    if a <= 0:
        return False
    vtx = -b / (2 * a)
    if not (n * 0.30 <= vtx <= n * 0.70):
        return False
    my = sy / n
    sst = sum((y - my) ** 2 for y in closes)
    ssr = sum((y - (a * x * x + b * x + c0)) ** 2 for x, y in zip(xs, closes))
    if (1 - ssr / sst if sst else 0) < r2min:
        return False
    rim = max(closes[:max(2, int(n * 0.2))]); bottom = min(closes)
    if (rim - bottom) / rim < depth_min:
        return False
    if not (prev_close <= rim < today_close):
        return False
    win_tv = [h["c"] * h["v"] for h in pw]
    if not win_tv:
        return False
    return today_tv > (sum(win_tv) / len(win_tv)) * volx


def build_focus_stock_page(
    focus_hl_clusters: list,
    stocks_info: dict,
    ticker_close_full: dict[str, list[dict]],
    stock_meta: dict,
    aetf_holdings_by_ticker: dict[str, list],
    today_str: str,
    yest_intersect_set: set[str],
    chip_signals: dict[str, dict] | None = None,
) -> str:
    """焦點股 tab:來源 = 熱門題材「焦點」(hl_sub)的 focal union。
    3 sub-tab(順序:交集股 / 出量股 / 潛力股):
    - 交集股:同時符合 2 項(含)以上條件,依符合條件數 desc(同數量再月線乖離 desc);多「符合條件」欄
    - 出量股:今日成交金額 > 前 5 交易日均(不含今日)× 2,依出量倍數 desc
    - 潛力股:condition A(多頭排列:MA5 > MA10 > MA20 且股價 < MA20×1.15)
      或 condition B(糾結突破:三均線糾結 + 股價站上所有均線但距離不太遠 +
      近 5 日量 > 近 30 日 × 2)或 condition C(回踩股:前一交易日入選交集股、
      今日跌逾 3.5% 但仍高於月線、且成交金額萎縮至前一交易日 ¼ 以下);
      C 股恆為前哨股,依月線乖離 desc
    全欄位 client-side 可點擊排序(ASC/DESC toggle)。
    """
    # focal = 焦點股(走 condition A + 全部條件);sentinel = 前哨股(今日跌
    # → chg ≤ -3),只為潛力股 condition B 評估,不參與其他 sub-tab。
    focal_to_clusters: dict[str, list[str]] = {}
    sentinel_to_clusters: dict[str, list[str]] = {}
    for c in (focus_hl_clusters or []):
        for s in c.focal:
            focal_to_clusters.setdefault(s.ticker, []).append(c.name)
        for s in (c.sentinel or []):
            sentinel_to_clusters.setdefault(s.ticker, []).append(c.name)
    sentinel_to_clusters = {t: cl for t, cl in sentinel_to_clusters.items()
                            if t not in focal_to_clusters}

    def _f(v):
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # per-ticker 計算 + condition 判定。focal 與 sentinel(前哨股)2026-05-24
    # 起一視同仁,等同評估全部條件(原本 sentinel 只限 potential C → 下跌股
    # 被排除在出量 / 新高 / 成長 / 籌碼 / 交集股之外;改為一致 → 下跌股的
    # 法人進場 / 成長 YoY / 等訊號也能進選股雷達)。
    cands: list[dict] = []
    # 籌碼股:散戶 / 大戶持股比「週減」的零界噪音緩衝(個百分點)。TDCC 集保
    # 級距金額換算的週變化有 ±0.1~0.3pp bucketing 噪音 → 週減須逾此值才認列。
    _HOLDER_NOISE = 0.3
    _scan = ([(t, cl, False) for t, cl in focal_to_clusters.items()]
             + [(t, cl, True) for t, cl in sentinel_to_clusters.items()])
    for tk, clusters, is_sentinel in _scan:
        info = stocks_info.get(tk, {})
        today_close = _f(info.get("close_price"))
        today_tv = _f(info.get("trading_value"))
        today_chg = _f(info.get("change_pct"))
        hist = ticker_close_full.get(tk, [])  # date asc
        prev = [h for h in hist
                if h.get("d") != today_str and h.get("c") and h.get("v")]
        prev5, prev30 = prev[-5:], prev[-30:]
        avg5_tv = (sum(h["c"] * h["v"] for h in prev5) / 5) if len(prev5) == 5 else None
        avg30_tv = (sum(h["c"] * h["v"] for h in prev30) / 30) if len(prev30) == 30 else None
        closes = [h["c"] for h in hist if h.get("c") is not None]
        ma5  = sum(closes[-5:])  / 5  if len(closes) >= 5  else None
        ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else None
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
        ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None
        ma20_bias = ((today_close - ma20) / ma20 * 100) if (today_close and ma20) else None
        vol_mult = (today_tv / avg5_tv) if (today_tv and avg5_tv) else None

        # 全域過濾:季線以下不做多 — 股價必須站上 60 日均(季線)才列入
        # 焦點股頁任一 sub-tab。MA60 算不出來(close 不足 60 筆)視為未確認
        # 站上季線,一併排除。
        if not (today_close and ma60 and today_close > ma60):
            continue

        meta = stock_meta.get(tk, {})
        # 潛力 condition A(多頭排列):MA5 > MA10 > MA20,且股價未脫離月線
        # (close < MA20 × 1.15)。
        is_potential_a = bool(
            ma5 and ma10 and ma20 and today_close
            and ma5 > ma10 > ma20
            and today_close < ma20 * 1.15
        )
        # 潛力 condition B(均線糾結突破):MA5 / MA10 / MA20 三線糾結
        # (max-min 相對均值 < 2.5%) + 股價站上所有均線但距離不太遠
        # (close ≤ MA20 × 1.05) + 近 5 日均成交金額 > 近 30 日均 × 2
        # (吸籌啟動 setup)。
        _MA_CONVERGE_PCT = 0.035   # 回測最佳(2026-06-04 從 0.025 放寬)
        _CLOSE_TIGHT_RATIO = 1.05
        _VOL_HEATING_MULT = 1.5    # 回測最佳(2026-06-04 從 2 放寬)
        _ma_set = [m for m in (ma5, ma10, ma20) if m is not None]
        _ma_converged = (
            len(_ma_set) == 3
            and ((max(_ma_set) - min(_ma_set)) / (sum(_ma_set) / 3)) < _MA_CONVERGE_PCT
        )
        is_potential_b = bool(
            _ma_converged and today_close
            and today_close > max(_ma_set)
            and today_close <= ma20 * _CLOSE_TIGHT_RATIO
            and avg5_tv and avg30_tv and avg5_tv > avg30_tv * _VOL_HEATING_MULT
        )
        # 潛力 condition C(回踩股):前一交易日入選交集股、今日跌逾 3.5%、
        # 仍高於月線(close > MA20)、且成交金額萎縮至前一交易日的 1/4 以下。
        # C 條件 chg < -3.5 自然只對 sentinel(focal chg > -3)發生;前一交易日
        # 成交金額 = 該股最近一筆非今日歷史的 close × volume。
        yest_tv = (prev[-1]["c"] * prev[-1]["v"]) if prev else None
        is_potential_c = bool(
            tk in yest_intersect_set
            and today_chg is not None and today_chg < -3.5
            and today_close and ma20 and today_close > ma20
            and today_tv and yest_tv and today_tv < yest_tv * 0.25
        )

        # 籌碼訊號(近 3 日外資/投信佔量% + 大戶/散戶持股週變);chip_signals
        # 已在 generate() 對齊 chip_history ∩ ticker_close_full 末 3 日算好。
        _chip = (chip_signals or {}).get(tk)
        chip_f3_pct = _chip["f3_pct"] if _chip else None
        chip_t3_pct = _chip["t3_pct"] if _chip else None
        chip_retail_chg = _chip.get("retail_chg") if _chip else None
        chip_big_chg = _chip.get("big_chg") if _chip else None

        # 潛力:A 多頭排列 OR B 糾結突破 OR C 回踩股。C 自然只對 sentinel 發生。
        is_potential = is_potential_a or is_potential_b or is_potential_c
        # 出量股:今日成交金額 > 前 5 交易日均 × 5(回測最佳,2026-06-04 從 ×3 調整)
        is_volume = bool(vol_mult and vol_mult > 5)
        # 看高做低股(碗型底)—— 新條件,2026-06-04 回測上線
        is_kgzd = _is_bowl_breakout(hist, today_str, today_close, today_tv)
        # 新高股:今日盤中觸及 52 週(~252 交易日)新高 — 今日盤中最高價
        # ≥ 過去 52 週(不含今日)最高盤中價。今日盤中高來自 trading_rankings
        # (stocks_info.high),baseline 來自 ticker_close_history.high。
        # high 缺值的列不計入(NULL 安全);歷史不足 252 筆則用掛牌以來最高。
        today_high = _f(info.get("high"))
        # 新高定義 2026-06-04 從 252 日(52週)調為 150 日(回測最佳)
        _past52_high = [h["high"] for h in hist
                        if h.get("d") != today_str and h.get("high") is not None][-150:]
        is_new_high = bool(today_high and _past52_high
                           and today_high >= max(_past52_high))
        # 成長股:月營收連 3 月 + 近一季 4 損益科目金額 YoY 皆 > 0
        is_growth = _is_growth_meta(meta)
        # 籌碼股(對齊附件三區;主力 / 前十大券商 4 條因 TWSE 付費券商
        # 分點資料無免費來源,捨棄):
        #   第1區(必須):散戶賣超 = 散戶持股比週減 > 0.3pp
        #   第2區(≥1):投信買超 ΣT3≥5%量 / 外資買超 ΣF3≥10%量 /
        #               大戶持股比週增 ≥1.5(大戶 = 持股 ≥5000萬;此即
        #               「籌碼鎖定率」—— 大戶吸籌)
        #   第3區(皆不可):外資賣超≤-10%量 / 投信賣超≤-5%量 /
        #               大戶持股比週減 > 0.3pp
        # 散戶 / 大戶週減用 _HOLDER_NOISE(0.3pp)緩衝濾 TDCC bucketing
        # 噪音;原第3區「散戶買超」排除已移除 —— 第1區強制散戶週減,該
        # 排除恆 false(死條件,2026-05-22 移除)。
        if _chip is None:
            is_chip = False
        else:
            _r1 = (chip_retail_chg is not None
                   and chip_retail_chg < -_HOLDER_NOISE)
            _r2 = (chip_t3_pct >= 0.05 or chip_f3_pct >= 0.10
                   or (chip_big_chg is not None and chip_big_chg >= 1.5))
            _r3 = (chip_f3_pct <= -0.10 or chip_t3_pct <= -0.05
                   or (chip_big_chg is not None
                       and chip_big_chg < -_HOLDER_NOISE))
            is_chip = bool(_r1 and _r2 and not _r3)

        # 早盤漲停股移除(2026-05-25):無 intraday tick 資料無法精準分 0930
        # 前後,日 OHLC 近似版誤判太多,user 決定移除整個 sub-tab + chip + 條件。

        matched: list[str] = []
        if is_kgzd:
            matched.append("看高做低")
        if is_volume:
            matched.append("出量")
        if is_potential:
            matched.append("潛力")
        if is_new_high:
            matched.append("新高")
        if is_growth:
            matched.append("成長")
        if is_chip:
            matched.append("籌碼")
        cands.append({
            "ticker": tk,
            "name": (info.get("name") or "")[:12],
            "today_tv": today_tv,
            "today_close": today_close,
            "vol_mult": vol_mult,
            "ma10": ma10, "ma20": ma20, "ma20_bias": ma20_bias,
            "pe": _f(meta.get("pe_ttm")),
            "peg": _f(meta.get("peg_ratio")),
            "peg_status": meta.get("peg_status"),
            "eps_yoy": _f(meta.get("eps_ttm_yoy")),
            # 三率 + YoY 方向(ingest 57c7e8b 起寫 stock_meta;dir 可能 NULL)
            "gross_margin": _f(meta.get("gross_margin")),
            "operating_margin": _f(meta.get("operating_margin")),
            "net_margin": _f(meta.get("net_margin")),
            "gm_dir": meta.get("gross_margin_yoy_dir"),
            "om_dir": meta.get("operating_margin_yoy_dir"),
            "nm_dir": meta.get("net_margin_yoy_dir"),
            # 營收增率(本身帶正負 → 正升負降)
            "revenue_mom": _f(meta.get("revenue_mom")),
            "revenue_yoy": _f(meta.get("revenue_yoy")),
            "clusters": clusters,
            "etf_rows": aetf_holdings_by_ticker.get(tk, []),
            "is_volume": is_volume, "is_potential": is_potential,
            "is_new_high": is_new_high, "is_growth": is_growth,
            "is_chip": is_chip, "chip_big_chg": chip_big_chg,
            "is_kgzd": is_kgzd,
            "matched": matched,
        })

    _by_bias = lambda c: -(c["ma20_bias"] if c["ma20_bias"] is not None else float("-inf"))
    # 交集股預設依「符合條件數」desc(多→少),同數量再依月線乖離 desc
    intersect_stocks = sorted(
        [c for c in cands if len(c["matched"]) >= 2],
        key=lambda c: (-len(c["matched"]), _by_bias(c)),
    )
    kgzd_stocks      = sorted([c for c in cands if c["is_kgzd"]], key=_by_bias)
    volume_stocks    = sorted([c for c in cands if c["is_volume"]],
                              key=lambda c: -c["vol_mult"])
    potential_stocks = sorted([c for c in cands if c["is_potential"]], key=_by_bias)
    new_high_stocks  = sorted([c for c in cands if c["is_new_high"]], key=_by_bias)
    growth_stocks    = sorted([c for c in cands if c["is_growth"]], key=_by_bias)
    # 籌碼股依大戶持股比週增 desc(大戶吸籌最多在前;None 排尾),同值再月線乖離 desc
    _chip_inf = float("-inf")
    chip_stocks = sorted(
        [c for c in cands if c["is_chip"]],
        key=lambda c: (-(c["chip_big_chg"] if c["chip_big_chg"] is not None else _chip_inf),
                       _by_bias(c)),
    )
    def _bias_cell(v):
        if v is None:
            return '<span class="muted">—</span>'
        cls = "up" if v > 0 else ("down" if v < 0 else "flat")
        sign = "+" if v > 0 else ""
        return f'<span class="{cls}">{sign}{v:.2f}%</span>'

    def _cluster_cell(names):
        # 點 chip → openThemeByName 開熱門題材 cluster chart modal;
        # stopPropagation 避免 bubble 到 row 的 showArtModal(個股 modal)
        # title 屬性 = hover 顯完整題材名(chip 本身 CSS 截斷顯 …)
        return "".join(
            f'<span class="fs-theme-chip" '
            f'title="{html_lib.escape(n)}" '
            f"onclick='event.stopPropagation();openThemeByName({json.dumps(n)})'>"
            f'{html_lib.escape(n)}</span>' for n in names
        ) or '<span class="muted">—</span>'

    # 三率 cell:數值 % + YoY 方向箭頭(up ▲紅 / down ▼綠 / flat — / NULL 無箭頭)
    def _margin_cell(val, yoy_dir):
        if val is None:
            return '<span class="muted">—</span>'
        if yoy_dir == "up":
            arrow = ' <span class="up">▲</span>'
        elif yoy_dir == "down":
            arrow = ' <span class="down">▼</span>'
        elif yoy_dir == "flat":
            arrow = ' <span class="flat">—</span>'
        else:  # NULL — yfinance 無季報,只顯數值
            arrow = ""
        return f"{val:.2f}%{arrow}"

    # PEG cell:status-aware 顯示。'ok_ttm'/'ok_q' 顯數字 + 計算法小標籤(TTM/季),
    # 配色 <1 綠(低估) / 1-1.5 灰(合理) / >1.5 紅(偏貴);其他 status 顯文字。
    def _peg_cell(c):
        st = c.get("peg_status")
        peg = c.get("peg")
        if st and st.startswith("ok_") and peg is not None and peg > 0:
            cls = "peg-low" if peg < 1 else ("peg-mid" if peg <= 1.5 else "peg-high")
            tag = "TTM" if st == "ok_ttm" else "季"
            return (f'<span class="{cls}" title="PEG = PE ÷ EPS YoY。<1 低估、≈1 合理、>1 偏貴;'
                    f'此值以{tag}法計算">{peg:.2f}<span class="peg-tag">{tag}</span></span>')
        if st == "eps_declining":
            return '<span class="muted" title="EPS YoY < 0(EPS 衰退,PEG 不適用)">EPS 衰退</span>'
        if st == "low_growth":
            return '<span class="muted" title="|EPS YoY| < 1% 或 |PEG| > 10 被 clip(低成長 / 異常)">低成長</span>'
        if st == "insufficient_history":
            return '<span class="muted" title="yfinance 季報資料不足,無法計算 PEG">—</span>'
        return '<span class="muted">—</span>'

    # 營收增率 cell:本身帶正負(正升 ▲紅 / 負降 ▼綠)
    def _rev_cell(val):
        if val is None:
            return '<span class="muted">—</span>'
        cls = "up" if val > 0 else ("down" if val < 0 else "flat")
        sign = "+" if val > 0 else ""
        arrow = " ▲" if val > 0 else (" ▼" if val < 0 else "")
        return f'<span class="{cls}">{sign}{val:.2f}%{arrow}</span>'

    _MATCH_CHIP_CLS = {"看高做低": "fs-mc-kgzd", "出量": "fs-mc-vol", "潛力": "fs-mc-pot",
                       "新高": "fs-mc-nh", "成長": "fs-mc-gr", "籌碼": "fs-mc-chip"}
    # 條件 → 短 key(交集股篩選列 data-cond / row data-matched 用)
    _MATCH_KEY = {"看高做低": "kgzd", "出量": "vol", "潛力": "pot", "新高": "nh", "成長": "gr",
                  "籌碼": "chip"}

    def _match_cell(matched):
        return "".join(
            f'<span class="fs-match-chip {_MATCH_CHIP_CLS.get(m, "")}">{m}</span>'
            for m in matched
        ) or '<span class="muted">—</span>'

    def _etf_held_count(etf_rows):
        return len([r for r in etf_rows if (r.get("lots") or 0) > 0])

    # column 配置:(label, sort-key, is-numeric, td-class)。
    # mode='volume' 插「出量倍數」、mode='intersect' 加「符合條件」。
    def _columns(mode):
        cols = [("標的", "tk", 0, ""), ("成交金額", "tv", 1, "r")]
        if mode == "volume":
            cols.append(("出量倍數", "volmult", 1, "r"))
        cols += [("月線乖離", "bias", 1, "r"), ("PE", "pe", 1, "r"),
                 ("PEG", "peg", 1, "r"),
                 ("毛利率", "gm", 1, "r"), ("營益率", "om", 1, "r"),
                 ("淨利率", "nm", 1, "r"),
                 ("營收月增", "rmom", 1, "r"), ("營收年增", "ryoy", 1, "r"),
                 ("隸屬題材", "theme", 1, ""), ("主動式 ETF", "etf", 1, "")]
        if mode == "intersect":
            cols.append(("符合條件", "match", 1, ""))
        return cols

    def _row(c, mode):
        tk, nm = c["ticker"], c["name"]
        click = f"showArtModal({json.dumps(tk)},{json.dumps(nm)},event)"
        pe = c["pe"]
        pe_str = f"{pe:.1f}" if (pe and pe > 0) else "—"
        # PEG sort key:ok_* 才用 peg_ratio 排,其他 status null → 排尾
        _ps = c.get("peg_status")
        peg_sort = c.get("peg") if (_ps and _ps.startswith("ok_") and c.get("peg") and c["peg"] > 0) else None
        tv = c["today_tv"] or 0
        bias = c["ma20_bias"]
        etf_n = _etf_held_count(c["etf_rows"])
        vm = c["vol_mult"]
        gm, om, nm = c["gross_margin"], c["operating_margin"], c["net_margin"]
        rmom, ryoy = c["revenue_mom"], c["revenue_yoy"]
        # data-* 給 client-side sortFsTable 用(數值欄缺值留空 → JS 排尾)
        attrs = (
            f'data-tk="{html_lib.escape(tk)}" '
            f'data-tv="{tv:.0f}" '
            f'data-bias="{f"{bias:.4f}" if bias is not None else ""}" '
            f'data-pe="{f"{pe:.4f}" if (pe and pe > 0) else ""}" '
            f'data-peg="{f"{peg_sort:.4f}" if peg_sort is not None else ""}" '
            f'data-gm="{f"{gm:.4f}" if gm is not None else ""}" '
            f'data-om="{f"{om:.4f}" if om is not None else ""}" '
            f'data-nm="{f"{nm:.4f}" if nm is not None else ""}" '
            f'data-rmom="{f"{rmom:.4f}" if rmom is not None else ""}" '
            f'data-ryoy="{f"{ryoy:.4f}" if ryoy is not None else ""}" '
            f'data-theme="{len(c["clusters"])}" '
            f'data-etf="{etf_n}" '
            f'data-volmult="{f"{vm:.4f}" if vm else ""}" '
            f'data-match="{len(c["matched"])}" '
            f'data-matched="{",".join(_MATCH_KEY.get(m, "") for m in c["matched"])}"'
        )
        # 品質濾網(交集股):策略模擬器(trade_sim 版本 C)的候選資格 ——
        # 含成長 + 月線乖離 <10% + 當日漲幅 <3% + 不爆量(vol_mult <2)四閘全過。
        # server 端先算好布林,前端只做顯隱(toggleFsQuality);任一欄缺值 →
        # 無法確認通過 → 視為不過(保守,= 策略實際能評估的標的)。
        if mode == "intersect":
            _chg = (stocks_info.get(tk) or {}).get("change_pct")
            qpass = (
                bool(c.get("is_growth"))
                and bias is not None and bias < 10
                and _chg is not None and _chg < 3
                and vm is not None and vm < 2
            )
            attrs += f' data-qpass="{1 if qpass else 0}"'
        tds = [
            # 標的 cell:用 _stk_pill(同熱門題材樣式,代號+名稱+股價(漲跌));
            # clickable=False — row 本身 onclick showArtModal 已 handle
            f'<td>{_stk_pill(tk, stocks_info, clickable=False)}</td>',
            f'<td class="r">{f"{tv/1e8:.0f} 億" if tv else "—"}</td>',
        ]
        if mode == "volume":
            tds.append(f'<td class="r"><b>{vm:.2f}×</b></td>' if vm else '<td class="r">—</td>')
        tds += [
            f'<td class="r">{_bias_cell(bias)}</td>',
            f'<td class="r">{pe_str}</td>',
            f'<td class="r">{_peg_cell(c)}</td>',
            f'<td class="r">{_margin_cell(gm, c["gm_dir"])}</td>',
            f'<td class="r">{_margin_cell(om, c["om_dir"])}</td>',
            f'<td class="r">{_margin_cell(nm, c["nm_dir"])}</td>',
            f'<td class="r">{_rev_cell(rmom)}</td>',
            f'<td class="r">{_rev_cell(ryoy)}</td>',
            f'<td>{_cluster_cell(c["clusters"])}</td>',
            f'<td>{_focus_stock_etf_cell(c["etf_rows"])}</td>',
        ]
        if mode == "intersect":
            tds.append(f'<td>{_match_cell(c["matched"])}</td>')
        return f"<tr class=\"fs-row\" {attrs} onclick='{click}'>{''.join(tds)}</tr>"

    def _table(rows, mode, empty_msg):
        if not rows:
            return f'<p class="muted-note">{empty_msg}</p>'
        ths = "".join(
            f'<th class="fs-th{(" " + cls) if cls else ""}" data-skey="{sk}" '
            f'data-snum="{num}" onclick="sortFsTable(this)">{label}'
            f'<span class="fs-sort-ind"></span></th>'
            for label, sk, num, cls in _columns(mode)
        )
        body = "".join(_row(c, mode) for c in rows)
        return (
            f'<table class="fs-table"><thead><tr>{ths}</tr></thead>'
            f'<tbody>{body}</tbody></table>'
        )

    int_html = _table(intersect_stocks, "intersect",
                      "今日無焦點股同時符合 2 項以上條件")
    kgzd_html = _table(kgzd_stocks, "kgzd",
                       "今日無焦點股形成碗型底並帶量突破頸線")
    vol_html = _table(volume_stocks, "volume",
                      "今日無焦點股出量(成交金額 > 前 5 日均 × 5)")
    pot_html = _table(potential_stocks, "potential",
                      "今日無焦點股符合潛力條件")
    nh_html  = _table(new_high_stocks, "newhigh",
                      "今日無焦點股盤中觸及 150 日新高")
    gr_html  = _table(growth_stocks, "growth",
                      "今日無焦點股符合成長條件(月營收連 3 月 + 4 損益科目金額 YoY 皆正)")
    chip_html = _table(chip_stocks, "chip", "今日無焦點股符合籌碼條件")

    nav_html = (
        '<div class="sub-tabs">'
        '<button class="sub-tab-btn active" data-fstab="int" type="button" '
        'onclick="showFocusStockTab(\'int\')">🎯 交集股</button>'
        '<button class="sub-tab-btn" data-fstab="vol" type="button" '
        'onclick="showFocusStockTab(\'vol\')">📊 出量股</button>'
        '<button class="sub-tab-btn" data-fstab="pot" type="button" '
        'onclick="showFocusStockTab(\'pot\')">🚀 潛力股</button>'
        '<button class="sub-tab-btn" data-fstab="nh" type="button" '
        'onclick="showFocusStockTab(\'nh\')">⛰ 新高股</button>'
        '<button class="sub-tab-btn" data-fstab="gr" type="button" '
        'onclick="showFocusStockTab(\'gr\')">🌱 成長股</button>'
        '<button class="sub-tab-btn" data-fstab="chip" type="button" '
        'onclick="showFocusStockTab(\'chip\')">🔒 籌碼股</button>'
        '<button class="sub-tab-btn" data-fstab="kgzd" type="button" '
        'onclick="showFocusStockTab(\'kgzd\')">🥣 看高做低股</button>'
        '</div>'
    )
    # 交集股條件篩選列(預設全 disabled;多選 AND;順序同 sub-tab;有交集股才顯示)
    _filter_conds = [("vol", "出量"), ("pot", "潛力"), ("nh", "新高"), ("gr", "成長"), ("chip", "籌碼"), ("kgzd", "看高做低")]
    # 品質濾網 = 策略模擬器(trade_sim 版本 C)候選資格;通過數預先算好放 chip
    _n_qpass = sum(
        1 for c in intersect_stocks
        if bool(c.get("is_growth"))
        and c.get("ma20_bias") is not None and c["ma20_bias"] < 10
        and (stocks_info.get(c["ticker"]) or {}).get("change_pct") is not None
        and (stocks_info.get(c["ticker"]) or {}).get("change_pct") < 3
        and c.get("vol_mult") is not None and c["vol_mult"] < 2
    )
    _int_filter_bar = ((
        '<div class="fs-filter-bar">'
        '<span class="fs-filter-label">篩選符合條件</span>'
        + "".join(
            f'<button type="button" class="fs-filter-btn" data-cond="{k}" '
            f'onclick="toggleFsFilter(this)">{lbl}</button>'
            for k, lbl in _filter_conds
        )
        # 品質濾網 toggle:獨立按鈕,與條件鈕 AND 疊加。tooltip 說明 = 策略候選資格。
        + '<button type="button" class="fs-filter-btn fs-quality-btn" id="fs-quality-btn" '
          'onclick="toggleFsQuality(this)" '
          'title="只顯示策略模擬器(版本 C)實際會考慮的候選股:'
          '同時「有成長(月營收/EPS YoY 正)、月線乖離 &lt;10%、當日漲幅 &lt;3%、不爆量(量能 &lt;5日均 ×2)」。'
          '回測顯示純交集股不論排序皆跑輸大盤,加這 4 濾網後才有超額報酬。">'
          f'🎯 品質濾網 <span class="fs-quality-n">通過 {_n_qpass}/{len(intersect_stocks)}</span></button>'
        + '</div>'
    ) if intersect_stocks else '')

    # sub-tab 表頭:「共 N 檔 / <說明>」同一行(count 在前,不換行;
    # 交集股的 <b> 帶 id=fs-int-count 供篩選時 JS 即時更新)
    def _pane_head(hint_text, rows, is_int=False):
        if not rows:
            return f'<p class="fs-hint">{hint_text}</p>'
        bid = ' id="fs-int-count"' if is_int else ''
        return (f'<p class="fs-hint">'
                f'<span class="fs-count">共 <b{bid}>{len(rows)}</b> 檔</span>'
                f'<span class="fs-sep">/</span>{hint_text}</p>')

    panes_html = (
        '<div class="fs-tab-pane active" id="fstab-int">'
        + _pane_head('同時符合 2 項(含)以上條件的焦點股,依符合條件數由多至少排序。',
                     intersect_stocks, True)
        + _int_filter_bar + int_html + '</div>'
        + '<div class="fs-tab-pane" id="fstab-vol">'
        + _pane_head('今日成交金額 &gt; 前 5 交易日均(不含今日)× 5,依出量倍數排序。',
                     volume_stocks)
        + vol_html + '</div>'
        + '<div class="fs-tab-pane" id="fstab-pot">'
        + _pane_head('五日均價 &gt; 十日均價 &gt; 月均價,且股價低於月均價 1.15 倍;'
                     '或五日 / 十日 / 月均線糾結、股價站上所有均線但距離不太遠、'
                     '近 5 日均成交金額 &gt; 近 30 日均 × 2;或前一交易日入選交集股、'
                     '今日跌逾 3.5% 但仍高於月線、且成交金額萎縮至前一交易日 ¼ '
                     '以下。依月線乖離率排序。',
                     potential_stocks)
        + pot_html + '</div>'
        + '<div class="fs-tab-pane" id="fstab-nh">'
        + _pane_head('今日盤中最高價觸及 150 日新高(≥ 過去 150 日最高價)的焦點股,'
                     '依月線乖離率排序。', new_high_stocks)
        + nh_html + '</div>'
        + '<div class="fs-tab-pane" id="fstab-gr">'
        + _pane_head('月營收連 3 月 YoY &gt; 0,且近一季毛利 / 營業利益 / 稅前淨利 / '
                     '稅後淨利金額年增率皆 &gt; 0,依月線乖離率排序。', growth_stocks)
        + gr_html + '</div>'
        + '<div class="fs-tab-pane" id="fstab-chip">'
        + _pane_head('散戶持股比週減(必須),且【投信買超 ≥ 5%量 / 外資買超 ≥ 10%量 / '
                     '大戶持股比週增 ≥ 1.5】至少一項,並排除外資賣超 ≥ 10%量 / 投信賣超 '
                     '≥ 5%量 / 大戶持股比週減;依大戶持股比週增排序。散戶 / 大戶持股比採 '
                     'TDCC 集保週資料近似運算,週減幅 ≤ 0.3 個百分點視為噪音不計',
                     chip_stocks)
        + chip_html + '</div>'
        + '<div class="fs-tab-pane" id="fstab-kgzd">'
        + _pane_head('近 120 日收盤形成碗型底(平滑 U 形、碗深 ≥13%),'
                     '今日帶量(成交金額 &gt; 視窗均量 × 3)收盤突破左緣頸線。'
                     '回測一年大賺小賠(Sharpe 全場最佳);依月線乖離率排序。',
                     kgzd_stocks)
        + kgzd_html + '</div>'
    )
    return nav_html + panes_html


# ── 每日精選閘(2026-06-11 統一版,取代 06-08 crash 閘 / 06-09 rally 閘三分支)──
# 此前精選只在 crash / rally 兩種極端盤啟用,普通日是規則真空:題材形成門檻僅
# 「同前綴 2 顆種子」,中等強度日(如 06-11 平盤混合日)隨便就 25-35 個題材全量
# 上架、同家族佔多欄。user 拍板 2026-06-11:**每天**動態精選、最多只留最強 10 個。
# 三套規則(crash 抗跌率 / rally 齊漲率 / normal 無閘)本質是同一件事 ——
# 「過半成員贏市場 + 加權贏市場」,統一成一套對所有盤勢連續適用的公式:
#   錨(market anchor):雙指數皆跌取較弱者(必須贏真正在崩的那邊;焦點 universe
#     偏中小/櫃買),否則取較強者(必須贏真正在漲的那邊)。06-10 拍板的 min/max
#     錨邏輯的推廣。
#   buffer(連續動態):錨 ≥ 0 → 2pp;錨 ≤ −3 → 1pp;中間線性內插。大跌日
#     「贏大盤 1pp」已是強訊號,平/漲日要贏 2pp 才算主流;線性過渡避免微跌/微漲
#     日門檻跳動(原 crash 1.0 / rally 2.0 在 0 軸兩側會跳 1pp)。
#   雙閘(成員 = focal + sentinel,chg=None 不入計算):
#     1. 領先率 = 成員 chg ≥ 錨 的佔比 ≥ DISTILL_MIN_LEAD_RATIO —— 排除靠 1-2 檔
#        拉高加權、其餘平庸的假強勢(crash 抗跌率與 rally 齊漲率的統一推廣)。
#     2. 成交值加權平均漲跌 ≥ 錨 + buffer。
#   傘狀去重(_distill_pick)後取前 DISTILL_MAX;過閘不足 DISTILL_MIN 時從未過閘
#   者依強度補足(公開站鐵則:永遠不空),展示文字改述「相對最強」。
# 決議沿用:**只看價格結果**,排除三大法人籌碼(法人淨流被權值股機械主導 + 同股
# 跨多題材重複計入,失真;user 定 2026-06-08/09)。
# 行為近似等價驗證:06-10 crash 日 gate 同為 −3.43;06-09 rally 日公式完全相同;
# 06-11 平盤混合日(TWII −0.18 / TPEX +0.29)模擬 29 → 過閘 8 → 去重 4
# (觸控面板IC / 被動元件(吸收電容器+MLCC)/ 記憶體模組(吸收NAND+SSD)/ 消費性MCU)。
DISTILL_MAX = 10           # 每日精選上限(user 2026-06-11)
DISTILL_MIN = 3            # 保底題材數(過閘不足時依強度補足,永遠不空)
DISTILL_MIN_LEAD_RATIO = 0.5   # 領先率門檻(過半成員自身贏錨)
DISTILL_BEAT_FULL = 2.0    # buffer 上端(錨 ≥ 0)
DISTILL_BEAT_FLOOR = 1.0   # buffer 下端(錨 ≤ TAPER_AT)
DISTILL_BEAT_TAPER_AT = -3.0   # 錨低於此值 buffer 鬆到 FLOOR;0~此值間線性內插


def _distill_beat(anchor: float) -> float:
    """buffer 隨錨連續調整:≥0 → 2pp,≤−3 → 1pp,中間線性。"""
    if anchor >= 0:
        return DISTILL_BEAT_FULL
    if anchor <= DISTILL_BEAT_TAPER_AT:
        return DISTILL_BEAT_FLOOR
    span = DISTILL_BEAT_FULL - DISTILL_BEAT_FLOOR
    return DISTILL_BEAT_FULL + anchor * (span / -DISTILL_BEAT_TAPER_AT)


def _distill_pick(scored: list, max_n: int) -> list:
    """crash / rally 精選共用:由強到弱貪婪選取 + 重疊抑制(2026-06-10 抽共用,
    原先只在 rally;crash 同樣會出現 MLCC/電容器/被動元件家族佔 3 欄)。

    scored = [(wchg, cluster)] 已按強度 desc。同源判定 overlap coefficient =
    |交集|/min(兩者大小)≥ DISTILL_OVERLAP_MAX(分母取較小者才抓得到「子集型」
    重複,如電容器⊂被動元件)。同源只佔一個聚焦欄位,**代表題材升級為較廣的傘狀
    題材**(非最強窄子角度)——否則窄角度會把同家族、卻不在其成分內的熱門股(如
    石英/頻率元件的晶技,只掛在被動元件傘下)整批藏掉。家族佔位由最強成員先到先
    claim,representative 隨後遇更廣同源成員時替換 → 保留全成分 + 高排名 + 仍只一欄。
    不提早 break:後續更廣的同源成員(強度較低、排在後面)仍需 upgrade 已選代表。
    """
    picked: list = []
    picked_sets: list[set] = []
    for _w, c in scored:
        fset = {s.ticker for s in c.focal}
        merged = False
        if fset:
            for idx, ps in enumerate(picked_sets):
                if not ps:
                    continue
                if len(fset & ps) / min(len(fset), len(ps)) >= DISTILL_OVERLAP_MAX:
                    if len(fset) > len(picked_sets[idx]):   # 升級為較廣傘狀題材
                        picked[idx] = c
                        picked_sets[idx] = fset
                    merged = True
                    break
        if merged:
            continue
        if len(picked) < max_n:
            picked.append(c)
            picked_sets.append(fset)
    return picked


# 大漲盤 banner 偵測門檻(rally 偵測為 stockgg 自有,ingest 每日都寫 breadth:
# breadth ≥ RALLY_BREADTH_THRESHOLD 且 TWII 漲幅 ≥ RALLY_MIN_INDEX_CHG 才渲
# 🚀 banner。注意 2026-06-11 起這只決定 banner 敘事,精選閘本身每天都跑)。
RALLY_BREADTH_THRESHOLD = 0.65   # 上漲家數佔比 ≥ 此值(配合指數強度才算大漲盤)
RALLY_MIN_INDEX_CHG = 1.5        # TWII 當日漲幅 ≥ 此值(%;確認是「超級」大漲盤)
# 重疊抑制(_distill_pick):同源家族(overlap coefficient ≥ 門檻)只佔一個聚焦
# 欄位,代表題材取較廣傘狀 —— 直擊「多題材股過多」。
DISTILL_OVERLAP_MAX = 0.67


def _distill_daily_clusters(clusters: list, stocks_info: dict,
                            twii_chg: float | None,
                            tpex_chg: float | None) -> tuple[list, dict]:
    """每日精選閘(統一版):任何盤勢都把焦點題材濃縮成最強 ≤DISTILL_MAX 個。

    規則見上方常數區塊註解。回傳 (picked, stats):
      picked = 原 cluster 物件子集(已傘狀去重;顯示順序仍交給外層 TV sort chip)
      stats  = {total, passed, picked, anchor, anchor_name, beat, gate, filled}
               給 banner / 精選說明列內插真值(門檻字眼不寫死)。
    """
    avail = [(v, n) for v, n in ((twii_chg, "加權"), (tpex_chg, "櫃買"))
             if v is not None]
    if not avail:
        anchor, anchor_name = 0.0, None    # 指數缺值:退絕對門檻(0 + 2pp)
    elif all(v < 0 for v, _ in avail):
        anchor, anchor_name = min(avail)   # 雙跌:贏真正在崩的那邊
    else:
        anchor, anchor_name = max(avail)   # 其餘:贏較強的那邊
    beat = _distill_beat(anchor)
    gate = anchor + beat

    scored: list = []      # 過雙閘者 (wchg, cluster)
    bench: list = []       # 未過閘者 (wchg, cluster),保底補位用
    for c in clusters:
        members = list(c.focal) + list(getattr(c, "sentinel", []) or [])
        if not members:
            continue
        lead_n = lead_den = 0
        num = den = 0.0
        for s in members:
            info = stocks_info.get(s.ticker) or {}
            chg = info.get("change_pct")
            if chg is None:
                continue                  # NULL 不判領先、不入加權(不當 0)
            lead_den += 1
            if chg >= anchor:
                lead_n += 1
            tv = float(info.get("trading_value") or 0)
            if tv > 0:
                num += chg * tv
                den += tv
        if lead_den == 0 or den <= 0:
            continue
        wchg = num / den
        if lead_n / lead_den >= DISTILL_MIN_LEAD_RATIO and wchg >= gate:
            scored.append((wchg, c))
        else:
            bench.append((wchg, c))
    scored.sort(key=lambda x: -x[0])
    bench.sort(key=lambda x: -x[0])

    picked = _distill_pick(scored, DISTILL_MAX)
    filled = False
    if len(picked) < DISTILL_MIN:
        # 保底:過閘 + 未過閘串接重選(過閘者仍排前),補足到 DISTILL_MIN。
        picked = _distill_pick(scored + bench, DISTILL_MIN)
        filled = True
    stats = {
        "total": len(clusters), "passed": len(scored), "picked": len(picked),
        "anchor": anchor, "anchor_name": anchor_name,
        "beat": beat, "gate": gate, "filled": filled,
    }
    return picked, stats


def build_focus_html(
    tw_ranks: list,
    sub_clusters: list,
    stocks_info: dict,
    theme_history_payload: dict,
    market_index_payload: dict | None = None,
    stock_meta: dict | None = None,
    highlight_subs: dict[str, list[tuple[str, str]]] | None = None,
    ticker_net_inst: dict[str, dict[str, float]] | None = None,
    focus_hl_clusters: list | None = None,
    market_notes: dict | None = None,
    focus_daily_subs: dict[str, set[str]] | None = None,
    focus_sorted_dates: list[str] | None = None,
    tw_breadth: dict | None = None,
    distill_stats: dict | None = None,
) -> tuple[str, dict]:
    """Build the 熱門題材 tab — 只渲染子產業 ranked list。

    2026-05-16 改:移除主產業 sub-tab(資訊與子產業重疊),主產業仍由
    `detect_industry_clusters` 計算但不在公開站顯示。前哨觀察(watch)同步
    從卡片內移除(只剩今日焦點 focal pills)。

    `_merge_identical_focal` 已在 focus_themes 那邊套用 —— focal ticker
    set 完全相同的子產業會被合併成 "A & B & C: ...stocks"。

    2026-05-16 加:每個 cluster 卡片內嵌 6 個月 TV trend sparkline (SVG);
    點 sparkline 彈出 modal 大圖。資料來自 theme_history_payload(可能空,
    則不渲染圖表),由 ingest 端 src/analysis/theme_history.py 寫 DB 後
    Q11 fetch 進來。

    Returns (html, modal_data) — modal_data 仍以 ticker 為 key,
    內容由下游 analyst consensus builder 填入。
    """
    stock_meta = stock_meta or {}
    all_stocks: dict[str, dict] = {}
    for r in tw_ranks:
        if _is_etf(r["ticker"], r.get("name", "")):
            continue
        extra = json.loads(r.get("extra") or "{}") if isinstance(r.get("extra"), str) else (r.get("extra") or {})
        meta = stock_meta.get(r["ticker"], {})
        all_stocks[r["ticker"]] = {
            "name": r["name"] or r["ticker"],
            "market": "TW",
            "board": extra.get("board", "TWSE"),
            "change_pct": float(r["change_pct"]) if r["change_pct"] is not None else None,
            "close_price": float(r["close_price"]) if r.get("close_price") is not None else None,
            "trading_value": float(r["trading_value"] or 0),
            "rank": r["rank"],
            "limit_up": bool(extra.get("is_limit_up") or r.get("is_limit_up_30m")),
            "is_limit_down": bool(extra.get("is_limit_down")),
            "is_punish": bool(extra.get("is_punish")),
            "punish_type": extra.get("punish_type"),
            "is_special": bool(extra.get("is_special")),
            "ma20_bias": stocks_info.get(r["ticker"], {}).get("ma20_bias"),
            # F2/F3 stock_meta 帶進來:cluster metric badge 與 pill 52w% 都讀這
            "week52_high": float(meta["week52_high"]) if meta.get("week52_high") is not None else None,
            "week52_low":  float(meta["week52_low"])  if meta.get("week52_low")  is not None else None,
            "pe_ttm":      float(meta["pe_ttm"])      if meta.get("pe_ttm")      is not None else None,
            "peg_ratio":   float(meta["peg_ratio"])   if meta.get("peg_ratio")   is not None else None,
            "peg_status":  meta.get("peg_status"),
            "dividend_yield": float(meta["dividend_yield"]) if meta.get("dividend_yield") is not None else None,
            "beta":        float(meta["beta"])        if meta.get("beta")        is not None else None,
        }

    if not sub_clusters and not highlight_subs:
        return '<p class="muted-note">今日尚無熱門產業</p>', {}

    # 拆兩半:main='近一年焦點' 走「近一年焦點」tab(顯前哨);其他走「泛分類」tab
    # 2026-05-18 起:hl_clusters 改吃 detect_focus_clusters 輸出(種子驅動);
    # pan_clusters 仍由 detect_industry_clusters 結果過濾(排除近一年焦點 main,
    # 避免與新 hl 邏輯重複)。
    def _is_highlight_cluster(c) -> bool:
        if c.main == HIGHLIGHT_MAIN:
            return True
        return any(m == HIGHLIGHT_MAIN for m, _s in (c.members or []))
    hl_clusters = list(focus_hl_clusters or [])
    pan_clusters = [c for c in sub_clusters if not _is_highlight_cluster(c)]

    # Modal data placeholders — analyst consensus filled downstream;兩 tab 共用
    # 同時含 hl_clusters 與 pan_clusters 的 focal + sentinel (hl 的 sentinel
    # 也可開 modal 看近一年趨勢)
    modal_data: dict[str, str] = {}
    _all_modal_src: list = list(hl_clusters) + list(pan_clusters)
    for c in _all_modal_src:
        for s in c.focal:
            modal_data[s.ticker] = ""
        for s in getattr(c, "sentinel", []) or []:
            modal_data[s.ticker] = ""

    # Cluster info modal(ⓘ button)資料源 — 兩條路徑:
    # - pan_sub(泛分類):topics_by_ticker — 以該 cluster 成交額最高 focal
    #   ticker 反查 topic.tickers,_render_topic_card 預渲成 HTML。
    # - hl_sub(焦點):topics_by_focus_theme — ingest ec138cd 起每個 topic
    #   有 AI 指派的 focus_themes(近一年焦點字典 sub 原字串);建 sub→topics
    #   反向索引,_industry_section_html 內用 cluster.members 比對。改走題材名
    #   而非龍頭股 ticker → 解「只認單一龍頭股、議題沒點名 ticker 就漏接」。
    topics_by_ticker: dict[str, str] = {}
    topics_by_focus_theme: dict[str, list[dict]] = {}
    if market_notes and market_notes.get("topics"):
        from collections import defaultdict
        _tk_topics = defaultdict(list)
        _ft_topics = defaultdict(list)
        for topic in market_notes["topics"]:
            for tk in topic.get("tickers", []) or []:
                _tk_topics[tk].append(topic)
            # 2026-06-08 v3:cluster 改前綴群組命名,topic.focus_themes 是完整 sub
            # 原字串 → 收斂到前綴當 key,才對得上 cluster.members 的前綴(_resolve_
            # cluster_topics 內 id(t) 去重,同前綴多 sub 命中同 topic 不重複渲)
            for sub in topic.get("focus_themes", []) or []:
                _ft_topics[_focus_sub_prefix(sub)].append(topic)
        topics_by_ticker = {
            tk: ''.join(_render_topic_card(t, stocks_info) for t in topics)
            for tk, topics in _tk_topics.items()
        }
        topics_by_focus_theme = dict(_ft_topics)

    # 兩 tab 共用 cluster card 排行版型,level 拿來區分 IIA_CLUSTERS namespace
    # + sort chip data-level + container id;近一年焦點 tab 在 cluster card 內
    # 多渲一個前哨 section(同題材但今日沒進 top-50 的標的)
    # 計算 hl_sub cluster 的「連續上榜天數 / 近 20 日上榜率」(來自 Q24)
    cluster_dynamics: dict[str, dict] = {}
    if focus_sorted_dates and focus_daily_subs and hl_clusters:
        for c in hl_clusters:
            # merged cluster.members 是 [(main, sub), ...];取 sub 列表
            cluster_subs = [s for _m, s in (c.members or [])]
            if not cluster_subs:
                cluster_subs = [c.name]  # 保險
            streak, rate20 = _cluster_streak_rate20(
                cluster_subs, focus_sorted_dates, focus_daily_subs)
            cluster_dynamics[c.cluster_id] = {"streak": streak, "rate20": rate20}

    hl_html = _industry_section_html(
        hl_clusters, all_stocks, "hl_sub", theme_history_payload,
        highlight_subs=highlight_subs, stock_meta=stock_meta,
        ticker_net_inst=ticker_net_inst,
        topics_by_ticker=topics_by_ticker,
        topics_by_focus_theme=topics_by_focus_theme,
        topics_stocks_info=stocks_info,
        cluster_dynamics=cluster_dynamics,
    ) if hl_clusters else '<p class="muted-note">今日「近一年焦點」題材無焦點股入榜</p>'
    pan_html = _industry_section_html(
        pan_clusters, all_stocks, "pan_sub", theme_history_payload,
        topics_by_ticker=topics_by_ticker,
    ) if pan_clusters else '<p class="muted-note">今日無泛分類熱門題材</p>'

    # sub-tabs:🌟 近一年焦點 / 📊 泛分類(同 cluster card 排行版型)
    nav_html = (
        '<div class="sub-tabs">'
        '<button class="sub-tab-btn active" data-stab="hl"  type="button" onclick="showSubTab(\'hl\')">🌟 焦點</button>'
        '<button class="sub-tab-btn"        data-stab="pan" type="button" onclick="showSubTab(\'pan\')">📊 泛分類</button>'
        '</div>'
    )
    # 大跌盤抗跌模式 banner（只在「焦點」pane 頂渲;ingest 把 tw_crash_mode
    # 寫進當日 ^TWII extra 才有,平盤日 tw_breadth 為空 → 不渲）。數字皆帶基準
    # （上漲家數/總家數 + 百分比），不外洩內部代號。
    #
    # **全門檻字眼從驅動邏輯的常數內插**(2026-06-08),避免調參後文字與真實邏輯
    # 脫節(例:beat_market 1.0→1.5、resilience 0.5→0.6、ingest 門檻變動)。
    #   - stockgg 自有:distill_stats(統一精選閘真值)/ DISTILL_MIN_LEAD_RATIO
    #   - ingest 自有(breadth 門檻 / 種子抗跌門檻):優先讀 tw_breadth payload
    #     (ingest 若有寫 threshold / seed_gain),缺則 fallback 到目前已知值。
    # 極端盤 banner(只在「焦點」pane 頂渲)。crash / rally 各一套文字,**全門檻字眼
    # 從驅動邏輯的常數內插**(避免調參後文字與真實邏輯脫節);banner 是否渲染、渲哪套
    # 完全由 populate 算出的 tw_breadth["mode"] 決定。
    crash_banner = ""
    _bmode = (tw_breadth or {}).get("mode")
    # 統一精選閘真值(2026-06-11):banner / 精選說明列的門檻字眼全部從
    # distill_stats + 常數內插,不寫死。
    _ds = distill_stats or {}
    _lp = int(round(DISTILL_MIN_LEAD_RATIO * 100))
    _lead_w = "過半" if _lp == 50 else f"逾 {_lp}%"
    _anchor_v = _ds.get("anchor")
    _aname = _ds.get("anchor_name") or "大盤"
    _anchor_s = (f"{_anchor_v:+.2f}%".replace("-", "−")
                 if _anchor_v is not None else "—")
    _beat_s = f"{_ds.get('beat', DISTILL_BEAT_FULL):.1f}".rstrip("0").rstrip(".")
    # 雙閘文字(crash / rally / 普通日共用;基準指數帶名稱與當日值,訪客可驗證)
    _gate_phrase = (f'{_lead_w}成分當日表現勝過{_aname}指數（{_anchor_s}）、'
                    f'且整體成交值加權漲跌再領先逾 {_beat_s} 個百分點')
    # 保底補位日(過閘不足 DISTILL_MIN)的補述,三種盤勢通用
    _fill_note = ('；今日通過門檻的題材不足,已依相對強度補足顯示'
                  if _ds.get("filled") else '')
    if _bmode in ("crash", "rally") and tw_breadth.get("up") is not None:
        _r = tw_breadth.get("ratio")
        _pct = f"{_r * 100:.1f}%" if isinstance(_r, (int, float)) else "—"
        _up, _tot = tw_breadth.get("up"), tw_breadth.get("total")
        if _bmode == "crash":
            # ingest-owned 門檻:payload 帶就用真值,否則 fallback(forward-compat)
            _bthr = tw_breadth.get("threshold")
            _bthr_v = _bthr if isinstance(_bthr, (int, float)) else 0.20
            _bthr_s = f"{_bthr_v * 100:.0f}%"
            _sgain = tw_breadth.get("seed_gain")
            _sgain_s = f"{abs(_sgain):g}%" if isinstance(_sgain, (int, float)) else "3%"
            # 觸發軌判別(2026-06-10 雙軌 OR):breadth < 門檻 → 廣度軌(原文字);
            # 否則為分化崩盤軌(breadth < div_breadth 且 min(TWII,TPEX) ≤ div_index,
            # 如 TWII −0.64 / TPEX −4.43 的權值撐盤中小崩日)→ 改述分化原因,
            # 不能再寫「低於 20% 門檻」(該日不成立)。
            _breadth_track = isinstance(_r, (int, float)) and _r < _bthr_v
            if _breadth_track:
                _cause = f'（{_up}/{_tot} 家），低於 {_bthr_s} 門檻。'
            else:
                _dvb = tw_breadth.get("div_breadth")
                _dvb_s = f"{_dvb * 100:.0f}%" if isinstance(_dvb, (int, float)) else "30%"
                _dvi = tw_breadth.get("div_index")
                _dvi_s = f"{abs(_dvi):g}%" if isinstance(_dvi, (int, float)) else "3%"
                _tw, _tp = tw_breadth.get("twii_chg"), tw_breadth.get("tpex_chg")
                _weak_name, _weak_chg = ("櫃買", _tp) if (
                    _tp is not None and (_tw is None or _tp <= _tw)) else ("加權", _tw)
                _weak_s = (f"{_weak_chg:+.2f}%".replace("-", "−")
                           if _weak_chg is not None else "—")
                # 第二軌可在「分化(權值撐、中小崩)」與「雙指數重挫但 breadth 未達
                # 極端」兩種盤觸發,editorial 尾句須兩者皆真,不寫死分化敘事。
                _cause = (f'（{_up}/{_tot} 家，低於 {_dvb_s}），'
                          f'且{_weak_name}指數重挫 <b>{_weak_s}</b>'
                          f'（跌逾 {_dvi_s}）。')
            crash_banner = (
                '<div class="crash-banner" role="status">'
                '<span class="crash-banner-icon">🛡️</span>'
                '<div class="crash-banner-txt">'
                f'<b>大跌盤模式</b>　今日上市櫃僅 <b>{_pct}</b> 個股上漲'
                f'{_cause}'
                f'個股收錄今日逆勢上漲或跌幅小於 {_sgain_s} 的相對抗跌股；'
                f'下方再<b>精選為 {len(hl_clusters)} 個真抗跌題材</b>'
                f'（{_gate_phrase}{_fill_note}）。'
                '</div></div>'
            )
        else:  # rally
            _rthr = int(round(RALLY_BREADTH_THRESHOLD * 100))     # breadth 門檻
            _ithr = f"{RALLY_MIN_INDEX_CHG:g}"                    # 指數強度門檻
            crash_banner = (
                '<div class="rally-banner" role="status">'
                '<span class="crash-banner-icon">🚀</span>'
                '<div class="crash-banner-txt">'
                f'<b>大漲盤模式</b>　今日上市櫃 <b>{_pct}</b> 個股上漲'
                f'（{_up}/{_tot} 家），普遍齊漲（達 {_rthr}% 且大盤漲逾 {_ithr}%）。'
                f'題材爆量易失焦,下方<b>精選為 {len(hl_clusters)} 個真領漲題材</b>'
                f'（{_gate_phrase}{_fill_note}）—— '
                '相對惜售追捧、可能是下一波主流。'
                '</div></div>'
            )
    elif _ds and _ds.get("total", 0) > _ds.get("picked", 0):
        # 普通日精選說明列(2026-06-11):低調一行,讓訪客知道有精選在運作、
        # 門檻是什麼(數字帶基準)。沒有精選效果(題材本來就 ≤ 上限且全過閘)
        # 或 distill_stats 缺(防禦)時不渲。
        if _ds.get("filled"):
            _note_txt = (f'今日 {_ds["total"]} 個熱門題材中,無題材明顯領先大盤'
                         f'（門檻:{_gate_phrase}），顯示相對最強 '
                         f'{len(hl_clusters)} 個。')
        else:
            _note_txt = (f'已自 {_ds["total"]} 個熱門題材精選 '
                         f'<b>{len(hl_clusters)} 個最強</b>：{_gate_phrase}；'
                         f'同質題材已合併，最多顯示 {DISTILL_MAX} 個。')
        crash_banner = f'<div class="distill-note" role="note">📌 {_note_txt}</div>'
    panes_html = (
        f'<div class="sub-tab-pane active" id="stab-hl">{crash_banner}{hl_html}</div>'
        f'<div class="sub-tab-pane" id="stab-pan">{pan_html}</div>'
    )
    return nav_html + panes_html, modal_data


# 焦點排行 tab (build_focus_ranking_html) 2026-05-19 移除。
# 相關 CSS 「Sprint 3: 焦點排行 row clickable」section 一併清掉。

# ── 股市筆記 tab ──────────────────────────────────────────────────────────────

def _render_topic_card(topic: dict, stocks_info: dict | None = None) -> str:
    """Render single market_notes topic 為 .topic-card HTML。
    build_notes_html(股市筆記 tab)與 cluster info modal(熱門題材 tab ⓘ)
    共用此 helper,確保兩處 CSS 樣式完全一致。
    """
    t_name = html_lib.escape(topic.get("topic", ""))
    sentiment = topic.get("sentiment", "中立")
    sent_cls = "sent-bull" if "偏多" in sentiment else ("sent-bear" if "偏空" in sentiment else "sent-neu")
    summary = html_lib.escape(topic.get("summary", ""))
    key_points = topic.get("key_points", [])
    kp_html = "".join(f'<li>{html_lib.escape(p)}</li>' for p in key_points[:5])
    tickers = topic.get("tickers", [])
    _si = stocks_info or {}
    tk_html = "".join(_stk_pill(t, _si) for t in tickers)
    return (
        '<div class="topic-card">'
        '<div class="topic-head">'
        f'<span class="topic-name">{t_name}</span>'
        f'<span class="sent-badge {sent_cls}">{html_lib.escape(sentiment)}</span>'
        '</div>'
        + (f'<p class="topic-sum">{summary}</p>' if summary else '')
        + (f'<ul class="kp-list">{kp_html}</ul>' if kp_html else '')
        + (f'<div class="tk-row">{tk_html}</div>' if tk_html else '')
        + '</div>'
    )


def _resolve_cluster_topics(
    cluster,
    level: str,
    topics_by_ticker: dict[str, str],
    topics_by_focus_theme: dict[str, list],
    stocks_info: dict,
) -> str:
    """回傳該 cluster 關聯的跨來源議題 HTML(topic cards 串接),無則 ''。

    - hl_sub(焦點):用 cluster.members 的 sub 名比對 topic.focus_themes
      (ingest ec138cd 起 AI 為每個 topic 指派的近一年焦點題材;值為
      theme_dictionary.json sub 原字串、ingest 端已做交集過濾)。merged
      cluster 有多個 member sub,跨 sub 命中同一 topic 依物件 id 去重、
      保留首次出現順序。
    - 其他 level(pan_sub 泛分類):舊路徑 —— 取成交額最高 focal ticker
      反查 topic.tickers(topics_by_ticker 已是預渲 HTML)。
    """
    if level == "hl_sub":
        seen: set[int] = set()
        ordered: list[dict] = []
        for _main, sub in (cluster.members or []):
            for t in topics_by_focus_theme.get(sub) or []:
                if id(t) in seen:
                    continue
                seen.add(id(t))
                ordered.append(t)
        return "".join(_render_topic_card(t, stocks_info) for t in ordered)
    if cluster.focal:
        primary = max(cluster.focal, key=lambda s: s.trading_value).ticker
        return topics_by_ticker.get(primary) or ""
    return ""


def build_notes_html(market_notes: dict | None, podcast_rows: list,
                     stocks_info: dict | None = None) -> str:
    parts = []

    if market_notes and market_notes.get("topics"):
        # Sort by latest contributing-article date. The underlying `articles`
        # / `sources` arrays drive ordering only — they are intentionally NOT
        # rendered on this public site (article titles + subscription source
        # names are copyrighted/derivative content; they stay in DB and in
        # the private admin UI only).
        def _topic_latest_date(t):
            dates = [a.get("date", "") for a in t.get("articles", []) if a.get("date")]
            return max(dates) if dates else "1900-01-01"
        topic_cards = [
            _render_topic_card(topic, stocks_info)
            for topic in sorted(market_notes["topics"], key=_topic_latest_date, reverse=True)
        ]
        parts.append(
            '<div class="section-hdr">🔀 跨來源共同議題（近7日）</div>'
            '<div class="topics-grid">' + ''.join(topic_cards) + '</div>'
        )
    else:
        parts.append(
            '<div class="section-hdr">🔀 跨來源共同議題</div>'
            '<p class="muted-note">每日分析完成後更新（需 GOOGLE_API_KEY）</p>'
        )

    # Podcast notes section removed in repo-split Phase 3.6 — derivative
    # transcript content lives only in the private repo.
    return '\n'.join(parts)


# ── Main generate ─────────────────────────────────────────────────────────────

async def generate():
    conn = await db.connect()

    report = await conn.fetchrow(
        "SELECT report_date, raw_response, market_notes_json "
        "FROM analysis_reports ORDER BY report_date DESC LIMIT 1"
    )

    # Market snapshots — each symbol uses its own latest non-null date
    snaps: dict = {}
    snap_dates: dict = {}
    # ^TWII extra 帶 ingest c587cd1 起寫入的全市場 breadth + 大跌盤旗標
    # (tw_crash_mode 真 → ingest 端焦點種子已改抗跌門檻;此處只負責讀來渲
    #  banner。欄位缺 = 該 snapshot 還沒被新版 ingest 寫過 → banner 不渲,無害)。
    tw_breadth: dict = {}
    _twii_extra: dict = {}
    for row in await conn.fetch("""
        SELECT DISTINCT ON (symbol)
            symbol, close_price, change_pct, snapshot_date, extra
        FROM market_snapshots
        WHERE close_price IS NOT NULL
        ORDER BY symbol, snapshot_date DESC
    """):
        extra = row["extra"] if isinstance(row["extra"], dict) else json.loads(row["extra"] or "{}")
        name = extra.get("name", row["symbol"])
        snaps[row["symbol"]] = {
            "name": name,
            "close": float(row["close_price"]) if row["close_price"] is not None else None,
            "chg": float(row["change_pct"]) if row["change_pct"] is not None else None,
        }
        snap_dates[row["symbol"]] = row["snapshot_date"]
        # ^TWII extra 先暫存,mode 判定移到指數漲跌校正之後(2026-06-11,
        # 見下方「指數漲跌單一真實來源」—— snapshot 的 change_pct 不可信)。
        if row["symbol"] == "^TWII" and isinstance(extra, dict):
            _twii_extra = extra

    # ── 大盤 / 櫃買指數歷史(Q21)──────────────────────────────────────────
    # 1095 天 OHLCV,供 cluster chart modal 三線 overlay + 產業地圖 modal。
    # 2026-06-11 起提前到此處 fetch:兼作「指數漲跌單一真實來源」的計算基礎。
    market_index_payload: dict[str, list[dict]] = {"TWII": [], "TPEX": []}
    _idx_sym_map = {"^TWII": "TWII", "^TWOII": "TPEX"}
    try:
        _idx_rows = await conn.fetch(
            "SELECT snapshot_date, symbol, open, high, low, close_price, volume, change_pct "
            "FROM market_snapshots "
            "WHERE symbol = ANY($1::text[]) "
            "AND snapshot_date >= current_date - INTERVAL '1095 days' "
            "ORDER BY symbol, snapshot_date",
            ["^TWII", "^TWOII"],
        )
        def _fnum(v):
            return _jc(v, 2)  # round 2 + 整數收斂(volume 的 .0 尾巴)
        for r in _idx_rows:
            _k = _idx_sym_map.get(r["symbol"])
            if not _k or r["close_price"] is None:
                continue
            # d 必須是 YYYY-MM-DD(對齊 ticker_close 日期 + lightweight-charts
            # time 格式);db.py 會把 timestamp 欄 coerce 成 datetime,故用
            # strftime 取日期,不可用 isoformat()(會帶 T00:00:00+00:00)。
            _d = r["snapshot_date"]
            _d = _d.strftime("%Y-%m-%d") if hasattr(_d, "strftime") else str(_d)[:10]
            # `close` 欄沿用舊名(cluster modal _computeIndexSeries 讀 p.close);
            # 新增 open / high / low / volume 給趨勢 tab K 線用(ingest 76f6728
            # 起 backfill 1 年 OHL,早期歷史與 today 暫無 OHL 的 row 三欄為 None)
            market_index_payload[_k].append({
                "d": _d,
                "close":  _fnum(r["close_price"]),
                "open":   _fnum(r.get("open")),
                "high":   _fnum(r.get("high")),
                "low":    _fnum(r.get("low")),
                "volume": _fnum(r.get("volume")),
            })
        _twii_ohl = sum(1 for r in market_index_payload["TWII"] if r.get("open") is not None)
        _tpex_ohl = sum(1 for r in market_index_payload["TPEX"] if r.get("open") is not None)
        print(f"  market_index (Q21): TWII={len(market_index_payload['TWII'])}d "
              f"(OHL {_twii_ohl}d) "
              f"TPEX={len(market_index_payload['TPEX'])}d (OHL {_tpex_ohl}d)")
    except Exception as exc:
        print(f"  ⚠ Q21 market index history failed: {exc}")

    # ── 指數漲跌「單一真實來源」校正(2026-06-11)─────────────────────────
    # ingest market_data 寫的 change_pct 偶發對「錯誤的前一交易日」計算
    # (06-11 ^TWII 對 06-09 收盤算出 −3.48%,實際對 06-10 應為 −0.18%),
    # 而同一表的 close_price 序列本身正確。依「值錯了先稽核所有管線、建單一
    # 真實來源」原則:漲跌一律由 Q21 收盤序列末兩日自算,覆寫 snaps 的 chg
    # → mode 判定 / 精選錨 / 市場 tab 顯示 / banner 全部消費同一個正確值。
    # snapshot 日期與序列末日不一致(回補延遲等)時不覆寫,維持原值。
    for _sym, _key in (("TWII", "^TWII"), ("TPEX", "^TWOII")):
        _rows = [r for r in market_index_payload.get(_sym, []) if r.get("close")]
        _snap = snaps.get(_key)
        if len(_rows) < 2 or not _snap:
            continue
        _sd = snap_dates.get(_key)
        _sd_str = (_sd.strftime("%Y-%m-%d") if hasattr(_sd, "strftime")
                   else str(_sd)[:10]) if _sd else None
        if _sd_str != _rows[-1]["d"]:
            continue
        _hist_chg = round((_rows[-1]["close"] / _rows[-2]["close"] - 1) * 100, 2)
        if _snap.get("chg") is not None and abs(_snap["chg"] - _hist_chg) > 0.05:
            print(f"  ⚠ {_key} change_pct 不一致:snapshot={_snap['chg']:+.2f}% "
                  f"vs 收盤序列={_hist_chg:+.2f}% → 採序列值")
        _snap["chg"] = _hist_chg

    # 大盤模式判定(校正後的 chg 才可信):crash 由 ingest 翻 tw_crash_mode(同時
    # 改了種子門檻);rally 為 stockgg 自有 —— breadth ≥ 門檻且 TWII 漲幅 ≥ 門檻。
    # 兩者都把 breadth payload + mode 帶下去給 banner 敘事(2026-06-11 起精選閘
    # 每天都跑、不再依 mode 分支;tw_breadth 空只代表非極端盤,不渲 crash/rally banner)。
    if isinstance(_twii_extra, dict) and _twii_extra:
        _ratio = _twii_extra.get("tw_breadth_ratio")
        _twii_chg_now = (snaps.get("^TWII") or {}).get("chg")
        _mode = None
        if _twii_extra.get("tw_crash_mode"):
            _mode = "crash"
        elif (isinstance(_ratio, (int, float)) and _ratio >= RALLY_BREADTH_THRESHOLD
              and _twii_chg_now is not None and _twii_chg_now >= RALLY_MIN_INDEX_CHG):
            _mode = "rally"
        if _mode:
            tw_breadth = {
                "mode": _mode,
                "up": _twii_extra.get("tw_breadth_up"),
                "total": _twii_extra.get("tw_breadth_total"),
                "ratio": _ratio,
                # ingest-owned crash 門檻(forward-compat;缺則 banner fallback)
                "threshold": _twii_extra.get("tw_breadth_threshold"),
                "seed_gain": _twii_extra.get("tw_seed_crash_gain"),
                # 分化崩盤軌門檻(ingest 雙軌 OR 起寫;缺則 banner fallback 30%/3%)
                "div_breadth": _twii_extra.get("tw_crash_div_breadth"),
                "div_index": _twii_extra.get("tw_crash_div_index"),
                "twii_chg": _twii_chg_now,
                "tpex_chg": (snaps.get("^TWOII") or {}).get("chg"),
            }

    snap_date = snap_dates.get("^GSPC") or snap_dates.get("^IXIC") or (
        max(snap_dates.values()) if snap_dates else None
    )

    # Rankings — rank_date 取「最新完整交易日」:必須 `rank IS NOT NULL`。
    # trading_rankings 內除了真實排名列(rank 1..N),還有 rank=NULL 的雜列
    # (special 處置/漲跌停、focus_member、market_notes_ref)。後者的 rank_date
    # 由各自來源決定(market_notes_ref 甚至用 per-ticker yfinance 收盤日),
    # 可能領先真實排名日。若盲取 MAX(rank_date) 會選到「只有 rank=NULL 雜列」
    # 的幽靈日期 → 公開站整頁空。加 `rank IS NOT NULL` 確保永遠回退到「已完整
    # 抓到 top-N 排名」的最新交易日(對齊公開站鐵則:永遠不空)。
    us_rank_date = await conn.fetchval(
        "SELECT MAX(rank_date) FROM trading_rankings WHERE market='US' AND rank IS NOT NULL"
    )
    tw_rank_date = await conn.fetchval(
        "SELECT MAX(rank_date) FROM trading_rankings WHERE market='TW' AND rank IS NOT NULL"
    )
    us_ranks, tw_ranks = [], []
    focus_seed_tickers: list[str] = []  # Q16, v2 detect_focus_clusters 用
    if us_rank_date:
        us_ranks = [dict(r) for r in await conn.fetch(
            f"""SELECT ROW_NUMBER() OVER (ORDER BY trading_value DESC NULLS LAST)::int AS rank,
                       ticker, name, trading_value, change_pct, close_price, extra
                FROM trading_rankings
                WHERE rank_date=$1 AND market='US'
                ORDER BY trading_value DESC NULLS LAST
                LIMIT {RANKINGS_TOP_N}""",
            us_rank_date,
        )]
    if tw_rank_date:
        tw_ranks = [dict(r) for r in await conn.fetch(
            f"""SELECT ROW_NUMBER() OVER (ORDER BY trading_value DESC NULLS LAST)::int AS rank,
                       ticker, name, trading_value, change_pct, close_price, high, open, low,
                       is_limit_up_30m, extra
                FROM trading_rankings
                WHERE rank_date=$1 AND market='TW'
                ORDER BY trading_value DESC NULLS LAST
                LIMIT {RANKINGS_TOP_N}""",
            tw_rank_date,
        )]
        # Q14:special rows(處置 / 漲跌停)not in top-50,合進來讓 cluster
        # detection 看得到「未進 top-N 但是是同題材的特殊狀態股」(2026-05-18 起)
        try:
            _existing_tickers = {r["ticker"] for r in tw_ranks}
            special_ranks = [dict(r) for r in await conn.fetch(
                "SELECT ticker, name, trading_value, change_pct, close_price, high, open, low, "
                "is_limit_up_30m, extra "
                "FROM trading_rankings WHERE rank_date=$1 AND market='TW' "
                "AND extra->>'is_special' = 'true' ORDER BY ticker",
                tw_rank_date,
            )]
            for sr in special_ranks:
                if sr["ticker"] in _existing_tickers:
                    continue  # 已在 top-50 不重複(flag 從 top-50 row 帶)
                sr["rank"] = None  # 不在 top-50,rank 顯「—」
                tw_ranks.append(sr)
            _n_special = len(tw_ranks) - RANKINGS_TOP_N
        except Exception as exc:
            _n_special = 0
            print(f"  ⚠ special rows query failed (Q14 not deployed yet?): {exc}")

        # Q15 v2(ingest 8f27ede / 2026-05-19 起):focus_member rows
        # (ticker 屬「近一年焦點」題材字典任一 sub 且 today 有交易,涵蓋
        # top-N ∪ special ∪ focus_extra 三 bucket 的並集)。給「焦點」tab
        # 新 detection v2 用 — sub 字典成員 today 有交易者切 focal / sentinel。
        # 廢 v1 is_volume_universe(commit bd85f1d → 8f27ede 撤,extra 不再寫)。
        try:
            _existing_tickers = {r["ticker"] for r in tw_ranks}
            focus_member_ranks = [dict(r) for r in await conn.fetch(
                "SELECT ticker, name, trading_value, change_pct, close_price, high, open, low, "
                "is_limit_up_30m, extra "
                "FROM trading_rankings WHERE rank_date=$1 AND market='TW' "
                "AND extra->>'is_focus_member' = 'true' ORDER BY ticker",
                tw_rank_date,
            )]
            for fr in focus_member_ranks:
                if fr["ticker"] in _existing_tickers:
                    continue
                fr["rank"] = None  # focus_extra bucket 沒有 rank
                tw_ranks.append(fr)
            _n_focus = len(tw_ranks) - RANKINGS_TOP_N - _n_special
            print(f"  tw_ranks: {RANKINGS_TOP_N} top + {_n_special} special + {_n_focus} focus_member = {len(tw_ranks)}")
        except Exception as exc:
            print(f"  ⚠ focus_member rows query failed (Q15 v2 not deployed?): {exc}")

        # Q16 v2:focus_seed ticker list((rank ≤ 120 OR 近漲停) AND chg > 4.45%,
        # ingest 預計算)。給 detect_focus_clusters v2 反查題材字典累計 sub 種子計數。
        # 只需 ticker(其他資訊走 Q6 / Q15 抓)。
        # 失敗 = 焦點 sub-tab 必空白 → 與 Q13 同等級 critical,db.py 已內建 5xx
        # retry,這裡若仍 raise 就是真壞 → 直接中止 deploy,讓上次成功的版本留在線上。
        try:
            focus_seed_rows = await conn.fetch(
                "SELECT ticker FROM trading_rankings WHERE rank_date=$1 "
                "AND market='TW' AND extra->>'is_focus_seed' = 'true' ORDER BY ticker",
                tw_rank_date,
            )
            focus_seed_tickers = [r["ticker"] for r in focus_seed_rows]
            print(f"  focus_seed_tickers: {len(focus_seed_tickers)}")
        except Exception as exc:
            print(f"  ✗ focus_seed (Q16) query failed: {exc}", file=sys.stderr)
            raise SystemExit(
                "[fatal] Q16 focus_seed 全 retry 後仍失敗,中止 deploy。"
                "焦點 sub-tab 沒 seed list 整片空白,留上次成功的 deploy 在線上,"
                "等下個 cron 再試。"
            )

    # PRIVATE data removed in repo-split Phase 3.6: articles.content,
    # articles.refined_content, and podcast refined_content are not read
    # by the public site. Theme clustering falls back to volume-only signal.
    ticker_arts: dict[str, list] = {}
    podcast_rows: list = []

    # Build stocks_info for theme detection (mirrors ranking data already fetched)
    stocks_info: dict[str, dict] = {}
    for r in us_ranks:
        stocks_info[r["ticker"]] = {
            "name": r["name"] or r["ticker"],
            "market": "US",
            "change_pct": float(r["change_pct"]) if r["change_pct"] is not None else None,
            "close_price": float(r["close_price"]) if r.get("close_price") is not None else None,
            "trading_value": float(r["trading_value"] or 0),
            "rank": r["rank"],
            "limit_up": False,
        }
    for r in tw_ranks:
        extra = json.loads(r.get("extra") or "{}") if isinstance(r.get("extra"), str) else (r.get("extra") or {})
        # 2026-05-18 起 ingest 端會把處置/漲跌停 ticker 也寫進 trading_rankings
        # (即使不在 top-50,rank=NULL,extra 帶 flag),公開站靠這些 flag
        # 顯小 tag「處」/「漲」/「跌」並進 cluster detection。向下相容:flag
        # 沒帶就 False。is_limit_up_30m 是舊欄保留(避免破壞舊資料),is_limit_up
        # 是新的收盤判定。
        stocks_info[r["ticker"]] = {
            "name": r["name"] or r["ticker"],
            "market": "TW",
            "board": extra.get("board", "TWSE"),
            "change_pct": float(r["change_pct"]) if r["change_pct"] is not None else None,
            "close_price": float(r["close_price"]) if r.get("close_price") is not None else None,
            "high": float(r["high"]) if r.get("high") is not None else None,
            "open": float(r["open"]) if r.get("open") is not None else None,
            "low": float(r["low"]) if r.get("low") is not None else None,
            "trading_value": float(r["trading_value"] or 0),
            "rank": r["rank"],  # 可能 None(extra.is_special=true 但不在 top-50)
            "limit_up": bool(extra.get("is_limit_up") or r.get("is_limit_up_30m")),
            "is_limit_down": bool(extra.get("is_limit_down")),
            "is_punish": bool(extra.get("is_punish")),
            "punish_type": extra.get("punish_type"),  # 'normal' | 'strict' | None
            "is_special": bool(extra.get("is_special")),  # 非 top-50 但因 punish/limit 加入
            "is_focus_member": bool(extra.get("is_focus_member")),  # ingest 8f27ede 起,題材字典成員
        }
    stocks_info = {k: v for k, v in stocks_info.items() if not _is_etf(k, v.get("name", ""))}

    # Industry clustering — TW top-30 only (theme_dictionary 2026-05 改成
    # statementdog.com/taiex source 之後不再有美股)。產生主產業與子產業
    # 兩份 ranked list。
    tw_top_volume = {t: info for t, info in stocks_info.items() if info.get("market") == "TW"}
    _main_clusters, sub_clusters = detect_industry_clusters(tw_top_volume)

    # 焦點 cluster detection v2(2026-05-19 起,對應 ingest 8f27ede):
    # seeds = is_focus_seed ((rank≤120 OR 近漲停) AND chg>4.45%, ingest 預計算 Q16)
    # focus_members = is_focus_member rows (Q15) ∩ stocks_info (filter ETF)
    # 算法:同 sub 種子數 ≥ 2 才算熱門;sub 字典成員 today 有交易者
    #   chg > -3 入 focal、chg < -3 入 sentinel。pan_sub 維持原 detect_industry_clusters。
    focus_members_info = {
        t: info for t, info in tw_top_volume.items() if info.get("is_focus_member")
    }
    focus_hl_clusters = detect_focus_clusters(focus_seed_tickers, focus_members_info)
    print(f"  focus_hl_clusters: {len(focus_hl_clusters)} (v2: seeds={len(focus_seed_tickers)}, members={len(focus_members_info)})")
    # 每日精選閘(2026-06-11 統一版,任何盤勢都跑;只看價格結果,排除法人籌碼)。
    # crash/rally banner 敘事仍由 tw_breadth["mode"] 決定,但精選本身不再分支:
    # 錨/buffer 隨雙指數連續調整(雙跌取較弱、否則取較強;buffer 2pp→1pp 線性)。
    _twii_chg = (snaps.get("^TWII") or {}).get("chg")
    _tpex_chg = (snaps.get("^TWOII") or {}).get("chg")
    _before = len(focus_hl_clusters)
    focus_hl_clusters, distill_stats = _distill_daily_clusters(
        focus_hl_clusters, stocks_info, _twii_chg, _tpex_chg)
    print(f"  daily distill: {_before} → {len(focus_hl_clusters)} 題材"
          f"(過閘 {distill_stats['passed']},≤{DISTILL_MAX}"
          f"{',保底補位' if distill_stats['filled'] else ''};"
          f"錨={distill_stats['anchor_name']}{distill_stats['anchor']:+.2f}"
          f" gate 加權漲跌≥{distill_stats['gate']:.2f},"
          f"TWII={_twii_chg},TPEX={_tpex_chg})")
    # main_clusters 仍計算(供未來/ ingest backport 用),但公開站 2026-05-16 起
    # 不在 UI 顯示;前哨觀察(watch)同步從卡片移除 → 不再需要查 watch change_pct
    # 也不再 yfinance 補 watch close,純粹靠 stocks_info(top-N from SQL)。

    # _focal_tw:所有焦點 ticker 集合,供 Q13 (ticker_close_history) 的 fetch
    # 範圍。MA20 乖離率改由 Q13 close 歷史自算(見下方 Q13 fetch 之後的區塊),
    # 不再 render-time 抓 yfinance。_focal_tw 涵蓋:
    #   - sub_clusters 的 focal(pan_sub + 舊 hl 路徑)
    #   - focus_hl_clusters 的 focal + sentinel(新 hl 路徑;sentinel 也要
    #     MA20/PE 給 pill 顯)
    _focal_tw_set: set[str] = {s.ticker for c in sub_clusters for s in c.focal}
    for c in focus_hl_clusters:
        for s in c.focal:
            _focal_tw_set.add(s.ticker)
        for s in (c.sentinel or []):
            _focal_tw_set.add(s.ticker)
    _focal_tw = list(_focal_tw_set)

    # 近一年焦點 highlight subs(從 theme_dictionary.json 讀,main='近一年焦點')。
    # 230 個 ticker 涵蓋 AI 伺服器 / 光通訊 / ASIC / 半導體 / 先進封裝 / PCB /
    # 記憶體 / 機器人 / 衛星 / 國防軍工 / 重電 / 綠能 等;不依賴當日 top-50,
    # 用來顯「該 sub 內哪些是當日焦點、哪些是前哨(未進 top-50)」。
    highlight_subs = _load_highlight_subs()
    highlight_tickers: set[str] = {t for tickers in highlight_subs.values() for t, _ in tickers}

    # stock_meta (Q12) — 公司基本面快照,給 sub_cluster 計算平均 PE / 殖利率
    # / beta,給 focal pill 算 52w 位置%,給 modal 顯示公司介紹,給前哨 pill 顯 PE。
    # 一次撈 focal_tw ∪ highlight_tickers,後者讓近一年焦點區的前哨股也能顯 PE。
    _all_meta_tickers = list(set(_focal_tw) | highlight_tickers)
    stock_meta: dict[str, dict] = {}
    if _all_meta_tickers:
        try:
            meta_rows = await conn.fetch(
                "SELECT ticker, name_zh, name_en, sector, industry, description, "
                "       website, employees, shares_outstanding, float_shares, "
                "       market_cap, pe_ttm, pe_forward, pb, eps_ttm, eps_forward, "
                "       book_value, dividend_yield, last_dividend, ex_dividend_date, "
                "       week52_high, week52_low, beta, gross_margin, operating_margin, "
                "       net_margin, margin_year_quarter, gross_margin_yoy_dir, "
                "       operating_margin_yoy_dir, net_margin_yoy_dir, revenue_mom, "
                "       revenue_yoy, revenue_month, revenue_yoy_3m_all_positive, "
                "       gross_profit_yoy, operating_income_yoy, pretax_income_yoy, "
                "       net_income_yoy, peg_ratio, peg_status, eps_ttm_yoy "
                "FROM stock_meta WHERE ticker = ANY($1::text[])",
                _all_meta_tickers,
            )
            for r in meta_rows:
                stock_meta[r["ticker"]] = dict(r)
            print(f"  Stock meta: {len(stock_meta)} / {len(_all_meta_tickers)} "
                  f"tickers covered (focal={len(_focal_tw)} + highlight={len(highlight_tickers)})")
        except Exception as exc:
            print(f"  ⚠ stock_meta query failed (table not yet populated?): {exc}")

    # Parse market_notes before closing (needed for tickers query).
    # raw_response and market_notes_json live in the same analysis_reports
    # row but are written ~10h apart (daily_briefing 07:30 writes raw_response,
    # run_market_notes 18:00/23:00 writes market_notes_json via ON CONFLICT
    # UPDATE). So Q1's latest row often has raw_response but a NULL
    # market_notes_json — fall back to the most recent row that has it.
    market_notes = None
    mn_raw = report["market_notes_json"] if report else None
    if not mn_raw:
        mn_row = await conn.fetchrow(
            "SELECT report_date, market_notes_json FROM analysis_reports "
            "WHERE market_notes_json IS NOT NULL ORDER BY report_date DESC LIMIT 1"
        )
        mn_raw = mn_row["market_notes_json"] if mn_row else None
    if mn_raw:
        market_notes = mn_raw if isinstance(mn_raw, dict) else json.loads(mn_raw)
    # 正規化 market_notes 各 topic 的 ticker(Gemini 格式 → 標準 ticker)。
    # 舊的 _gemini_name_lookup / _theme_name_lookup 名稱 fallback 已隨
    # render-time yfinance 補抓一起移除 —— market_notes ticker 的 name 現在
    # 由下方 Q8(trading_rankings)直接回傳。
    if market_notes and market_notes.get("topics"):
        for _topic in market_notes["topics"]:
            _topic["tickers"] = [_normalize_ticker(_raw)
                                 for _raw in _topic.get("tickers", [])]

    # Extend stocks_info with close / change% for market notes tickers not in top-N
    if market_notes and market_notes.get("topics"):
        notes_tickers = list({
            t for topic in market_notes["topics"]
            for t in topic.get("tickers", [])
            if t not in stocks_info
        })
        if notes_tickers:
            nr = await conn.fetch(
                """SELECT DISTINCT ON (ticker) ticker, name, change_pct, close_price, market
                   FROM trading_rankings WHERE ticker = ANY($1::text[])
                   ORDER BY ticker, rank_date DESC""",
                notes_tickers,
            )
            for r in nr:
                stocks_info[r["ticker"]] = {
                    "name": r["name"] or r["ticker"],
                    "market": r["market"],
                    "change_pct": float(r["change_pct"]) if r["change_pct"] is not None else None,
                    "close_price": float(r["close_price"]) if r.get("close_price") is not None else None,
                    "trading_value": 0,
                    "rank": 99,
                    "limit_up": False,
                }

    # Catalyst events — past 14 days through next 21 days. Past events show
    # what already happened (and stay clickable to see preview_text written
    # before the event); future events show what to watch.
    catalyst_events = []
    try:
        catalyst_events = [dict(r) for r in await conn.fetch(
            """SELECT id, event_date, event_type, ticker, market, title, importance,
                      preview_text, visible
               FROM catalyst_events
               WHERE visible = TRUE
                 AND event_date >= CURRENT_DATE - INTERVAL '14 days'
                 AND event_date <= CURRENT_DATE + INTERVAL '21 days'
               ORDER BY event_date, importance DESC, ticker"""
        )]
    except Exception as exc:
        print(f"  ⚠ catalyst_events query failed: {exc}")

    # Theme history (Q11) — 過去 180 天 per (main, sub) per day 的 focal
    # breakdown,供 cluster 卡片 sparkline + 點擊彈出大圖使用。資料由
    # StockGG-ingest 端 src/analysis/theme_history.py 寫入。若 table 還沒
    # 建立(ingest 還沒 deploy),靜默回退到「無 chart」狀態,公開站照常運作。
    theme_history_rows: list = []
    _hist_keys_set: set[str] = {f"{m}||{s}" for c in sub_clusters for m, s in c.members}
    # 加上 hl_sub cluster 焦點股的「其他 main」分類 (m, s) keys:讓 theme_history
    # 抓得到這些 ticker 的 net_inst(focal_breakdown 內),否則 hl_sub cluster
    # 的 sparkline + chart histogram 都是空的。同 ticker 同日的 net_inst 在不同
    # (m, s) row 是同值,任何一個 row 拿得到都行。
    _hl_focal_tickers = {
        s.ticker for c in sub_clusters
        for s in c.focal
        if (c.main == HIGHLIGHT_MAIN or any(m == HIGHLIGHT_MAIN for m, _ in (c.members or [])))
    }
    # 新 hl 路徑(focus_hl_clusters):focal + sentinel 都要列入,讓 chart modal
    # 加權指數 + sparkline 拿得到歷史 net_inst / close。
    for c in focus_hl_clusters:
        for s in c.focal:
            _hl_focal_tickers.add(s.ticker)
        for s in (c.sentinel or []):
            _hl_focal_tickers.add(s.ticker)
    if _hl_focal_tickers:
        _theme_dict = json.loads(_THEME_DICT_PATH.read_text(encoding="utf-8")) if _THEME_DICT_PATH.exists() else {}
        for tk in _hl_focal_tickers:
            info = (_theme_dict.get("stocks") or {}).get(tk, {})
            for ind in info.get("industries", []) if isinstance(info, dict) else []:
                m = ind.get("main")
                if not m or m == HIGHLIGHT_MAIN or ind.get("disabled"):
                    continue
                for s in ind.get("subs", []):
                    _hist_keys_set.add(f"{m}||{s}")
    _hist_keys = list(_hist_keys_set)
    if _hist_keys:
        try:
            theme_history_rows = [dict(r) for r in await conn.fetch(
                """SELECT rank_date, main_industry, sub_industry,
                          focal_count, focal_breakdown, total_tv, avg_chg_pct
                   FROM theme_history
                   WHERE main_industry || '||' || sub_industry = ANY($1::text[])
                     AND rank_date >= CURRENT_DATE - INTERVAL '400 days'
                   ORDER BY main_industry, sub_industry, rank_date""",
                _hist_keys,
            )]
            print(f"  Theme history: {len(theme_history_rows)} rows for {len(_hist_keys)} (main,sub) keys")
        except Exception as exc:
            print(f"  ⚠ theme_history query failed (table not yet populated?): {exc}")

    # Q25 — 半年內 trading_rankings 內 is_focus_seed='true' 的 ticker × rank_date。
    # 對每天用 stockgg 本機 detect_focus_clusters 邏輯(hot_subs_from_seeds)重算
    # daily hot_subs,給:
    #   (1) hl_sub cluster header「連續上榜天數 / 近 20 日上榜率」chip
    #   (2)「📈 趨勢」menu 上圖 2 條序列(熱門題材數量 / 題材延續性)
    # 2026-05-28 取代 Q24:Q24 讀 ingest 寫的 theme_history sub_industry,但
    # ingest 寫條件「字典成員 ∩ universe ≥ 2」≠ stockgg「is_focus_seed ≥ 2」
    # → 數量差很多(84 vs 真實 8)。改讀 raw seed 在 stockgg 端重算,既正確、
    # 又支援「detect_focus_clusters 邏輯異動後歷史自動重算」。
    focus_daily_subs: dict[str, set[str]] = {}
    focus_sorted_dates: list[str] = []
    try:
        q25_rows = await conn.fetch(
            "SELECT rank_date, ticker FROM trading_rankings "
            "WHERE market = 'TW' AND extra->>'is_focus_seed' = 'true' "
            "AND rank_date >= current_date - INTERVAL '180 days' "
            "ORDER BY rank_date, ticker"
        )
        _seeds_by_day: dict[str, list[str]] = collections.defaultdict(list)
        for r in q25_rows:
            _d = r["rank_date"]
            d_str = _d.strftime("%Y-%m-%d") if hasattr(_d, "strftime") else str(_d)[:10]
            _seeds_by_day[d_str].append(r["ticker"])
        # 對每天用當前 detect_focus_clusters step 1-2 邏輯算 hot_subs。
        # 字典每天用同一份(stockgg 不存歷史字典)→ 預載一次,免每天重 IO。
        from src.analysis.focus_themes import _load_dict as _focus_load_dict
        _dict_data = _focus_load_dict()
        for d_str, seeds in _seeds_by_day.items():
            focus_daily_subs[d_str] = hot_subs_from_seeds(seeds, _dict_data)
        focus_sorted_dates = sorted(focus_daily_subs.keys())
        print(f"  is_focus_seed history (Q25): {len(q25_rows)} seed-rows, "
              f"{len(focus_sorted_dates)} trading days, "
              f"today hot_subs = {len(focus_daily_subs.get(focus_sorted_dates[-1], set())) if focus_sorted_dates else 0}")
    except Exception as exc:
        print(f"  ⚠ Q25 is_focus_seed history query failed: {exc}")


    # Q27 — focus_radar_history 最新 row,給選股雷達 sub-tab status block 用
    radar_today: dict | None = None
    try:
        _r27 = await conn.fetch(
            "select rank_date, intersect_tickers, per_ticker_conds, pot_subtype, "
            "breakdown, universe_size from focus_radar_history "
            "where rank_date = (select max(rank_date) from focus_radar_history)"
        )
        if _r27:
            row = _r27[0]
            _d = row["rank_date"]
            d_str = _d.strftime("%Y-%m-%d") if hasattr(_d, "strftime") else str(_d)[:10]
            ti = row["intersect_tickers"] or []
            ptc = row["per_ticker_conds"]
            if isinstance(ptc, str):
                ptc = json.loads(ptc) if ptc else {}
            bd = row["breakdown"] if isinstance(row["breakdown"], dict) else json.loads(row["breakdown"] or "{}")
            radar_today = {
                "d": d_str,
                "intersect_tickers": list(ti) if isinstance(ti, list) else [],
                "per_ticker_conds": ptc or {},
                "breakdown": bd,
                "universe_size": int(row["universe_size"]),
            }
            print(f"  focus_radar today (Q27): {d_str}, intersect={len(radar_today['intersect_tickers'])}")
    except Exception as exc:
        print(f"  ⚠ Q27 focus_radar today query failed: {exc}")

    await conn.close()

    # market_notes 提到、但不在 top-N rankings 的 ticker:ingest 自 commit
    # 11a88d4 起把這些補進 trading_rankings(rank=NULL,extra.is_market_notes_ref),
    # Q8 即撈得到 → 不再 render-time 用 yfinance 補。Q8 仍撈不到的極冷門股
    # (yfinance 本身也無資料)無 stocks_info entry,pill 顯「—」。

    raw_report   = (report["raw_response"] or "") if report else ""
    report_date  = report["report_date"].strftime("%Y/%m/%d") if report else "—"
    directions  = parse_directions(raw_report)
    report_html = md_to_html(raw_report)
    report_html = _pillify_in_html(report_html, stocks_info)
    updated_at  = datetime.now(timezone.utc).strftime("%m/%d %H:%M UTC")

    # Build IIA_HISTORY payload: {"main||sub": [{d, s:{ticker:[tv,chg]}}, ...]}
    # Compact array form (tv, chg) to keep bundle size manageable。
    # ticker_net_inst per-ticker net_inst 反向索引 2026-05-19 起改走 Q17
    # (ticker_net_inst_history,ingest commit ed3b2e9)— 不再從 focal_breakdown
    # 推。原因:對「純近一年焦點」ticker(從沒進 universe)focal_breakdown
    # 永遠缺,反向索引拿不到 → 該 cluster sparkline / modal histogram 全空。
    theme_history_payload: dict[str, list] = {}
    for r in theme_history_rows:
        key = f"{r['main_industry']}||{r['sub_industry']}"
        d = r["rank_date"]
        date_str = d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]
        breakdown = r["focal_breakdown"] or {}
        if isinstance(breakdown, str):
            try:
                breakdown = json.loads(breakdown)
            except Exception:
                breakdown = {}
        # Compact 6-tuple per ticker: [tv, chg, close, net_inst, shares_out, volume]
        # shares_out 用來算 cluster market-cap weighted index(F0);
        # volume(2026-05-18 ingest 5a172be 起)目前未在前端使用,保留供未來
        # 統計或顯示「當日成交股數」用
        # _jc 數值壓縮:tv/net/shares/volume 取整、chg 4 位、close 2 位
        # (ingest 寫進 jsonb 的 float 帶 .0 尾巴與 float32 雜訊)
        stocks_compact = {
            tk: [_jc(v.get("tv")), _jc(v.get("chg"), 4), _jc(v.get("close"), 2),
                 _jc(v.get("net_inst")), _jc(v.get("shares_out")), _jc(v.get("volume"))]
            for tk, v in breakdown.items()
            if isinstance(v, dict)
        }
        theme_history_payload.setdefault(key, []).append({"d": date_str, "s": stocks_compact})

    # ticker_close_history (Q13) — per-ticker × per-date close + shares_out,
    # 400 天歷史。用來:
    # (1) hl_sub cluster chart modal 的「焦點股加權指數」資料源(theme_history
    #     沒有「近一年焦點」main 的 row,無法用 focal_breakdown 5-tuple)
    # (2) hl_sub cluster sparkline 也走這(close-based 趨勢)
    # 對 pan_sub 仍可用,但目前還靠 focal_breakdown(後續可漸進切過去)
    ticker_close_payload: dict[str, list[dict]] = {}
    # ticker_close_full:含 volume 的完整 per-ticker close history,server-side
    # 給「焦點股」頁算 5 日均成交金額 / MA10 / MA20 用(history.json 的
    # ticker_close_payload 不含 volume,維持 modal chart payload 精簡)。
    ticker_close_full: dict[str, list[dict]] = {}
    # ── Q38 / Q39 產業地圖(在此提前 fetch,讓 industry-map TW ticker 也納入下面
    #    的 ticker_close / ticker_net_inst 歷史 → modal 內子產業趨勢圖有資料)──
    indmap_rows: list[dict] = []
    indmap_edges: list[dict] = []
    # 人名 / 非產業焦點黑名單(2026-06-08):與焦點題材的前綴黑名單同精神,人名當
    # 產業地圖節點不妥(user 指定移除「黃仁勳」)。濾掉這些 focus 的 rows → 節點
    # 不生成;連到它的供應鏈邊因 tag2idx 查無自動跳過(下方再按名稱濾一次保持 Q39
    # log 準確)。statementdog 的 industry_focus_map 是獨立 dataset,與
    # theme_dictionary 的 FOCUS_PREFIX_BLOCKLIST 不共用。
    _INDMAP_FOCUS_EXCLUDE = {"黃仁勳"}
    try:
        indmap_rows = [dict(r) for r in await conn.fetch(
            "select focus_tag, focus_name, axis, axis_kind, axis_order, "
            "sub_industry, sub_order, description, ticker, stock_name, "
            "market, rating, rating_rank from industry_focus_map "
            "order by focus_name, axis_order, sub_order, rating_rank desc, ticker"
        )]
        indmap_rows = [r for r in indmap_rows
                       if (r.get("focus_name") or "").strip() not in _INDMAP_FOCUS_EXCLUDE]
        _im_focus = len({r.get("focus_tag") for r in indmap_rows})
        print(f"  industry_map (Q38): {len(indmap_rows)} rows, {_im_focus} focuses")
    except Exception as exc:
        print(f"  ⚠ Q38 industry_map query failed: {exc}")
    try:
        indmap_edges = [dict(r) for r in await conn.fetch(
            "select from_focus_tag, from_focus_name, to_focus_tag, to_focus_name, "
            "relation, strength from industry_supply_edges "
            "order by strength desc, from_focus_name"
        )]
        indmap_edges = [
            e for e in indmap_edges
            if (e.get("from_focus_name") or "").strip() not in _INDMAP_FOCUS_EXCLUDE
            and (e.get("to_focus_name") or "").strip() not in _INDMAP_FOCUS_EXCLUDE
        ]
        print(f"  industry_supply_edges (Q39): {len(indmap_edges)} edges")
    except Exception as exc:
        print(f"  ⚠ Q39 industry_supply_edges query failed: {exc}")
    # industry-map 純台股 ticker(4~6 碼數字)納入歷史 ticker 集,供子產業趨勢圖
    _indmap_tw = {r["ticker"] for r in indmap_rows
                  if r.get("ticker") and str(r["ticker"]).isdigit()}

    _hist_tickers = list(set(_focal_tw) | set(highlight_tickers) | _indmap_tw)

    async def _fetch_ticker_batched(sql: str, tickers: list[str], *,
                                     batch_size: int = 60, retries: int = 2,
                                     label: str) -> list:
        """分批 fetch 避免 Supabase Edge 單次 timeout / 6MB 上限(已踩過 546)。
        每 batch 失敗 retry,全 retry 都掛才放棄該 batch 並 raise。"""
        out = []
        for i in range(0, len(tickers), batch_size):
            chunk = tickers[i:i + batch_size]
            last_exc = None
            for attempt in range(retries + 1):
                try:
                    rows = await conn.fetch(sql, chunk)
                    out.extend(rows)
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt < retries:
                        await asyncio.sleep(0.8 * (attempt + 1))
            if last_exc is not None:
                raise RuntimeError(
                    f"{label} batch {i // batch_size + 1} (size={len(chunk)}) "
                    f"failed after {retries + 1} attempts: {last_exc}"
                ) from last_exc
        return out

    if _hist_tickers:
        try:
            tch_rows = await _fetch_ticker_batched(
                "SELECT ticker, rank_date, close, shares_out, volume, high, open, low FROM ticker_close_history "
                "WHERE ticker = ANY($1::text[]) "
                "AND rank_date >= current_date - INTERVAL '400 days' "
                "ORDER BY ticker, rank_date",
                _hist_tickers,
                label="Q13 ticker_close_history",
            )
            for r in tch_rows:
                # rank_date 是 timestamp(asyncpg → datetime),取 YYYY-MM-DD
                # 跟 theme_history payload 的 d 欄(YYYY-MM-DD)對齊,
                # _computeClusterSeries 的 dateSet union 才會 match
                _d = r["rank_date"]
                d_str = _d.strftime("%Y-%m-%d") if hasattr(_d, "strftime") else str(_d)[:10]
                # _jc 壓數值精度:價格 round(2) 去 float32 雜訊(=真實 tick 價),
                # 股數/成交股數取整 —— history.json / kline.json 體積減半的關鍵
                _close = _jc(r["close"], 2)
                _shares = _jc(r["shares_out"])
                _vol = _jc(r["volume"])
                _high = _jc(r["high"], 2)
                _open = _jc(r["open"], 2)
                _low  = _jc(r["low"], 2)
                ticker_close_payload.setdefault(r["ticker"], []).append({
                    "d": d_str, "c": _close, "s": _shares,
                })
                ticker_close_full.setdefault(r["ticker"], []).append({
                    "d": d_str, "c": _close, "s": _shares, "v": _vol,
                    "high": _high, "open": _open, "low": _low,
                })
            print(f"  ticker_close_history: {len(tch_rows)} rows for "
                  f"{len(ticker_close_payload)}/{len(_hist_tickers)} tickers")
            # 個股 modal 日 K 線(P2):per-ticker JSON 寫到 docs/kline/
            # <ticker>.json,lazy fetch,免暴露 anon key 給 client。
            # 格式:[[d,o,h,l,c,v], ...](compact array,~60 bytes/row)。
            # 不入 git(docs/kline/ 加 .gitignore),wrangler-action assets
            # 直接 deploy 整個 docs/。日 K 最多 730 天 (~50KB/檔)。
            # 2026-05-25 v2:per-ticker docs/kline/<tk>.json 改為單一
            # docs/kline.json 含所有 ticker(`{"b": stamp, "k": {tk: [[d,o,h,l,c,v],...], ...}}`)。
            # 原因:per-ticker 路徑 450 個 manifest entry 對 Cloudflare Workers
            # Static Assets 的 edge node sync 慢(實測 deploy 完 >40s 後 user fetch
            # 仍 404,且 retry 1.2s/2.5s 也沒救),單一 entry sync 較快。
            # client 端 _fetchKline 改 lazy 載 kline.json 一次,後續 ticker 從
            # in-memory dict 取。檔案 ~6MB / gzip ~2MB,跟 history.json 同等級。
            _build_stamp_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
            # 根因修正(2026-06-08):kline 的「今日」那根 K 一律以 trading_rankings
            # (stocks_info)的權威 OHLC 為準,不再純信 Q13 ticker_close_history。
            #
            # 為什麼:同一檔的「今日收盤」原本有兩個來源 —— 卡片 pill/cluster 走
            # trading_rankings(Q14,17:30 主 job),modal K 線走 Q13(另一支 ingest
            # job + 易 stale 的 kline.json 邊緣部署)。兩者不同步時,對 99% 股票只差
            # 1 天≈價差微小沒人發現;但處置/跌停股日跳 ±10% → modal 顯一個差 10% 的
            # 舊價、跟 pill 今日價打架(「處置股股價抓錯」的反覆根因)。在此把今日那
            # 根錨定到 trading_rankings → modal 末根 ≡ pill close,與 Q13 是否落後無關。
            _today_str = (tw_rank_date.strftime("%Y-%m-%d")
                          if hasattr(tw_rank_date, "strftime") else str(tw_rank_date)[:10])
            _kline_anchored = 0
            _kline_all: dict[str, list] = {}
            for tk, rows in ticker_close_full.items():
                kline_arr = [
                    [r["d"], r.get("open"), r.get("high"), r.get("low"),
                     r.get("c"), r.get("v")]
                    for r in rows
                    if r.get("open") is not None and r.get("c") is not None
                ]
                si = stocks_info.get(tk)
                if si and si.get("market") == "TW" and si.get("close_price") is not None:
                    c = _jc(si["close_price"], 2)
                    # 處置/跌停鎖死等情形 O/H/L 可能缺 → 退回 c(doji),不可留 None
                    # 否則 lightweight-charts 蠟燭畫不出來。
                    o = _jc(si["open"], 2) if si.get("open") is not None else c
                    h = _jc(si["high"], 2) if si.get("high") is not None else c
                    l = _jc(si["low"], 2) if si.get("low") is not None else c
                    has_today = bool(kline_arr) and kline_arr[-1][0] == _today_str
                    if has_today:
                        v = kline_arr[-1][5]  # Q13 已有今日 → 留其真實股數 volume
                    else:
                        tv = si.get("trading_value") or 0  # 無股數,用成交值/收盤近似
                        v = round(tv / c) if c else None
                    today_bar = [_today_str, o, h, l, c, v]
                    if has_today:
                        if abs((kline_arr[-1][4] or 0) - c) > 1e-9:
                            _kline_anchored += 1  # Q13 今日收盤與權威不符 → 覆寫
                        kline_arr[-1] = today_bar
                    elif not kline_arr or kline_arr[-1][0] < _today_str:
                        kline_arr.append(today_bar)  # Q13 落後沒今日 → 補權威今日根
                        _kline_anchored += 1
                if kline_arr:
                    _kline_all[tk] = kline_arr
            _kline_path = OUT_FILE.parent / "kline.json"
            _kline_path.write_text(
                json.dumps({"b": _build_stamp_iso, "k": _kline_all},
                           ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            _kline_size = _kline_path.stat().st_size
            print(f"  kline.json: {len(_kline_all)} tickers, {_kline_size:,} bytes "
                  f"(今日根錨定 trading_rankings:{_kline_anchored} 檔補/正)")
            # 不再寫 per-ticker docs/kline/<tk>.json fallback —— 跟 docs/kline.json
            # 同名衝突(file vs directory)疑似讓 Cloudflare Workers Static Assets
            # silent drop /kline.json manifest entry,造成線上 10 分鐘後仍 404
            # (假設 A:path collision,2026-05-25)。同時主動清掉舊目錄避殘留。
            import shutil
            _old_kline_dir = OUT_FILE.parent / "kline"
            if _old_kline_dir.exists():
                shutil.rmtree(_old_kline_dir)
        except Exception as exc:
            # Q13 失敗 = kline.json 與 history.json 的 ticker_close section 都拿不到。
            # 若繼續走完並 deploy,Cloudflare Workers Static Assets 會用「沒 kline.json」
            # 的版本整批替換邊緣 manifest,把上一次好的版本也抹掉 → 用戶端 404 直到下次
            # cron。直接 raise 中止 workflow,讓上次成功的 deploy 留在線上。
            print(f"  ✗ ticker_close_history (Q13) query failed: {exc}", file=sys.stderr)
            raise SystemExit(
                "[fatal] Q13 ticker_close_history 全 batch retry 後仍失敗,中止 deploy。"
                "讓上次成功的 kline.json 留在 CDN,等下個 cron 再試。"
            )

    # MA20 乖離率(熱門題材 cluster 卡「平均乖離」metric 用)— 由 Q13 close
    # 歷史自算,不再呼叫 yfinance。bias = (今日收盤 − 20 日均) / 20 日均 × 100;
    # 今日收盤取 stocks_info(trading_rankings),20 日均取 ticker_close_history
    # 最後 20 筆 close。算法與 build_focus_stock_page 內一致。
    for _t in _focal_tw:
        if _t not in stocks_info:
            continue
        _closes = [r["c"] for r in ticker_close_payload.get(_t, []) if r.get("c") is not None]
        if len(_closes) < 20:
            continue
        _ma20 = sum(_closes[-20:]) / 20
        try:
            _tc = stocks_info[_t].get("close_price")
            _tc = float(_tc) if _tc is not None else None
        except (TypeError, ValueError):
            _tc = None
        if _tc and _ma20:
            stocks_info[_t]["ma20_bias"] = (_tc - _ma20) / _ma20 * 100

    # Q17 — ticker_net_inst_history per-ticker × per-date 攤平歷史 net_inst
    # (NTD,T86/3insti × close)。取代 2026-05-18 從 theme_history.focal_breakdown
    # 反向索引建 ticker_net_inst 的舊 path(對「純近一年焦點」ticker — 從沒
    # 進過 universe — focal_breakdown 永遠缺,反向索引拿不到 → sparkline /
    # modal histogram 空)。Ingest commit ed3b2e9 起對「近一年焦點」字典
    # ~322 ticker × 400 day 寫滿;此處對 _hist_tickers(focal_tw ∪ highlight)
    # 範圍 fetch,pan_sub focal 若不在字典內 Q17 回 0 row 無影響。
    ticker_net_inst: dict[str, dict[str, float]] = {}  # ticker -> {date_str: net_inst (NTD)}
    if _hist_tickers:
        try:
            tni_rows = await _fetch_ticker_batched(
                "SELECT ticker, rank_date, net_inst FROM ticker_net_inst_history "
                "WHERE ticker = ANY($1::text[]) "
                "AND rank_date >= current_date - INTERVAL '400 days' "
                "ORDER BY ticker, rank_date",
                _hist_tickers,
                label="Q17 ticker_net_inst_history",
            )
            for r in tni_rows:
                _d = r["rank_date"]
                d_str = _d.strftime("%Y-%m-%d") if hasattr(_d, "strftime") else str(_d)[:10]
                ni = r["net_inst"]
                if ni is None:
                    continue
                # 取整(NTD;小數分毫無意義,payload 每值省 3 字元)
                ticker_net_inst.setdefault(r["ticker"], {})[d_str] = _jc(ni)
            print(f"  ticker_net_inst_history: {len(tni_rows)} rows for "
                  f"{len(ticker_net_inst)}/{len(_hist_tickers)} tickers")
        except Exception as exc:
            print(f"  ⚠ ticker_net_inst_history query failed (Q17 not deployed?): {exc}")

    # Q22 / Q23 — 籌碼股(選股雷達 sub-tab)資料源:
    #   Q22 ticker_chip_history daily 三大法人分項 net_shares(近 3 交易日)
    #   Q23 ticker_holder_dist 週資料(TDCC 集保大戶持股)
    # chip_signals[ticker] = {f3, t3, v3, f3_pct, t3_pct, lock, retail_chg}:
    #   近 3 日 = chip_history ∩ ticker_close_full 共同日期取末 3 筆(對齊
    #   外資/投信 net_shares 與成交量同 3 日,避免兩表 latest 日期差 1 天錯位)。
    chip_signals: dict[str, dict] = {}
    if _hist_tickers:
        chip_by_tk: dict[str, dict[str, tuple]] = {}
        try:
            chip_rows = await conn.fetch(
                "SELECT ticker, rank_date, foreign_net_shares, trust_net_shares "
                "FROM ticker_chip_history "
                "WHERE ticker = ANY($1::text[]) "
                "AND rank_date >= current_date - INTERVAL '30 days' "
                "ORDER BY ticker, rank_date",
                _hist_tickers,
            )
            for r in chip_rows:
                _d = r["rank_date"]
                d_str = _d.strftime("%Y-%m-%d") if hasattr(_d, "strftime") else str(_d)[:10]
                chip_by_tk.setdefault(r["ticker"], {})[d_str] = (
                    float(r["foreign_net_shares"]) if r["foreign_net_shares"] is not None else 0.0,
                    float(r["trust_net_shares"]) if r["trust_net_shares"] is not None else 0.0,
                )
            print(f"  ticker_chip_history (Q22): {len(chip_rows)} rows for "
                  f"{len(chip_by_tk)} tickers")
        except Exception as exc:
            print(f"  ⚠ ticker_chip_history query failed (Q22 not deployed?): {exc}")

        # TDCC 持股級距上 / 下限(股)。散戶 / 大戶皆改金額定義,免固定股數
        # 級距對高 / 低價股失真(¥3000 股 1 張即 300萬、¥10 股 100 張才 100萬)。
        _TDCC_UB = {1: 999, 2: 5000, 3: 10000, 4: 15000, 5: 20000, 6: 30000,
                    7: 40000, 8: 50000, 9: 100000, 10: 200000, 11: 400000,
                    12: 600000, 13: 800000, 14: 1000000}
        _TDCC_LB = {1: 1, 2: 1000, 3: 5001, 4: 10001, 5: 15001, 6: 20001,
                    7: 30001, 8: 40001, 9: 50001, 10: 100001, 11: 200001,
                    12: 400001, 13: 600001, 14: 800001, 15: 1000001}
        _RETAIL_CAP = 10_000_000   # 散戶:持股市值 < 1000萬(級距上限 × 股價)
        _BIG_FLOOR  = 50_000_000   # 大戶:持股市值 ≥ 5000萬(級距下限 × 股價)

        def _level_pct_sum(levels, lv_set: set) -> float | None:
            """lv_set 指定級距的 p(佔集保庫存%)加總;散戶 / 大戶共用。"""
            if isinstance(levels, str):
                try:
                    levels = json.loads(levels)
                except (ValueError, TypeError):
                    return None
            if not isinstance(levels, dict):
                return None
            tot = 0.0
            for L in lv_set:
                p = (levels.get(str(L)) or {}).get("p")
                if p is not None:
                    tot += float(p)
            return tot

        holder_by_tk: dict[str, dict] = {}
        try:
            hd_rows = await conn.fetch(
                "SELECT ticker, data_date, levels "
                "FROM ticker_holder_dist "
                "WHERE ticker = ANY($1::text[]) "
                "AND data_date >= current_date - INTERVAL '60 days' "
                "ORDER BY ticker, data_date",
                _hist_tickers,
            )
            _hd_acc: dict[str, list] = {}
            for r in hd_rows:
                _hd_acc.setdefault(r["ticker"], []).append(r)
            for tk, rows in _hd_acc.items():
                # 散戶 = 級距上限 × 股價 < 1000萬;大戶 = 級距下限 × 股價 ≥
                # 5000萬(中間 1000萬~5000萬 為中實戶)。big_chg = 大戶持股比
                # 週變(即「籌碼鎖定率」)。兩週用同一股價(最新收盤)→ 週變
                # 純反映持股結構,免受股價波動把級距推過門檻。
                close = stocks_info.get(tk, {}).get("close_price")
                retail_chg = big_chg = None
                if close and close > 0 and len(rows) >= 2:
                    retail_lv = {L for L, ub in _TDCC_UB.items()
                                 if ub * close < _RETAIL_CAP}
                    big_lv = {L for L, lb in _TDCC_LB.items()
                              if lb * close >= _BIG_FLOOR}
                    rp_now = _level_pct_sum(rows[-1]["levels"], retail_lv)
                    rp_prev = _level_pct_sum(rows[-2]["levels"], retail_lv)
                    if rp_now is not None and rp_prev is not None:
                        retail_chg = rp_now - rp_prev
                    bp_now = _level_pct_sum(rows[-1]["levels"], big_lv)
                    bp_prev = _level_pct_sum(rows[-2]["levels"], big_lv)
                    if bp_now is not None and bp_prev is not None:
                        big_chg = bp_now - bp_prev
                holder_by_tk[tk] = {"retail_chg": retail_chg, "big_chg": big_chg}
            print(f"  ticker_holder_dist (Q23): {len(hd_rows)} rows for "
                  f"{len(holder_by_tk)} tickers")
        except Exception as exc:
            print(f"  ⚠ ticker_holder_dist query failed (Q23 not deployed?): {exc}")

        for tk in _hist_tickers:
            cdates = chip_by_tk.get(tk)
            if not cdates:
                continue
            vol_by_d = {row["d"]: row["v"] for row in ticker_close_full.get(tk, [])
                        if row.get("v") is not None}
            common = sorted(d for d in cdates if d in vol_by_d)
            if len(common) < 3:
                continue
            last3 = common[-3:]
            f3 = sum(cdates[d][0] for d in last3)
            t3 = sum(cdates[d][1] for d in last3)
            v3 = sum(vol_by_d[d] for d in last3)
            if not v3 or v3 <= 0:
                continue
            hd = holder_by_tk.get(tk, {})
            chip_signals[tk] = {
                "f3": f3, "t3": t3, "v3": v3,
                "f3_pct": f3 / v3, "t3_pct": t3 / v3,
                "retail_chg": hd.get("retail_chg"), "big_chg": hd.get("big_chg"),
            }
        print(f"  chip_signals: {len(chip_signals)} tickers with 近3日籌碼")

    # 大盤 / 櫃買指數歷史(Q21)已提前到 snaps populate 之後 fetch(2026-06-11
    # 「指數漲跌單一真實來源」改動,見該處註解),此處 market_index_payload 已就緒。

    # ── Q36 / Q37 風控儀錶板(🛡️ 風控 tab,取代舊趨勢頁;ingest b67fa04)────
    # Q36 = snapshot 最新一筆(今日建議部位 + 4 組訊號 + 回測背書 meta);
    # Q37 = history 近 ~3y(畫「依建議部位調倉淨值 vs 買進持有」雙線)。
    # 誠實定位:OOS 未打贏 buy&hold(僅 3 次崩跌樣本),caveat 直接 render。
    risk_snapshot: dict | None = None
    risk_history: list[dict] = []

    def _risk_obj(v):
        return v if isinstance(v, (dict, list)) else (json.loads(v) if v else None)

    def _risk_f(v):
        return round(float(v), 4) if v is not None else None

    try:
        _r36 = await conn.fetchrow(
            "select snapshot_date, risk_score, position_pct, level, components, "
            "triggers, backtest_meta from risk_dashboard_snapshot "
            "order by snapshot_date desc limit 1"
        )
        if _r36:
            _sd = _r36["snapshot_date"]
            _sd = _sd.strftime("%Y-%m-%d") if hasattr(_sd, "strftime") else str(_sd)[:10]
            risk_snapshot = {
                "d": _sd,
                "score": round(float(_r36["risk_score"]), 3),
                "position": int(_r36["position_pct"]),
                "level": _r36["level"],
                "components": _risk_obj(_r36["components"]) or {},
                "triggers": _risk_obj(_r36["triggers"]) or [],
                "meta": _risk_obj(_r36["backtest_meta"]) or {},
            }
        _r37 = await conn.fetch(
            "select snapshot_date, risk_score, position_pct, twii_close, tpex_close, "
            "label_realized, strat_nav, bh_nav from risk_dashboard_history "
            "where snapshot_date >= (current_date - $1::int) order by snapshot_date asc",
            1100,
        )
        for r in _r37:
            _d = r["snapshot_date"]
            _d = _d.strftime("%Y-%m-%d") if hasattr(_d, "strftime") else str(_d)[:10]
            risk_history.append({
                "d": _d,
                "strat": _risk_f(r["strat_nav"]),
                "bh": _risk_f(r["bh_nav"]),
                "pos": int(r["position_pct"]) if r["position_pct"] is not None else None,
            })
        print(f"  risk_dashboard (Q36/Q37): snapshot={'yes' if risk_snapshot else 'no'}, "
              f"history={len(risk_history)} day, today_pos="
              f"{risk_snapshot['position'] if risk_snapshot else 'n/a'}")
    except Exception as exc:
        print(f"  ⚠ Q36/Q37 risk_dashboard query failed: {exc}")

    # ── Q40 / Q41 動能策略模擬器(📈 策略模擬 tab;ingest 195ac88)────────
    # trade_sim_nav = 每日 NAV + twii/etf 對照 + 當前持股 jsonb;
    # trade_sim_trades = 交易明細。ingest 每晚 22:05 全量重算後觸發 deploy。
    sim_nav_rows: list[dict] = []
    sim_trades: list[dict] = []
    try:
        sim_nav_rows = [dict(r) for r in await conn.fetch(
            "SELECT sim_date, nav, cash, twii, etf, etf2, positions FROM trade_sim_nav "
            "ORDER BY sim_date DESC LIMIT 200"
        )]
        sim_trades = [dict(r) for r in await conn.fetch(
            "SELECT sim_date, ticker, name, side, shares, price, reason, pnl, hold_days "
            "FROM trade_sim_trades ORDER BY sim_date DESC, id DESC LIMIT 500"
        )]
        print(f"  trade_sim (Q40/Q41): nav={len(sim_nav_rows)} day, "
              f"trades={len(sim_trades)}")
    except Exception as exc:
        print(f"  ⚠ Q40/Q41 trade_sim query failed: {exc}")

    # Q38 / Q39 產業地圖已在前面(_hist_tickers 構建前)fetch,讓 industry-map
    # ticker 也納入 ticker_close / ticker_net_inst 歷史(供 modal 內子產業趨勢圖)。

    # 「市場行情」ranking table 只顯前 N (RANKINGS_TOP_N=50),過濾 Q14 special
    # 與 Q15 focus_member 的 rank=NULL row(它們是 cluster detection universe,
    # 不該出現在 ranking 表)。cluster detection / stocks_info path 仍走完整
    # tw_ranks(含 special + focus_member)。
    _tw_rank_table_rows = [r for r in tw_ranks if r.get("rank") is not None][:RANKINGS_TOP_N]

    focus_html, modal_data = build_focus_html(
        tw_ranks, sub_clusters, stocks_info, theme_history_payload,
        market_index_payload, stock_meta,
        highlight_subs=highlight_subs,
        ticker_net_inst=ticker_net_inst,
        focus_hl_clusters=focus_hl_clusters,
        market_notes=market_notes,
        focus_daily_subs=focus_daily_subs,
        focus_sorted_dates=focus_sorted_dates,
        tw_breadth=tw_breadth,
        distill_stats=distill_stats,
    )

    notes_html  = build_notes_html(market_notes, podcast_rows, stocks_info)
    catalyst_html = build_catalyst_html(catalyst_events, stocks_info)
    risk_html   = build_risk_page(risk_snapshot, risk_history)
    tradesim_html = build_trade_sim_page(sim_nav_rows, sim_trades)
    indmap_html = build_industry_map_page(indmap_rows, stocks_info, indmap_edges)

    # ── 主動式 ETF(2026-05-20 對應 ingest f5faa21)──
    # Q18 拿全 23 檔 ETF master(按 AUM desc);Q19 對每 ETF 抓 latest holdings + diff;
    # Python 端 reverse-index 為 ticker → [etf-holding rows] 供個股 modal 用,
    # 同時餵 build_active_etf_page 渲 ETF tab UI。
    aetf_list: list[dict] = []
    aetf_holdings_by_etf: dict[str, list[dict]] = {}
    aetf_holdings_by_ticker: dict[str, list[dict]] = {}
    try:
        aetf_list = [dict(r) for r in await conn.fetch(
            "SELECT etf_code, etf_name, short_name, issuer, aum_ntd, "
            "nav_per_unit, units_outstanding, listing_date, expense_ratio, "
            "fund_url FROM active_etf_meta "
            "ORDER BY aum_ntd DESC NULLS LAST, etf_code"
        )]
        print(f"  active_etf_meta: {len(aetf_list)} ETFs")
        for etf in aetf_list:
            try:
                rows = await conn.fetch(
                    "WITH last_two AS (SELECT DISTINCT holding_date FROM active_etf_holdings "
                    "WHERE etf_code = $1 ORDER BY holding_date DESC LIMIT 2), "
                    "has_baseline AS (SELECT COUNT(*) >= 2 AS yes FROM last_two), "
                    "latest AS (SELECT MAX(holding_date) AS d FROM last_two), "
                    "prev AS (SELECT MIN(holding_date) AS d FROM last_two "
                    "WHERE holding_date < (SELECT d FROM latest)) "
                    "SELECT COALESCE(t.ticker, y.ticker) AS ticker, "
                    "COALESCE(t.name, y.name) AS name, t.lots, t.weight_pct, "
                    "t.market_value_ntd, t.market, t.is_cash, y.lots AS prev_lots, "
                    "CASE WHEN (SELECT yes FROM has_baseline) "
                    "THEN COALESCE(t.lots, 0) - COALESCE(y.lots, 0) "
                    "ELSE NULL END AS lots_chg, "
                    "(SELECT yes FROM has_baseline) AS has_baseline, "
                    "(SELECT d FROM latest) AS data_date, "
                    "CASE WHEN NOT (SELECT yes FROM has_baseline) THEN NULL "
                    "WHEN t.lots IS NULL OR t.lots = 0 THEN 'exit' "
                    "WHEN y.lots IS NULL OR y.lots = 0 THEN 'new' "
                    "WHEN t.lots > y.lots THEN 'add' "
                    "WHEN t.lots < y.lots THEN 'reduce' "
                    "ELSE 'hold' END AS action "
                    "FROM (SELECT * FROM active_etf_holdings WHERE etf_code = $1 "
                    "AND holding_date = (SELECT d FROM latest)) t "
                    "FULL OUTER JOIN (SELECT * FROM active_etf_holdings WHERE etf_code = $1 "
                    "AND holding_date = (SELECT d FROM prev)) y ON t.ticker = y.ticker "
                    "ORDER BY t.weight_pct DESC NULLS LAST",
                    etf["etf_code"],
                )
                holdings = [dict(r) for r in rows]
                aetf_holdings_by_etf[etf["etf_code"]] = holdings
                # 把該 ETF 最新 holdings 日期帶到 etf dict,讓 tab bar 能顯「更新日期」。
                # holdings 每 row 都帶同樣的 data_date(latest CTE);空 list 視為 None。
                etf["data_date"] = holdings[0].get("data_date") if holdings else None
                # Reverse index for modal:per ticker
                for h in holdings:
                    tk = h.get("ticker")
                    if not tk:
                        continue
                    aetf_holdings_by_ticker.setdefault(tk, []).append({
                        **h,
                        "etf_code": etf["etf_code"],
                        "short_name": etf.get("short_name"),
                        "issuer": etf.get("issuer"),
                        "aum_ntd": etf.get("aum_ntd"),
                    })
            except Exception as exc:
                print(f"  ⚠ active_etf_holdings Q19({etf['etf_code']}) failed: {exc}")
    except Exception as exc:
        print(f"  ⚠ active_etf_meta Q18 failed: {exc}")

    # 公開頁只列「純台股 AUM 前 10 大」主動式 ETF:
    #   排除指定碼(非純台股 / 無持股,user 2026-06-04 指定)+ 需有持股資料,
    #   aetf_list 已按 AUM desc(Q18 ORDER BY)→ 取前 10。
    #   全球 / 美國型(如 00990A AI新經濟、00997A 美國增長)與無持股者(00400A)移除。
    #   ingest 端對應停掉非保留檔的爬蟲排程(見 commit 附 prompt)。
    _AETF_EXCLUDE = {"00990A", "00400A", "00997A"}
    _AETF_TOP_N = 10
    aetf_list = [e for e in aetf_list
                 if e["etf_code"] not in _AETF_EXCLUDE
                 and aetf_holdings_by_etf.get(e["etf_code"])][:_AETF_TOP_N]
    _keep_codes = {e["etf_code"] for e in aetf_list}
    aetf_holdings_by_etf = {k: v for k, v in aetf_holdings_by_etf.items() if k in _keep_codes}
    aetf_holdings_by_ticker = {
        tk: kept for tk, lst in aetf_holdings_by_ticker.items()
        if (kept := [h for h in lst if h.get("etf_code") in _keep_codes])
    }
    print(f"  主動式 ETF 公開保留(純台股 AUM 前 {_AETF_TOP_N}): {sorted(_keep_codes)}")

    # 對每個 ticker 內 ETF 列表按 AUM desc 排序(Q19 個別 fetch 沒帶 ETF aum,
    # 反向 index 時各 ETF 順序不一定)
    for tk, lst in aetf_holdings_by_ticker.items():
        lst.sort(key=lambda h: -(float(h.get("aum_ntd") or 0)))

    # 各家主動式 ETF 官方公布時間不同 → 每檔取自己最新 updated_at(台北時間),
    # 顯示在該 ETF tab 頂部「持股更新」。單一頁面 MAX 會誤導故不用。
    try:
        _etf_upd_rows = await conn.fetch(
            "SELECT etf_code, MAX(updated_at) AS t FROM active_etf_holdings GROUP BY etf_code")
        _etf_ts = {r["etf_code"]: _fmt_data_stamp(r["t"]) for r in _etf_upd_rows}
        # 台北日期版,供「資料已更新 n/total」badge 判「今天公布了沒」(見 _taipei_date)
        _etf_ud = {r["etf_code"]: _taipei_date(r["t"]) for r in _etf_upd_rows}
    except Exception as exc:
        print(f"  ⚠ active_etf per-ETF updated_at query failed: {exc}")
        _etf_ts, _etf_ud = {}, {}
    for _etf in aetf_list:
        _etf["updated_ts"] = _etf_ts.get(_etf.get("etf_code"))
        _etf["updated_date"] = _etf_ud.get(_etf.get("etf_code"))
    from collections import defaultdict as _dd
    # ── 近期加減碼趨勢(逐日,跨保留 ETF;retention 目前 ~14 天,延長後到一個月)──
    # 多日持股 diff:同 (etf,ticker) 連續持股日 lots 差 × 每張價 → 當日加 / 減碼金額。
    # 注意:全清倉(該日整筆消失)不在連續列差內,故 flow 略低估清倉量(清倉另見共識區)。
    aetf_trend: list[dict] = []
    if _keep_codes:
        try:
            _tr = await conn.fetch(
                "SELECT etf_code, holding_date, ticker, lots, market_value_ntd "
                "FROM active_etf_holdings WHERE etf_code = ANY($1::text[]) "
                "AND holding_date >= current_date - INTERVAL '35 days' "
                "ORDER BY etf_code, ticker, holding_date",
                sorted(_keep_codes))
            _seq: dict[tuple, list] = _dd(list)
            for r in _tr:
                _seq[(r["etf_code"], r["ticker"])].append(
                    (_aetf_date_fmt(r["holding_date"]), _aetf_f(r["lots"]), _aetf_f(r["market_value_ntd"])))
            _day_add: dict[str, float] = _dd(float)
            _day_red: dict[str, float] = _dd(float)
            for (_etf, _tk), _s in _seq.items():
                _s.sort()
                for _i in range(1, len(_s)):
                    (_d0, _l0, _m0), (_d1, _l1, _m1) = _s[_i - 1], _s[_i]
                    if _l0 is None or _l1 is None:
                        continue
                    _lc = _l1 - _l0
                    if _lc == 0:
                        continue
                    _price = (_m1 / _l1) if (_l1 and _m1 is not None) else 0
                    _val = _lc * _price
                    (_day_add if _val > 0 else _day_red)[_d1] += _val
            _dates = sorted(set(_day_add) | set(_day_red))
            aetf_trend = [{"d": d, "add": round(_day_add.get(d, 0)), "red": round(_day_red.get(d, 0))}
                          for d in _dates]
            print(f"  主動式 ETF 趨勢: {len(aetf_trend)} 交易日 diff")
        except Exception as exc:
            print(f"  ⚠ active_etf 多日趨勢 query 失敗: {exc}")

    aetf_html = build_active_etf_page(aetf_list, aetf_holdings_by_etf, aetf_trend)

    # ── 各頁「資料最後更新時間」(Q31-Q35)──────────────────────────────────
    # 取各資料源表最新寫入 timestamptz,轉台北時間。單條失敗(403 / 空表)只是
    # 該頁不顯 badge,不影響整體 render。
    async def _max_ts(query: str, *params) -> str | None:
        try:
            return _fmt_data_stamp(await conn.fetchval(query, *params))
        except Exception as exc:
            print(f"  ⚠ data-stamp query failed ({query[:48]}...): {exc}")
            return None
    # Q31/Q32 帶 filter 走索引(見 db-proxy allowlist 註解):純 MAX 全表掃描在大表
    # 冷啟動 / cron 寫入尖峰會撞 Edge CPU 上限 timeout。
    ts_rankings = await _max_ts(
        "SELECT MAX(created_at) FROM trading_rankings "
        "WHERE market='TW' AND rank_date >= current_date - INTERVAL '5 days'")
    ts_chip     = await _max_ts(
        "SELECT MAX(updated_at) FROM ticker_chip_history "
        "WHERE ticker = ANY($1::text[]) AND rank_date >= current_date - INTERVAL '14 days'",
        _hist_tickers) if _hist_tickers else None
    ts_reports  = await _max_ts("SELECT MAX(created_at) FROM analysis_reports")
    focus_stamp_html  = _stamp_badge(ts_rankings)
    # 選股雷達:rankings 與籌碼更新時間併同一行右上(籌碼不另佔一列)
    fstock_stamp_html = _stamp_badge_multi([("資料更新", ts_rankings), ("籌碼資料更新", ts_chip)])
    notes_stamp_html  = _stamp_badge(ts_reports)
    market_stamp_html = _stamp_badge(ts_reports)

    # ── 焦點股 tab(2026-05-20):出量股 / 潛力股,來源 = hl_sub focal union ──
    _today_str = tw_rank_date.strftime("%Y-%m-%d") if tw_rank_date else ""
    # 潛力股 condition B 需「前一交易日入選交集股」名單 → 重算昨日 focus pipeline
    _yest_intersect = await _compute_yesterday_intersect(
        conn, ticker_close_full, stock_meta, _today_str)
    focus_stock_html = build_focus_stock_page(
        focus_hl_clusters, stocks_info, ticker_close_full,
        stock_meta, aetf_holdings_by_ticker, _today_str, _yest_intersect,
        chip_signals,
    )

    # ── 個股 modal data:2026-05-20 取代「intro + analyst」為「持股主動式 ETF」表 ──
    # _yf_analyst_batch + _build_company_intro_html + _build_analyst_html + radar
    # SVG 全廢除(IIA_RADAR / _radarSvg 一併移除)。
    _all_modal_tickers: set[str] = set(modal_data.keys())
    if market_notes and market_notes.get("topics"):
        for _topic in market_notes["topics"]:
            _all_modal_tickers.update(_topic.get("tickers", []))
    for _tk in _all_modal_tickers | set(modal_data.keys()):
        modal_data[_tk] = _aetf_render_modal_body(
            aetf_holdings_by_ticker.get(_tk, []),
            stock_meta.get(_tk),
        )

    # ── Indicator helpers ─────────────────────────────────────────────────────
    def ind(sym):
        d = snaps.get(sym, {})
        return d.get("close"), d.get("chg")

    # ── SEO / Open Graph(Line / FB / X / Google preview)──────────────────────
    site_url = "https://stockgg.v4578469.workers.dev"
    _twii_close, _twii_chg = ind("^TWII")
    _n_themes = len(sub_clusters)
    _seo_bits = ["台股每日題材趨勢分析"]
    if _twii_close is not None and _twii_chg is not None:
        _seo_bits.append(f"加權指數 {_twii_close:,.0f}({_twii_chg:+.2f}%)")
    if _n_themes:
        _seo_bits.append(f"{_n_themes} 個熱門題材")
    _seo_bits.append("外資三大法人流向、AI 智能解析")
    seo_description = "｜".join(_seo_bits)[:155]

    # Modal data JS (escaped JSON string values)
    modal_js_entries = ",\n".join(
        f'  {json.dumps(k)}: {json.dumps(v)}'
        for k, v in modal_data.items()
    )

    # Radar chart metrics 計算 + IIA_RADAR JSON payload 2026-05-20 全廢
    # (個股 modal body 改為「持股主動式 ETF」表,server-side render 進
    # artModalData,前端不再需要客戶端雷達 SVG)。

    # Catalyst preview modal payload(2026-05-19 改 chip inline expandable →
    # showCatalystModal 彈窗,複用 art-modal dialog)
    _has_pv = [ev for ev in catalyst_events if (ev.get("preview_text") or "").strip()]
    catalyst_modal_data_json = json.dumps(
        {int(ev["id"]): md_to_html_simple(ev["preview_text"]) for ev in _has_pv},
        ensure_ascii=False, separators=(",", ":"),
    )
    catalyst_modal_titles_json = json.dumps(
        {int(ev["id"]): ev["title"] for ev in _has_pv},
        ensure_ascii=False, separators=(",", ":"),
    )
    # ── Page HTML ─────────────────────────────────────────────────────────────
    # CSS / JS 2026-05 起抽成 docs/style.css + docs/app.js 獨立檔(原本內嵌
    # 在這個 f-string,~2000 行 + escaping 雷區)。內容雜湊當 ?v= cache-bust,
    # 改檔即自動失效舊快取。
    _docs_dir = OUT_FILE.parent
    css_ver = (hashlib.md5((_docs_dir / "style.css").read_bytes()).hexdigest()[:8]
               if (_docs_dir / "style.css").exists() else "0")
    js_ver = (hashlib.md5((_docs_dir / "app.js").read_bytes()).hexdigest()[:8]
              if (_docs_dir / "app.js").exists() else "0")
    # build_stamp 讓每次 regen 的 HTML 必有新 hash,Cloudflare Workers Static Assets
    # 才會強制重傳替換掉舊版(2026-05-25 修:wrangler 偶爾會卡舊 manifest)
    build_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    page = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IIA 投資情報 {report_date}</title>
<meta name="description" content="{seo_description}">
<meta name="theme-color" content="#0f1117">
<link rel="canonical" href="{site_url}">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📈</text></svg>">
<!-- Open Graph(Facebook / Line / 一般 social preview)-->
<meta property="og:type" content="website">
<meta property="og:locale" content="zh_TW">
<meta property="og:site_name" content="IIA 投資情報">
<meta property="og:url" content="{site_url}">
<meta property="og:title" content="IIA 投資情報 {report_date}">
<meta property="og:description" content="{seo_description}">
<!-- Twitter Card -->
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="IIA 投資情報 {report_date}">
<meta name="twitter:description" content="{seo_description}">
<link rel="stylesheet" href="style.css?v={css_ver}">
</head>
<body>

<header>
  <button class="brand" onclick="showTab('focus');window.scrollTo(0,0);" title="回首頁">IIA 投資情報</button>
  <nav class="tabs">
    <button class="tab-btn active" data-tab="focus"    onclick="showTab('focus')">熱門題材</button>
    <button class="tab-btn"        data-tab="fstock"   onclick="showTab('fstock')">選股雷達</button>
    <button class="tab-btn"        data-tab="aetf"     onclick="showTab('aetf')">主動式 ETF</button>
    <button class="tab-btn"        data-tab="indmap"   onclick="showTab('indmap')">🗺️ 產業地圖</button>
    <button class="tab-btn"        data-tab="notes"    onclick="showTab('notes')">市場話題</button>
    <button class="tab-btn"        data-tab="market"   onclick="showTab('market')">國際金融</button>
    <button class="tab-btn"        data-tab="risk"     onclick="showTab('risk')">🛡️ 風控</button>
    <button class="tab-btn"        data-tab="tradesim" onclick="showTab('tradesim')">📈 策略模擬</button>
  </nav>
  <div class="search-box">
    <input type="search" id="site-search" placeholder="搜尋 ticker / 公司"
           autocomplete="off" spellcheck="false"
           oninput="onSearchInput(this.value)"
           onfocus="onSearchInput(this.value)"
           onkeydown="onSearchKey(event)">
    <div class="search-dropdown" id="search-dropdown" hidden></div>
  </div>
</header>

<div class="wrap">
  <!-- Tab 1: 國際金融(原「市場行情」) -->
  <div id="tab-market" class="tab-pane">
    {market_stamp_html}
    <div class="card">
      <div class="sec">每日分析報告（{report_date}）</div>
      <div class="report">{report_html or '<p style="color:var(--muted)">今日報告尚未生成</p>'}</div>
    </div>
    <div class="card">
      <div class="sec">📅 事件日曆（前 2 週 ~ 後 3 週）</div>
      {catalyst_html}
    </div>
    <div class="ranks">
      <div class="card">
        <div class="sec">美股 成交值前 {RANKINGS_TOP_N}</div>
        <table>
          <thead><tr><th>#</th><th>代號</th><th>名稱</th>
            <th style="text-align:right">股價(漲跌%)</th>
            <th style="text-align:right">成交值</th></tr></thead>
          <tbody>{rank_rows_html(us_ranks, 'US')}</tbody>
        </table>
      </div>
      <div class="card">
        <div class="sec">台股 成交值前 {RANKINGS_TOP_N}</div>
        <table>
          <thead><tr><th>#</th><th>代號</th><th>名稱</th>
            <th style="text-align:right">股價(漲跌%)</th>
            <th style="text-align:right">成交值</th></tr></thead>
          <tbody>{rank_rows_html(_tw_rank_table_rows, 'TW')}</tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Tab 2: 熱門題材(預設首頁) -->
  <div id="tab-focus" class="tab-pane active">
    {focus_stamp_html}
    {focus_html}
  </div>

  <!-- 焦點排行 tab 2026-05-19 移除 -->

  <!-- Tab: 選股雷達(原「焦點股」;出量股 / 潛力股 / 交集股 / 新高股 / 成長股) -->
  <div id="tab-fstock" class="tab-pane">
    {fstock_stamp_html}
    {focus_stock_html}
  </div>

  <!-- Tab: 主動式 ETF(更新時間 per-ETF 顯示於各檔頂部,各家公布時間不同) -->
  <div id="tab-aetf" class="tab-pane">
    {aetf_html}
  </div>

  <!-- Tab 4: 市場話題(原「股市筆記」) -->
  <div id="tab-notes" class="tab-pane">
    {notes_stamp_html}
    {notes_html}
  </div>

  <!-- Tab: 🗺️ 產業地圖(焦點產業階層 + 跨產業關聯;資料 = ingest Q38) -->
  <div id="tab-indmap" class="tab-pane">
    {indmap_html}
  </div>

  <!-- Tab: 🛡️ 風控儀錶板(取代舊趨勢頁,2026-06-06;資料 = ingest Q36/Q37) -->
  <div id="tab-risk" class="tab-pane">
    {risk_html}
  </div>

  <!-- Tab: 📈 策略模擬(動能策略 paper trading;資料 = ingest Q40/Q41,每晚 22:05 重算) -->
  <div id="tab-tradesim" class="tab-pane">
    {tradesim_html}
  </div>

</div>

<!-- Article modal (個股 modal,加大 + 左右導覽 + counter 比照 tc-modal,2026-05-25) -->
<dialog id="art-modal">
  <div class="art-shell">
    <div class="art-topbar">
      <span class="art-counter" id="art-counter" aria-live="polite"></span>
      <button class="art-close" type="button" aria-label="關閉"
              onclick="document.getElementById('art-modal').close()">✕</button>
    </div>
    <div class="art-shell-row">
      <div class="art-nav art-nav-left">
        <button class="art-nav-arrow" type="button" title="上一檔" aria-label="上一檔"
                id="art-nav-prev" onclick="artNavTicker('prev')">←</button>
      </div>
      <div class="art-panel">
        <div class="modal-hdr">
          <span class="modal-hdr-title" id="modal-title"></span>
        </div>
        <div class="modal-body" id="modal-body"></div>
      </div>
      <div class="art-nav art-nav-right">
        <button class="art-nav-arrow" type="button" title="下一檔" aria-label="下一檔"
                id="art-nav-next" onclick="artNavTicker('next')">→</button>
      </div>
    </div>
  </div>
</dialog>

<!-- 產業地圖:跨產業關聯 modal(點個股 → 它出現的所有焦點產業) -->
<dialog id="im-modal">
  <div class="im-modal-shell">
    <div class="im-modal-top">
      <span class="im-modal-title" id="im-modal-title"></span>
      <button class="im-modal-close" type="button" aria-label="關閉"
              onclick="document.getElementById('im-modal').close()">✕</button>
    </div>
    <div class="im-modal-body" id="im-modal-body"></div>
  </div>
</dialog>

<!-- Theme chart modal (子產業 6 個月 TV / 平均漲跌 趨勢) -->
<dialog id="theme-chart-dialog">
  <div class="tc-shell">
  <div class="tc-topbar">
    <button class="tc-sort-chip" data-sort="tv" type="button" onclick="tcSetSort('tv')">成交金額</button>
    <button class="tc-sort-chip active" data-sort="chg" type="button" onclick="tcSetSort('chg')">平均漲跌</button>
    <button class="tc-sort-chip" data-sort="bias" type="button" onclick="tcSetSort('bias')">平均乖離</button>
    <button class="tc-sort-chip" data-sort="pe" type="button" onclick="tcSetSort('pe')">平均 PE</button>
    <button class="tc-sort-chip" data-sort="peg" type="button" onclick="tcSetSort('peg')">平均 PEG</button>
    <span class="tc-counter" id="tc-counter" aria-live="polite"></span>
    <button class="tc-close" type="button" aria-label="關閉"
            onclick="document.getElementById('theme-chart-dialog').close()">✕</button>
  </div>
  <div class="tc-shell-row">
  <div class="tc-nav tc-nav-left">
    <button class="tc-nav-arrow" type="button" title="上一個題材" aria-label="上一個題材"
            onclick="tcNavTheme('prev')">←</button>
  </div>
  <div class="tc-panel">
  <div class="tc-hdr">
    <div class="tc-title" id="tc-title" style="flex:1;min-width:0"></div>
    <div class="tc-period">
      <button class="tc-period-chip" data-period="1m" type="button" onclick="setChartPeriod('1m')">1M</button>
      <button class="tc-period-chip" data-period="3m" type="button" onclick="setChartPeriod('3m')">3M</button>
      <button class="tc-period-chip active" data-period="6m" type="button" onclick="setChartPeriod('6m')">6M</button>
      <button class="tc-period-chip" data-period="1y" type="button" onclick="setChartPeriod('1y')">1Y</button>
      <button class="tc-period-chip" data-period="all" type="button" onclick="setChartPeriod('all')">ALL</button>
    </div>
  </div>
  <div class="tc-body">
    <!-- 左欄:焦點 ticker 垂直列表(點擊在 modal 內 disable;依成交金額 desc 排序) -->
    <aside class="tc-tickerlist-col">
      <div class="tc-tickerlist-label">焦點 · 點擊納入/排除</div>
      <div class="tc-ticker-chips" id="tc-ticker-chips"></div>
    </aside>

    <!-- 右欄:兩張 chart 上下排列,各自 flex:1 自適應 -->
    <div class="tc-charts-col">
      <!-- Chart 1(上):焦點股加權指數 / 個股強弱 mode tab(右上)-->
      <div class="tc-chart-label">
        焦點股加權指數
        <span class="tc-info" tabindex="0"
              title="加權指數計算法&#10;1. 每檔焦點股當日市值 = 收盤價 × 流通在外股數&#10;2. cluster daily mcap = Σ 全部焦點股當日市值;某檔某日缺資料時用該檔最後一次有資料的 close × shares 延續(per-ticker forward-fill,標準加權指數做法)&#10;3. 三條線(cluster / TWII / TPEX)同時 rebase 到 100(取三條共同起點當基準),純看相對強弱不看絕對水位&#10;4. cluster 線會依「焦點 chip 列表」即時重算&#10;5. 個股強弱模式:focal 內 enabled 個股各自 rebase 100 from startDate,互比強弱;左側 toggle 同步控顯隱">ⓘ</span>
        <span class="tc-legend">
          <button class="tc-leg-chip leg-cluster active" type="button" onclick="toggleIndexLine('cluster')"><span class="leg-sw"></span>焦點股</button>
          <button class="tc-leg-chip leg-twii active" type="button" onclick="toggleIndexLine('twii')"><span class="leg-sw"></span>大盤(TWII)</button>
          <button class="tc-leg-chip leg-tpex active" type="button" onclick="toggleIndexLine('tpex')"><span class="leg-sw"></span>櫃買(TPEX)</button>
        </span>
        <span class="tc-tk-legend" id="tc-tk-legend"></span>
        <span class="tc-price-mode">
          <button class="tc-mode-chip active" data-cmode="index" type="button" onclick="setChartMode('index')">指數</button>
          <button class="tc-mode-chip" data-cmode="strength" type="button" onclick="setChartMode('strength')">個股</button>
        </span>
      </div>
      <div class="tc-chart" id="tc-chart-price"></div>

      <!-- Chart 2(下):三大法人資金淨流入流出 + 當日/累計 切換 -->
      <div class="tc-chart-label">
        三大法人資金淨流入流出(億 TWD)
        <span class="tc-info" tabindex="0"
              title="三大法人(外資 + 投信 + 自營商)當日合計買賣超「金額」(NTD)。&#10;cluster 當日淨流入 = Σ 全部焦點股淨買賣金額(單位轉億 TWD);某檔某日缺資料當 0(不 forward-fill,因為法人買賣超是日結 transaction)。&#10;紅柱 = 法人淨買、綠柱 = 法人淨賣。&#10;切換「累計」會把當日數值改成從圖表起點開始的滾動累加,看資金長期流向。">ⓘ</span>
        <span class="tc-net-mode">
          <button class="tc-mode-chip active" data-mode="daily" type="button" onclick="setNetMode('daily')">當日</button>
          <button class="tc-mode-chip" data-mode="cum" type="button" onclick="setNetMode('cum')">累計</button>
        </span>
      </div>
      <div class="tc-chart" id="tc-chart-net"></div>

      <div class="tc-empty" id="tc-empty" style="display:none">尚無歷史資料</div>
    </div>
  </div>
  </div>
  <div class="tc-nav tc-nav-right">
    <button class="tc-nav-arrow" type="button" title="下一個題材" aria-label="下一個題材"
            onclick="tcNavTheme('next')">→</button>
  </div>
  </div>
  </div>
</dialog>

<button id="scroll-top-btn" class="scroll-top-btn" type="button"
        title="回到頂端" aria-label="回到頁面頂端"
        onclick="window.scrollTo({{top:0,behavior:'smooth'}})">↑</button>

<footer>
  <div class="disclaimer">
    <h3>⚠ 投資免責聲明</h3>
    <p>本網站內容由自動化系統匯整公開市場資料、研究文章與 AI 分析模型產出，
    僅供個人參考與資訊揭露之用，<strong>不構成任何形式的投資建議、要約或推薦</strong>。
    所有資料未經獨立查證，可能含有錯誤、延遲或遺漏，且不保證即時、準確或完整。</p>
    <p>使用者應自行評估投資風險、進行獨立判斷，並諮詢合格的金融、會計、稅務或法律專業人士。
    依本網站內容所為之任何投資決策及其後果，由使用者完全自負，本網站經營者及其關聯方
    對使用者因使用或無法使用本網站所致之任何直接或間接損失，
    <strong>概不負任何責任</strong>。</p>
    <p>本網站所引用之第三方品牌、商標、節目名稱、文章標題與股票代號，
    皆為其各自所有者之財產，僅作為事實識別與引用之用，並無代表、授權或背書之意涵。</p>
  </div>
  <div class="meta">StockGG &nbsp;·&nbsp; 資料僅供參考，不構成投資建議</div>
</footer>

<script>
const artModalData = {{
{modal_js_entries}
}};
const catalystModalData = {catalyst_modal_data_json};
const catalystModalTitles = {catalyst_modal_titles_json};
</script>
<script src="app.js?v={js_ver}"></script>
<!-- build {build_stamp} -->
</body>
</html>"""

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(page, encoding="utf-8")
    print(f"Generated {OUT_FILE}  ({len(page):,} bytes)")

    # 把 chart 用的歷史 payload 寫到獨立 history.json,modal 首次打開才 fetch。
    # 結構:
    #   history:          {"main||sub":[{d, s:{ticker:[tv,chg,close,net,shares]}}, ...]}
    #   index:            {"TWII":[{d, close}], "TPEX":[...]}
    #   ticker_close:     {ticker:[{d, c, s}, ...]}  ← Q13,for hl_sub 加權指數
    #   ticker_net_inst:  {ticker:{date: net_shares}} ← per-ticker 反向索引,
    #                     hl_sub cluster sparkline + histogram 跨 main 合成用
    ticker_net_inst_payload = {
        tk: [{"d": d, "n": v} for d, v in sorted(days.items())]
        for tk, days in ticker_net_inst.items()
    }
    hist_file = OUT_FILE.parent / "history.json"
    hist_file.write_text(
        json.dumps(
            {
                "history": theme_history_payload,
                "index": market_index_payload or {},
                "ticker_close": ticker_close_payload,
                "ticker_net_inst": ticker_net_inst_payload,
            },
            ensure_ascii=False, separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    print(f"Generated {hist_file}  ({hist_file.stat().st_size:,} bytes)")


if __name__ == "__main__":
    asyncio.run(generate())
