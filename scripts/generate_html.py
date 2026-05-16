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

from src.analysis.focus_themes import detect_industry_clusters, IndustryCluster
from src.utils.config import RANKINGS_TOP_N

OUT_FILE = Path(__file__).resolve().parents[1] / "docs" / "index.html"

_ETF_TW_RE = re.compile(r'^00\d')

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
    return (
        f'<div class="stk-pill"{click}{extra}>'
        f'<span class="sp-ticker">{html_lib.escape(ticker)}</span>'
        f'<span class="mkt-badge {mkt_cls}">{market}</span>'
        f'{name_span}'
        f'<span class="sp-quote {pct_cls}">{quote}</span>'
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


def _yf_analyst_batch(tickers: list[str]) -> dict[str, dict]:
    """Concurrently fetch analyst consensus (target price + recommendation) via yfinance.
    Returns {ticker: {target_mean, target_median, target_high, target_low, n_analysts,
                       recommendation, currency}} for tickers that have data.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    try:
        import yfinance as yf
    except ImportError:
        return {}

    def _fetch_one(orig: str) -> tuple[str, dict | None]:
        core = orig.split(".")[0]
        yf_sym = (core + ".TW") if core.isdigit() else orig
        try:
            info = yf.Ticker(yf_sym).info
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
        futures = {ex.submit(_fetch_one, t): t for t in tickers}
        for fut in as_completed(futures):
            orig, data = fut.result()
            if data:
                result[orig] = data
    return result


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


def _yf_batch_fetch(entries: list[tuple[str, str]]) -> dict[str, dict]:
    """Sync: batch-fetch close / change% / trading value via yfinance.
    entries = [(ticker, market), ...]
    Returns {ticker: {"close": float|None, "change_pct": float|None, "trading_value": float|None}}
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}
    yf_map: dict[str, str] = {}  # yf_sym -> original_ticker
    for orig, market in entries:
        yf_sym = (orig + ".TW") if market == "TW" and not orig.upper().endswith(".TW") else orig
        yf_map[yf_sym] = orig
    result: dict[str, dict] = {}
    if not yf_map:
        return result
    try:
        syms = list(yf_map)
        raw = yf.download(syms, period="2d", progress=False, auto_adjust=True, group_by="ticker")
        if raw.empty:
            return result
        for yf_sym, orig in yf_map.items():
            try:
                close = (raw[yf_sym]["Close"] if len(syms) > 1 else raw["Close"]).dropna()
                vol   = (raw[yf_sym]["Volume"] if len(syms) > 1 else raw["Volume"]).dropna()
                entry: dict = {"close": None, "change_pct": None, "trading_value": None}
                if len(close) >= 1:
                    entry["close"] = float(close.iloc[-1])
                if len(close) >= 2:
                    entry["change_pct"] = round(
                        float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100), 2
                    )
                if len(close) >= 1 and len(vol) >= 1:
                    entry["trading_value"] = float(close.iloc[-1] * vol.iloc[-1])
                result[orig] = entry
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
        if market == "TW":
            extra = json.loads(r.get("extra") or "{}") if isinstance(r.get("extra"), str) else (r.get("extra") or {})
            b = extra.get("board", "TWSE")
            board = f'<span class="board-badge {b.lower()}">{b}</span>'
        rows.append(
            f'<tr><td class="rank">{r["rank"]}</td>'
            f'<td class="ticker">{html_lib.escape(r["ticker"])}</td>'
            f'<td class="name">{html_lib.escape((r["name"] or "")[:10])}{board}</td>'
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


def _industry_section_html(
    clusters: list[IndustryCluster],
    all_stocks: dict,
    level: str,
) -> str:
    """Render industry cluster cards. level = "main" | "sub".
    前哨觀察(watch)已從顯示移除(2026-05-16),只保留今日焦點。
    sub level:加廣泛概念股 panel(>3 個 cluster 出現的 ticker 可點擊濾除,
    觸發 FLIP 動畫重排 + TV 重算)。
    """
    if not clusters:
        label = "主產業" if level == "main" else "子產業"
        return f'<p class="muted-note">今日尚無{label}熱門產業</p>'

    # 廣泛概念股(sub-only):同 ticker 在 >3 個 merged/dedup'd sub-cluster
    # 出現 → 變成可濾除 chip
    universal: dict[str, str] = {}
    if level == "sub":
        from collections import Counter
        counts: Counter = Counter()
        for c in clusters:
            for s in c.focal:
                counts[s.ticker] += 1
        for t, n in counts.items():
            if n > 3:
                info = all_stocks.get(t, {})
                universal[t] = (info.get("name") or t)[:8]

    univ_html = ""
    if universal:
        chips = "".join(
            f'<button class="univ-chip" data-ticker="{html_lib.escape(t)}" type="button" '
            f"onclick=\"toggleUniv({json.dumps(t)})\">"
            f"{html_lib.escape(t)}&nbsp;{html_lib.escape(n)}</button>"
            for t, n in universal.items()
        )
        univ_html = (
            '<div class="univ-panel">'
            '<span class="univ-label">廣泛概念股(點擊濾除):</span>'
            f'{chips}'
            '</div>'
        )

    cards = []
    cluster_json: list[dict] = []
    for idx, c in enumerate(clusters):
        n_focal = len(c.focal)
        if n_focal >= 5:
            strength_cls, strength_lbl = "strength-high", "強勢"
        elif n_focal >= 3:
            strength_cls, strength_lbl = "strength-mid", "中型"
        else:
            strength_cls, strength_lbl = "strength-vol", "量能輪動"

        card_id = f"cc-{level}-{idx}"
        cluster_json.append({
            "cardId": card_id,
            "focal": [{"ticker": s.ticker, "tv": s.trading_value} for s in c.focal],
            "baseTv": c.trading_value,
        })
        focal_pills = "".join(
            _stk_pill(
                s.ticker, all_stocks,
                extra_attrs=f'data-cluster-ticker="{html_lib.escape(s.ticker)}" data-tv="{int(s.trading_value)}"',
            )
            for s in c.focal
        )

        tv_str = f"{c.trading_value/1e8:.0f}億" if c.trading_value > 0 else ""
        meta_text = f"{n_focal} 檔焦點" + (f" · {tv_str}" if tv_str else "")

        icon = "🔷" if level == "main" else "🔸"
        subtitle = (
            f'<div class="cluster-subtitle">屬於 {html_lib.escape(c.main)}</div>'
            if level == "sub" else ""
        )

        # Merged cluster name(focal 完全相同的子產業聚合) → 拆成 parts
        # 渲染,讓 CSS media query 控制 mobile/tablet 收合;否則純文字。
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
                f'<span class="cluster-name cn-merged" data-parts="{len(parts)}">'
                f'{icon} {"".join(parts_html_pieces)}'
                f'<button class="cn-toggle" type="button" '
                f'onclick="toggleClusterName(this)">+ ▾</button>'
                f'</span>'
            )
        else:
            name_html = f'<span class="cluster-name">{icon} {html_lib.escape(c.name)}</span>'

        cards.append(f"""
<div class="cluster-card" id="{card_id}">
  <div class="cluster-hdr">
    {name_html}
    <span class="cluster-strength {strength_cls}">{strength_lbl}</span>
    <span class="cluster-meta">{meta_text}</span>
  </div>
  {subtitle}
  <div class="cluster-focal-stocks">{focal_pills}</div>
</div>""")

    cluster_json_str = json.dumps(cluster_json, ensure_ascii=False, separators=(",", ":"))
    return (
        univ_html
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
) -> tuple[str, dict]:
    """Build the 熱門題材 tab — 只渲染子產業 ranked list。

    2026-05-16 改:移除主產業 sub-tab(資訊與子產業重疊),主產業仍由
    `detect_industry_clusters` 計算但不在公開站顯示。前哨觀察(watch)同步
    從卡片內移除(只剩今日焦點 focal pills)。

    `_merge_identical_focal` 已在 focus_themes 那邊套用 —— focal ticker
    set 完全相同的子產業會被合併成 "A & B & C: ...stocks"。

    Returns (html, modal_data) — modal_data 仍以 ticker 為 key,
    內容由下游 analyst consensus builder 填入。
    """
    all_stocks: dict[str, dict] = {}
    for r in tw_ranks:
        if _is_etf(r["ticker"], r.get("name", "")):
            continue
        extra = json.loads(r.get("extra") or "{}") if isinstance(r.get("extra"), str) else (r.get("extra") or {})
        all_stocks[r["ticker"]] = {
            "name": r["name"] or r["ticker"],
            "market": "TW",
            "board": extra.get("board", "TWSE"),
            "change_pct": float(r["change_pct"]) if r["change_pct"] is not None else None,
            "close_price": float(r["close_price"]) if r.get("close_price") is not None else None,
            "trading_value": float(r["trading_value"] or 0),
            "rank": r["rank"],
            "limit_up": bool(r.get("is_limit_up_30m")),
        }

    if not sub_clusters:
        return '<p class="muted-note">今日尚無熱門產業</p>', {}

    # Modal data placeholders — analyst consensus filled downstream
    modal_data: dict[str, str] = {}
    for ticker in {s.ticker for c in sub_clusters for s in c.focal}:
        modal_data[ticker] = ""

    sub_html = _industry_section_html(sub_clusters, all_stocks, "sub")
    return (
        '<div class="section-hdr">🎯 子產業排行</div>' + sub_html,
        modal_data,
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
        stocks_info[r["ticker"]] = {
            "name": r["name"] or r["ticker"],
            "market": "TW",
            "board": extra.get("board", "TWSE"),
            "change_pct": float(r["change_pct"]) if r["change_pct"] is not None else None,
            "close_price": float(r["close_price"]) if r.get("close_price") is not None else None,
            "trading_value": float(r["trading_value"] or 0),
            "rank": r["rank"],
            "limit_up": bool(r.get("is_limit_up_30m")),
        }
    stocks_info = {k: v for k, v in stocks_info.items() if not _is_etf(k, v.get("name", ""))}

    # Industry clustering — TW top-30 only (theme_dictionary 2026-05 改成
    # statementdog.com/taiex source 之後不再有美股)。產生主產業與子產業
    # 兩份 ranked list。
    tw_top_volume = {t: info for t, info in stocks_info.items() if info.get("market") == "TW"}
    _main_clusters, sub_clusters = detect_industry_clusters(tw_top_volume)
    # main_clusters 仍計算(供未來/ ingest backport 用),但公開站 2026-05-16 起
    # 不在 UI 顯示;前哨觀察(watch)同步從卡片移除 → 不再需要查 watch change_pct
    # 也不再 yfinance 補 watch close,純粹靠 stocks_info(top-N from SQL)。

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
                    _yf_needed.append((t, "TW" if _core.isdigit() else "US"))
    if _yf_needed:
        _yf_needed = list({t[0]: t for t in _yf_needed}.values())  # dedup by ticker
        yf_data = await asyncio.to_thread(_yf_batch_fetch, _yf_needed)
        # Patch stocks_info for market_notes tickers not in any ranking
        for ticker, market in _yf_needed:
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

    focus_html, modal_data = build_focus_html(tw_ranks, sub_clusters)
    notes_html  = build_notes_html(market_notes, podcast_rows, stocks_info)
    catalyst_html = build_catalyst_html(catalyst_events, stocks_info)

    # ── Analyst target prices: batch-fetch then inject into every modal ────────
    _all_modal_tickers: set[str] = set(modal_data.keys())
    if market_notes and market_notes.get("topics"):
        for _topic in market_notes["topics"]:
            _all_modal_tickers.update(_topic.get("tickers", []))
    if _all_modal_tickers:
        print(f"  Fetching analyst data for {len(_all_modal_tickers)} tickers…")
        _analyst = await asyncio.to_thread(_yf_analyst_batch, list(_all_modal_tickers))
    else:
        _analyst = {}

    # Modal: analyst consensus only. Article snippets removed in
    # repo-split Phase 3.6 — subscription text lives only in the private repo.
    for _tk in list(modal_data.keys()):
        modal_data[_tk] = _build_analyst_html(_analyst.get(_tk, {}))
    for _tk in _all_modal_tickers:
        if _tk not in modal_data:
            _a_html = _build_analyst_html(_analyst.get(_tk, {}))
            if _a_html:
                modal_data[_tk] = _a_html

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

    # Modal data JS (escaped JSON string values)
    modal_js_entries = ",\n".join(
        f'  {json.dumps(k)}: {json.dumps(v)}'
        for k, v in modal_data.items()
    )
    # ── Page HTML ─────────────────────────────────────────────────────────────
    page = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IIA 投資情報 {report_date}</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📈</text></svg>">
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
          border-radius:6px;font-size:.88rem;font-weight:500;transition:.15s}}
