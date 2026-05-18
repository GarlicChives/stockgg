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


def podcast_content_to_html(content: str, is_refined: bool = False) -> str:
    if not content:
        return '<p style="color:var(--muted)">（無內容）</p>'
    if is_refined:
        text = html_lib.escape(content[:6000])
        # 【標題】 → section heading
        text = re.sub(r'【(.+?)】', r'<h4>\1</h4>', text)
        # （一）、（二）、... → subsection with spacing above
        text = re.sub(
            r'^（([一二三四五六七八九十]+)）[、，](.+)$',
            r'<div class="pod-subsec">（\1）、\2</div>',
            text, flags=re.MULTILINE,
        )
        # 1. 2. 3. → numbered items (no br)
        text = re.sub(
            r'^(\d+)\.\s*(.+)$',
            r'<div class="pod-num-item"><span class="pod-num">\1.</span>\2</div>',
            text, flags=re.MULTILINE,
        )
        # - bullet items
        text = re.sub(
            r'^-\s+(.+)$',
            r'<div class="pod-bul-item">• \1</div>',
            text, flags=re.MULTILINE,
        )
        # collapse remaining blank lines
        text = re.sub(r'\n{2,}', '\n', text)
        text = text.replace('\n', '')
        return f'<div class="pod-notes">{text}</div>'
    else:
        # Raw transcript — show only first 800 chars with note
        preview = content[:800]
        note = '<p class="pod-raw-note">（以下為原始逐字稿節錄，完整分析待 AI 整理後更新）</p>' if len(content) > 800 else ''
        return f'{note}<pre class="pod-raw">{html_lib.escape(preview)}{"…" if len(content) > 800 else ""}</pre>'


def extract_relevant_para(content: str, ticker: str, name: str, max_chars: int = 700) -> str:
    """Return paragraph(s) from content that mention the ticker or stock name."""
    if not content:
        return ""
    paras = [p.strip() for p in re.split(r'\n{2,}|\n(?=\d+\.|•|-\s)', content) if p.strip()]
    name_prefix = name[:2] if name and len(name) >= 2 else ""
    relevant = [p for p in paras if ticker in p or (name_prefix and name_prefix in p)]
    if not relevant:
        relevant = paras[:1]
    result = '\n\n'.join(relevant[:3])
    if len(result) > max_chars:
        result = result[:max_chars] + "…"
    return result


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


def _stk_pill(ticker: str, stocks_info: dict, clickable: bool = True, extra_attrs: str = "") -> str:
    """Unified stock chip: ticker + market badge + name + "price(chg%)" 報價。

    報價 span 用 fmt_pct 的 css class (up=紅 down=綠 flat=白 neutral=灰),
    全站股票標的(報告段末 pill / 題材卡 / 跨來源議題 / rankings 表) 共用。
    """
    info = stocks_info.get(ticker, {})
    _core = ticker.split(".")[0]
    market = info.get("market") or ("TW" if _core.isdigit() else "US")
    name = info.get("name", "")
    chg = info.get("change_pct")
    close = info.get("close_price")
    mkt_cls = "mkt-tw" if market == "TW" else "mkt-us"
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
        f'<span class="sp-ticker">{html_lib.escape(ticker)}</span>'
        f'<span class="mkt-badge {mkt_cls}">{market}</span>'
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


def _yf_resolve_sym(ticker: str, board: str | None) -> str:
    """共用 helper:選對 yfinance suffix。
    board ∈ {'TWSE', 'TPEX', 'US', None};None → 預設 TWSE(數字 ticker 加 .TW)。
    上櫃股(TPEX)必須加 .TWO,不然 yfinance 回 "possibly delisted"。
    """
    up = ticker.upper()
    if up.endswith(".TW") or up.endswith(".TWO"):
        return ticker
    if board == "TPEX":
        return ticker + ".TWO"
    core = ticker.split(".")[0]
    if core.isdigit():  # TW numeric ticker,board 未知或 TWSE → 預設 .TW
        return ticker + ".TW"
    return ticker  # US


def _yf_analyst_batch(ticker_boards: dict[str, str | None]) -> dict[str, dict]:
    """Concurrently fetch analyst consensus (target price + recommendation) via yfinance.
    ticker_boards: {ticker: board},board ∈ {'TWSE', 'TPEX', 'US', None}。
    None 時對 TW 數字 ticker 預設 .TW;若 .TW 沒資料 fallback 試 .TWO。
    Returns {ticker: {target_mean, target_median, target_high, target_low, n_analysts,
                       recommendation, currency}} for tickers that have data.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    try:
        import yfinance as yf
    except ImportError:
        return {}

    def _fetch_one(orig: str, board: str | None) -> tuple[str, dict | None]:
        yf_sym = _yf_resolve_sym(orig, board)
        try:
            info = yf.Ticker(yf_sym).info
            # 若 .TW 沒拿到 targetMeanPrice 且 ticker 是 TW 數字,fallback 試 .TWO
            if not info.get("targetMeanPrice"):
                core = orig.split(".")[0]
                if core.isdigit() and yf_sym.upper().endswith(".TW") and not yf_sym.upper().endswith(".TWO"):
                    try:
                        info = yf.Ticker(core + ".TWO").info
                    except Exception:
                        pass
            mean = info.get("targetMeanPrice")
            if not mean:
                return orig, None
            return orig, {
                "target_mean":   mean,
                "target_median": info.get("targetMedianPrice"),
                "target_high":   info.get("targetHighPrice"),
                "target_low":    info.get("targetLowPrice"),
                "n_analysts":    info.get("numberOfAnalystOpinions"),
                "recommendation": info.get("recommendationKey", ""),
                "currency":      info.get("currency", "USD"),
            }
        except Exception:
            return orig, None

    result: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_fetch_one, t, b): t for t, b in ticker_boards.items()}
        for fut in as_completed(futures):
            orig, data = fut.result()
            if data:
                result[orig] = data
    return result


def _build_company_intro_html(meta: dict | None) -> str:
    """F1: 公司介紹 section,顯示在 modal 頂部(analyst consensus 之前)。
    沒有 stock_meta(非 focal 股 / ingest 還沒抓到)→ 空字串。"""
    if not meta:
        return ""
    name_zh = (meta.get("name_zh") or "").strip()
    name_en = (meta.get("name_en") or "").strip()
    sector = (meta.get("sector") or "").strip()
    industry = (meta.get("industry") or "").strip()
    desc = (meta.get("description") or "").strip()
    web = (meta.get("website") or "").strip()
    emp = meta.get("employees")

    if not any([name_zh, name_en, sector, desc, web, emp]):
        return ""

    bits = []
    if sector or industry:
        tag = " · ".join(x for x in [sector, industry] if x)
        bits.append(f'<div class="ci-tags">{html_lib.escape(tag)}</div>')
    if name_en and name_en.lower() != (name_zh or "").lower():
        bits.append(f'<div class="ci-name-en">{html_lib.escape(name_en)}</div>')
    meta_line = []
    if emp:
        try:
            meta_line.append(f'員工 {int(emp):,} 人')
        except Exception:
            pass
    if web:
        meta_line.append(f'<a href="{html_lib.escape(web)}" target="_blank" rel="noopener">官網 ↗</a>')
    if meta_line:
        bits.append(f'<div class="ci-meta">{" · ".join(meta_line)}</div>')
    if desc:
        # 限制長度避免 modal 暴增
        if len(desc) > 600:
            desc = desc[:600] + "…"
        bits.append(f'<div class="ci-desc">{html_lib.escape(desc)}</div>')

    return (
        '<div class="modal-section">'
        '<div class="modal-section-hdr">🏢 公司介紹</div>'
        + "".join(bits)
        + '</div>'
    )


def _build_analyst_html(data: dict) -> str:
    """Build analyst consensus HTML section for a modal. Returns '' if no data."""
    if not data or not data.get("target_mean"):
        return ""

    rec = data.get("recommendation", "")
    rec_label, rec_color = _REC_LABEL.get(rec, ("", "#7a8ba0"))

    currency = data.get("currency", "USD")
    if currency == "TWD":
        def _fp(p: float | None) -> str:
            return f"NT${p:,.0f}" if p else "—"
    else:
        def _fp(p: float | None) -> str:
            return f"${p:,.2f}" if p else "—"

    mean   = _fp(data.get("target_mean"))
    median = _fp(data.get("target_median"))
    high   = _fp(data.get("target_high"))
    low    = _fp(data.get("target_low"))
    n      = data.get("n_analysts")

    meta_parts = []
    if rec_label:
        meta_parts.append(f'<span class="analyst-rec" style="color:{rec_color}">{rec_label}</span>')
    if n:
        meta_parts.append(f'{n} 位分析師')
    meta_html = " &middot; ".join(meta_parts)

    return (
        '<div class="modal-section">'
        '<div class="modal-section-hdr">📊 機構目標價共識</div>'
        '<div class="analyst-grid">'
        f'<div class="ag-cell"><span class="ag-label">均值</span><span class="ag-val">{mean}</span></div>'
        f'<div class="ag-cell"><span class="ag-label">中位數</span><span class="ag-val">{median}</span></div>'
        f'<div class="ag-cell"><span class="ag-label">高</span><span class="ag-val ag-high">{high}</span></div>'
        f'<div class="ag-cell"><span class="ag-label">低</span><span class="ag-val ag-low">{low}</span></div>'
        '</div>'
        f'<div class="ag-meta">{meta_html}</div>'
        '</div>'
    )


def _yf_batch_fetch(entries: list[tuple[str, str, str | None]]) -> dict[str, dict]:
    """Sync: batch-fetch close / change% / trading value via yfinance.
    entries = [(ticker, market, board|None), ...]; board ∈ {'TWSE','TPEX',None}。
    對 board=None 的 TW 數字 ticker 預設 .TW,空回時 fallback 試 .TWO。
    Returns {ticker: {"close": float|None, "change_pct": float|None, "trading_value": float|None}}
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}

    def _do_download(yf_map: dict[str, str]) -> dict[str, dict]:
        """主 batch:download → 取每個 yf_sym 的 close/vol → entry dict"""
        out: dict[str, dict] = {}
        if not yf_map:
            return out
        try:
            syms = list(yf_map)
            raw = yf.download(syms, period="2d", progress=False, auto_adjust=True, group_by="ticker")
            if raw.empty:
                return out
            for yf_sym, orig in yf_map.items():
                try:
                    close = (raw[yf_sym]["Close"] if len(syms) > 1 else raw["Close"]).dropna()
                    vol   = (raw[yf_sym]["Volume"] if len(syms) > 1 else raw["Volume"]).dropna()
                    if len(close) == 0:
                        continue
                    entry: dict = {"close": None, "change_pct": None, "trading_value": None}
                    entry["close"] = float(close.iloc[-1])
                    if len(close) >= 2:
                        entry["change_pct"] = round(
                            float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100), 2
                        )
                    if len(vol) >= 1:
                        entry["trading_value"] = float(close.iloc[-1] * vol.iloc[-1])
                    out[orig] = entry
                except Exception:
                    pass
        except Exception:
            pass
        return out

    # 第一輪:依 board 組正確 suffix
    yf_map: dict[str, str] = {_yf_resolve_sym(orig, board): orig for orig, _mkt, board in entries}
    result = _do_download(yf_map)

    # 第二輪 fallback:對沒拿到資料的 TW 數字 ticker 且第一輪用了 .TW,改試 .TWO
    missing_tw = [
        orig for orig, _mkt, board in entries
        if orig not in result
        and orig.split(".")[0].isdigit()
        and not orig.upper().endswith(".TWO")
        and board != "TPEX"  # 已是 TPEX 就用過 .TWO 了,不必再試
    ]
    if missing_tw:
        retry_map = {orig.split(".")[0] + ".TWO": orig for orig in missing_tw}
        result.update(_do_download(retry_map))

    return result


def _yf_market_index_history(period: str = "6mo") -> dict[str, list[dict]]:
    """抓大盤(^TWII)與櫃買(^TWO)指數歷史 close,供 chart 三線 overlay 對比。
    Returns: {'TWII': [{d, close}, ...], 'TPEX': [{d, close}, ...]}
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}
    result: dict[str, list[dict]] = {}
    # ^TWO 在 yfinance 完全沒資料;^TWOII (TPEx Composite) 偶爾會 flaky
    # 回空 → 對該 symbol 個別 retry 一次,避免 chart 整個沒線
    import time as _time
    syms_map = {"^TWII": "TWII", "^TWOII": "TPEX"}
    for yf_sym, key in syms_map.items():
        for attempt in range(2):
            try:
                df = yf.download(yf_sym, period=period, progress=False, auto_adjust=True)
                if df.empty:
                    if attempt == 0:
                        _time.sleep(0.7)
                        continue
                    break
                # yfinance 4.x 即使單 ticker 也回 MultiIndex columns:
                # ('Close', '^TWII') 等。需 squeeze inner level 為 Series。
                close = df["Close"]
                if hasattr(close, "ndim") and close.ndim == 2:
                    close = close.iloc[:, 0]
                close = close.dropna()
                series = []
                for ts, v in close.items():
                    d = ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]
                    val = v.item() if hasattr(v, "item") else float(v)
                    series.append({"d": d, "close": round(float(val), 2)})
                result[key] = series
                break
            except Exception:
                if attempt == 0:
                    _time.sleep(0.7)
                else:
                    break
    return result


def _yf_ma20_bias_batch(ticker_boards: dict[str, str]) -> dict[str, float]:
    """Batch yfinance,回傳 {ticker: ma20_bias%}。
    ma20_bias = (今日收盤 - 20MA) / 20MA * 100。台股 only(TW 焦點股用)。

    ticker_boards: {ticker: board} 其中 board ∈ {"TWSE", "TPEX"}。
    yfinance suffix 必須對:上市(TWSE)→ .TW、上櫃(TPEX)→ .TWO。
    若一律加 .TW 會讓上櫃股(例:5347 世界先進)抓不到資料(yfinance 回
    "possibly delisted")→ ma20_bias 永遠 null,排序時被當缺值排尾段。
    """
    if not ticker_boards:
        return {}
    try:
        import yfinance as yf
    except ImportError:
        return {}
    yf_map = {_yf_resolve_sym(t, b): t for t, b in ticker_boards.items()}
    result: dict[str, float] = {}
    try:
        syms = list(yf_map)
        raw = yf.download(syms, period="2mo", progress=False, auto_adjust=True, group_by="ticker")
        if raw.empty:
            return result
        for yf_sym, orig in yf_map.items():
            try:
                close = (raw[yf_sym]["Close"] if len(syms) > 1 else raw["Close"]).dropna()
                if len(close) < 20:
                    continue
                ma20 = float(close.iloc[-20:].mean())
                if ma20 <= 0:
                    continue
                today = float(close.iloc[-1])
                result[orig] = round((today - ma20) / ma20 * 100, 2)
            except Exception:
                pass
    except Exception:
        pass
    return result


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

def _vol_label(rank: int) -> str:
    if rank <= 5:   return '<span class="vol-tag vol-hot">爆量</span>'
    if rank <= 15:  return '<span class="vol-tag vol-high">量大</span>'
    return '<span class="vol-tag vol-mid">量增</span>'


def _build_stock_cards(ticker_list: list[tuple[str, dict]],
                       ticker_arts: dict, market: str) -> tuple[str, dict]:
    """Build stock cards for a market. Returns (html, modal_data dict).

    Article snippets / counts removed in repo-split Phase 3.6. Cards still
    render; modal data is populated downstream by _build_analyst_html
    (machine-derived consensus, no subscription text).
    """
    modal_data: dict[str, str] = {}
    cards = []
    for ticker, info in ticker_list:
        chg = info["change_pct"]
        pct_str, pct_cls = fmt_pct(chg)
        mkt_badge = f'<span class="mkt-badge mkt-{market.lower()}">{market}</span>'
        vol_html = _vol_label(info["rank"])

        if market == "US":
            val_str = f"${info['trading_value']/1e9:.1f}B"
        else:
            val_str = f"{info['trading_value']/1e8:.0f}億"
        board_badge = ""
        if market == "TW":
            board = info.get("board", "TWSE")
            board_badge = f'<span class="board-sm {board.lower()}">{board}</span>'
        limit_badge = '<span class="limit-up-badge">漲停⬆</span>' if info.get("limit_up") else ""

        modal_data[ticker] = ""  # filled later by analyst-consensus builder

        cards.append(f"""
<div class="stock-card" onclick="showArtModal('{html_lib.escape(ticker)}','{html_lib.escape(info['name'][:12])}')">
  <div class="sc-head">
    <span class="sc-ticker">{html_lib.escape(ticker)}</span>
    {mkt_badge}{board_badge}{limit_badge}
    <span class="sc-name">{html_lib.escape(info['name'][:12])}</span>
  </div>
  <div class="sc-meta">
    <span class="sc-pct {pct_cls}">{pct_str}</span>
    <span class="sc-val">{val_str}</span>
    <span class="sc-rank">#{info['rank']}</span>
    {vol_html}
  </div>
