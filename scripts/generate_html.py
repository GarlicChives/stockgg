#!/usr/bin/env python3
"""Generate docs/index.html from latest DB data for GitHub Pages.

Three-tab layout:
  市場行情 — Full AI report + US/TW rankings
  焦點股   — TW/US sub-tabs, article-matched stocks + popup modal
  股市筆記  — Cross-source topic intersection + podcast notes (collapsible)

Fixed elements:
  - Ticker tape (sticky top, seamless continuous scroll)
  - Direction badge (fixed top-right: short/mid term + report date)
"""
import asyncio
import collections
import hashlib
import html as html_lib
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import db
from dotenv import load_dotenv

load_dotenv()

from src.analysis.focus_themes import (
    detect_industry_clusters,
    detect_focus_clusters,
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
        quote = f"{price_str}({pct_str})" if chg is not None else price_str
    else:
        quote = pct_str
    name_span = f'<span class="sp-name">{html_lib.escape(name[:8])}</span>' if name else ""
    click = f" onclick='showArtModal({json.dumps(ticker)},{json.dumps(name[:12])})'" if clickable else ""
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
            quote = f"{price_str} ({pct_str})" if chg is not None else price_str
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


def _sparkline_bars_svg(values: list[float], width: int = 84, height: int = 22) -> str:
    """Histogram sparkline:每天一根 bar,紅(正/買)綠(負/賣)。
    values 是 daily 三大法人淨流入金額(億 TWD),正買負賣。
    """
    if not values or all(v == 0 for v in values):
        return ""
    abs_max = max(abs(v) for v in values)
    if abs_max <= 0:
        return ""
    n = len(values)
    bar_w = width / n
    mid = height / 2
    bars = []
    for i, v in enumerate(values):
        x = i * bar_w
        h = abs(v) / abs_max * (height / 2 - 1)
        if h < 0.5:
            h = 0.5
        if v >= 0:
            y = mid - h
            cls = "spark-up"
        else:
            y = mid
            cls = "spark-down"
        bars.append(
            f'<rect class="{cls}" x="{x:.2f}" y="{y:.2f}" '
            f'width="{max(bar_w - 0.4, 0.5):.2f}" height="{h:.2f}" />'
        )
    return (
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" preserveAspectRatio="none">'
        + "".join(bars)
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


def _industry_section_html(
    clusters: list[IndustryCluster],
    all_stocks: dict,
    level: str,
    history_payload: dict | None = None,
    highlight_subs: dict[str, list[tuple[str, str]]] | None = None,
    stock_meta: dict | None = None,
    ticker_net_inst: dict[str, dict[str, float]] | None = None,
    topics_by_ticker: dict[str, str] | None = None,
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
    # threshold 動態:cluster 數多(>20)用 >3;少(hl_sub 通常 12 上下)放寬到 >1,
    # 避免人工編彙的 cluster 集合內幾乎沒人達 >3 門檻 → universal panel 永遠空
    universal: dict[str, str] = {}
    if level in ("sub", "hl_sub", "pan_sub"):
        from collections import Counter
        counts: Counter = Counter()
        for c in clusters:
            for s in c.focal:
                counts[s.ticker] += 1
        threshold = 3 if len(clusters) > 20 else 1
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
        univ_html = (
            '<div class="univ-panel">'
            '<span class="univ-label">多題材股:</span>'
            f'{chips}'
            '</div>'
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
        )

        member_keys = [f"{m}||{s}" for m, s in (c.members or [])]
        # focal entries 帶 6 維 metric,供前端 sort chip / modal chip 用。
        # toggle universal 後前端依 _univDis 重算。
        def _focal_entry(s):
            info = all_stocks.get(s.ticker, {})
            mkt = info.get("market") or ("TW" if s.ticker.split(".")[0].isdigit() else "US")
            return {
                "ticker": s.ticker,
                "n":     (info.get("name") or "")[:10],
                "mkt":   mkt,
                "tv":    s.trading_value,
                "chg":   info.get("change_pct"),
                "close": info.get("close_price"),
                "bias":  info.get("ma20_bias"),
                "pe":    info.get("pe_ttm"),
            }
        cluster_json.append({
            "cardId": card_id,
            "memberKeys": member_keys,
            "name": c.name,
            "focal": [_focal_entry(s) for s in c.focal],
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

        # Cluster info ⓘ button:該 cluster 成交額最高 focal(c.focal 已 sort
        # by trading_value desc on line ~1054 sort_by_chg 之前)反查 topics_by_ticker。
        # 若有 match → render ⓘ button(onclick → showClusterTopicModal)+
        # 把 topic HTML 加進 cluster_topic_payload[card_id]。沒 match → 不渲。
        info_btn_html = ""
        if c.focal:
            _primary_tk = max(c.focal, key=lambda s: s.trading_value).ticker
            _topics_html = _topics_by_ticker.get(_primary_tk)
            if _topics_html:
                cluster_topic_payload[card_id] = _topics_html
                info_btn_html = (
                    f'<button class="cluster-info-btn" type="button" '
                    f"onclick=\"showClusterTopicModal('{card_id}')\" "
                    f'title="點擊查看此題材關聯議題">ⓘ</button>'
                )

        cards.append(f"""
<div class="cluster-card" id="{card_id}">
  <div class="cluster-hdr">
    <span class="cluster-name-wrap">{name_html}{info_btn_html}</span>
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

    return (
        '<div class="aetf-section">'
        '<h3 class="aetf-modal-hdr">持股主動式 ETF</h3>'
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


def build_active_etf_page(etf_list: list, holdings_by_etf: dict[str, list]) -> str:
    """主動式 ETF 頁:tab nav(按 AUM desc 一檔一 tab)+ 各 tab content:
    頂部 ETF 資訊 bar / 今日異動 4 區 / 全持股 list(weight_pct desc)。
    """
    if not etf_list:
        return '<p class="muted-note">尚無主動式 ETF 資料</p>'

    nav_opts = []
    panes = []
    for i, etf in enumerate(etf_list):
        code = etf["etf_code"]
        active = " active" if i == 0 else ""
        label = etf.get("short_name") or code
        aum_b = float(etf.get("aum_ntd") or 0) / 1e8
        selected = " selected" if i == 0 else ""
        nav_opts.append(
            f'<option value="{code}"{selected}>{html_lib.escape(str(label))}</option>'
        )

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
        bar_html = (
            '<div class="aetf-info">'
            f'<span class="aetf-name">{html_lib.escape(etf.get("etf_name") or code)}</span>'
            f'<span class="aetf-meta"><span class="muted">AUM</span> <b>{aum_b:.0f} 億</b></span>'
            + (f'<span class="aetf-meta"><span class="muted">NAV</span> <b>{float(nav_per):.2f}</b></span>' if nav_per else '')
            + (f'<span class="aetf-meta"><span class="muted">上市</span> {listing}</span>' if listing else '')
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
                f"onclick='showArtModal({json.dumps(tk)},{json.dumps(nm)})' "
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
            click = f"showArtModal({json.dumps(tk)},{json.dumps(nm)})"
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

    return (
        '<div class="aetf-select-row">'
        '<label class="aetf-select-label" for="aetf-select">選 ETF</label>'
        '<select id="aetf-select" class="aetf-select" onchange="showAetfTab(this.value)">'
        + "".join(nav_opts)
        + '</select>'
        '</div>'
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


def build_focus_stock_page(
    focus_hl_clusters: list,
    stocks_info: dict,
    ticker_close_full: dict[str, list[dict]],
    stock_meta: dict,
    aetf_holdings_by_ticker: dict[str, list],
    today_str: str,
) -> str:
    """焦點股 tab:來源 = 熱門題材「焦點」(hl_sub)的 focal union。
    3 sub-tab(順序:交集股 / 出量股 / 潛力股):
    - 交集股:同時符合 2 項(含)以上條件,依符合條件數 desc(同數量再月線乖離 desc);多「符合條件」欄
    - 出量股:今日成交金額 > 前 5 交易日均(不含今日)× 2,依出量倍數 desc
    - 潛力股:MA10 > MA20 且 股價 < MA20×1.2,依月線乖離 desc
    全欄位 client-side 可點擊排序(ASC/DESC toggle)。
    """
    focal_to_clusters: dict[str, list[str]] = {}
    for c in (focus_hl_clusters or []):
        for s in c.focal:
            focal_to_clusters.setdefault(s.ticker, []).append(c.name)

    def _f(v):
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # per-ticker 計算 + condition 判定
    cands: list[dict] = []
    for tk, clusters in focal_to_clusters.items():
        info = stocks_info.get(tk, {})
        today_close = _f(info.get("close_price"))
        today_tv = _f(info.get("trading_value"))
        hist = ticker_close_full.get(tk, [])  # date asc
        prev = [h for h in hist
                if h.get("d") != today_str and h.get("c") and h.get("v")]
        prev5 = prev[-5:]
        avg5_tv = (sum(h["c"] * h["v"] for h in prev5) / 5) if len(prev5) == 5 else None
        closes = [h["c"] for h in hist if h.get("c") is not None]
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

        # 條件判定(未來新增條件 → 加 is_xxx + matched.append)
        is_volume = bool(vol_mult and vol_mult > 3)
        is_potential = bool(
            ma10 and ma20 and today_close
            and ma10 > ma20
            and today_close < ma20 * 1.2
        )
        # 新高股:今日股價創 52 週(~252 交易日)新高 — 今日 close ≥ 過去
        # 52 週(不含今日)最高 close。歷史不足 252 筆則用掛牌以來最高。
        _hist_excl_today = [h["c"] for h in hist
                            if h.get("d") != today_str and h.get("c") is not None]
        _past52 = _hist_excl_today[-252:]
        is_new_high = bool(today_close and _past52 and today_close >= max(_past52))
        meta = stock_meta.get(tk, {})
        # 成長股:月營收連 3 月 YoY>0 + 近一季 4 損益科目金額 YoY 皆 >0
        # (NULL 視為不符合 — 缺資料不誤判)。注意用金額年增率欄
        # gross_profit_yoy 等,不是 margin 比率的 *_yoy_dir。
        _gp_yoy = _f(meta.get("gross_profit_yoy"))
        _oi_yoy = _f(meta.get("operating_income_yoy"))
        _pt_yoy = _f(meta.get("pretax_income_yoy"))
        _ni_yoy = _f(meta.get("net_income_yoy"))
        is_growth = bool(
            meta.get("revenue_yoy_3m_all_positive") is True
            and _gp_yoy is not None and _gp_yoy > 0
            and _oi_yoy is not None and _oi_yoy > 0
            and _pt_yoy is not None and _pt_yoy > 0
            and _ni_yoy is not None and _ni_yoy > 0
        )
        matched: list[str] = []
        if is_volume:
            matched.append("出量")
        if is_potential:
            matched.append("潛力")
        if is_new_high:
            matched.append("新高")
        if is_growth:
            matched.append("成長")
        cands.append({
            "ticker": tk,
            "name": (info.get("name") or "")[:12],
            "today_tv": today_tv,
            "today_close": today_close,
            "vol_mult": vol_mult,
            "ma10": ma10, "ma20": ma20, "ma20_bias": ma20_bias,
            "pe": _f(meta.get("pe_ttm")),
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
            "matched": matched,
        })

    _by_bias = lambda c: -(c["ma20_bias"] if c["ma20_bias"] is not None else float("-inf"))
    # 交集股預設依「符合條件數」desc(多→少),同數量再依月線乖離 desc
    intersect_stocks = sorted(
        [c for c in cands if len(c["matched"]) >= 2],
        key=lambda c: (-len(c["matched"]), _by_bias(c)),
    )
    volume_stocks    = sorted([c for c in cands if c["is_volume"]],
                              key=lambda c: -c["vol_mult"])
    potential_stocks = sorted([c for c in cands if c["is_potential"]], key=_by_bias)
    new_high_stocks  = sorted([c for c in cands if c["is_new_high"]], key=_by_bias)
    growth_stocks    = sorted([c for c in cands if c["is_growth"]], key=_by_bias)

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

    # 營收增率 cell:本身帶正負(正升 ▲紅 / 負降 ▼綠)
    def _rev_cell(val):
        if val is None:
            return '<span class="muted">—</span>'
        cls = "up" if val > 0 else ("down" if val < 0 else "flat")
        sign = "+" if val > 0 else ""
        arrow = " ▲" if val > 0 else (" ▼" if val < 0 else "")
        return f'<span class="{cls}">{sign}{val:.2f}%{arrow}</span>'

    _MATCH_CHIP_CLS = {"出量": "fs-mc-vol", "潛力": "fs-mc-pot",
                       "新高": "fs-mc-nh", "成長": "fs-mc-gr"}
    # 條件 → 短 key(交集股篩選列 data-cond / row data-matched 用)
    _MATCH_KEY = {"出量": "vol", "潛力": "pot", "新高": "nh", "成長": "gr"}

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
                 ("毛利率", "gm", 1, "r"), ("營益率", "om", 1, "r"),
                 ("淨利率", "nm", 1, "r"),
                 ("營收月增", "rmom", 1, "r"), ("營收年增", "ryoy", 1, "r"),
                 ("隸屬題材", "theme", 1, ""), ("主動式 ETF", "etf", 1, "")]
        if mode == "intersect":
            cols.append(("符合條件", "match", 1, ""))
        return cols

    def _row(c, mode):
        tk, nm = c["ticker"], c["name"]
        click = f"showArtModal({json.dumps(tk)},{json.dumps(nm)})"
        pe = c["pe"]
        pe_str = f"{pe:.1f}" if (pe and pe > 0) else "—"
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
    vol_html = _table(volume_stocks, "volume",
                      "今日無焦點股出量(成交金額 > 前 5 日均 × 3)")
    pot_html = _table(potential_stocks, "potential",
                      "今日無焦點股符合潛力條件(MA10 > MA20 且股價 < MA20×1.2)")
    nh_html  = _table(new_high_stocks, "newhigh",
                      "今日無焦點股創 52 週新高")
    gr_html  = _table(growth_stocks, "growth",
                      "今日無焦點股符合成長條件(月營收連 3 月 + 4 損益科目金額 YoY 皆正)")

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
        '</div>'
    )
    # 交集股條件篩選列(預設全 disabled;多選 AND;順序同 sub-tab;有交集股才顯示)
    _filter_conds = [("vol", "出量"), ("pot", "潛力"), ("nh", "新高"), ("gr", "成長")]
    _int_filter_bar = ((
        '<div class="fs-filter-bar">'
        '<span class="fs-filter-label">篩選符合條件</span>'
        + "".join(
            f'<button type="button" class="fs-filter-btn" data-cond="{k}" '
            f'onclick="toggleFsFilter(this)">{lbl}</button>'
            for k, lbl in _filter_conds
        )
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
        + _pane_head('今日成交金額 &gt; 前 5 交易日均(不含今日)× 3,依出量倍數排序。',
                     volume_stocks)
        + vol_html + '</div>'
        + '<div class="fs-tab-pane" id="fstab-pot">'
        + _pane_head('十日均價 &gt; 月均價、股價低於月均價 1.2 倍,依月線乖離率排序。',
                     potential_stocks)
        + pot_html + '</div>'
        + '<div class="fs-tab-pane" id="fstab-nh">'
        + _pane_head('今日股價創 52 週新高的焦點股,依月線乖離率排序。', new_high_stocks)
        + nh_html + '</div>'
        + '<div class="fs-tab-pane" id="fstab-gr">'
        + _pane_head('月營收連 3 月 YoY &gt; 0,且近一季毛利 / 營業利益 / 稅前淨利 / '
                     '稅後淨利金額年增率皆 &gt; 0,依月線乖離率排序。', growth_stocks)
        + gr_html + '</div>'
    )
    return nav_html + panes_html


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

    # Cluster info modal(ⓘ button)資料源:以「該 cluster 成交額最高 focal
    # ticker」反查 market_notes.topics 內 tickers 包含該 ticker 的 topic,
    # 收集 _render_topic_card HTML。同 ticker 可能對應多 topic 也都列出。
    # 對應 cluster.focal[0] (focal 已 sort by trading_value desc on _industry_section_html 內)。
    # 此處先以 ticker 為 key 預先 cache,_industry_section_html 內再 keyed by card_id。
    topics_by_ticker: dict[str, str] = {}
    if market_notes and market_notes.get("topics"):
        from collections import defaultdict
        _tk_topics = defaultdict(list)
        for topic in market_notes["topics"]:
            for tk in topic.get("tickers", []) or []:
                _tk_topics[tk].append(topic)
        topics_by_ticker = {
            tk: ''.join(_render_topic_card(t, stocks_info) for t in topics)
            for tk, topics in _tk_topics.items()
        }

    # 兩 tab 共用 cluster card 排行版型,level 拿來區分 IIA_CLUSTERS namespace
    # + sort chip data-level + container id;近一年焦點 tab 在 cluster card 內
    # 多渲一個前哨 section(同題材但今日沒進 top-50 的標的)
    hl_html = _industry_section_html(
        hl_clusters, all_stocks, "hl_sub", theme_history_payload,
        highlight_subs=highlight_subs, stock_meta=stock_meta,
        ticker_net_inst=ticker_net_inst,
        topics_by_ticker=topics_by_ticker,
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
    panes_html = (
        f'<div class="sub-tab-pane active" id="stab-hl">{hl_html}</div>'
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
                       ticker, name, trading_value, change_pct, close_price,
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
                "SELECT ticker, name, trading_value, change_pct, close_price, "
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
                "SELECT ticker, name, trading_value, change_pct, close_price, "
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

        # Q16 v2:focus_seed ticker list(rank ≤ 300 AND chg > 4.5%, ingest
        # 預計算)。給 detect_focus_clusters v2 反查題材字典累計 sub 種子計數。
        # 只需 ticker(其他資訊走 Q6 / Q15 抓)。
        try:
            focus_seed_rows = await conn.fetch(
                "SELECT ticker FROM trading_rankings WHERE rank_date=$1 "
                "AND market='TW' AND extra->>'is_focus_seed' = 'true' ORDER BY ticker",
                tw_rank_date,
            )
            focus_seed_tickers = [r["ticker"] for r in focus_seed_rows]
            print(f"  focus_seed_tickers: {len(focus_seed_tickers)}")
        except Exception as exc:
            print(f"  ⚠ focus_seed query failed (Q16 not deployed?): {exc}")

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
    # seeds = is_focus_seed (rank≤300 + chg>4.5%, ingest 預計算 Q16)
    # focus_members = is_focus_member rows (Q15) ∩ stocks_info (filter ETF)
    # 算法:同 sub 種子數 ≥ 2 才算熱門;sub 字典成員 today 有交易者
    #   chg > -3 入 focal、chg < -3 入 sentinel。pan_sub 維持原 detect_industry_clusters。
    focus_members_info = {
        t: info for t, info in tw_top_volume.items() if info.get("is_focus_member")
    }
    focus_hl_clusters = detect_focus_clusters(focus_seed_tickers, focus_members_info)
    print(f"  focus_hl_clusters: {len(focus_hl_clusters)} (v2: seeds={len(focus_seed_tickers)}, members={len(focus_members_info)})")
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
                "       net_income_yoy "
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
        stocks_compact = {
            tk: [v.get("tv"), v.get("chg"), v.get("close"),
                 v.get("net_inst"), v.get("shares_out"), v.get("volume")]
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
    _hist_tickers = list(set(_focal_tw) | set(highlight_tickers))
    if _hist_tickers:
        try:
            tch_rows = await conn.fetch(
                "SELECT ticker, rank_date, close, shares_out, volume FROM ticker_close_history "
                "WHERE ticker = ANY($1::text[]) "
                "AND rank_date >= current_date - INTERVAL '400 days' "
                "ORDER BY ticker, rank_date",
                _hist_tickers,
            )
            for r in tch_rows:
                # rank_date 是 timestamp(asyncpg → datetime),取 YYYY-MM-DD
                # 跟 theme_history payload 的 d 欄(YYYY-MM-DD)對齊,
                # _computeClusterSeries 的 dateSet union 才會 match
                _d = r["rank_date"]
                d_str = _d.strftime("%Y-%m-%d") if hasattr(_d, "strftime") else str(_d)[:10]
                _close = float(r["close"]) if r["close"] is not None else None
                _shares = float(r["shares_out"]) if r["shares_out"] is not None else None
                _vol = float(r["volume"]) if r["volume"] is not None else None
                ticker_close_payload.setdefault(r["ticker"], []).append({
                    "d": d_str, "c": _close, "s": _shares,
                })
                ticker_close_full.setdefault(r["ticker"], []).append({
                    "d": d_str, "c": _close, "s": _shares, "v": _vol,
                })
            print(f"  ticker_close_history: {len(tch_rows)} rows for "
                  f"{len(ticker_close_payload)}/{len(_hist_tickers)} tickers")
        except Exception as exc:
            print(f"  ⚠ ticker_close_history query failed: {exc}")

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
            tni_rows = await conn.fetch(
                "SELECT ticker, rank_date, net_inst FROM ticker_net_inst_history "
                "WHERE ticker = ANY($1::text[]) "
                "AND rank_date >= current_date - INTERVAL '400 days' "
                "ORDER BY ticker, rank_date",
                _hist_tickers,
            )
            for r in tni_rows:
                _d = r["rank_date"]
                d_str = _d.strftime("%Y-%m-%d") if hasattr(_d, "strftime") else str(_d)[:10]
                ni = r["net_inst"]
                if ni is None:
                    continue
                ticker_net_inst.setdefault(r["ticker"], {})[d_str] = float(ni)
            print(f"  ticker_net_inst_history: {len(tni_rows)} rows for "
                  f"{len(ticker_net_inst)}/{len(_hist_tickers)} tickers")
        except Exception as exc:
            print(f"  ⚠ ticker_net_inst_history query failed (Q17 not deployed?): {exc}")

    # 大盤(^TWII)+ 櫃買(^TWOII)指數 400 天 daily close — Q21,從 ingest
    # 寫入的 market_snapshots 讀,供 chart 第二張三線 overlay(都 rebase to
    # 100 看相對強弱)。2026-05 起資料收集移回 ingest,stockgg 不再 render-time
    # 抓 yfinance;今日 close 由 ingest 每日 fetch_and_store 寫入,無需 patch。
    market_index_payload: dict[str, list[dict]] = {"TWII": [], "TPEX": []}
    _idx_sym_map = {"^TWII": "TWII", "^TWOII": "TPEX"}
    try:
        _idx_rows = await conn.fetch(
            "SELECT snapshot_date, symbol, close_price, change_pct FROM market_snapshots "
            "WHERE symbol = ANY($1::text[]) "
            "AND snapshot_date >= current_date - INTERVAL '400 days' "
            "ORDER BY symbol, snapshot_date",
            ["^TWII", "^TWOII"],
        )
        for r in _idx_rows:
            _k = _idx_sym_map.get(r["symbol"])
            if not _k or r["close_price"] is None:
                continue
            # d 必須是 YYYY-MM-DD(對齊 ticker_close 日期 + lightweight-charts
            # time 格式);db.py 會把 timestamp 欄 coerce 成 datetime,故用
            # strftime 取日期,不可用 isoformat()(會帶 T00:00:00+00:00)。
            _d = r["snapshot_date"]
            _d = _d.strftime("%Y-%m-%d") if hasattr(_d, "strftime") else str(_d)[:10]
            market_index_payload[_k].append(
                {"d": _d, "close": round(float(r["close_price"]), 2)})
        print(f"  market_index (Q21): TWII={len(market_index_payload['TWII'])}d "
              f"TPEX={len(market_index_payload['TPEX'])}d")
    except Exception as exc:
        print(f"  ⚠ Q21 market index history failed: {exc}")

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
    )
    notes_html  = build_notes_html(market_notes, podcast_rows, stocks_info)
    catalyst_html = build_catalyst_html(catalyst_events, stocks_info)

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

    # 對每個 ticker 內 ETF 列表按 AUM desc 排序(Q19 個別 fetch 沒帶 ETF aum,
    # 反向 index 時各 ETF 順序不一定)
    for tk, lst in aetf_holdings_by_ticker.items():
        lst.sort(key=lambda h: -(float(h.get("aum_ntd") or 0)))

    aetf_html = build_active_etf_page(aetf_list, aetf_holdings_by_etf)

    # ── 焦點股 tab(2026-05-20):出量股 / 潛力股,來源 = hl_sub focal union ──
    _today_str = tw_rank_date.strftime("%Y-%m-%d") if tw_rank_date else ""
    focus_stock_html = build_focus_stock_page(
        focus_hl_clusters, stocks_info, ticker_close_full,
        stock_meta, aetf_holdings_by_ticker, _today_str,
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

    INDICATORS = [
        ("S&amp;P 500", "^GSPC",    True),
        ("NASDAQ",      "^IXIC",    True),
        ("SOX",         "^SOX",     True),
        ("東證 TOPIX",  "1308.T",   True),
        ("韓股 KOSPI",  "^KS11",    True),
        ("台股 TWII",   "^TWII",    True),
        ("VIX",         "^VIX",     True),
        ("10Y 殖利率",  "^TNX",     False),
        ("DXY",         "DX-Y.NYB", True),
        ("恐慌貪婪",    "FEAR_GREED", True),
    ]

    # Ticker tape — duplicate content for seamless loop
    tape_items = []
    for label, sym, show_pct in INDICATORS:
        close, chg = ind(sym)
        if close is None:
            continue
        if sym in ("^VIX", "^TNX", "FEAR_GREED"):
            val = f"{close:.2f}"
        else:
            val = f"{close:,.0f}"
        pct_html = ""
        if show_pct and chg is not None:
            arrow = "▲" if chg >= 0 else "▼"
            cls = "tape-up" if chg >= 0 else "tape-down"
            pct_html = f'<span class="{cls}">{arrow}{abs(chg):.2f}%</span>'
        tape_items.append(
            f'<span class="tape-item">{label}&nbsp;<b>{val}</b>'
            f'{"&nbsp;" + pct_html if pct_html else ""}</span>'
        )
    tape_content = '&ensp;·&ensp;'.join(tape_items)
    # Duplicate for seamless loop; animation runs translateX(-50%)
    tape_html = f'<div class="tape-track">{tape_content}&ensp;&ensp;&ensp;&ensp;{tape_content}</div>'

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

<!-- Ticker tape -->
<div class="tape">{tape_html}</div>

<header>
  <button class="brand" onclick="showTab('focus');window.scrollTo(0,0);" title="回首頁">IIA 投資情報</button>
  <nav class="tabs">
    <button class="tab-btn active" data-tab="focus"    onclick="showTab('focus')">熱門題材</button>
    <button class="tab-btn"        data-tab="fstock"   onclick="showTab('fstock')">選股雷達</button>
    <button class="tab-btn"        data-tab="aetf"     onclick="showTab('aetf')">主動式 ETF</button>
    <button class="tab-btn"        data-tab="notes"    onclick="showTab('notes')">市場話題</button>
    <button class="tab-btn"        data-tab="market"   onclick="showTab('market')">國際金融</button>
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
    {focus_html}
  </div>

  <!-- 焦點排行 tab 2026-05-19 移除 -->

  <!-- Tab: 選股雷達(原「焦點股」;出量股 / 潛力股 / 交集股 / 新高股 / 成長股) -->
  <div id="tab-fstock" class="tab-pane">
    {focus_stock_html}
  </div>

  <!-- Tab: 主動式 ETF -->
  <div id="tab-aetf" class="tab-pane">
    {aetf_html}
  </div>

  <!-- Tab 4: 市場話題(原「股市筆記」) -->
  <div id="tab-notes" class="tab-pane">
    {notes_html}
  </div>
</div>

<!-- Article modal -->
<dialog id="art-modal">
  <div class="modal-hdr">
    <span class="modal-hdr-title" id="modal-title"></span>
    <button class="modal-close" onclick="document.getElementById('art-modal').close()">✕</button>
  </div>
  <div class="modal-body" id="modal-body"></div>
</dialog>

<!-- Theme chart modal (子產業 6 個月 TV / 平均漲跌 趨勢) -->
<dialog id="theme-chart-dialog">
  <div class="tc-hdr">
    <div class="tc-title" id="tc-title" style="flex:1;min-width:0"></div>
    <div class="tc-period">
      <button class="tc-period-chip" data-period="1m" type="button" onclick="setChartPeriod('1m')">1M</button>
      <button class="tc-period-chip" data-period="3m" type="button" onclick="setChartPeriod('3m')">3M</button>
      <button class="tc-period-chip active" data-period="6m" type="button" onclick="setChartPeriod('6m')">6M</button>
      <button class="tc-period-chip" data-period="1y" type="button" onclick="setChartPeriod('1y')">1Y</button>
      <button class="tc-period-chip" data-period="all" type="button" onclick="setChartPeriod('all')">ALL</button>
    </div>
    <button class="tc-close" type="button"
            onclick="document.getElementById('theme-chart-dialog').close()">✕</button>
  </div>
  <div class="tc-body">
    <!-- 左欄:焦點 ticker 垂直列表(點擊在 modal 內 disable;依成交金額 desc 排序) -->
    <aside class="tc-tickerlist-col">
      <div class="tc-tickerlist-label">焦點 · 點擊納入/排除</div>
      <div class="tc-ticker-chips" id="tc-ticker-chips"></div>
    </aside>

    <!-- 右欄:兩張 chart 上下排列,各自 flex:1 自適應 -->
    <div class="tc-charts-col">
      <!-- Chart 1(上):焦點股加權指數 vs 大盤 -->
      <div class="tc-chart-label">
        焦點股加權指數 vs 大盤
        <span class="tc-info" tabindex="0"
              title="加權指數計算法&#10;1. 每檔焦點股當日市值 = 收盤價 × 流通在外股數&#10;2. cluster daily mcap = Σ 全部焦點股當日市值;某檔某日缺資料時用該檔最後一次有資料的 close × shares 延續(per-ticker forward-fill,標準加權指數做法)&#10;3. 三條線(cluster / TWII / TPEX)同時 rebase 到 100(取三條共同起點當基準),純看相對強弱不看絕對水位&#10;4. cluster 線會依「焦點 chip 列表」即時重算">ⓘ</span>
        <span class="tc-legend">
          <button class="tc-leg-chip leg-cluster active" type="button" onclick="toggleIndexLine('cluster')"><span class="leg-sw"></span>焦點股</button>
          <button class="tc-leg-chip leg-twii active" type="button" onclick="toggleIndexLine('twii')"><span class="leg-sw"></span>大盤(TWII)</button>
          <button class="tc-leg-chip leg-tpex active" type="button" onclick="toggleIndexLine('tpex')"><span class="leg-sw"></span>櫃買(TPEX)</button>
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