.tab-btn:hover{{background:rgba(255,255,255,.04);color:var(--text)}}
.tab-btn.active{{background:var(--accent);color:#fff}}
.tab-pane{{display:none}}
.tab-pane.active{{display:block}}

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
td.num{{text-align:right}}
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

/* ── Theme clusters ── */
.focus-clusters{{display:flex;flex-direction:column;gap:.85rem;margin-bottom:1.5rem}}
.cluster-card{{background:#12151f;border-radius:10px;padding:1rem 1.1rem;
               border-left:3px solid var(--accent);will-change:transform}}
.cluster-hdr{{display:flex;align-items:center;gap:.55rem;flex-wrap:wrap;margin-bottom:.7rem}}
.cluster-name{{font-size:.95rem;font-weight:700}}

/* Merged cluster name (focal 完全相同的子產業聚合) — mobile/tablet 收合 */
.cn-merged{{display:inline-flex;flex-wrap:wrap;align-items:baseline;gap:.1rem .25rem}}
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
.cluster-strength{{font-size:.65rem;font-weight:700;padding:.15rem .4rem;border-radius:4px}}
.strength-high{{background:#1a3a2a;color:#4caf82}}
.strength-mid{{background:#1e2235;color:var(--muted)}}
.strength-vol{{background:#2a2a1a;color:#c8a84b}}
.cluster-meta{{font-size:.72rem;color:var(--muted);margin-left:auto}}
.cluster-subtitle{{font-size:.7rem;color:var(--muted);margin:.1rem 0 .35rem;letter-spacing:.02em}}
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

.up{{color:var(--up)}} .down{{color:var(--down)}} .flat{{color:#fff}} .neutral{{color:var(--muted)}}
footer{{color:var(--muted);font-size:.75rem;
        padding:1.5rem 1rem;border-top:1px solid var(--border);margin-top:.5rem;
        line-height:1.6}}
footer .disclaimer{{max-width:760px;margin:0 auto .8rem;text-align:left}}
footer .disclaimer h3{{color:#a0b0cc;font-size:.78rem;font-weight:600;
                       margin:0 0 .35rem;letter-spacing:.04em}}
footer .meta{{text-align:center;padding-top:.6rem;border-top:1px dashed var(--border)}}
</style>
</head>
<body>

<!-- Ticker tape -->
<div class="tape">{tape_html}</div>

<header>
  <button class="brand" onclick="showTab('market');window.scrollTo(0,0);" title="回首頁">IIA 投資情報</button>
  <nav class="tabs">
    <button class="tab-btn active" data-tab="market" onclick="showTab('market')">市場行情</button>
    <button class="tab-btn"        data-tab="focus"  onclick="showTab('focus')">熱門題材</button>
    <button class="tab-btn"        data-tab="notes"  onclick="showTab('notes')">股市筆記</button>
  </nav>
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

  <!-- Tab 3: 股市筆記 -->
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

function showArtModal(ticker, name) {{
  const modal = document.getElementById('art-modal');
  document.getElementById('modal-title').textContent = ticker + ' ' + name;
  document.getElementById('modal-body').innerHTML = artModalData[ticker] || '<p style="color:#7a8ba0">尚無分析師或文章資料</p>';
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

function _initMergedNames() {{
  document.querySelectorAll('.cn-merged').forEach(_refreshClusterToggle);
}}
window.addEventListener('load', _initMergedNames);
window.addEventListener('resize', _initMergedNames);

/* 廣泛概念股濾除 — 點 univ-chip 把該 ticker 在每個 cluster 內反灰、
 * cluster meta 重算、整列依 activeTv 重排(FLIP 動畫) */
const _univDis = new Set();
function toggleUniv(ticker) {{
  if (_univDis.has(ticker)) _univDis.delete(ticker);
  else _univDis.add(ticker);
  document.querySelectorAll('.univ-chip[data-ticker="' + ticker + '"]').forEach(b => {{
    b.classList.toggle('disabled', _univDis.has(ticker));
  }});
  _recalcClusters('sub');
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

  // 2. 重算每個 cluster 的 active 狀態
  const states = clusters.map(c => {{
    const activeFocal = c.focal.filter(f => !_univDis.has(f.ticker));
    const disabledTv  = c.focal.reduce((s, f) => _univDis.has(f.ticker) ? s + f.tv : s, 0);
    return {{ cardId: c.cardId, activeFocal, activeTv: c.baseTv - disabledTv, visible: activeFocal.length > 0 }};
  }});

  // 3. 卡片顯示 / 隱藏 + meta 更新
  states.forEach(s => {{
    const el = cardEls[s.cardId];
    if (!el) return;
    if (!s.visible) {{ el.style.display = 'none'; return; }}
    el.style.display = '';
    const meta = el.querySelector('.cluster-meta');
    if (meta) {{
      const tvStr = (s.activeTv / 1e8).toFixed(0) + '億';
      meta.textContent = s.activeFocal.length + ' 檔焦點' + (s.activeTv > 0 ? ' · ' + tvStr : '');
    }}
  }});

  // 4. 依 activeTv 重排 DOM
  const visibleSorted = states.filter(s => s.visible).sort((a, b) => b.activeTv - a.activeTv);
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
</script>
</body>
</html>"""

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(page, encoding="utf-8")
    print(f"Generated {OUT_FILE}  ({len(page):,} bytes)")


if __name__ == "__main__":
    asyncio.run(generate())