</div>""")
    return ''.join(cards), modal_data


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
        # 指標說明 panel(預設收合,點 ⓘ 展開)。文案要跟 _yf_ma20_bias_batch
        # / 770~787 的 simple-mean 邏輯對齊;改公式必須同步改這段。
        # 指標說明:summary 固定位置,展開時 panel 以 absolute 浮層動畫滑下;
        # 動畫由 anim-details JS 處理(攔截 summary click,max-height transition)
        explainer_html = (
            '<details class="metric-explainer anim-details">'
            '<summary>ⓘ 指標計算說明</summary>'
            '<div class="anim-panel metric-panel">'
            '<ul>'
            '<li><b>漲跌</b>：cluster 焦點股「當日收盤漲跌%」的<b>簡單算術平均</b>'
            '(skip 缺值)。例：3 檔焦點 +2% / -1% / +5% → 平均 +2.00%。</li>'
            '<li><b>乖離</b>：焦點股「20MA 乖離率%」的簡單平均;'
            '每檔乖離 = (今日收盤 − 過去 20 日收盤均線)÷ 20MA × 100;'
            '數值越正越「過熱」、越負越「超賣」。資料源 yfinance,台股 only。</li>'
            '<li><b>PE</b>：焦點股 <b>PE (TTM)</b> 簡單平均;'
            'skip 虧損股(PE ≤ 0)避免拉低均值。資料源 yfinance,週日 04:00 更新。</li>'
            '</ul>'
            '<p class="metric-note">⚠ 三項皆為<b>簡單算術平均</b>(每檔等權重),'
            '與點開 chart modal 內的「焦點股加權指數」(用市值 × shares 加權) <b>不同</b>。'
            '小型股對 cluster header 的影響與大型股相同。</p>'
            '</div>'
            '</details>'
        )
        # data-level 讓 _refreshSortUi / setClusterSort 知道這個 chip 屬於哪個 sub-tab,
        # state per level(_clusterSort[level] / _clusterSortDir[level]),兩 tab 各管自己。
        sort_html = (
            '<div class="sort-explainer-row">'
            '<div class="sort-row">'
            '<span class="sort-label">排序：</span>'
            f'<button class="sort-chip active" data-sort="tv"    data-level="{level}" data-dir="desc" type="button" onclick="setClusterSort(\'tv\',\'{level}\')">成交金額</button>'
            f'<button class="sort-chip"        data-sort="chg"   data-level="{level}" type="button" onclick="setClusterSort(\'chg\',\'{level}\')">平均漲跌</button>'
            f'<button class="sort-chip"        data-sort="bias"  data-level="{level}" type="button" onclick="setClusterSort(\'bias\',\'{level}\')">平均乖離</button>'
            f'<button class="sort-chip"        data-sort="pe"    data-level="{level}" type="button" onclick="setClusterSort(\'pe\',\'{level}\')">平均 PE</button>'
            '</div>'
            + explainer_html
            + '</div>'
        )

    univ_html = ""
    if universal:
        chips = "".join(
            f'<button class="univ-chip" data-ticker="{html_lib.escape(t)}" type="button"'
            f" onclick='toggleUniv({json.dumps(t)})'>"
            f"{html_lib.escape(t)}&nbsp;{html_lib.escape(n)}</button>"
            for t, n in universal.items()
        )
        univ_html = (
            '<div class="univ-panel">'
            '<span class="univ-label">廣泛概念股(點擊濾除):</span>'
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

    # 預設依「成交金額」desc 排序(cluster.trading_value):跟 JS
    # _getSortKey 預設 'tv' 一致,首次 _recalcClusters 不會觸發 FLIP
    # 動畫(dy≈0)→ 無視覺跳動。
    clusters = sorted(clusters, key=lambda c: -(c.trading_value or 0))

    # sub-level 判斷:hl_sub / pan_sub 都視同 sub(顯 sparkline / subtitle 等),
    # 提到 for-loop 外避免每 iter 重算 + 解決前向使用 UnboundLocalError
    is_sub_level = level in ("sub", "hl_sub", "pan_sub")
    cards = []
    cluster_json: list[dict] = []
    for idx, c in enumerate(clusters):
        n_focal = len(c.focal)
        # 焦點股平均漲跌幅
        chgs = [s.change_pct for s in c.focal if s.change_pct is not None]
        avg_chg = sum(chgs) / len(chgs) if chgs else None
        # 焦點股平均 20MA 乖離率(來自 all_stocks 由 yfinance 補進來的值)
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

        # 順序:漲跌 / 乖離 / PE(2026-05-18 起殖利/β 全站移除)
        # 點 badge → setFocalSort(card_id, key):只動該題材內 focal pill 順序
        card_id = f"cc-{level}-{idx}"
        metric_html = (
            _metric_badge("漲跌", avg_chg, "點擊依此題材內個股漲跌幅排序", "chg", card_id, is_default_sort=True)
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

        tv_str = f"{c.trading_value/1e8:.0f}億" if c.trading_value > 0 else ""
        meta_text = f"{n_focal} 檔焦點" + (f" · {tv_str}" if tv_str else "")

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
                        f'<span class="sp-ticker">{html_lib.escape(tk)}</span>'
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

        cards.append(f"""
<div class="cluster-card" id="{card_id}">
  <div class="cluster-hdr">
    {name_html}
    {metric_html}
    <span class="cluster-meta">{meta_text}</span>
    {spark_html}
  </div>
  {subtitle}
  <div class="cluster-focal-stocks">{focal_pills}{sentinel_toggle}</div>
  {sentinel_panel}
</div>""")

    cluster_json_str = json.dumps(cluster_json, ensure_ascii=False, separators=(",", ":"))
    return (
        sort_html
        + univ_html
        + f'<div id="cluster-container-{level}" class="focus-clusters">'
        + "".join(cards)
        + "</div>"
        + f"<script>if(!window.IIA_CLUSTERS)window.IIA_CLUSTERS={{}};"
          f"window.IIA_CLUSTERS.{level}={cluster_json_str};</script>"
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
                pid = f"prev-{ev['id']}"
                chips.append(
                    f'<span class="{cls}"{data_attr} '
                    f"onclick=\"document.getElementById('{pid}').classList.toggle('open')\">"
                    f"{html_lib.escape(label)} 📝</span>"
                )
            else:
                chips.append(f'<span class="{cls}"{data_attr}>{html_lib.escape(label)}</span>')

        # Render previews (now for any event type that has preview_text — not
        # just earnings; past events use the same mechanism to surface the
        # preview written before the event date).
        preview_blocks = []
        for ev in evs:
            txt = (ev.get("preview_text") or "").strip()
            if not txt:
                continue
            pid = f"prev-{ev['id']}"
            head_tk = (ev.get("ticker") or "").strip()
            head_title = (ev.get("title") or "").strip()
            head_label = (f"{head_tk} 法說 preview" if ev["event_type"] == "earnings" and head_tk
                          else head_title or "事件 preview")
            preview_blocks.append(
                f'<div id="{pid}" class="cal-preview">'
                f'<div class="cal-preview-head">📝 {html_lib.escape(head_label)}</div>'
                f'<pre class="cal-preview-body">{html_lib.escape(txt)}</pre>'
                f'</div>'
            )
        day_html.append(
            f'<div class="{day_cls}"><div class="cal-date">{date_label}</div>'
            f'<div class="cal-events">{"".join(chips)}</div></div>'
            + "".join(preview_blocks)
        )
    return '<div class="cal-list">' + "".join(day_html) + "</div>"


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

    # 兩 tab 共用 cluster card 排行版型,level 拿來區分 IIA_CLUSTERS namespace
    # + sort chip data-level + container id;近一年焦點 tab 在 cluster card 內
    # 多渲一個前哨 section(同題材但今日沒進 top-50 的標的)
    hl_html = _industry_section_html(
        hl_clusters, all_stocks, "hl_sub", theme_history_payload,
        highlight_subs=highlight_subs, stock_meta=stock_meta,
        ticker_net_inst=ticker_net_inst,
    ) if hl_clusters else '<p class="muted-note">今日「近一年焦點」題材無焦點股入榜</p>'
    pan_html = _industry_section_html(
        pan_clusters, all_stocks, "pan_sub", theme_history_payload,
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


# ── 焦點排行 tab (Sprint 3) ───────────────────────────────────────────────────

def build_focus_ranking_html(
    focal_tickers: list[str],
    all_stocks: dict,
    stock_meta: dict,
    top_n: int = 15,
) -> str:
    """焦點排行 tab:對「目前所有 displayed cluster 的 union focal 股」
    按殖利率 desc 與 PE asc 各排一張 Top N table。row 點開 modal。

    濾除規則:
    - 高殖利率:dividend_yield is not None AND > 0
    - 估值偏低:pe_ttm is not None AND > 0(濾掉虧損股,負 PE 沒參考意義)
    """
    if not stock_meta or not focal_tickers:
        return ('<p class="muted-note">尚無 stock_meta 資料'
                '(等 ingest weekly cron 跑完即會自動填入)</p>')

    def _f(v):
        """DB NUMERIC 經 JSON 可能是 str/Decimal,統一轉 float / None。"""
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    candidates: list[dict] = []
    for tk in set(focal_tickers):
        meta = stock_meta.get(tk) or {}
        stk = all_stocks.get(tk) or {}
        candidates.append({
            "ticker": tk,
            "name": (meta.get("name_zh") or stk.get("name") or tk)[:10],
            "close": _f(stk.get("close_price")),
            "chg": _f(stk.get("change_pct")),
            "pe_ttm": _f(meta.get("pe_ttm")),
            "market_cap": _f(meta.get("market_cap")),
            "sector": (meta.get("sector") or "")[:14],
        })

    by_pe = sorted(
        [c for c in candidates
         if c["pe_ttm"] is not None and c["pe_ttm"] > 0],
        key=lambda c: c["pe_ttm"],
    )[:top_n]

    def _mcap_str(v: float | None) -> str:
        if v is None or v <= 0:
            return "—"
        if v >= 1e12:
            return f"{v/1e12:.2f} 兆"
        return f"{v/1e8:.0f} 億"

    def _rows(items: list[dict], key: str, fmt: str) -> str:
        if not items:
            return '<tr><td colspan="7" style="text-align:center;color:var(--muted)">無符合資料</td></tr>'
        out = []
        for i, c in enumerate(items, 1):
            chg = c["chg"]
            pct_str, pct_cls = fmt_pct(chg)
            if c["close"] is not None:
                quote = f"{c['close']:.2f}" + (f" ({pct_str})" if chg is not None else "")
            else:
                quote = pct_str
            value = fmt.format(c[key])
            mcap = _mcap_str(c["market_cap"])
            sector = html_lib.escape(c["sector"]) if c["sector"] else "—"
            click = f"showArtModal({json.dumps(c['ticker'])},{json.dumps(c['name'][:12])})"
            out.append(
                f"<tr class=\"rank-row\" onclick='{click}'>"
                f'<td class="rank">{i}</td>'
                f'<td class="ticker">{html_lib.escape(c["ticker"])}</td>'
                f'<td class="name">{html_lib.escape(c["name"])}</td>'
                f'<td class="num {pct_cls}">{quote}</td>'
                f'<td class="num"><strong>{value}</strong></td>'
                f'<td class="num">{mcap}</td>'
                f'<td class="col-sector">{sector}</td>'
                f'</tr>'
            )
        return "".join(out)

    def _dl(tid: str, fname: str) -> str:
        return (f'<button class="rank-dl" type="button" '
                f'onclick="downloadRankCSV(\'{tid}\',\'{fname}\')" '
                f'title="下載 CSV">⬇ CSV</button>')

    # 2026-05-18 起殖利率全站移除 → 高殖利率 Top 15 table 拿掉,
    # 焦點排行只剩「估值偏低 PE TTM」一張表。
    return (
        '<div class="ranks ranks-single">'
        '<div class="card">'
        '<div class="sec sec-row">'
        '<span>📉 估值偏低焦點 Top 15(PE TTM)</span>'
        + _dl('rank-pe', 'stockgg-low-pe') +
        '</div>'
        '<table id="rank-pe">'
        '<thead><tr><th>#</th><th>代號</th><th>名稱</th>'
        '<th style="text-align:right">股價(漲跌)</th>'
        '<th style="text-align:right">PE</th>'
        '<th style="text-align:right">市值</th>'
        '<th class="col-sector">產業</th></tr></thead>'
        f'<tbody>{_rows(by_pe, "pe_ttm", "{:.1f}")}</tbody>'
        '</table>'
        '</div>'
        '</div>'
    )


# ── 股市筆記 tab ──────────────────────────────────────────────────────────────

def build_notes_html(market_notes: dict | None, podcast_rows: list,
                     stocks_info: dict | None = None) -> str:
    parts = []

    if market_notes and market_notes.get("topics"):
        topic_cards = []
        # Sort by latest contributing-article date. The underlying `articles`
        # / `sources` arrays drive ordering only — they are intentionally NOT
        # rendered on this public site (article titles + subscription source
        # names are copyrighted/derivative content; they stay in DB and in
        # the private admin UI only).
        def _topic_latest_date(t):
            dates = [a.get("date", "") for a in t.get("articles", []) if a.get("date")]
            return max(dates) if dates else "1900-01-01"
        for topic in sorted(market_notes["topics"], key=_topic_latest_date, reverse=True):
            t_name = html_lib.escape(topic.get("topic", ""))
            sentiment = topic.get("sentiment", "中立")
            sent_cls = "sent-bull" if "偏多" in sentiment else ("sent-bear" if "偏空" in sentiment else "sent-neu")
            summary = html_lib.escape(topic.get("summary", ""))
            key_points = topic.get("key_points", [])
            kp_html = "".join(f'<li>{html_lib.escape(p)}</li>' for p in key_points[:5])
            tickers = topic.get("tickers", [])
            _si = stocks_info or {}
            tk_html = "".join(_stk_pill(t, _si) for t in tickers)
            topic_cards.append(f"""
<div class="topic-card">
  <div class="topic-head">
    <span class="topic-name">{t_name}</span>
    <span class="sent-badge {sent_cls}">{html_lib.escape(sentiment)}</span>
  </div>
  {f'<p class="topic-sum">{summary}</p>' if summary else ''}
  {f'<ul class="kp-list">{kp_html}</ul>' if kp_html else ''}
  {f'<div class="tk-row">{tk_html}</div>' if tk_html else ''}
</div>""")
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

    # Rankings
    us_rank_date = await conn.fetchval(
        "SELECT MAX(rank_date) FROM trading_rankings WHERE market='US'"
    )
    tw_rank_date = await conn.fetchval(
        "SELECT MAX(rank_date) FROM trading_rankings WHERE market='TW'"
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
                "FROM trading_rankings WHERE rank_date=$1 AND market='tw' "
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
                "FROM trading_rankings WHERE rank_date=$1 AND market='tw' "
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
                "AND market='tw' AND extra->>'is_focus_seed' = 'true' ORDER BY ticker",
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

    # MA20 乖離率:給每個 sub-cluster 算「焦點股平均 20MA 乖離」用。
    # yfinance 一次性 batch 抓所有焦點 ticker 的 2 個月收盤,算 MA20 +
    # bias%,patch 回 stocks_info(_industry_section_html 從那邊讀)。
    # **重要**:必須帶 board 進去(TWSE→.TW、TPEX→.TWO),否則上櫃股
    # 拿不到資料。stocks_info 的 board 來自 trading_rankings.extra.board。
    # _focal_tw 涵蓋:
    #   - sub_clusters 的 focal(pan_sub + 舊 hl 路徑)
    #   - focus_hl_clusters 的 focal + sentinel(2026-05-18 加,新 hl 路徑;
    #     sentinel 也要 MA20/PE 給 pill 顯)
    _focal_tw_set: set[str] = {s.ticker for c in sub_clusters for s in c.focal}
    for c in focus_hl_clusters:
        for s in c.focal:
            _focal_tw_set.add(s.ticker)
        for s in (c.sentinel or []):
            _focal_tw_set.add(s.ticker)
    _focal_tw = list(_focal_tw_set)
    _focal_board_map = {
        t: stocks_info.get(t, {}).get("board", "TWSE")
        for t in _focal_tw
    }
    if _focal_board_map:
        _ma20 = await asyncio.to_thread(_yf_ma20_bias_batch, _focal_board_map)
        for t, bias in _ma20.items():
            if t in stocks_info:
                stocks_info[t]["ma20_bias"] = bias

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
                "       week52_high, week52_low, beta "
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
    # Normalize Gemini-formatted tickers and extract embedded Chinese names
    _gemini_name_lookup: dict[str, str] = {}
    if market_notes and market_notes.get("topics"):
        for _topic in market_notes["topics"]:
            _normalized = []
            for _raw in _topic.get("tickers", []):
                _tick = _normalize_ticker(_raw)
                _normalized.append(_tick)
                _m = _TICKER_PAREN_RE.search(_raw.strip())
                if _m:
                    _inner = _m.group(1).strip()
                    _outer = _raw.strip()[:_m.start()].strip()
                    if (re.match(r'^[A-Z0-9]{2,8}$', _inner, re.IGNORECASE)
                            and _outer and not _outer.isascii()):
                        _gemini_name_lookup[_tick] = _outer
            _topic["tickers"] = _normalized

    # Build name fallback from theme_dictionary.json (2026-05 schema:
    # ticker-centric `stocks` 物件,純台股)
    _theme_name_lookup: dict[str, str] = {}
    try:
        _td_path = Path(__file__).resolve().parent.parent / "data" / "theme_dictionary.json"
        _td = json.loads(_td_path.read_text(encoding="utf-8"))
        for _ticker, _info in _td.get("stocks", {}).items():
            _name = _info.get("name")
            if _ticker and _name:
                _theme_name_lookup[_ticker] = _name
    except Exception:
        pass

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
                      preview_text
               FROM catalyst_events
               WHERE event_date >= CURRENT_DATE - INTERVAL '14 days'
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

    # yfinance: 為「market_notes 提到但不在 top-N rankings 也不在 Q8 回傳」
    # 的 ticker 補抓 close / change_pct,塞回 stocks_info(讓段末 pill 與
    # topic-card pill 都能正確顯示 price(chg%)。watch 路徑已移除,因公開站
    # 已不顯示前哨觀察。
    _yf_needed: list[tuple[str, str]] = []
    if market_notes and market_notes.get("topics"):
        for topic in market_notes["topics"]:
            for t in topic.get("tickers", []):
                if t not in stocks_info:
                    _core = t.split(".")[0]
                    # board unknown(這些 ticker 不在 trading_rankings 內);
                    # _yf_batch_fetch 對 board=None 會試 .TW 再 fallback .TWO
                    _yf_needed.append((t, "TW" if _core.isdigit() else "US", None))
    if _yf_needed:
        _yf_needed = list({t[0]: t for t in _yf_needed}.values())  # dedup by ticker
        yf_data = await asyncio.to_thread(_yf_batch_fetch, _yf_needed)
        # Patch stocks_info for market_notes tickers not in any ranking
        for ticker, market, _board in _yf_needed:
            if ticker not in stocks_info:
                d = yf_data.get(ticker, {})
                stocks_info[ticker] = {
                    "name": _gemini_name_lookup.get(ticker) or _theme_name_lookup.get(ticker) or ticker,
                    "market": market,
                    "change_pct": d.get("change_pct"),
                    "close_price": d.get("close"),
                    "trading_value": 0,
                    "rank": 99,
                    "limit_up": False,
                }

    raw_report   = (report["raw_response"] or "") if report else ""
    report_date  = report["report_date"].strftime("%Y/%m/%d") if report else "—"
    directions  = parse_directions(raw_report)
    report_html = md_to_html(raw_report)
    report_html = _pillify_in_html(report_html, stocks_info)
    updated_at  = datetime.now(timezone.utc).strftime("%m/%d %H:%M UTC")

    # Build IIA_HISTORY payload: {"main||sub": [{d, s:{ticker:[tv,chg]}}, ...]}
    # Compact array form (tv, chg) to keep bundle size manageable。
    # 同時建一個 per-ticker net_inst 反向索引,給 hl_sub cluster 用(它的
    # member_keys (近一年焦點||...) 沒 theme_history row,但其 focal ticker
    # 在「其他 main」(半導體等)的 row 內出現過,net_inst 是 ticker-level
    # transaction,跨 (m,s) 值一樣,所以可以共用)。
    theme_history_payload: dict[str, list] = {}
    ticker_net_inst: dict[str, dict[str, float]] = {}  # ticker -> {date_str: net_inst (shares)}
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
        # 反向索引 ticker → date → net_inst(同 ticker 同日跨 (m,s) 值一樣,
        # setdefault 第一次寫入,後續同日寫入會 dedup 為同值)
        for tk, v in breakdown.items():
            if not isinstance(v, dict):
                continue
            ni = v.get("net_inst")
            if ni is None:
                continue
            ticker_net_inst.setdefault(tk, {})[date_str] = ni

    # ticker_close_history (Q13) — per-ticker × per-date close + shares_out,
    # 400 天歷史。用來:
    # (1) hl_sub cluster chart modal 的「焦點股加權指數」資料源(theme_history
    #     沒有「近一年焦點」main 的 row,無法用 focal_breakdown 5-tuple)
    # (2) hl_sub cluster sparkline 也走這(close-based 趨勢)
    # 對 pan_sub 仍可用,但目前還靠 focal_breakdown(後續可漸進切過去)
    ticker_close_payload: dict[str, list[dict]] = {}
    _hist_tickers = list(set(_focal_tw) | set(highlight_tickers))
    if _hist_tickers:
        try:
            tch_rows = await conn.fetch(
                "SELECT ticker, rank_date, close, shares_out FROM ticker_close_history "
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
                ticker_close_payload.setdefault(r["ticker"], []).append({
                    "d": d_str,
                    "c": float(r["close"]) if r["close"] is not None else None,
                    "s": float(r["shares_out"]) if r["shares_out"] is not None else None,
                })
            print(f"  ticker_close_history: {len(tch_rows)} rows for "
                  f"{len(ticker_close_payload)}/{len(_hist_tickers)} tickers")
        except Exception as exc:
            print(f"  ⚠ ticker_close_history query failed: {exc}")

    # 大盤(^TWII)+ 櫃買(^TWO)指數歷史,供 chart 第二張三線 overlay 對比
    # 焦點股 line vs 大盤 vs 櫃買(都 rebase to 100,看相對強弱)。
    market_index_payload = await asyncio.to_thread(_yf_market_index_history, "6mo")

    focus_html, modal_data = build_focus_html(
        tw_ranks, sub_clusters, stocks_info, theme_history_payload,
        market_index_payload, stock_meta,
        highlight_subs=highlight_subs,
        ticker_net_inst=ticker_net_inst,
        focus_hl_clusters=focus_hl_clusters,
    )
    notes_html  = build_notes_html(market_notes, podcast_rows, stocks_info)
    ranking_html = build_focus_ranking_html(_focal_tw, stocks_info, stock_meta)
    catalyst_html = build_catalyst_html(catalyst_events, stocks_info)

    # ── Analyst target prices: batch-fetch then inject into every modal ────────
    _all_modal_tickers: set[str] = set(modal_data.keys())
    if market_notes and market_notes.get("topics"):
        for _topic in market_notes["topics"]:
            _all_modal_tickers.update(_topic.get("tickers", []))
    if _all_modal_tickers:
        print(f"  Fetching analyst data for {len(_all_modal_tickers)} tickers…")
        # 帶 board 給 _yf_analyst_batch,讓 TPEX 股(如 5347)能正確抓到 .TWO
        _modal_tk_boards: dict[str, str | None] = {
            t: stocks_info.get(t, {}).get("board") for t in _all_modal_tickers
        }
        _analyst = await asyncio.to_thread(_yf_analyst_batch, _modal_tk_boards)
    else:
        _analyst = {}

    # Modal: 公司介紹(F1)+ analyst consensus。intro 在前,有 stock_meta
    # 才出;analyst 用 yfinance 既有 batch 結果。
    for _tk in list(modal_data.keys()):
        intro = _build_company_intro_html(stock_meta.get(_tk))
        modal_data[_tk] = intro + _build_analyst_html(_analyst.get(_tk, {}))
    for _tk in _all_modal_tickers:
        if _tk not in modal_data:
            intro = _build_company_intro_html(stock_meta.get(_tk))
            _a_html = _build_analyst_html(_analyst.get(_tk, {}))
            combined = intro + _a_html
            if combined:
                modal_data[_tk] = combined

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

    # ── Radar chart metrics(modal 內 3 維雷達圖;殖利率/β 2026-05-18 移除)──
    # 涵蓋所有焦點股 + ranking + market_notes 提到的 ticker。
    # 缺維度的留 None,前端 normalize 函數視為 0(該軸點落中心)。
    def _f(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    radar_metrics: dict[str, dict] = {}
    for tk in set(list(stocks_info.keys()) + list((stock_meta or {}).keys())):
        info = stocks_info.get(tk, {})
        meta = (stock_meta or {}).get(tk, {})
        close = _f(info.get("close_price"))
        w52h, w52l = _f(meta.get("week52_high")), _f(meta.get("week52_low"))
        w52pos = None
        if close is not None and w52h and w52l and w52h > w52l:
            w52pos = max(0.0, min(1.0, (close - w52l) / (w52h - w52l)))
        radar_metrics[tk] = {
            "chg":  _f(info.get("change_pct")),
            "pe":   _f(meta.get("pe_ttm")),
            "w52":  w52pos,
        }
    def _mavg(key):
        xs = [m[key] for m in radar_metrics.values() if m.get(key) is not None]
        return round(sum(xs) / len(xs), 4) if xs else None
    radar_market_avg = {k: _mavg(k) for k in ("chg", "pe", "w52")}
    radar_payload_json = json.dumps(
        {"stocks": radar_metrics, "market": radar_market_avg},
        ensure_ascii=False, separators=(",", ":"),
    )
    # ── Page HTML ─────────────────────────────────────────────────────────────
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
<style>
:root {{
  --bg:#0f1117; --card:#1a1d26; --border:#2a2e40;
  --text:#e2e8f0; --muted:#7a8ba0;
  --up:#ef5350; --down:#26a69a; --accent:#6c8ef5;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);
      font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
      line-height:1.65;font-size:15px}}
a{{color:var(--accent)}}
button{{cursor:pointer;border:none;outline:none}}

/* ── Ticker tape (seamless) ── */
.tape{{position:sticky;top:0;z-index:200;
       background:var(--card);border-bottom:1px solid var(--border);
       height:36px;overflow:hidden;display:flex;align-items:center;
       white-space:nowrap}}
.tape-track{{display:inline-block;
             animation:tape-scroll 90s linear infinite;
             will-change:transform}}
.tape-item{{display:inline-block;padding:0 .5rem;font-size:1.1rem;color:var(--muted)}}
.tape-item b{{color:var(--text)}}
.tape-up{{color:var(--up)}} .tape-down{{color:var(--down)}}
@keyframes tape-scroll{{0%{{transform:translateX(0)}}100%{{transform:translateX(-50%)}}}}


/* ── Header (logo + tabs in one row) ── */
header{{background:var(--card);border-bottom:1px solid var(--border);
        padding:.5rem 1.5rem;display:flex;align-items:center;gap:1.5rem;
        flex-wrap:wrap}}
.brand{{font-size:1rem;font-weight:700;color:var(--accent);
        cursor:pointer;white-space:nowrap;letter-spacing:.02em;
        background:transparent;border:0;padding:.25rem 0;font-family:inherit;
        transition:.15s}}
.brand:hover{{filter:brightness(1.18)}}

/* ── Tabs ── */
.tabs{{display:flex;gap:.3rem}}
.tab-btn{{background:transparent;color:var(--muted);padding:.42rem .9rem;
          border-radius:6px;font-size:.88rem;font-weight:500;transition:.15s;
          white-space:nowrap}}  /* nowrap 防中文按鈕在窄螢幕斷行成單字 */
.tab-btn:hover{{background:rgba(255,255,255,.04);color:var(--text)}}
.tab-btn.active{{background:var(--accent);color:#fff}}
@media(max-width:480px){{
  .tab-btn{{padding:.4rem .55rem;font-size:.8rem}}  /* 4 個 button 在窄螢幕仍排得下 */
}}
.tab-pane{{display:none}}
.tab-pane.active{{display:block}}

/* ── Site search(header 右側 ticker/公司搜尋)─────────────────────────── */
.search-box{{position:relative;margin-left:auto}}
.search-box input{{font-family:inherit;font-size:.78rem;width:14rem;
                    background:rgba(255,255,255,.04);color:var(--text);
                    border:1px solid var(--border);border-radius:6px;
                    padding:.35rem .65rem;transition:.15s;outline:none}}
.search-box input:focus{{border-color:var(--accent);
                          background:rgba(124,138,242,.06)}}
.search-box input::placeholder{{color:var(--muted);font-weight:500}}
.search-dropdown{{position:absolute;top:calc(100% + 4px);right:0;left:0;
                   max-height:340px;overflow-y:auto;
                   background:#161922;border:1px solid var(--border);
                   border-radius:7px;box-shadow:0 6px 22px rgba(0,0,0,.5);
                   z-index:200;font-size:.76rem}}
.search-item{{display:flex;align-items:center;gap:.55rem;
               padding:.45rem .7rem;cursor:pointer;
               border-bottom:1px solid rgba(255,255,255,.04)}}
.search-item:last-child{{border-bottom:none}}
.search-item:hover, .search-item.kb-active{{background:rgba(124,138,242,.13)}}
.si-ticker{{font-weight:700;color:var(--accent);min-width:3.2rem}}
.si-name{{color:var(--text);flex:1;white-space:nowrap;overflow:hidden;
          text-overflow:ellipsis}}
.si-cluster{{color:var(--muted);font-size:.68rem;white-space:nowrap}}
.search-empty{{padding:.6rem .8rem;color:var(--muted);font-style:italic}}
/* highlight 動畫 — 搜尋結果跳轉到該 cluster 卡片時高亮 1.5s */
.cluster-card.search-hi{{animation:searchHi 1.6s ease-out}}
@keyframes searchHi {{
  0%, 30% {{ box-shadow: 0 0 0 2px var(--accent), 0 0 24px rgba(124,138,242,.5); }}
  100% {{ box-shadow: none; }}
}}
@media(max-width:680px){{
  .search-box{{flex-basis:100%;margin:.4rem 0 0}}
  .search-box input{{width:100%}}
}}

.wrap{{max-width:1120px;margin:0 auto;padding:1.25rem 1.1rem}}

/* ── Sub-tabs (焦點股) ── */
.sub-tabs{{display:flex;gap:.35rem;margin-bottom:.9rem}}
.sub-tab-btn{{background:var(--card);color:var(--muted);
              padding:.32rem .8rem;border-radius:6px;
              font-size:.82rem;font-weight:500;
              border:1px solid var(--border);transition:.15s}}
.sub-tab-btn.active{{background:var(--accent);color:#fff;border-color:var(--accent)}}
.sub-tab-pane{{display:none}}
.sub-tab-pane.active{{display:block}}

/* ── Card ── */
.card{{background:var(--card);border:1px solid var(--border);border-radius:12px;
       padding:1.2rem 1.35rem;margin-bottom:1.1rem}}
.sec{{font-size:1rem;font-weight:700;color:var(--accent);letter-spacing:.04em;
      margin-bottom:.85rem}}
.sec-row{{display:flex;align-items:center;justify-content:space-between;gap:.6rem}}
.rank-dl{{font-family:inherit;font-size:.66rem;font-weight:600;
          padding:.22rem .55rem;border-radius:5px;cursor:pointer;
          background:rgba(124,138,242,.08);color:var(--accent);
          border:1px solid rgba(124,138,242,.3);transition:.15s;
          letter-spacing:.04em;flex-shrink:0}}
.rank-dl:hover{{background:rgba(124,138,242,.18);border-color:var(--accent)}}
.section-hdr{{font-size:.88rem;font-weight:700;color:var(--accent);letter-spacing:.04em;
              margin:1rem 0 .65rem}}

/* ── Report ── */
.report h2{{color:var(--accent);font-size:.98rem;font-weight:600;
            margin:1.1rem 0 .5rem;padding-bottom:.3rem;
            border-bottom:1px solid var(--border)}}
.report h3{{color:#a0b0cc;font-size:.9rem;font-weight:600;margin:.9rem 0 .35rem}}
.report p{{margin-bottom:.55rem;font-size:.9rem}}
.report ul{{padding-left:1.3rem;margin-bottom:.55rem}}
.report li{{margin-bottom:.25rem;font-size:.9rem}}
.report strong{{color:#c0cfe0}}
.report-stocks{{display:flex;flex-wrap:wrap;gap:.35rem;margin:.4rem 0 .65rem}}
.report li .report-stocks{{margin:.35rem 0 .15rem}}

/* ── Catalyst calendar ── */
.cal-empty{{color:var(--muted);font-size:.85rem;padding:.4rem 0}}
.cal-list{{display:flex;flex-direction:column;gap:.1rem}}
.cal-day{{display:grid;grid-template-columns:90px 1fr;gap:.65rem;
          padding:.4rem 0;border-bottom:1px solid var(--border);font-size:.85rem}}
.cal-day:last-child{{border-bottom:none}}
.cal-day.past{{opacity:.55}}
.cal-day.today .cal-date{{color:var(--accent);font-weight:700}}
.cal-date{{color:var(--muted);font-weight:600;font-variant-numeric:tabular-nums}}
.cal-events{{display:flex;flex-wrap:wrap;gap:.3rem}}
.cal-ev{{padding:.15rem .45rem;border-radius:4px;
        background:rgba(255,255,255,.04);color:#c8d4e5;font-size:.82rem}}
.cal-ev.imp-3{{background:rgba(255,150,80,.18);color:#ffba88;font-weight:600}}
.cal-ev.cal-fomc{{background:rgba(255,100,120,.18);color:#ff9aa8;font-weight:600}}
.cal-ev.cal-conference{{background:rgba(120,180,255,.15);color:#a8c8e8}}
.cal-ev.cal-policy{{background:rgba(200,160,255,.15);color:#c8b0e8}}
.cal-ev.has-preview{{cursor:pointer;text-decoration:underline dotted rgba(255,255,255,.3)}}
.cal-ev.has-preview:hover{{filter:brightness(1.15)}}
.cal-preview{{grid-column:1 / -1;display:none;margin:.4rem 0 .2rem 90px;
              padding:.55rem .75rem;background:rgba(255,255,255,.04);
              border-left:2px solid var(--accent);border-radius:4px}}
.cal-preview.open{{display:block}}
.cal-preview-head{{font-size:.78rem;color:var(--accent);font-weight:600;margin-bottom:.35rem}}
.cal-preview-body{{font-size:.82rem;color:#c0cad8;white-space:pre-wrap;
                   font-family:inherit;line-height:1.5;margin:0}}

/* ── Rankings ── */
.ranks{{display:grid;grid-template-columns:1fr 1fr;gap:1.1rem}}
@media(max-width:680px){{.ranks{{grid-template-columns:1fr}}}}
table{{width:100%;border-collapse:collapse}}
th{{color:var(--muted);font-weight:500;font-size:.7rem;text-align:left;
    padding:.28rem .4rem;border-bottom:1px solid var(--border)}}
td{{padding:.25rem .4rem;border-bottom:1px solid rgba(42,46,64,.4);font-size:.8rem}}
td.rank{{color:var(--muted);width:1.6rem}}
td.ticker{{font-weight:600}}
td.name{{white-space:nowrap}}     /* 防 mobile 窄欄把名稱斷成單字直排 */
td.num{{text-align:right;white-space:nowrap}}
/* Sprint 3: 焦點排行 row clickable */
tr.rank-row{{cursor:pointer;transition:background .12s}}
tr.rank-row:hover td{{background:rgba(16,185,129,.08)}}
/* 產業欄文字較長,mobile 隱藏空出空間給其他欄 */
@media(max-width:600px){{
  .col-sector{{display:none}}
}}
tr:last-child td{{border-bottom:none}}
.board-badge{{font-size:.55rem;font-weight:600;padding:.1rem .3rem;
              border-radius:3px;margin-left:.3rem;vertical-align:middle}}
.board-sm{{font-size:.55rem;font-weight:600;padding:.05rem .25rem;
           border-radius:3px;margin-left:.2rem;vertical-align:middle}}
.twse{{background:#1a2a3a;color:#6c8ef5}}
.tpex{{background:#1a2e24;color:#26a69a}}

/* ── Focus stocks ── */
.focus-themes{{display:flex;flex-direction:column;gap:.85rem;margin-bottom:1rem}}
.focus-theme{{background:#12151f;border-radius:10px;padding:1rem 1.1rem;
              border-left:3px solid var(--accent)}}
.theme-top{{display:flex;align-items:center;gap:.55rem;flex-wrap:wrap;margin-bottom:.45rem}}
.theme-ttl{{font-size:.95rem;font-weight:700}}
.sent-badge{{font-size:.65rem;font-weight:700;padding:.15rem .45rem;border-radius:4px}}
/* Asian convention: 紅=偏多/bullish, 綠=偏空/bearish (matches up/down) */
.sent-bull{{background:#3a1a1a;color:#ef7a78}}
.sent-bear{{background:#1a3a2e;color:#5dc4b9}}
.sent-neu{{background:#1e2235;color:var(--muted)}}
.src-note{{font-size:.72rem;color:var(--muted)}}
.theme-summary{{font-size:.85rem;color:#b0bfcf;margin:.35rem 0}}
.kp-list{{font-size:.82rem;padding-left:1.2rem;color:#b0bfcf;margin:.35rem 0}}
.kp-list li{{margin-bottom:.2rem}}
.focus-chips{{display:flex;flex-wrap:wrap;gap:.35rem;margin:.45rem 0}}
.focus-chip{{background:#1e2235;border-left:2px solid #555;font-size:.78rem;
             font-weight:600;padding:.18rem .45rem;border-radius:5px}}
.focus-chip-match{{background:#1a2a3a;border-left:2px solid var(--accent);
                   font-size:.78rem;font-weight:700;padding:.18rem .45rem;border-radius:5px;
                   color:var(--accent)}}
.theme-arts{{margin-top:.4rem}}

/* ── Universal stock toggle panel ── */
.univ-panel{{display:flex;align-items:center;flex-wrap:wrap;gap:.4rem .55rem;
             margin-bottom:.85rem;padding:.6rem .85rem;
             background:#0d1019;border-radius:8px;border:1px solid var(--border)}}
.univ-label{{font-size:.7rem;color:var(--muted);font-weight:600;white-space:nowrap}}
.univ-chip{{font-size:.75rem;font-weight:600;padding:.2rem .55rem;border-radius:20px;
            background:#1a2030;color:var(--accent);border:1px solid #2a3a50;transition:.15s}}
.univ-chip:hover{{background:#1e2a40}}
.univ-chip.disabled{{background:#1e1215;color:#6a5060;border-color:#2e2025;text-decoration:line-through}}

/* ── Cluster sort chips ── */
/* sort chip 與 ⓘ 指標說明合併一列(左:排序,右:ⓘ 說明)。
 * 容器 position:relative,讓展開的 panel 可以 absolute 浮層,
 * summary 位置完全不動;close 動畫由 anim-details JS 跑 max-height transition */
.sort-explainer-row{{position:relative;display:flex;align-items:center;
                      flex-wrap:wrap;gap:.5rem 1rem;margin-bottom:.7rem}}
.sort-explainer-row .sort-row{{margin-bottom:0;flex:0 1 auto}}
.sort-explainer-row .metric-explainer{{margin-bottom:0;margin-left:auto;flex:0 1 auto}}
.metric-explainer{{padding:.2rem 0;font-size:.72rem;color:var(--muted);line-height:1.55}}
.metric-explainer summary{{cursor:pointer;color:var(--accent);font-weight:600;
                            font-size:.73rem;letter-spacing:.02em;user-select:none;
                            list-style:none;padding:.18rem .55rem;border-radius:5px;
                            transition:background .15s}}
.metric-explainer summary:hover{{background:rgba(124,138,242,.08)}}
.metric-explainer summary::-webkit-details-marker{{display:none}}
/* 展開的 panel = absolute 浮層,寬度跟著 sort-explainer-row,
 * 顯示在 row 下方;max-height + opacity 雙重 transition 跑動畫 */
.metric-explainer .metric-panel{{position:absolute;top:calc(100% + .35rem);
                                  left:0;right:0;z-index:25;
                                  background:rgba(20,24,36,.96);
                                  border:1px solid var(--border);border-radius:8px;
                                  box-shadow:0 8px 24px rgba(0,0,0,.45);
                                  padding:.7rem 1rem;
                                  max-height:0;overflow:hidden;opacity:0;
                                  transition:max-height .28s ease,opacity .22s ease,padding .15s}}
.metric-explainer[open] .metric-panel{{padding:.7rem 1rem;opacity:1}}
.metric-explainer ul{{padding-left:1.1rem;margin:0;list-style:disc}}
.metric-explainer li{{margin-bottom:.3rem}}
.metric-explainer li b{{color:var(--text);font-weight:700}}
.metric-explainer .metric-note{{margin-top:.5rem;padding-top:.35rem;
                                  border-top:1px dashed var(--border);
                                  font-size:.7rem;color:var(--muted)}}
.sort-row{{display:flex;align-items:center;flex-wrap:wrap;gap:.4rem;
           margin-bottom:.7rem;padding:.1rem .15rem}}
.sort-label{{font-size:.7rem;color:var(--muted);font-weight:600}}
.sort-chip{{font-family:inherit;font-size:.7rem;font-weight:600;
            padding:.22rem .65rem;border-radius:5px;cursor:pointer;
            background:rgba(255,255,255,.04);color:var(--muted);
            border:1px solid var(--border);transition:.15s;letter-spacing:.02em}}
.sort-chip:hover{{color:var(--text);background:rgba(124,138,242,.08)}}
.sort-chip.active{{background:var(--accent-glow);color:var(--accent);
                    border-color:rgba(124,138,242,.4)}}
/* desc / asc 方向箭頭(只在 active chip 出現,點同 chip 切換方向) */
.sort-chip.active[data-dir="desc"]::after{{content:" ↓";font-weight:800}}
.sort-chip.active[data-dir="asc"]::after{{content:" ↑";font-weight:800}}

/* ── Modal radar chart(個股 5 維 vs 焦點股平均)─────────────────────────── */
.radar-card{{background:rgba(255,255,255,.02);border:1px solid var(--border);
             border-radius:8px;padding:.7rem .8rem;margin-bottom:1rem}}
.radar-title{{font-size:.72rem;color:var(--muted);font-weight:600;
               margin-bottom:.3rem;text-align:center;letter-spacing:.04em}}
.radar-svg{{width:100%;max-width:260px;height:auto;display:block;margin:0 auto}}
.radar-grid{{fill:none;stroke:rgba(255,255,255,.08);stroke-width:.5}}
.radar-spoke{{stroke:rgba(255,255,255,.06);stroke-width:.5}}
.radar-avg{{fill:rgba(124,138,242,.10);stroke:rgba(124,138,242,.55);
             stroke-width:.9;stroke-dasharray:2 1.5}}
.radar-stock{{fill:rgba(239,83,80,.18);stroke:rgba(239,83,80,.85);
               stroke-width:1.3}}
.radar-label{{fill:#a8b5c8;font-size:6.5px;font-weight:600;
               font-family:inherit}}
.radar-val{{fill:var(--accent);font-size:5.8px;font-weight:700;
             font-family:inherit;letter-spacing:.02em}}
.radar-legend{{display:flex;justify-content:center;gap:1.2rem;
                font-size:.66rem;margin-top:.4rem;font-weight:600}}
.rl-stock{{color:#ef5350}}
.rl-avg{{color:var(--accent)}}

/* ── Highlight 區 (近一年焦點) ─────────────────────────────────────────── */
/* 前哨 section:toggle 按鈕 inline append 到 focal 末段,點開後 panel 在
 * focal-stocks div 下方動畫展開(同題材完整 ticker list 扣掉今日 focal,
 * 顯虛線淡色 pill + PE chip,non-clickable)。 */
.sntl-toggle-inline{{display:inline-flex;align-items:center;gap:.25rem;
                      padding:.18rem .55rem;border-radius:4px;
                      font-size:.66rem;color:var(--muted);font-weight:600;
                      letter-spacing:.04em;user-select:none;cursor:pointer;
                      background:rgba(255,255,255,.02);border:1px dashed rgba(255,255,255,.12);
                      font-family:inherit;line-height:1.4;
                      transition:background .15s,color .15s,border-color .15s}}
.sntl-toggle-inline:hover{{color:var(--accent);background:rgba(124,138,242,.08);
                            border-color:rgba(124,138,242,.4)}}
.sntl-toggle-inline .sntl-arrow{{display:inline-block;font-size:.7rem;line-height:1;
                                   transition:transform .25s ease}}
.sntl-toggle-inline.expanded .sntl-arrow{{transform:rotate(180deg)}}
.cluster-sentinel-stocks{{display:flex;flex-wrap:wrap;gap:.3rem .35rem;
                            margin-top:.4rem;padding-top:.4rem;
                            border-top:1px dashed rgba(255,255,255,.08);
                            overflow:hidden;
                            transition:max-height .28s ease,opacity .22s ease}}
/* 顯式 override:.cluster-sentinel-stocks 設了 display:flex 會蓋掉 [hidden]
 * 屬性的 UA display:none,要再加一條 [hidden] 規則特異性提升才能真隱 */
.cluster-sentinel-stocks[hidden]{{display:none}}
.snt-pill{{display:inline-flex;align-items:center;gap:.3rem;
            padding:.2rem .55rem;border-radius:5px;
            background:rgba(255,255,255,.02);
            border:1px dashed rgba(255,255,255,.18);
            color:var(--muted);font-size:.72rem;font-weight:600;
            transition:border-color .15s,background .15s;cursor:default}}
.snt-pill:hover{{border-color:rgba(124,138,242,.5);background:rgba(124,138,242,.06)}}
.snt-pill .sp-ticker{{font-weight:700;font-size:.76rem;color:var(--text)}}
.snt-pill .sp-name{{color:var(--muted);font-size:.7rem}}
.snt-pe{{font-size:.6rem;font-weight:700;padding:.08rem .3rem;border-radius:3px;
         background:rgba(124,138,242,.10);color:var(--accent);letter-spacing:.02em;
         margin-left:.15rem}}
.sntl-hint{{font-weight:400;font-size:.65rem;color:var(--muted);
            margin-left:.3rem;letter-spacing:0}}

/* ── Theme clusters ── */
.focus-clusters{{display:flex;flex-direction:column;gap:.85rem;margin-bottom:1.5rem}}
.cluster-card{{background:#12151f;border-radius:10px;padding:1rem 1.1rem;
               border-left:3px solid var(--accent);will-change:transform}}
/* cluster-hdr: nowrap 強制單行,標題用 flex-grow + ellipsis 自動吃可用空間,
 * 不會把 sparkline / meta 擠到下一行(寬度判斷由瀏覽器 layout 處理,
 * 不再用 30 字硬閾值)。標題太長被截 ellipsis 時 hover 顯 title attr 全名,
 * 點擊切 .expanded 解掉 nowrap 允許多行顯示。 */
.cluster-hdr{{display:flex;align-items:center;gap:.55rem;flex-wrap:nowrap;
              margin-bottom:.7rem;min-width:0}}
.cluster-name{{font-size:.95rem;font-weight:700;
                flex:1 1 auto;min-width:5rem;
                overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
                cursor:pointer;transition:white-space .2s}}
.cluster-name.expanded{{white-space:normal;overflow:visible}}
.cluster-metric,.cluster-meta,.spark-btn,.metric-explainer{{flex-shrink:0}}

/* Merged cluster name (focal 完全相同的子產業聚合) — mobile/tablet 收合。
 * flex-wrap:nowrap 讓 cn-merged 內部不要自己 wrap,搭配 .cluster-name 的
 * ellipsis truncate 邏輯,讓長 merged name 也走「自動截尾 + 點擊展開」。 */
.cn-merged{{display:inline-flex;flex-wrap:nowrap;align-items:baseline;gap:.1rem .25rem}}
.cn-merged > *{{flex-shrink:0}}
.cn-part{{display:inline}}
.cn-sep{{display:inline;color:var(--muted);font-weight:500}}
.cn-toggle{{display:none;font-size:.7rem;font-weight:700;
            background:var(--accent-glow,rgba(108,142,245,.15));
            color:var(--accent);border:none;padding:.05rem .4rem;
            border-radius:4px;cursor:pointer;margin-left:.25rem;
            font-family:inherit;line-height:1.4}}
.cn-toggle:hover{{filter:brightness(1.2)}}
/* Tablet (≤900px): 預設顯示前 3 parts;超過 4 出現按鈕 */
@media(max-width:900px){{
  .cn-merged:not(.expanded) > span:nth-child(n+6){{display:none}}
  .cn-merged[data-parts="4"]:not(.expanded) .cn-toggle,
  .cn-merged[data-parts="5"]:not(.expanded) .cn-toggle,
  .cn-merged[data-parts="6"]:not(.expanded) .cn-toggle,
  .cn-merged[data-parts="7"]:not(.expanded) .cn-toggle,
  .cn-merged[data-parts="8"]:not(.expanded) .cn-toggle,
  .cn-merged[data-parts="9"]:not(.expanded) .cn-toggle,
  .cn-merged[data-parts="10"]:not(.expanded) .cn-toggle{{display:inline-block}}
  .cn-merged.expanded .cn-toggle{{display:inline-block}}
}}
/* Mobile (≤480px): 預設顯示前 2 parts;超過 3 出現按鈕 */
@media(max-width:480px){{
  .cn-merged:not(.expanded) > span:nth-child(n+4){{display:none}}
  .cn-merged[data-parts="3"]:not(.expanded) .cn-toggle{{display:inline-block}}
}}
/* Cluster header metrics (取代舊 cluster-strength badge):
   focal 股平均漲跌、平均 20MA 乖離。.up/.down/.flat/.neutral 沿用 fmt_pct
   的 css class (.cluster-metric.up 等比 global .up 更具體,wins) */
.cluster-metric{{font-size:.66rem;font-weight:700;padding:.16rem .42rem;
                 border-radius:4px;font-variant-numeric:tabular-nums;
                 white-space:nowrap;cursor:help}}
.cluster-metric.up{{background:rgba(239,83,80,.16);color:#f47471}}
.cluster-metric.down{{background:rgba(38,166,154,.14);color:#5dc4b9}}
.cluster-metric.flat{{background:rgba(255,255,255,.06);color:#fff}}
.cluster-metric.neutral{{background:rgba(255,255,255,.05);color:var(--muted)}}
/* metric-btn:badge 可點擊觸發 setClusterSort,is-active-sort 標當前排序維度 */
.cluster-metric.metric-btn{{cursor:pointer;transition:filter .15s,outline-color .15s,box-shadow .15s;
                             user-select:none;outline:1px solid transparent;outline-offset:0}}
.cluster-metric.metric-btn:hover{{filter:brightness(1.18)}}
.cluster-metric.metric-btn:focus-visible{{outline-color:var(--accent)}}
.cluster-metric.metric-btn.is-active-sort{{outline:1.5px solid var(--accent);outline-offset:1px;
                                            box-shadow:0 0 0 2px rgba(124,138,242,.18)}}
.cluster-metric.metric-btn.is-active-sort[data-dir="desc"]::after{{content:" ↓";opacity:.85}}
.cluster-metric.metric-btn.is-active-sort[data-dir="asc"]::after{{content:" ↑";opacity:.85}}
.cluster-meta{{font-size:.72rem;color:var(--muted);margin-left:auto}}
.cluster-meta .meta-label{{opacity:.75}}
.cluster-meta .meta-val{{font-weight:700;margin-left:.15rem}}
.cluster-subtitle{{font-size:.7rem;color:var(--muted);margin:.1rem 0 .35rem;letter-spacing:.02em}}

/* Sparkline button (cluster card 內嵌 6 個月 TV trend) */
.spark-btn{{display:inline-flex;align-items:center;gap:.3rem;
            background:transparent;border:none;cursor:pointer;
            padding:.1rem .35rem;border-radius:5px;transition:background .15s}}
.spark-btn:hover{{background:rgba(255,255,255,.05)}}
/* hl_sub cluster 沒 sparkline 資料時的純 chart 入口按鈕 */
.spark-btn-icon{{font-size:1rem;padding:.15rem .55rem;
                  border:1px solid var(--border);color:var(--accent);
                  background:rgba(124,138,242,.06)}}
.spark-btn-icon:hover{{background:rgba(124,138,242,.18);border-color:rgba(124,138,242,.5)}}
.sparkline{{display:block;width:84px;height:22px}}
/* 紅買綠賣亞洲慣例 */
.sparkline .spark-up{{fill:var(--up)}}
.sparkline .spark-down{{fill:var(--down)}}
.sparkline .spark-mid{{stroke:rgba(255,255,255,.08);stroke-width:.5}}
.spark-label{{font-size:.62rem;color:var(--muted);font-weight:600}}

/* Theme chart modal — 自適應 vh、左右兩欄、無外層 scrollbar(只左欄超過時隱藏式滾) */
dialog#theme-chart-dialog{{background:var(--card);border:1px solid var(--border);
                          border-radius:14px;color:var(--text);padding:0;
                          width:min(1100px,96vw);height:min(820px,92vh);overflow:hidden;
                          position:fixed;top:50%;left:50%;
                          transform:translate(-50%,-50%);margin:0}}
dialog#theme-chart-dialog[open]{{display:flex;flex-direction:column;
                                  animation:tcDialogOpen .26s cubic-bezier(.2,.7,.25,1)}}
dialog#theme-chart-dialog::backdrop{{background:rgba(0,0,0,.65)}}
dialog#theme-chart-dialog[open]::backdrop{{animation:tcBackdropFade .26s ease-out}}
@keyframes tcDialogOpen{{
  from{{opacity:0;transform:translate(-50%,-46%) scale(.96)}}
  to  {{opacity:1;transform:translate(-50%,-50%) scale(1)}}
}}
@keyframes tcBackdropFade{{from{{opacity:0}}to{{opacity:1}}}}
@media(max-width:680px){{
  dialog#theme-chart-dialog{{width:100vw;height:90vh;
                              top:auto;bottom:0;left:0;right:0;
                              transform:translateY(0);
                              border-radius:14px 14px 0 0;border-bottom:none}}
  @keyframes tcDialogOpen{{
    from{{opacity:0;transform:translateY(20px)}}
    to  {{opacity:1;transform:translateY(0)}}
  }}
}}
.tc-hdr{{display:flex;align-items:center;gap:.6rem;
        padding:.85rem 1.1rem;border-bottom:1px solid var(--border);
        flex-shrink:0}}
.tc-title{{font-size:1rem;font-weight:700;line-height:1.35}}
.tc-close{{background:transparent;color:var(--muted);font-size:1.1rem;
          padding:.2rem .4rem;border-radius:5px;line-height:1;border:none;cursor:pointer}}
.tc-close:hover{{background:rgba(255,255,255,.06);color:var(--text)}}
/* body 改成左右 flex 兩欄;不滾自己,charts-col 各 chart 用 flex:1 撐滿 vh */
.tc-body{{padding:.6rem .8rem .8rem;flex:1;display:flex;gap:.7rem;
         min-height:0;overflow:hidden}}
@media(max-width:680px){{
  .tc-body{{flex-direction:column;gap:.5rem}}
}}
/* 左欄:垂直 ticker 列表,scrollbar 隱藏但仍可滾 */
.tc-tickerlist-col{{flex:0 0 250px;display:flex;flex-direction:column;gap:.4rem;
                     padding:.55rem .55rem;border:1px solid var(--border);
                     border-radius:8px;background:rgba(255,255,255,.015);
                     overflow:hidden;min-height:0}}
@media(max-width:680px){{
  .tc-tickerlist-col{{flex:0 0 auto;max-height:30vh}}
}}
.tc-tickerlist-label{{font-size:.7rem;color:var(--muted);font-weight:600;
                       letter-spacing:.04em;padding:.1rem .2rem .3rem;flex-shrink:0;
                       border-bottom:1px solid var(--border)}}
.tc-ticker-chips{{display:flex;flex-direction:column;gap:.32rem;flex:1;min-height:0;
                   overflow-y:auto;padding-right:.1rem;
                   scrollbar-width:none;overscroll-behavior:contain}}
.tc-ticker-chips::-webkit-scrollbar{{display:none}}
.tc-ticker-chips .modal-tk-pill{{align-self:stretch}}
/* 右欄:charts 容器,垂直堆兩張 chart,各 flex:1 撐滿空高 */
.tc-charts-col{{flex:1;display:flex;flex-direction:column;min-width:0;min-height:0;
                 gap:.2rem}}
.tc-chart{{width:100%;flex:1;min-height:160px}}
.tc-chart-label{{font-size:.7rem;font-weight:600;color:var(--muted);
                 letter-spacing:.04em;margin:.3rem 0 .1rem;
                 display:flex;align-items:center;gap:.4rem;flex-shrink:0}}
.tc-chart-label::before{{content:"";width:3px;height:11px;background:var(--accent);
                         border-radius:2px}}
.tc-legend{{display:inline-flex;gap:.3rem;margin-left:.6rem;flex-wrap:wrap}}
/* 時間粒度 chip(1M/3M/6M/1Y/ALL) */
.tc-period{{display:inline-flex;gap:.15rem;align-items:center;flex-shrink:0}}
.tc-period-chip{{font-size:.66rem;font-weight:700;padding:.22rem .45rem;
                  border-radius:5px;background:rgba(255,255,255,.05);
                  color:var(--muted);border:none;cursor:pointer;
                  transition:.15s;font-family:inherit;letter-spacing:.02em}}
.tc-period-chip:hover{{color:var(--text)}}
.tc-period-chip.active{{background:var(--accent-glow);color:var(--accent)}}
@media(max-width:480px){{
  .tc-period-chip{{padding:.18rem .35rem;font-size:.6rem}}
}}
.tc-leg-chip{{display:inline-flex;align-items:center;gap:.3rem;
              font-size:.66rem;font-weight:700;padding:.15rem .45rem;
              border-radius:4px;background:rgba(255,255,255,.05);
              color:var(--muted);border:none;cursor:pointer;transition:.15s;
              font-family:inherit}}
.tc-leg-chip .leg-sw{{display:inline-block;width:10px;height:2px;
                      background:currentColor;border-radius:1px}}
.tc-leg-chip:hover{{color:var(--text)}}
.tc-leg-chip.active.leg-cluster{{color:#10b981}}
.tc-leg-chip.active.leg-twii{{color:#f59e0b}}
.tc-leg-chip.active.leg-tpex{{color:#94aef7}}
.tc-leg-chip:not(.active){{opacity:.5;text-decoration:line-through}}
/* Modal 內可點擊 ticker pill:複用 .stk-pill 樣式,加 disable 視覺。
 * 在左欄垂直列表時 width:100%、內容 nowrap(中文不要破字)、
 * 超寬就讓 sp-name 截字。 */
.modal-tk-pill{{cursor:pointer;flex-shrink:0;
                transition:opacity .18s,filter .18s,border-color .18s}}
.modal-tk-pill:hover{{border-color:var(--accent)}}
.modal-tk-pill.is-dis{{opacity:.35;filter:grayscale(.8)}}
.modal-tk-pill.is-dis .sp-quote{{text-decoration:line-through}}
.modal-tk-pill .sp-ticker,
.modal-tk-pill .mkt-badge,
.modal-tk-pill .sp-name,
.modal-tk-pill .sp-quote{{white-space:nowrap}}
.modal-tk-pill .sp-name{{overflow:hidden;text-overflow:ellipsis;min-width:0;
                          flex-shrink:0;max-width:7em}}
.modal-tk-pill .sp-quote{{margin-left:auto;flex-shrink:0}}
/* tooltip 觸發 ⓘ icon(native title attribute) */
.tc-info{{display:inline-flex;align-items:center;justify-content:center;
          width:14px;height:14px;border-radius:50%;
          background:rgba(124,138,242,.15);color:var(--accent);
          font-size:.6rem;font-weight:700;cursor:help;line-height:1;
          margin-left:.3rem;flex-shrink:0;font-style:normal}}
.tc-info:hover,.tc-info:focus{{background:rgba(124,138,242,.3);outline:none}}
/* 三大法人 histogram 當日/累計 切換 */
.tc-net-mode{{display:inline-flex;gap:.15rem;margin-left:auto;flex-shrink:0}}
.tc-mode-chip{{font-size:.65rem;font-weight:700;padding:.18rem .5rem;
                border-radius:4px;background:rgba(255,255,255,.05);
                color:var(--muted);border:none;cursor:pointer;
                transition:.15s;font-family:inherit;letter-spacing:.02em}}
.tc-mode-chip:hover{{color:var(--text)}}
.tc-mode-chip.active{{background:var(--accent-glow);color:var(--accent)}}
.tc-empty{{color:var(--muted);font-size:.85rem;text-align:center;padding:2rem 0}}
.cluster-section-label{{font-size:.68rem;color:var(--muted);font-weight:600;
                         text-transform:uppercase;letter-spacing:.04em;margin:.55rem 0 .3rem}}
.cluster-focal-stocks{{display:flex;flex-wrap:wrap;gap:.45rem;margin-bottom:.4rem}}
.focal-pill{{display:flex;align-items:center;gap:.3rem;
             background:var(--card);border:1px solid var(--border);border-radius:8px;
             padding:.35rem .65rem;cursor:pointer;transition:.15s}}
.focal-pill:hover{{border-color:var(--accent)}}
.fp-ticker{{font-weight:800;font-size:.85rem}}
.fp-name{{font-size:.75rem;color:var(--muted)}}
.fp-pct{{font-weight:700;font-size:.82rem}}
.fp-rank{{color:var(--muted);font-size:.7rem}}
.cluster-watch-stocks{{display:flex;flex-wrap:wrap;gap:.3rem;margin-bottom:.4rem}}
.watch-chip{{font-size:.78rem;padding:.15rem .45rem;border-radius:5px;font-weight:600;display:inline-flex;align-items:center;gap:.25rem}}
.watch-chip.tw{{background:#12201a;border:1px solid #1a3a2a;color:#4caf82}}
.watch-chip.us{{background:#121520;border:1px solid #1a2a3a;color:#6c8ef5}}
.wc-up{{color:#ef5350;font-size:.72rem}}.wc-down{{color:#26a69a;font-size:.72rem}}
.cluster-sc{{font-size:.72rem;color:var(--muted);margin:.2rem 0 .35rem;display:flex;flex-wrap:wrap;gap:.4rem .8rem}}
.sc-label{{font-weight:700;color:#6c7a8a}}.sc-items{{color:var(--muted)}}
.cluster-arts{{margin-top:.3rem}}

/* ── Stock grid (clickable cards) ── */
.stock-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(175px,1fr));gap:.7rem}}
.stock-card{{background:#12151f;border-radius:9px;padding:.8rem .9rem;
             cursor:pointer;transition:.15s;border:1px solid transparent}}
.stock-card:hover{{border-color:var(--accent);background:#14182a}}
.sc-head{{display:flex;align-items:center;gap:.35rem;flex-wrap:wrap;margin-bottom:.35rem}}
.sc-ticker{{font-size:.9rem;font-weight:800}}
.mkt-badge{{font-size:.55rem;font-weight:700;padding:.1rem .3rem;border-radius:3px}}
.mkt-us{{background:#1a2a3a;color:#6c8ef5}}
.mkt-tw{{background:#1a2e24;color:#26a69a}}
.sc-name{{font-size:.75rem;color:var(--muted);margin-left:auto}}
.sc-meta{{display:flex;align-items:center;gap:.4rem;flex-wrap:wrap;font-size:.8rem}}
.sc-pct{{font-weight:700}}
.sc-val{{color:var(--muted)}}
.sc-rank{{color:var(--muted);font-size:.72rem}}
.vol-tag{{font-size:.62rem;font-weight:600;padding:.1rem .3rem;border-radius:3px}}
.vol-hot{{background:#2a1a1a;color:#ef5350}}
.vol-high{{background:#2a2a14;color:#c8a840}}
.vol-mid{{background:#1e2235;color:var(--muted)}}
.limit-up-badge{{font-size:.62rem;font-weight:700;padding:.1rem .3rem;
                 border-radius:3px;background:#2a1a3a;color:#cf6ef5}}
.sc-arts-hint{{font-size:.72rem;color:var(--muted);margin-top:.4rem}}

/* ── Article modal (centered) ── */
dialog#art-modal{{background:var(--card);border:1px solid var(--border);
                  border-radius:14px;color:var(--text);padding:0;
                  width:min(680px,96vw);max-height:80vh;overflow:hidden;
                  position:fixed;top:50%;left:50%;
                  transform:translate(-50%,-50%);margin:0}}
dialog#art-modal[open]{{display:flex;flex-direction:column}}
dialog#art-modal::backdrop{{background:rgba(0,0,0,.65)}}
.modal-hdr{{display:flex;align-items:center;gap:.6rem;
            padding:.85rem 1.1rem;border-bottom:1px solid var(--border);
            flex-shrink:0}}
.modal-hdr-title{{font-size:.95rem;font-weight:700;flex:1}}
.modal-close{{background:transparent;color:var(--muted);font-size:1.1rem;
              padding:.2rem .4rem;border-radius:5px;line-height:1}}
.modal-close:hover{{background:#1e2235;color:var(--text)}}
.modal-body{{overflow-y:auto;padding:.9rem 1.1rem;flex:1}}
.modal-art{{background:#12151f;border-radius:8px;padding:.75rem .9rem;margin-bottom:.7rem}}
.modal-art-meta{{font-size:.72rem;color:var(--muted);margin-bottom:.25rem}}
.modal-art-title{{font-size:.85rem;font-weight:600;color:#c8d8ea;margin-bottom:.35rem}}
.modal-snip{{font-size:.82rem;color:#b0bfcf;white-space:pre-wrap;line-height:1.65}}
.modal-section{{margin-bottom:.9rem}}
/* F1: 公司介紹 section */
.ci-tags{{font-size:.7rem;color:var(--accent);font-weight:600;margin-bottom:.25rem;letter-spacing:.04em}}
.ci-name-en{{font-size:.78rem;color:var(--muted);font-style:italic;margin-bottom:.3rem}}
.ci-meta{{font-size:.75rem;color:var(--muted);margin-bottom:.4rem}}
.ci-meta a{{color:var(--accent);text-decoration:none}}
.ci-meta a:hover{{text-decoration:underline}}
.ci-desc{{font-size:.82rem;color:#c0cad8;line-height:1.6}}
.modal-section-hdr{{font-size:.68rem;font-weight:600;color:var(--muted);
                    text-transform:uppercase;letter-spacing:.07em;margin-bottom:.5rem}}
.analyst-grid{{display:grid;grid-template-columns:1fr 1fr;gap:.4rem;margin-bottom:.4rem}}
.ag-cell{{background:#0f1117;border-radius:7px;padding:.45rem .65rem}}
.ag-label{{font-size:.66rem;color:var(--muted);display:block;margin-bottom:.1rem}}
.ag-val{{font-size:.95rem;font-weight:700}}
.ag-high{{color:var(--up)}}.ag-low{{color:var(--down)}}
.ag-meta{{font-size:.74rem;color:var(--muted)}}
.analyst-rec{{font-weight:700}}

/* ── Cross-source topics (tab 3) ── */
.topics-grid{{display:flex;flex-direction:column;gap:.85rem;margin-bottom:1.25rem}}
.topic-card{{background:#12151f;border-radius:10px;padding:1rem 1.1rem;
             border-left:3px solid var(--up)}}
.topic-head{{display:flex;align-items:center;gap:.5rem;margin-bottom:.4rem;flex-wrap:wrap}}
.topic-name{{font-size:.9rem;font-weight:700}}
.topic-sum{{font-size:.84rem;color:#b0bfcf;margin:.35rem 0}}
.tk-row{{display:flex;flex-wrap:wrap;gap:.3rem;margin:.35rem 0}}

/* ── Podcast notes ── */
.pod-source{{background:var(--card);border:1px solid var(--border);
             border-radius:10px;margin-bottom:.75rem}}
.pod-src-hdr{{padding:.75rem 1rem;cursor:pointer;display:flex;
              align-items:center;gap:.5rem;user-select:none}}
.pod-src-hdr:hover{{background:#1e2235;border-radius:10px}}
.pod-src-arrow{{font-size:.75rem;color:var(--muted);transition:.2s;width:1rem}}
.pod-src-name{{font-weight:700;font-size:.9rem}}
.ep-cnt{{font-size:.72rem;color:var(--muted);margin-left:auto}}
.pod-episodes{{padding:.5rem 1rem 1rem}}
.pod-episodes.hidden{{display:none}}
.ep-block{{border-top:1px solid var(--border);padding:.5rem 0}}
.ep-hdr{{display:flex;align-items:baseline;gap:.5rem;cursor:pointer;
         padding:.3rem 0;user-select:none}}
.ep-hdr:hover .ep-title{{color:var(--accent)}}
.ep-arrow{{font-size:.7rem;color:var(--muted);width:.9rem;transition:.2s}}
.ep-title{{font-size:.85rem;font-weight:600;flex:1}}
.ep-date{{font-size:.72rem;color:var(--muted);white-space:nowrap}}
.ep-body{{margin:.5rem 0 .5rem .9rem;font-size:.82rem;color:#b0bfcf;line-height:1.7}}
.ep-body.hidden{{display:none}}
.pod-raw{{white-space:pre-wrap;font-family:inherit;font-size:.8rem;
          color:#b0bfcf;line-height:1.75;overflow-wrap:break-word;margin:0}}
.pod-raw-note{{font-size:.72rem;color:var(--muted);margin-bottom:.4rem;font-style:italic}}
.pod-notes{{font-size:.82rem;color:#b0bfcf;line-height:1.6;overflow-wrap:break-word}}
.pod-notes h4{{color:var(--accent);font-size:.82rem;font-weight:700;
               margin:.9rem 0 .35rem;border-bottom:1px solid var(--border);padding-bottom:.2rem}}
.pod-subsec{{font-weight:600;color:#c0cfe0;font-size:.83rem;margin:.8rem 0 .15rem}}
.pod-num-item{{padding:.1rem 0 .1rem .9rem;}}
.pod-num{{color:var(--muted);font-size:.78rem;margin-right:.3rem}}
.pod-bul-item{{padding:.1rem 0 .1rem .9rem;}}
.muted-note{{color:var(--muted);font-size:.85rem;padding:.5rem 0}}
.art-seg-chips{{display:flex;flex-wrap:wrap;gap:.25rem;margin-top:.3rem}}
.art-seg-chip{{font-size:.7rem;font-weight:600;padding:.1rem .35rem;
               border-radius:4px;background:#1e2235;border:1px solid #2a3050;
               color:var(--accent);cursor:pointer;transition:.15s}}
.art-seg-chip:hover{{background:#252a40;border-color:var(--accent)}}

/* ── Unified stock pill (全站統一模組) ── */
.stk-pill{{display:inline-flex;align-items:center;gap:.28rem;
           background:var(--card);border:1px solid var(--border);border-radius:7px;
           padding:.3rem .6rem;cursor:pointer;transition:.15s;font-size:.82rem}}
.stk-pill:hover{{border-color:var(--accent)}}
.stk-pill.pill-disabled{{opacity:.32;filter:grayscale(.75);transition:opacity .18s,filter .18s}}
.cluster-card{{transition:transform .38s cubic-bezier(.25,.46,.45,.94)}}
.stk-pill[onclick=""],.stk-pill:not([onclick]){{cursor:default}}
.sp-ticker{{font-weight:800;font-size:.85rem}}
.sp-name{{font-size:.72rem;color:var(--muted)}}
.sp-quote{{font-weight:700;font-size:.78rem;font-variant-numeric:tabular-nums}}
/* 處置 / 漲跌停 小 tag(ingest 5a172be 起 extra 帶 flag):
 *   嚴處 = 紅底(嚴格處置;最嚴重)
 *   處   = 橘底(一般處置;警示等級)
 *   漲   = 紅底(亞洲漲)
 *   跌   = 綠底(亞洲跌)
 * pill / rankings table 都用同套 chip,_flag_chips() 共用渲染 */
.sp-tag{{font-size:.58rem;font-weight:700;padding:.06rem .3rem;border-radius:3px;
         letter-spacing:.04em;line-height:1.3;flex-shrink:0;cursor:help}}
.sp-tag.tag-strict{{background:rgba(220,38,38,.22);color:#fca5a5;
                     border:1px solid rgba(220,38,38,.5)}}
.sp-tag.tag-punish{{background:rgba(245,158,11,.18);color:#f59e0b;
                     border:1px solid rgba(245,158,11,.35)}}
.sp-tag.tag-limit-up{{background:rgba(239,83,80,.18);color:#f47471;
                       border:1px solid rgba(239,83,80,.4)}}
.sp-tag.tag-limit-down{{background:rgba(38,166,154,.18);color:#5dc4b9;
                         border:1px solid rgba(38,166,154,.4)}}

.up{{color:var(--up)}} .down{{color:var(--down)}} .flat{{color:#fff}} .neutral{{color:var(--muted)}}
footer{{color:var(--muted);font-size:.75rem;
        padding:1.5rem 1rem;border-top:1px solid var(--border);margin-top:.5rem;
        line-height:1.6}}
footer .disclaimer{{max-width:760px;margin:0 auto .8rem;text-align:left}}
footer .disclaimer h3{{color:#a0b0cc;font-size:.78rem;font-weight:600;
                       margin:0 0 .35rem;letter-spacing:.04em}}
footer .meta{{text-align:center;padding-top:.6rem;border-top:1px dashed var(--border)}}
.share-row{{display:flex;flex-wrap:wrap;justify-content:center;align-items:center;
            gap:.4rem;max-width:760px;margin:0 auto 1rem;padding-bottom:.8rem;
            border-bottom:1px dashed var(--border)}}
.share-label{{color:var(--muted);font-size:.72rem;margin-right:.25rem}}
.share-btn{{font-family:inherit;font-size:.72rem;font-weight:600;
            padding:.32rem .7rem;border-radius:6px;cursor:pointer;
            background:rgba(255,255,255,.04);color:var(--text);
            border:1px solid var(--border);transition:.15s}}
.share-btn:hover{{background:rgba(124,138,242,.12);
                   border-color:rgba(124,138,242,.4);color:var(--accent)}}
@media(max-width:480px){{
  .share-row{{gap:.3rem}}
  .share-btn{{padding:.28rem .55rem;font-size:.68rem}}
  .share-label{{flex-basis:100%;text-align:center;margin:0 0 .35rem}}
}}
</style>
</head>
<body>

<!-- Ticker tape -->
<div class="tape">{tape_html}</div>

<header>
  <button class="brand" onclick="showTab('market');window.scrollTo(0,0);" title="回首頁">IIA 投資情報</button>
  <nav class="tabs">
    <button class="tab-btn active" data-tab="market"  onclick="showTab('market')">市場行情</button>
    <button class="tab-btn"        data-tab="focus"   onclick="showTab('focus')">熱門題材</button>
    <button class="tab-btn"        data-tab="ranking" onclick="showTab('ranking')">焦點排行</button>
    <button class="tab-btn"        data-tab="notes"   onclick="showTab('notes')">股市筆記</button>
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
  <!-- Tab 1: 市場行情 -->
  <div id="tab-market" class="tab-pane active">
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
          <tbody>{rank_rows_html(tw_ranks, 'TW')}</tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Tab 2: 熱門題材 -->
  <div id="tab-focus" class="tab-pane">
    {focus_html}
  </div>

  <!-- Tab 3: 焦點排行 (Sprint 3) -->
  <div id="tab-ranking" class="tab-pane">
    {ranking_html}
  </div>

  <!-- Tab 4: 股市筆記 -->
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
              title="加權指數計算法&#10;1. 每檔焦點股當日市值 = 收盤價 × 流通在外股數(shares_outstanding)&#10;2. cluster daily mcap = Σ 全部焦點股當日市值;某檔某日缺資料時用該檔最後一次有資料的 close × shares 延續(per-ticker forward-fill,標準加權指數做法)&#10;3. 三條線(cluster / TWII / TPEX)同時 rebase 到 100(取三條共同起點當基準),純看相對強弱不看絕對水位&#10;4. shares_outstanding 來自 stock_meta(每週日 04:00 由 ingest 端 yfinance Ticker.info 拉),新熱門股當日由 18:30 cron 即時補&#10;5. cluster 線會依「焦點 chip 列表」即時重算">ⓘ</span>
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
              title="資料來源:TWSE T86(集中市場)+ TPEX 3insti(店頭)三大法人(外資 + 投信 + 自營商)當日合計買賣超「金額」(NTD)。&#10;cluster 當日淨流入 = Σ 全部焦點股淨買賣金額(單位轉億 TWD);某檔某日缺資料當 0(不 forward-fill,因為法人買賣超是日結 transaction)。&#10;紅柱 = 法人淨買、綠柱 = 法人淨賣。&#10;切換「累計」會把當日數值改成從圖表起點開始的滾動累加,看資金長期流向。">ⓘ</span>
        <span class="tc-net-mode">
          <button class="tc-mode-chip active" data-mode="daily" type="button" onclick="setNetMode('daily')">當日</button>
          <button class="tc-mode-chip" data-mode="cum" type="button" onclick="setNetMode('cum')">累計</button>
        </span>
      </div>
      <div class="tc-chart" id="tc-chart-net"></div>

      <div class="tc-empty" id="tc-empty" style="display:none">尚無歷史資料(資料每日 18:30 由 ingest 端產生)</div>
    </div>
  </div>
</dialog>

<footer>
  <div class="share-row">
    <span class="share-label">分享今日報告：</span>
    <button class="share-btn share-native" type="button" onclick="shareReport('native')" hidden>原生分享</button>
    <button class="share-btn" type="button" onclick="shareReport('line')">Line</button>
    <button class="share-btn" type="button" onclick="shareReport('x')">X</button>
    <button class="share-btn" type="button" onclick="shareReport('fb')">Facebook</button>
    <button class="share-btn" type="button" onclick="shareReport('copy')">複製連結</button>
  </div>
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
window.IIA_RADAR = {radar_payload_json};

function showTab(name) {{
  document.querySelectorAll('.tab-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-pane').forEach(p =>
    p.classList.toggle('active', p.id === 'tab-' + name));
}}

function showSubTab(name) {{
  document.querySelectorAll('.sub-tab-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.stab === name));
  document.querySelectorAll('.sub-tab-pane').forEach(p =>
    p.classList.toggle('active', p.id === 'stab-' + name));
}}

/* 個股 3 維雷達:漲跌 / PE / 52w%(殖利率 + β 2026-05-18 全站移除),normalize 到 0~1 後畫
 * polygon。同時疊一條 dashed 全焦點股平均對比。SVG concat 不用 template
 * literal 是為了避開 Python fstring `{{}}` escape 噪音。 */
function _radarSvg(ticker) {{
  const data = (window.IIA_RADAR || {{}});
  const m = (data.stocks || {{}})[ticker];
  if (!m) return '';
  const avg = data.market || {{}};
  // 維度 normalize:統一 0~1 範圍。PE 越低越好(反向),其他越高越好。
  // (2026-05-18:殖利率 + β 全站移除,從 5 維變 3 維三角形)
  const N = {{
    chg:  v => v == null ? 0 : Math.max(0, Math.min(1, (v + 8) / 16)),
    pe:   v => v == null ? 0 : Math.max(0, Math.min(1, 1 - v / 40)),
    w52:  v => v == null ? 0 : Math.max(0, Math.min(1, v)),
  }};
  const dims = [
    {{ key:'chg',  label:'漲跌',  raw:m.chg,  fmt:v => v==null?'—':(v>=0?'+':'')+v.toFixed(2)+'%' }},
    {{ key:'pe',   label:'PE',    raw:m.pe,   fmt:v => v==null?'—':v.toFixed(1) }},
    {{ key:'w52',  label:'52w%',  raw:m.w52,  fmt:v => v==null?'—':(v*100).toFixed(0)+'%' }},
  ];
  // 若全 null,無資料就不畫
  if (dims.every(d => d.raw == null)) return '';
  const cx = 80, cy = 80, r = 50;
  const ang = (i) => -Math.PI/2 + i * 2*Math.PI/dims.length;
  const pt = (i, v) => {{
    const a = ang(i);
    return [cx + Math.cos(a)*r*v, cy + Math.sin(a)*r*v];
  }};
  const pts = (vals) => vals.map((v, i) => {{
    const [x, y] = pt(i, v);
    return x.toFixed(1)+','+y.toFixed(1);
  }}).join(' ');
  const stockVals = dims.map(d => N[d.key](d.raw));
  const avgVals = dims.map(d => N[d.key](avg[d.key]));
  const ringVals = (s) => dims.map(_ => s);
  const parts = [];
  parts.push('<div class="radar-card">');
  parts.push('<div class="radar-title">三維雷達 vs 全焦點股平均</div>');
  parts.push('<svg class="radar-svg" viewBox="0 0 160 165">');
  parts.push('<polygon class="radar-grid" points="' + pts(ringVals(1))    + '"/>');
  parts.push('<polygon class="radar-grid" points="' + pts(ringVals(0.66)) + '"/>');
  parts.push('<polygon class="radar-grid" points="' + pts(ringVals(0.33)) + '"/>');
  dims.forEach((_, i) => {{
    const [x, y] = pt(i, 1);
    parts.push('<line class="radar-spoke" x1="' + cx + '" y1="' + cy +
               '" x2="' + x.toFixed(1) + '" y2="' + y.toFixed(1) + '"/>');
  }});
  parts.push('<polygon class="radar-avg"   points="' + pts(avgVals)   + '"/>');
  parts.push('<polygon class="radar-stock" points="' + pts(stockVals) + '"/>');
  dims.forEach((d, i) => {{
    const [lx, ly] = pt(i, 1.32);
    const ca = Math.cos(ang(i));
    const anchor = ca > 0.2 ? 'start' : (ca < -0.2 ? 'end' : 'middle');
    parts.push('<text class="radar-label" x="' + lx.toFixed(1) + '" y="' + ly.toFixed(1) +
               '" text-anchor="' + anchor + '">' + d.label + '</text>');
    parts.push('<text class="radar-val" x="' + lx.toFixed(1) + '" y="' + (ly+7).toFixed(1) +
               '" text-anchor="' + anchor + '">' + d.fmt(d.raw) + '</text>');
  }});
  parts.push('</svg>');
  parts.push('<div class="radar-legend">');
  parts.push('<span class="rl-stock">▬ ' + ticker + '</span>');
  parts.push('<span class="rl-avg">▬ 焦點股平均</span>');
  parts.push('</div>');
  parts.push('</div>');
  return parts.join('');
}}

function showArtModal(ticker, name) {{
  const modal = document.getElementById('art-modal');
  document.getElementById('modal-title').textContent = ticker + ' ' + name;
  const radar = _radarSvg(ticker);
  const body = artModalData[ticker] || '<p style="color:#7a8ba0">尚無分析師或文章資料</p>';
  document.getElementById('modal-body').innerHTML = radar + body;
  modal.showModal();
}}

/* Merged cluster name — 計算螢幕對應 visible 閾值並產出 "+N ▾" / "收合 ▴" */
function _mergedVisibleCount() {{
  const w = window.innerWidth;
  if (w <= 480) return 2;
  if (w <= 900) return 3;
  return Infinity;
}}

function _refreshClusterToggle(el) {{
  const btn = el.querySelector('.cn-toggle');
  if (!btn) return;
  const parts = parseInt(el.dataset.parts, 10) || 0;
  if (el.classList.contains('expanded')) {{
    btn.textContent = '收合 ▴';
    return;
  }}
  const visible = _mergedVisibleCount();
  if (parts > visible) {{
    btn.textContent = '+' + (parts - visible) + ' ▾';
  }} else {{
    btn.textContent = '';
  }}
}}

function toggleClusterName(btn) {{
  const el = btn.closest('.cn-merged');
  if (!el) return;
  el.classList.toggle('expanded');
  _refreshClusterToggle(el);
}}

/* cluster-name 點擊展開/收合:用 CSS .expanded 切 white-space:nowrap → normal
 * 取代之前的 30 字硬閾值。寬度由瀏覽器 layout 自動判斷(cluster-hdr nowrap
 * + cluster-name flex:1 + ellipsis),空間不夠就 ellipsis 自動截尾,
 * 不會把 sparkline 擠到下一行;hover 顯 title attr 全名,點擊解 nowrap
 * 多行展開。 */
function toggleNameExpand(el) {{
  el.classList.toggle('expanded');
}}

function _initMergedNames() {{
  document.querySelectorAll('.cn-merged').forEach(_refreshClusterToggle);
}}
window.addEventListener('load', _initMergedNames);
window.addEventListener('resize', _initMergedNames);

/* 頁面 load 時刷一次 sort UI 狀態 + 跑 _recalcClusters 把 cluster meta
 * 文字校正成「平均乖離 X%」(Python 初始 render 只寫「N 檔焦點 · TV」)。
 * 因 Python 端已 pre-sort by bias desc,DOM 順序跟 JS 算出來一致 →
 * FLIP 動畫 dy≈0 不會跳。 */
window.addEventListener('load', () => {{
  const C = window.IIA_CLUSTERS || {{}};
  ['hl_sub', 'pan_sub', 'sub'].forEach(lv => {{
    if (typeof _refreshSortUi === 'function') _refreshSortUi(lv);
    if (typeof _recalcClusters === 'function' && C[lv]) _recalcClusters(lv);
  }});
}});

/* 廣泛概念股濾除 — 點 univ-chip 把該 ticker 在每個 cluster 內反灰、
 * cluster meta 重算、整列依 activeTv 重排(FLIP 動畫)。state 全域共用,
 * 兩 sub-tab(hl_sub / pan_sub)的 cluster 都受影響。 */
const _univDis = new Set();

/* cluster 排序 state per level('hl_sub' / 'pan_sub'),預設 'chg' desc。
 * 重複點同一個 chip → 切 desc ↔ asc;切不同 key → 重置 desc。
 * 兩 tab 各管自己的 state,sort chip 用 data-level 鎖定該 tab。 */
const _clusterSort = {{}};      // level -> 'chg' / 'bias' / ...
const _clusterSortDir = {{}};   // level -> 'desc' / 'asc'
function _getSortKey(level)  {{ return _clusterSort[level] || 'tv'; }}
function _getSortDir(level)  {{ return _clusterSortDir[level] || 'desc'; }}
/* 只刷該 level 的 sort-chip(只影響該 sub-tab),不會誤動別 tab */
function _refreshSortUi(level) {{
  const key = _getSortKey(level), dir = _getSortDir(level);
  document.querySelectorAll('.sort-chip[data-level="' + level + '"]').forEach(b => {{
    const on = b.dataset.sort === key;
    b.classList.toggle('active', on);
    b.dataset.dir = on ? dir : '';
  }});
}}

/* ── Per-cluster focal sort ─────────────────────────────────────────────────
 * cluster header 的 metric badge(乖離/漲跌/PE/殖利/β)點擊只動該題材
 * 內的 focal pill 順序,不影響外層 cluster 排序。state per cardId,
 * 預設 bias desc(對齊 Python 端 focal_sorted 初始順序)。 */
const _focalSort = new Map();  // cardId -> {{ key, dir }}
function _getFocalSort(cardId) {{
  if (!_focalSort.has(cardId)) _focalSort.set(cardId, {{ key: 'chg', dir: 'desc' }});
  return _focalSort.get(cardId);
}}
function setFocalSort(cardId, key) {{
  const cur = _getFocalSort(cardId);
  if (cur.key === key) cur.dir = cur.dir === 'desc' ? 'asc' : 'desc';
  else {{ cur.key = key; cur.dir = 'desc'; }}
  _renderFocalSort(cardId);
}}

/* 依排序 key 算 pill 報價括號內的內容 + 顏色 class。
 * chg(預設):「close(±X.XX%)」沿用既有格式不加 prefix
 * 其他:「close(prefix value)」加維度 prefix,避免使用者混淆是哪一項 */
function _focalQuoteByKey(f, key) {{
  if (f.close == null) {{
    // 沒收盤價就只顯該維度數字
    if (key === 'chg') {{ const p = _fmtPctJs(f.chg); return {{ str: p.str, cls: p.cls }}; }}
    return {{ str: '—', cls: 'neutral' }};
  }}
  const closeStr = f.close.toFixed(2);
  if (key === 'chg') {{
    const p = _fmtPctJs(f.chg);
    return {{ str: closeStr + (f.chg != null ? '(' + p.str + ')' : ''), cls: p.cls }};
  }}
  if (key === 'bias') {{
    const v = f.bias;
    if (v == null) return {{ str: closeStr + '(乖離 —)', cls: 'neutral' }};
    const sign = v > 0 ? '+' : '';
    const cls = v > 0 ? 'up' : (v < 0 ? 'down' : 'flat');
    return {{ str: closeStr + '(乖離 ' + sign + v.toFixed(2) + '%)', cls }};
  }}
  if (key === 'pe') {{
    const v = f.pe;
    return {{ str: closeStr + '(PE ' + (v == null || v <= 0 ? '—' : v.toFixed(1)) + ')', cls: 'neutral' }};
  }}
  // 2026-05-18 起 yield/beta 全站移除,fallback 顯純 close
  return {{ str: closeStr, cls: 'neutral' }};
}}

function _renderFocalSort(cardId) {{
  const card = document.getElementById(cardId);
  if (!card) return;
  const cluster = _findClusterDef(cardId);
  if (!cluster) return;
  const state = _getFocalSort(cardId);
  // 排序 focal entries(skip _univDis 在外層 _recalcClusters 用 pill-disabled
  // 表達,排序這裡不過濾,保持 pill 都存在,只是順序變)。null 永遠排尾段
  // 不受方向影響(避免缺資料卡在最前面誤導,實例:5347 沒 ma20_bias)。
  const dirMul = state.dir === 'asc' ? -1 : 1;
  const sorted = cluster.focal.slice().sort((a, b) => {{
    const va = a[state.key], vb = b[state.key];
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    return (vb - va) * dirMul;
  }});
  // 拿 DOM pill 重排 + 更新 quote span 顯示當前 sort key 的值
  const container = card.querySelector('.cluster-focal-stocks');
  if (!container) return;
  const pillMap = {{}};
  container.querySelectorAll('.stk-pill[data-cluster-ticker]').forEach(p => {{
    pillMap[p.dataset.clusterTicker] = p;
  }});
  sorted.forEach(f => {{
    const p = pillMap[f.ticker];
    if (!p) return;
    container.appendChild(p);
    const q = p.querySelector('.sp-quote');
    if (q) {{
      const r = _focalQuoteByKey(f, state.key);
      q.textContent = r.str;
      q.className = 'sp-quote ' + r.cls;
    }}
  }});
  // 更新該卡片內 badge 的 active 狀態(只此卡)
  card.querySelectorAll('.cluster-metric.metric-btn').forEach(b => {{
    const on = b.dataset.sort === state.key;
    b.classList.toggle('is-active-sort', on);
    if (on) b.dataset.dir = state.dir;
    else b.removeAttribute('data-dir');
  }});
}}
function setClusterSort(mode, level) {{
  level = level || 'sub';  // 舊頁面(沒 data-level)fallback 給 'sub'
  if (mode === _getSortKey(level)) {{
    _clusterSortDir[level] = _getSortDir(level) === 'desc' ? 'asc' : 'desc';
  }} else {{
    _clusterSort[level] = mode;
    _clusterSortDir[level] = 'desc';
  }}
  _refreshSortUi(level);
  _recalcClusters(level);
}}
function toggleUniv(ticker) {{
  if (_univDis.has(ticker)) _univDis.delete(ticker);
  else _univDis.add(ticker);
  document.querySelectorAll('.univ-chip[data-ticker="' + ticker + '"]').forEach(b => {{
    b.classList.toggle('disabled', _univDis.has(ticker));
  }});
  // 兩 sub-tab 的 cluster 都受影響(_univDis 是全域 state),都重算
  const C = window.IIA_CLUSTERS || {{}};
  ['hl_sub', 'pan_sub', 'sub'].forEach(lv => {{ if (C[lv]) _recalcClusters(lv); }});
  // 若 theme chart modal 開著,連動重算
  const dlg = document.getElementById('theme-chart-dialog');
  if (dlg && dlg.open && _openThemeCardId) {{
    _renderThemeChart(_openThemeCardId);
  }}
}}

function _recalcClusters(level) {{
  const container = document.getElementById('cluster-container-' + level);
  if (!container) return;
  const clusters = (window.IIA_CLUSTERS || {{}})[level] || [];
  if (!clusters.length) return;

  const cardEls = {{}};
  clusters.forEach(c => {{
    const el = document.getElementById(c.cardId);
    if (el) cardEls[c.cardId] = el;
  }});

  // F — record positions BEFORE
  const firsts = {{}};
  Object.entries(cardEls).forEach(([id, el]) => {{
    if (el.style.display !== 'none') firsts[id] = el.getBoundingClientRect();
  }});

  // 1. focal pill 反灰
  clusters.forEach(c => {{
    const el = cardEls[c.cardId];
    if (!el) return;
    el.querySelectorAll('[data-cluster-ticker]').forEach(pill => {{
      pill.classList.toggle('pill-disabled', _univDis.has(pill.dataset.clusterTicker));
    }});
  }});

  // 2. 重算每個 cluster 的 active 狀態 + 6 維 sort 值(PE 跟 Python 一致 skip ≤ 0)
  const _mean = (arr) => {{
    const xs = arr.filter(v => v != null);
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
  }};
  const states = clusters.map(c => {{
    const activeFocal = c.focal.filter(f => !_univDis.has(f.ticker));
    const disabledTv  = c.focal.reduce((s, f) => _univDis.has(f.ticker) ? s + f.tv : s, 0);
    return {{
      cardId: c.cardId,
      activeFocal,
      activeTv: c.baseTv - disabledTv,
      visible: activeFocal.length > 0,
      avgChg:   _mean(activeFocal.map(f => f.chg)),
      avgBias:  _mean(activeFocal.map(f => f.bias)),
      avgPe:    _mean(activeFocal.map(f => (f.pe != null && f.pe > 0) ? f.pe : null)),
    }};
  }});

  // 3. 卡片顯示 / 隱藏 + meta 更新(meta 依 _clusterSort 顯不同維度)
  // (2026-05-18 起殖利率/β 全站移除,META_FMT 只剩 tv / chg / bias / pe)
  const _fmtPct2 = (v) => v == null ? '—' : (v > 0 ? '+' : '') + v.toFixed(2) + '%';
  const _pctCls = (v) => v == null ? 'neutral' : (v > 0 ? 'up' : v < 0 ? 'down' : 'flat');
  const META_FMT = {{
    tv:    {{ label: '成交額',  val: (s) => (s.activeTv / 1e8).toFixed(0) + '億',         cls: (s) => 'neutral' }},
    chg:   {{ label: '平均漲跌', val: (s) => _fmtPct2(s.avgChg),                          cls: (s) => _pctCls(s.avgChg) }},
    bias:  {{ label: '平均乖離', val: (s) => _fmtPct2(s.avgBias),                         cls: (s) => _pctCls(s.avgBias) }},
    pe:    {{ label: '平均 PE',  val: (s) => s.avgPe == null ? '—' : s.avgPe.toFixed(1),  cls: (s) => 'neutral' }},
  }};
  const _sortKey = _getSortKey(level);
  const _sortDir = _getSortDir(level);
  const fmt = META_FMT[_sortKey] || META_FMT.tv;
  states.forEach(s => {{
    const el = cardEls[s.cardId];
    if (!el) return;
    if (!s.visible) {{ el.style.display = 'none'; return; }}
    el.style.display = '';
    const meta = el.querySelector('.cluster-meta');
    if (meta) {{
      const valStr = fmt.val(s);
      const showLabel = _sortKey !== 'tv';
      meta.innerHTML = s.activeFocal.length + ' 檔焦點 · '
        + (showLabel ? '<span class="meta-label">' + fmt.label + '</span> ' : '')
        + '<span class="meta-val ' + fmt.cls(s) + '">' + valStr + '</span>';
    }}
  }});

  // 4. 依 per-level _clusterSort 重排 DOM(None 排到最後,不受方向影響)
  const _key = (s) => {{
    if (_sortKey === 'chg')   return s.avgChg;
    if (_sortKey === 'bias')  return s.avgBias;
    if (_sortKey === 'pe')    return s.avgPe;
    return s.activeTv;  // 'tv' default
  }};
  const _dirMul = _sortDir === 'asc' ? -1 : 1;
  const visibleSorted = states.filter(s => s.visible).sort((a, b) => {{
    const va = _key(a), vb = _key(b);
    // null 永遠排尾段(無論 asc/desc),避免缺資料 cluster 卡在最前面
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    return (vb - va) * _dirMul;
  }});
  visibleSorted.forEach(s => {{
    const el = cardEls[s.cardId];
    if (el) container.appendChild(el);
  }});

  // L+I+P — FLIP
  const lasts = {{}};
  Object.entries(cardEls).forEach(([id, el]) => {{
    if (el.style.display !== 'none') lasts[id] = el.getBoundingClientRect();
  }});
  const animated = [];
  Object.keys(firsts).forEach(id => {{
    const el = cardEls[id];
    if (!el || !lasts[id]) return;
    const dy = firsts[id].top - lasts[id].top;
    if (Math.abs(dy) < 1) return;
    el.style.transition = 'none';
    el.style.transform = 'translateY(' + dy + 'px)';
    animated.push(el);
  }});
  if (animated.length) {{
    requestAnimationFrame(() => requestAnimationFrame(() => {{
      animated.forEach(el => {{
        el.style.transition = 'transform .38s cubic-bezier(.25,.46,.45,.94)';
        el.style.transform = '';
      }});
    }}));
  }}
}}

/* ── Theme chart modal — 6 個月 TV / 平均漲跌 趨勢 ────────────────────────── */
/* IIA_HISTORY / IIA_INDEX_HISTORY 不再 inline(~1 MB),改 fetch history.json,
 * 由 openThemeChart 首次點擊時觸發。後續同 session 一次就好。 */
let _historyLoadPromise = null;
function _loadHistory() {{
  if (window.IIA_HISTORY) return Promise.resolve();
  if (_historyLoadPromise) return _historyLoadPromise;
  _historyLoadPromise = fetch('history.json', {{ cache: 'no-cache' }})
    .then(r => {{ if (!r.ok) throw new Error('history.json ' + r.status); return r.json(); }})
    .then(data => {{
      window.IIA_HISTORY = data.history || {{}};
      window.IIA_INDEX_HISTORY = data.index || {{}};
      window.IIA_TICKER_CLOSE = data.ticker_close || {{}};  // Q13 per-ticker 400 天 close+shares
      // ticker_net_inst:per-ticker daily 法人淨買賣股數;hl_sub cluster
      // 也能拿到 net_inst(從 focal ticker 在「其他 main」row 內 backfill)
      const tni = data.ticker_net_inst || {{}};
      const tniIdx = {{}};
      for (const tk in tni) {{
        const m = {{}};
        (tni[tk] || []).forEach(p => {{ m[p.d] = p.n; }});
        tniIdx[tk] = m;
      }}
      window.IIA_TICKER_NET_INST = tniIdx;
    }})
    .catch(err => {{
      _historyLoadPromise = null;  // 失敗時可重試
      throw err;
    }});
  return _historyLoadPromise;
}}

let _lwcLoadPromise = null;
let _openThemeCardId = null;       // 目前打開的 cluster cardId(null = 關)
let _tcCharts = {{ net: null, price: null, netSeries: null,
                    clusterSeries: null, twiiSeries: null, tpexSeries: null }};
const _lineVis = {{ cluster: true, twii: true, tpex: true }};
// 時間粒度('1m'/'3m'/'6m'/'1y'/'all'),預設 6m,點 chip 切換
let _chartPeriod = '6m';
const _PERIOD_DAYS = {{ '1m': 30, '3m': 90, '6m': 180, '1y': 365 }};
// Modal 內 ticker disable set(每次 openThemeChart 都會清空,不影響外層 _univDis)
let _modalTickerDis = new Set();
// 三大法人 histogram 模式:'daily'=當日值、'cum'=累計
let _netMode = 'daily';

/* 給定 series([{{time:'YYYY-MM-DD',...}}, ...]),按 _chartPeriod 截尾段。
 * cutoff 用 series 最末天往回推(不是 today),避免週末/假期讓 1m 變空。
 * 'all' 或無 mapping 不過濾。 */
function _filterByPeriod(series) {{
  if (!series || !series.length || _chartPeriod === 'all') return series;
  const days = _PERIOD_DAYS[_chartPeriod];
  if (!days) return series;
  const lastTime = series[series.length - 1].time;
  const lastMs = new Date(lastTime + 'T00:00:00Z').getTime();
  const cutoffMs = lastMs - days * 86400000;
  const cutoff = new Date(cutoffMs).toISOString().slice(0, 10);
  return series.filter(p => p.time >= cutoff);
}}

function setChartPeriod(p) {{
  if (p === _chartPeriod) return;
  _chartPeriod = p;
  document.querySelectorAll('.tc-period-chip').forEach(b => {{
    b.classList.toggle('active', b.dataset.period === p);
  }});
  if (_openThemeCardId) _renderThemeChart(_openThemeCardId);
}}

function _loadLightweightCharts() {{
  if (window.LightweightCharts) return Promise.resolve();
  if (_lwcLoadPromise) return _lwcLoadPromise;
  _lwcLoadPromise = new Promise((resolve, reject) => {{
    const s = document.createElement('script');
    s.src = 'https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js';
    s.onload = () => resolve();
    s.onerror = (e) => {{ _lwcLoadPromise = null; reject(e); }};
    document.head.appendChild(s);
  }});
  return _lwcLoadPromise;
}}

function _findClusterDef(cardId) {{
  // 跨 sub-tab(hl_sub / pan_sub / sub legacy)找 cluster def
  const C = window.IIA_CLUSTERS || {{}};
  for (const lv of ['hl_sub', 'pan_sub', 'sub']) {{
    const hit = (C[lv] || []).find(c => c.cardId === cardId);
    if (hit) return hit;
  }}
  return null;
}}

/* 算單一 cluster 的 daily series:
 *   - netSeries:三大法人淨流入(億),用真實當日值,不 forward-fill
 *     (法人買賣超是日結 transaction,沒交易=0,不能用昨日延伸)
 *   - priceSeries:market-cap = Σ(close × shares_out) per day,
 *     **per-ticker forward-fill**(歷史上焦點股不一定每天都在 top-50,
 *     缺的日子用該檔上一次有資料的 close × shares 延續,標準加權指數做法)
 *     之後 _rebaseSeries 把它 rebase 到 100。
 * payload 5-tuple [tv, chg, close, net_inst, shares_out]
 * 鎖定今天的 cluster.focal ticker set,**同時套 _univDis(外層) + _modalTickerDis(modal 內)** 過濾。 */
function _computeClusterSeries(cluster) {{
  const hist = window.IIA_HISTORY || {{}};
  const tch  = window.IIA_TICKER_CLOSE || {{}};       // Q13:per-ticker 400 天 close+shares
  const tnet = window.IIA_TICKER_NET_INST || {{}};    // per-ticker daily net_inst(跨 main 索引)
  const keys = cluster.memberKeys || [];
  const todayFocals = [...new Set((cluster.focal || []).map(f => f.ticker))]
    .filter(t => !_univDis.has(t) && !_modalTickerDis.has(t));

  // 收集所有出現過的 dates(ticker_close ∪ ticker_net_inst ∪ theme_history)
  const dateSet = new Set();
  todayFocals.forEach(t => (tch[t] || []).forEach(p => dateSet.add(p.d)));
  todayFocals.forEach(t => Object.keys(tnet[t] || {{}}).forEach(d => dateSet.add(d)));
  keys.forEach(k => (hist[k] || []).forEach(row => dateSet.add(row.d)));
  const dates = [...dateSet].sort();
  if (!dates.length) return {{ netSeries: [], priceSeries: [] }};

  // 三個資料源:
  //   ticker_close[ticker] = [{{d, c, s}}, ...]  ← 400 天 close+shares,所有 focal 都有
  //   ticker_net_inst[ticker][date] = net_shares ← 跨 main 反向索引,hl_sub 也能拿
  //   hist[key].s[ticker] = [tv,chg,close,net,shares] ← 舊路徑當 fallback
  const raw = {{}};   // ticker -> {{date -> {{close, shares, net}}}}
  todayFocals.forEach(t => {{
    raw[t] = {{}};
    // 1) ticker_close 的 close+shares
    (tch[t] || []).forEach(p => {{
      raw[t][p.d] = {{ close: p.c, shares: p.s, net: null }};
    }});
    // 2) ticker_net_inst 的 net(per-ticker,跨 main 已合一)
    const tnetMap = tnet[t] || {{}};
    Object.entries(tnetMap).forEach(([d, n]) => {{
      const slot = raw[t][d] || (raw[t][d] = {{ close: null, shares: null, net: null }});
      slot.net = n;
    }});
    // 3) fallback 從 hist 補 close/shares/net(舊路徑;新路徑沒值的話)
    keys.forEach(k => {{
      (hist[k] || []).forEach(row => {{
        const v = (row.s || {{}})[t];
        if (!v) return;
        const slot = raw[t][row.d] || (raw[t][row.d] = {{ close: null, shares: null, net: null }});
        if (slot.close == null && v[2] != null) slot.close = v[2];
        if (slot.shares == null && v[4] != null) slot.shares = v[4];
        if (slot.net == null && v[3] != null) slot.net = v[3];
      }});
    }});
  }});

  // per-ticker forward-fill close/shares (net 不 fill,法人買賣超是 daily transaction)
  const filled = {{}};
  todayFocals.forEach(t => {{
    filled[t] = {{}};
    let lastClose = null, lastShares = null;
    dates.forEach(d => {{
      const day = raw[t][d];
      if (day && day.close != null) lastClose = day.close;
      if (day && day.shares != null) lastShares = day.shares;
      if (lastClose != null && lastShares != null) {{
        filled[t][d] = {{ close: lastClose, shares: lastShares }};
      }}
    }});
  }});

  // 合成 daily mcap (filled) + daily net (raw only)
  const netSeries = [];
  const priceSeries = [];
  dates.forEach(d => {{
    let mcap = 0, net = 0;
    todayFocals.forEach(t => {{
      const f = filled[t][d];
      if (f) mcap += f.close * f.shares;
      const r = raw[t][d];
      if (r && r.net != null) net += r.net;
    }});
    const netBn = net / 1e8;
    netSeries.push({{
      time: d, value: netBn,
      color: netBn >= 0 ? 'rgba(239,83,80,.8)' : 'rgba(38,166,154,.8)',
    }});
    if (mcap > 0) priceSeries.push({{ time: d, value: mcap }});
  }});
  return {{ netSeries, priceSeries }};
}}

/* rebase series to 100 at common start date,回傳 {{time, value}} list。
 * common start 取三條線的最晚開始日,確保起點對齊。
 * 若 series 為空 / 無 base 對應 → 回 [] */
function _rebaseSeries(series, startDate) {{
  if (!series || !series.length) return [];
  const base = series.find(p => p.time >= startDate);
  if (!base || !base.value) return [];
  return series
    .filter(p => p.time >= startDate)
    .map(p => ({{ time: p.time, value: +(p.value / base.value * 100).toFixed(2) }}));
}}

/* 從 IIA_INDEX_HISTORY 撈大盤 / 櫃買的 (time, close) series */
function _computeIndexSeries(key) {{
  const arr = (window.IIA_INDEX_HISTORY || {{}})[key] || [];
  return arr.map(p => ({{ time: p.d, value: p.close }}));
}}

function _disposeThemeCharts() {{
  ['net', 'price'].forEach(k => {{
    if (_tcCharts[k]) {{
      try {{ _tcCharts[k].remove(); }} catch (e) {{}}
      _tcCharts[k] = null;
    }}
  }});
  _tcCharts.netSeries = null;
  _tcCharts.clusterSeries = null;
  _tcCharts.twiiSeries = null;
  _tcCharts.tpexSeries = null;
}}

/* 把當日 netSeries 轉成滾動累計;color 依累計值正負重算 */
function _applyNetMode(series) {{
  if (_netMode !== 'cum' || !series.length) return series;
  let acc = 0;
  return series.map(p => {{
    acc += p.value;
    return {{
      time: p.time, value: +acc.toFixed(2),
      color: acc >= 0 ? 'rgba(239,83,80,.8)' : 'rgba(38,166,154,.8)',
    }};
  }});
}}

/* JS 版本的 fmt_pct(對齊 Python helpers.fmt_pct 行為,亞洲紅漲綠跌) */
function _fmtPctJs(v) {{
  if (v == null) return {{ str: '—', cls: 'neutral' }};
  if (v > 0)  return {{ str: '+' + v.toFixed(2) + '%', cls: 'up' }};
  if (v < 0)  return {{ str: v.toFixed(2) + '%', cls: 'down' }};
  return {{ str: '0.00%', cls: 'flat' }};
}}
/* HTML escape — modal chip 內 ticker / name 都會塞回 DOM,防注入 */
function _escHtml(s) {{
  s = String(s == null ? '' : s);
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}}

/* Modal 的 ticker chip 列表渲染。狀態 = _modalTickerDis ∪ _univDis(外層已 disable 的不顯示)。
 * 點擊 toggle modal-only disable,然後 re-render(setData 路徑,不 dispose)。
 * Chip 結構複用 .stk-pill 全站樣式(sp-ticker / mkt-badge / sp-name / sp-quote),
 * 加 .modal-tk-pill 給 cursor + disable 視覺 */
function _renderTickerChips(cluster) {{
  const box = document.getElementById('tc-ticker-chips');
  if (!box) return;
  // 左欄垂直列表依當日成交金額 desc 排序(常用焦點靠上,長尾擠下方)
  const focals = (cluster.focal || []).filter(f => !_univDis.has(f.ticker))
    .slice().sort((a, b) => (b.tv || 0) - (a.tv || 0));
  box.innerHTML = focals.map(f => {{
    const dis = _modalTickerDis.has(f.ticker) ? ' is-dis' : '';
    const pct = _fmtPctJs(f.chg);
    let quote;
    if (f.close != null) {{
      quote = f.close.toFixed(2) + (f.chg != null ? '(' + pct.str + ')' : '');
    }} else {{
      quote = pct.str;
    }}
    const nameHtml = f.n ? '<span class="sp-name">' + _escHtml(f.n) + '</span>' : '';
    const tk = _escHtml(f.ticker);
    // 不顯 mkt-badge(TW/US):modal 左欄空間有限,且全部都是同一 cluster 內的標的,
    // 市場類別由 cluster 上下文已表達,pill 內再標一次是 noise
    return '<div class="stk-pill modal-tk-pill' + dis + '" '
      + 'data-ticker="' + tk + '" '
      + 'onclick="toggleModalTicker(\\'' + tk + '\\')">'
      + '<span class="sp-ticker">' + tk + '</span>'
      + nameHtml
      + '<span class="sp-quote ' + pct.cls + '">' + _escHtml(quote) + '</span>'
      + '</div>';
  }}).join('');
}}

function toggleModalTicker(ticker) {{
  if (_modalTickerDis.has(ticker)) _modalTickerDis.delete(ticker);
  else _modalTickerDis.add(ticker);
  if (_openThemeCardId) _renderThemeChart(_openThemeCardId);
}}

function setNetMode(mode) {{
  if (mode === _netMode) return;
  _netMode = mode;
  document.querySelectorAll('.tc-mode-chip').forEach(b => {{
    b.classList.toggle('active', b.dataset.mode === mode);
  }});
  if (_openThemeCardId) _renderThemeChart(_openThemeCardId);
}}

/* 兩張 chart crosshair 同步:hover 在 A 時 B 也畫出垂直虛線。
 * 用 flag 防止 setCrosshairPosition 觸發對方 subscribeCrosshairMove
 * 造成 feedback loop。clearCrosshairPosition 也要對稱。 */
let _crosshairLock = false;
function _syncCrosshair(srcChart, dstChart, dstSeries) {{
  srcChart.subscribeCrosshairMove(param => {{
    if (_crosshairLock || !dstChart || !dstSeries) return;
    _crosshairLock = true;
    try {{
      if (param.time) {{
        // 找到 dst series 該時間點的值;沒對到就用 0(用來定位垂直線)
        const dstData = dstSeries.data ? dstSeries.data() : null;
        let dstVal = 0;
        if (Array.isArray(dstData)) {{
          const hit = dstData.find(p => p.time === param.time);
          if (hit) dstVal = hit.value;
        }}
        dstChart.setCrosshairPosition(dstVal, param.time, dstSeries);
      }} else {{
        dstChart.clearCrosshairPosition();
      }}
    }} finally {{ _crosshairLock = false; }}
  }});
}}

function _renderThemeChart(cardId) {{
  const cluster = _findClusterDef(cardId);
  if (!cluster) return;
  _renderTickerChips(cluster);
  document.getElementById('tc-title').textContent = '🔸 ' + cluster.name;
  let {{ netSeries, priceSeries }} = _computeClusterSeries(cluster);
  let twiiRaw = _computeIndexSeries('TWII');
  let tpexRaw = _computeIndexSeries('TPEX');
  // 按 _chartPeriod 截尾段(1M/3M/6M/1Y/ALL)
  netSeries = _filterByPeriod(netSeries);
  priceSeries = _filterByPeriod(priceSeries);
  twiiRaw = _filterByPeriod(twiiRaw);
  tpexRaw = _filterByPeriod(tpexRaw);
  // **關鍵**:四條線必須對齊到同一個 startDate,crosshair 垂直線才會在兩張
  // chart 的相同 X pixel(時間軸對應 pixel 一致)。否則 net 比 price 早幾天
  // 開始,X 軸 mapping 不同 → 同時間在兩圖不同位置 → 虛線錯位。
  const starts = [
    priceSeries[0]?.time, twiiRaw[0]?.time, tpexRaw[0]?.time, netSeries[0]?.time
  ].filter(Boolean).sort();
  const startDate = starts[starts.length - 1];
  netSeries = netSeries.filter(p => p.time >= startDate);
  // accumulator 在對齊後重算(累計起點要跟 startDate 一致才有意義)
  netSeries = _applyNetMode(netSeries);
  const empty = document.getElementById('tc-empty');
  const netEl = document.getElementById('tc-chart-net');
  const priceEl = document.getElementById('tc-chart-price');
  if (!netSeries.length) {{
    empty.style.display = '';
    netEl.style.display = 'none';
    priceEl.style.display = 'none';
    return;
  }}
  empty.style.display = 'none';
  netEl.style.display = '';
  priceEl.style.display = '';

  _disposeThemeCharts();
  const chartOpts = {{
    layout: {{
      background: {{ type: 'solid', color: 'transparent' }},
      textColor: '#7c8290',
      attributionLogo: false,
    }},
    grid: {{ vertLines: {{ color: 'rgba(255,255,255,.04)' }}, horzLines: {{ color: 'rgba(255,255,255,.04)' }} }},
    rightPriceScale: {{ borderColor: 'rgba(255,255,255,.08)' }},
    timeScale: {{ borderColor: 'rgba(255,255,255,.08)', timeVisible: false }},
    crosshair: {{ mode: 1 }},
    autoSize: true,
    handleScroll: {{ mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true }},
    handleScale: {{ mouseWheel: false, axisPressedMouseMove: true, pinch: true }},
  }};

  // Chart 1(上):焦點股加權指數 vs 大盤(rebase 100 from startDate)
  const clusterRebased = _rebaseSeries(priceSeries, startDate);
  const twiiRebased = _rebaseSeries(twiiRaw, startDate);
  const tpexRebased = _rebaseSeries(tpexRaw, startDate);
  _tcCharts.price = LightweightCharts.createChart(priceEl, chartOpts);
  const lineOpts = (color) => ({{
    color, lineWidth: 2,
    priceFormat: {{ type: 'custom', formatter: v => v.toFixed(1) }},
  }});
  _tcCharts.clusterSeries = _tcCharts.price.addLineSeries(lineOpts('#10b981'));
  _tcCharts.clusterSeries.setData(clusterRebased);
  _tcCharts.clusterSeries.applyOptions({{ visible: _lineVis.cluster }});
  _tcCharts.twiiSeries = _tcCharts.price.addLineSeries(lineOpts('#f59e0b'));
  _tcCharts.twiiSeries.setData(twiiRebased);
  _tcCharts.twiiSeries.applyOptions({{ visible: _lineVis.twii }});
  _tcCharts.tpexSeries = _tcCharts.price.addLineSeries(lineOpts('#94aef7'));
  _tcCharts.tpexSeries.setData(tpexRebased);
  _tcCharts.tpexSeries.applyOptions({{ visible: _lineVis.tpex }});

  // Chart 2(下):資金淨流入流出 histogram
  _tcCharts.net = LightweightCharts.createChart(netEl, chartOpts);
  const netSer = _tcCharts.net.addHistogramSeries({{
    priceFormat: {{ type: 'custom', formatter: v => (v >= 0 ? '+' : '') + v.toFixed(1) + '億' }},
    base: 0,
  }});
  netSer.setData(netSeries);
  _tcCharts.netSeries = netSer;

  _tcCharts.price.timeScale().fitContent();
  _tcCharts.net.timeScale().fitContent();

  // **關鍵 crosshair 對齊**:lightweight-charts 的 right priceScale 寬度依
  // 內容自動撐(net 的「+800.0億」比 price 的「190.0」寬幾 px),導致兩張
  // chart 的 plot area 左邊起點錯位 → 同一時間 T 落在不同 X pixel →
  // 兩條垂直虛線會差幾 px。修法:render 完後 measure 兩邊實際寬度,
  // 取 max 套 minimumWidth(設 min 比實際寬只會多撐不會 truncate),
  // 兩張 chart 的 right scale 就完全同寬,plot area 對齊。
  // 用 requestAnimationFrame 確保 DOM layout 完成才 measure。
  requestAnimationFrame(() => {{
    if (!_tcCharts.price || !_tcCharts.net) return;
    const pW = _tcCharts.price.priceScale('right').width();
    const nW = _tcCharts.net.priceScale('right').width();
    const maxW = Math.max(pW, nW);
    if (maxW > 0) {{
      _tcCharts.price.priceScale('right').applyOptions({{ minimumWidth: maxW }});
      _tcCharts.net.priceScale('right').applyOptions({{ minimumWidth: maxW }});
    }}
  }});

  // Time-range sync(不用 logical-range):時間語意更穩,即使兩 series 點數不同
  // 也能精準對齊;搭配上面 startDate 對齊,X 軸 pixel 一致
  let _syncBusy = false;
  const syncRange = (src, dst) => src.timeScale().subscribeVisibleTimeRangeChange(r => {{
    if (_syncBusy || !r || !dst) return;
    _syncBusy = true;
    try {{ dst.timeScale().setVisibleRange(r); }} finally {{ _syncBusy = false; }}
  }});
  syncRange(_tcCharts.price, _tcCharts.net);
  syncRange(_tcCharts.net, _tcCharts.price);

  // crosshair 兩張圖雙向同步(垂直虛線貫穿兩張)
  _syncCrosshair(_tcCharts.price, _tcCharts.net, _tcCharts.netSeries);
  _syncCrosshair(_tcCharts.net, _tcCharts.price, _tcCharts.clusterSeries);
}}

function toggleIndexLine(key) {{
  _lineVis[key] = !_lineVis[key];
  const seriesKey = key === 'cluster' ? 'clusterSeries' : key === 'twii' ? 'twiiSeries' : 'tpexSeries';
  if (_tcCharts[seriesKey]) {{
    _tcCharts[seriesKey].applyOptions({{ visible: _lineVis[key] }});
  }}
  const btn = document.querySelector('.tc-leg-chip.leg-' + key);
  if (btn) btn.classList.toggle('active', _lineVis[key]);
}}

function openThemeChart(cardId) {{
  _openThemeCardId = cardId;
  // Reset modal-only state(disable set + histogram mode 都不跨 cluster 持久化)
  _modalTickerDis = new Set();
  _netMode = 'daily';
  document.querySelectorAll('.tc-mode-chip').forEach(b => {{
    b.classList.toggle('active', b.dataset.mode === 'daily');
  }});
  const dlg = document.getElementById('theme-chart-dialog');
  if (!dlg) return;
  dlg.showModal();
  // 顯示 loading hint(首次 fetch history.json 可能要 ~1 秒)
  const tcEmpty = document.getElementById('tc-empty');
  if (!window.IIA_HISTORY) {{
    tcEmpty.textContent = '載入歷史資料中…';
    tcEmpty.style.display = '';
  }}
  Promise.all([_loadLightweightCharts(), _loadHistory()])
    .then(() => _renderThemeChart(cardId))
    .catch(err => {{
      console.error('Failed to load chart deps', err);
      tcEmpty.textContent = '圖表載入失敗';
      tcEmpty.style.display = '';
    }});
}}

// 關 dialog 時清理
(function () {{
  const dlg = document.getElementById('theme-chart-dialog');
  if (!dlg) return;
  dlg.addEventListener('close', () => {{
    _openThemeCardId = null;
    _disposeThemeCharts();
  }});
  // backdrop click 關閉(像 art-modal)
  dlg.addEventListener('click', (e) => {{
    const rect = dlg.getBoundingClientRect();
    if (e.clientX < rect.left || e.clientX > rect.right
        || e.clientY < rect.top || e.clientY > rect.bottom) {{
      dlg.close();
    }}
  }});
  // 防止 wheel 滾動穿透到外層頁面:只有 target 在左欄 ticker 列表內才放行
  // (chart 自有 wheel zoom 處理,padding/標題等空白處則 preventDefault)
  dlg.addEventListener('wheel', (e) => {{
    if (!e.target.closest('.tc-ticker-chips')) {{
      e.preventDefault();
    }}
  }}, {{passive: false}});
}})();

function toggleEl(id) {{
  const el = document.getElementById(id);
  if (!el) return;
  const nowHidden = el.classList.toggle('hidden');
  const arrow = document.getElementById('arrow-' + id);
  if (arrow) arrow.textContent = nowHidden ? '▶' : '▼';
}}

// Close modal on backdrop click
document.getElementById('art-modal').addEventListener('click', function(e) {{
  if (e.target === this) this.close();
}});

/* ── 分享報告 ─────────────────────────────────────────────────────────────── */
/* 桌機 → 對應社群 share URL 開新視窗;手機(支援 navigator.share)→ 原生 sheet。
 * 標題 + 描述從 <meta> 取,免再 hard-code。 */
function shareReport(target) {{
  const url = (document.querySelector('link[rel="canonical"]')?.href) || location.href;
  const title = (document.querySelector('meta[property="og:title"]')?.content) || document.title;
  const desc = (document.querySelector('meta[name="description"]')?.content) || '';
  const text = title + ' — ' + desc;
  const u = encodeURIComponent(url);
  const t = encodeURIComponent(text);
  if (target === 'native' && navigator.share) {{
    navigator.share({{ title, text: desc, url }}).catch(() => {{}});
    return;
  }}
  if (target === 'copy') {{
    if (navigator.clipboard) {{
      navigator.clipboard.writeText(url).then(() => _shareToast('已複製連結 ✓'),
                                              () => _shareToast('複製失敗,請手動複製'));
    }} else {{
      _shareToast(url);
    }}
    return;
  }}
  const links = {{
    line: 'https://social-plugins.line.me/lineit/share?url=' + u,
    x:    'https://twitter.com/intent/tweet?url=' + u + '&text=' + t,
    fb:   'https://www.facebook.com/sharer/sharer.php?u=' + u,
  }};
  if (links[target]) window.open(links[target], '_blank', 'noopener,width=600,height=540');
}}

function _shareToast(msg) {{
  let el = document.getElementById('share-toast');
  if (!el) {{
    el = document.createElement('div');
    el.id = 'share-toast';
    el.style.cssText = 'position:fixed;left:50%;bottom:1.5rem;transform:translateX(-50%);' +
      'background:rgba(15,17,23,.95);border:1px solid var(--accent);color:var(--text);' +
      'padding:.6rem 1.1rem;border-radius:8px;font-size:.78rem;font-weight:600;' +
      'z-index:9999;box-shadow:0 4px 18px rgba(0,0,0,.6);transition:opacity .25s';
    document.body.appendChild(el);
  }}
  el.textContent = msg;
  el.style.opacity = '1';
  setTimeout(() => {{ el.style.opacity = '0'; }}, 1800);
}}

// mobile: 顯示原生分享按鈕(opt-in 給支援 navigator.share 的環境)
if (navigator.share) {{
  document.querySelector('.share-native')?.removeAttribute('hidden');
}}

/* ── 站內搜尋 ─────────────────────────────────────────────────────────────── */
/* 從 IIA_CLUSTERS 全部 sub-tab(hl_sub / pan_sub / sub legacy)建反向索引
 * (ticker → cluster cardId + name)。預先建一次,後續每次按鍵 O(N) linear。 */
const _searchIdx = (() => {{
  const out = [];
  const seen = new Set();
  const C = window.IIA_CLUSTERS || {{}};
  ['hl_sub', 'pan_sub', 'sub'].flatMap(lv => C[lv] || []).forEach(c => {{
    (c.focal || []).forEach(f => {{
      if (seen.has(f.ticker)) return;
      seen.add(f.ticker);
      out.push({{ ticker: f.ticker, name: f.n || '', cardId: c.cardId, cluster: c.name }});
    }});
  }});
  return out;
}})();

let _searchKbIdx = -1;

function onSearchInput(q) {{
  const dd = document.getElementById('search-dropdown');
  q = (q || '').trim().toLowerCase();
  if (!q) {{ dd.hidden = true; return; }}
  // ticker / 公司名 / cluster 名(子產業)三軸搜尋。dedup by ticker,
  // 同 ticker 在多 cluster 只取第一個(scrollIntoView 跳哪都合理)。
  const hits = _searchIdx.filter(it =>
    it.ticker.toLowerCase().includes(q) ||
    (it.name    && it.name.toLowerCase().includes(q)) ||
    (it.cluster && it.cluster.toLowerCase().includes(q))
  ).slice(0, 12);
  if (!hits.length) {{
    dd.innerHTML = '<div class="search-empty">無相符結果(只搜尋熱門題材內的焦點股)</div>';
  }} else {{
    dd.innerHTML = hits.map((it, i) =>
      '<div class="search-item" data-i="' + i +
      '" data-ticker="' + it.ticker +
      '" data-card="' + it.cardId +
      '" onclick="onSearchPick(this)">' +
      '<span class="si-ticker">' + it.ticker + '</span>' +
      '<span class="si-name">' + it.name + '</span>' +
      '<span class="si-cluster">' + it.cluster + '</span>' +
      '</div>'
    ).join('');
  }}
  dd.hidden = false;
  _searchKbIdx = -1;
}}

function onSearchKey(e) {{
  const dd = document.getElementById('search-dropdown');
  if (e.key === 'Escape') {{
    dd.hidden = true;
    e.target.blur();
    return;
  }}
  const items = dd.querySelectorAll('.search-item');
  if (!items.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {{
    e.preventDefault();
    _searchKbIdx = e.key === 'ArrowDown'
      ? Math.min(_searchKbIdx + 1, items.length - 1)
      : Math.max(_searchKbIdx - 1, 0);
    items.forEach((it, i) => it.classList.toggle('kb-active', i === _searchKbIdx));
    items[_searchKbIdx]?.scrollIntoView({{ block: 'nearest' }});
    return;
  }}
  if (e.key === 'Enter') {{
    e.preventDefault();
    const target = _searchKbIdx >= 0 ? items[_searchKbIdx] : items[0];
    if (target) onSearchPick(target);
  }}
}}

function onSearchPick(el) {{
  const cardId = el.dataset.card;
  showTab('focus');
  // 切到 cluster 所在的 sub-tab(看 cardId 開頭判 hl_sub / pan_sub)
  const card = document.getElementById(cardId);
  if (card) {{
    const pane = card.closest('.sub-tab-pane');
    if (pane && pane.id) {{
      const stab = pane.id.replace(/^stab-/, '');
      if (stab) showSubTab(stab);
    }}
    setTimeout(() => {{
      card.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
      card.classList.remove('search-hi');
      void card.offsetWidth;  // restart animation
      card.classList.add('search-hi');
    }}, 80);
  }}
  document.getElementById('search-dropdown').hidden = true;
  document.getElementById('site-search').value = '';
}}

// 點 search-box 外面 → 收 dropdown
document.addEventListener('click', e => {{
  if (!e.target.closest('.search-box')) {{
    const dd = document.getElementById('search-dropdown');
    if (dd) dd.hidden = true;
  }}
}});

/* ── 動畫 <details> ─────────────────────────────────────────────────────────
 * 攔截 .anim-details summary click,跑 max-height + opacity transition。
 * 注意:transitionend 對每個 property 都會 fire,opacity (.22s) 早於
 * max-height (.28s),必須 filter propertyName === 'max-height' 才不會
 * 在 opacity 完成時誤清 inline maxHeight 導致 panel collapse。 */
function _animDetailsOpen(details) {{
  const panel = details.querySelector('.anim-panel');
  if (!panel) return;
  details.open = true;
  panel.style.maxHeight = '0px';
  panel.style.opacity = '0';
  void panel.offsetWidth;
  const targetH = panel.scrollHeight;
  panel.style.maxHeight = targetH + 'px';
  panel.style.opacity = '1';
  panel.addEventListener('transitionend', function te(e) {{
    if (e.propertyName !== 'max-height') return;
    panel.style.maxHeight = 'none';  // 完成後設 none,讓 [open] 規則接手
    panel.removeEventListener('transitionend', te);
  }});
}}
function _animDetailsClose(details) {{
  const panel = details.querySelector('.anim-panel');
  if (!panel) {{ details.open = false; return; }}
  panel.style.maxHeight = panel.scrollHeight + 'px';
  void panel.offsetWidth;
  panel.style.maxHeight = '0px';
  panel.style.opacity = '0';
  panel.addEventListener('transitionend', function te(e) {{
    if (e.propertyName !== 'max-height') return;
    details.open = false;
    panel.style.maxHeight = '';
    panel.style.opacity = '';
    panel.removeEventListener('transitionend', te);
  }});
}}
document.addEventListener('click', e => {{
  const summary = e.target.closest('summary');
  if (!summary) return;
  const details = summary.parentElement;
  if (!details || !details.classList.contains('anim-details')) return;
  e.preventDefault();
  if (details.open) _animDetailsClose(details);
  else _animDetailsOpen(details);
}});

/* 點 anim-details 外面 → 收起(避免 panel 一直浮在上面擋畫面) */
document.addEventListener('click', e => {{
  if (e.target.closest('.anim-details')) return;
  document.querySelectorAll('.anim-details[open]').forEach(d => _animDetailsClose(d));
}});

/* 前哨 inline toggle:button 在 focal-stocks div 內、panel 在 div 下方 sibling,
 * data-target 對應 panel id。max-height + opacity transition,跟 anim-details
 * 同 pattern 但不需要 <details>/<summary> 結構限制(讓 button 能 inline 在
 * 一排焦點 chip 之間)。 */
function toggleSentinelInline(btn) {{
  const panel = document.getElementById(btn.dataset.target);
  if (!panel) return;
  const isHidden = panel.hidden;
  if (isHidden) {{
    panel.hidden = false;
    panel.style.maxHeight = '0px';
    panel.style.opacity = '0';
    void panel.offsetWidth;
    panel.style.maxHeight = panel.scrollHeight + 'px';
    panel.style.opacity = '1';
    btn.classList.add('expanded');
    panel.addEventListener('transitionend', function te(e) {{
      if (e.propertyName !== 'max-height') return;
      panel.style.maxHeight = 'none';
      panel.removeEventListener('transitionend', te);
    }});
  }} else {{
    panel.style.maxHeight = panel.scrollHeight + 'px';
    void panel.offsetWidth;
    panel.style.maxHeight = '0px';
    panel.style.opacity = '0';
    btn.classList.remove('expanded');
    panel.addEventListener('transitionend', function te(e) {{
      if (e.propertyName !== 'max-height') return;
      panel.hidden = true;
      panel.style.maxHeight = '';
      panel.style.opacity = '';
      panel.removeEventListener('transitionend', te);
    }});
  }}
}}

/* ── 焦點排行 → CSV 下載 ──────────────────────────────────────────────────── */
/* 從現有 <table> DOM 萃取(避免重複資料);UTF-8 BOM 讓 Excel 開檔不亂碼。
 * 註:Python fstring 會 escape \\n / \\r,寫進 JS 必須雙反斜線。 */
function downloadRankCSV(tableId, baseName) {{
  const tbl = document.getElementById(tableId);
  if (!tbl) return;
  const esc = (s) => {{
    s = (s || '').replace(/\\s+/g, ' ').trim();
    return /[",\\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }};
  const rowsToCsv = (sel) => [...tbl.querySelectorAll(sel)].map(tr =>
    [...tr.children].map(td => esc(td.textContent)).join(',')
  );
  const lines = [...rowsToCsv('thead tr'), ...rowsToCsv('tbody tr')].filter(Boolean);
  if (!lines.length) return;
  const csv = '\\ufeff' + lines.join('\\r\\n');  // BOM for Excel
  const blob = new Blob([csv], {{ type: 'text/csv;charset=utf-8' }});
  const today = new Date().toISOString().slice(0, 10);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = baseName + '-' + today + '.csv';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {{ URL.revokeObjectURL(a.href); a.remove(); }}, 100);
}}
</script>
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
