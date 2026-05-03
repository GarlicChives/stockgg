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
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg
from dotenv import load_dotenv

load_dotenv()

OUT_FILE = Path(__file__).resolve().parents[1] / "docs" / "index.html"

SOURCE_NAMES = {
    "macromicro":          "財經M平方",
    "vocus":               "韭菜王",
    "statementdog":        "財報狗",
    "investanchors":       "投資錨點",
    "pressplay":           "財經捕手",
    "podcast_gooaye":      "股癌 Gooaye",
    "podcast_macromicro":  "財經M平方",
    "podcast_chives_grad": "韭菜畢業班",
    "podcast_stock_barrel":"股海飯桶",
    "podcast_zhaohua":     "兆華與股惑仔",
}

PODCAST_SOURCES = [
    "podcast_gooaye",
    "podcast_macromicro",
    "podcast_chives_grad",
    "podcast_stock_barrel",
    "podcast_zhaohua",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt_pct(v) -> tuple[str, str]:
    if v is None:
        return "N/A", "neutral"
    css = "up" if v >= 0 else "down"
    return f"{'+' if v >= 0 else ''}{v:.2f}%", css


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
        # Structured refined notes — render headings and bullets properly
        text = html_lib.escape(content[:6000])
        text = re.sub(r'【(.+?)】', r'<h4>\1</h4>', text)
        text = re.sub(r'^(\d+\.) ', r'<br>\1 ', text, flags=re.MULTILINE)
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


# ── Ranking rows HTML ─────────────────────────────────────────────────────────

def rank_rows_html(ranks, market: str) -> str:
    rows = []
    for r in ranks:
        chg = float(r["change_pct"]) if r["change_pct"] is not None else None
        pct, css = fmt_pct(chg)
        if market == "US":
            val = f"${float(r['trading_value'] or 0)/1e9:.1f}B"
        else:
            val = f"{float(r['trading_value'] or 0)/1e8:.0f}億"
            if r.get("is_limit_up_30m"):
                val += " ⬆"
        board = ""
        if market == "TW":
            extra = json.loads(r.get("extra") or "{}") if isinstance(r.get("extra"), str) else (r.get("extra") or {})
            b = extra.get("board", "TWSE")
            board = f'<span class="board-badge {b.lower()}">{b}</span>'
        rows.append(
            f'<tr><td class="rank">{r["rank"]}</td>'
            f'<td class="ticker">{html_lib.escape(r["ticker"])}</td>'
            f'<td class="name">{html_lib.escape((r["name"] or "")[:10])}{board}</td>'
            f'<td class="num">{val}</td>'
            f'<td class="num {css}">{pct}</td></tr>'
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
    """Build stock cards for a market. Returns (html, modal_data dict)."""
    modal_data: dict[str, str] = {}
    cards = []
    for ticker, info in ticker_list:
        arts = ticker_arts.get(ticker, [])
        if not arts:
            continue  # only show stocks with article coverage
        chg = info["change_pct"]
        pct_str, pct_cls = fmt_pct(chg)
        mkt_badge = f'<span class="mkt-badge mkt-{market.lower()}">{market}</span>'
        vol_html = _vol_label(info["rank"])
        art_count = len(arts)

        if market == "US":
            val_str = f"${info['trading_value']/1e9:.1f}B"
        else:
            val_str = f"{info['trading_value']/1e8:.0f}億"
        board_badge = ""
        if market == "TW":
            board = info.get("board", "TWSE")
            board_badge = f'<span class="board-sm {board.lower()}">{board}</span>'
        limit_badge = '<span class="limit-up-badge">漲停⬆</span>' if info.get("limit_up") else ""

        # Build modal HTML for this ticker
        modal_html_parts = []
        for a in arts[:5]:
            src_name = html_lib.escape(SOURCE_NAMES.get(a["source"] or "", a["source"] or ""))
            dt = str(a["published_at"])[:10] if a["published_at"] else "?"
            title = html_lib.escape((a["title"] or "")[:70])
            relevant = extract_relevant_para(
                a.get("full_content") or "",
                ticker,
                info["name"],
            )
            snippet_html = f'<div class="modal-snip">{html_lib.escape(relevant)}</div>' if relevant else ""
            modal_html_parts.append(
                f'<div class="modal-art">'
                f'<div class="modal-art-meta">📰 {dt} · {src_name}</div>'
                f'<div class="modal-art-title">{title}</div>'
                f'{snippet_html}'
                f'</div>'
            )
        modal_data[ticker] = ''.join(modal_html_parts)

        safe_ticker = re.sub(r'[^A-Za-z0-9]', '_', ticker)
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
  <div class="sc-arts-hint">📰 {art_count} 篇相關文章</div>
</div>""")
    return ''.join(cards), modal_data


def build_focus_html(us_ranks: list, tw_ranks: list,
                     ticker_arts: dict, market_notes: dict | None) -> tuple[str, dict]:
    """Build the 焦點股 tab content. Returns (html, modal_data)."""
    stocks: dict[str, dict] = {}
    for r in us_ranks:
        stocks[r["ticker"]] = {
            "name": r["name"] or r["ticker"],
            "market": "US",
            "change_pct": float(r["change_pct"]) if r["change_pct"] is not None else None,
            "trading_value": float(r["trading_value"] or 0),
            "rank": r["rank"],
            "limit_up": False,
        }
    for r in tw_ranks:
        extra = json.loads(r.get("extra") or "{}") if isinstance(r.get("extra"), str) else (r.get("extra") or {})
        stocks[r["ticker"]] = {
            "name": r["name"] or r["ticker"],
            "market": "TW",
            "board": extra.get("board", "TWSE"),
            "change_pct": float(r["change_pct"]) if r["change_pct"] is not None else None,
            "trading_value": float(r["trading_value"] or 0),
            "rank": r["rank"],
            "limit_up": bool(r.get("is_limit_up_30m")),
        }

    tw_list = [(t, i) for t, i in stocks.items() if i["market"] == "TW"]
    us_list = [(t, i) for t, i in stocks.items() if i["market"] == "US"]
    # Sort: stocks with more articles first, then by trading value
    tw_list.sort(key=lambda x: (-len(ticker_arts.get(x[0], [])), -x[1]["trading_value"]))
    us_list.sort(key=lambda x: (-len(ticker_arts.get(x[0], [])), -x[1]["trading_value"]))

    all_modal_data: dict[str, str] = {}
    sections: list[str] = []

    # ── AI cross-source themes ────────────────────────────────────────────────
    if market_notes and market_notes.get("topics"):
        cards = []
        for topic in market_notes["topics"]:
            t_name = html_lib.escape(topic.get("topic", ""))
            sentiment = topic.get("sentiment", "中立")
            sent_cls = "sent-bull" if "偏多" in sentiment else ("sent-bear" if "偏空" in sentiment else "sent-neu")
            sources_str = " × ".join(html_lib.escape(s) for s in topic.get("sources", []))
            summary = html_lib.escape(topic.get("summary", ""))
            key_points = topic.get("key_points", [])
            kp_html = "".join(f'<li>{html_lib.escape(p)}</li>' for p in key_points[:4])
            t_chips = []
            for tk_raw in topic.get("tickers", []):
                m = re.search(r'[（(]([A-Z0-9]+)[）)]', tk_raw)
                code = m.group(1) if m else ""
                display = html_lib.escape(tk_raw)
                in_today = code in stocks
                chip_cls = "focus-chip-match" if in_today else "focus-chip"
                t_chips.append(f'<span class="{chip_cls}">{display}</span>')
            art_items = []
            for art in topic.get("articles", [])[:3]:
                src = html_lib.escape(art.get("source", ""))
                title = html_lib.escape(art.get("title", "")[:50])
                dt = art.get("date", "")
                art_items.append(f'<div class="art-ref">📰 [{dt} {src}] {title}</div>')
            cards.append(f"""
<div class="focus-theme">
  <div class="theme-top">
    <span class="theme-ttl">{t_name}</span>
    <span class="sent-badge {sent_cls}">{html_lib.escape(sentiment)}</span>
    <span class="src-note">{sources_str}</span>
  </div>
  {f'<p class="theme-summary">{summary}</p>' if summary else ''}
  {f'<ul class="kp-list">{kp_html}</ul>' if kp_html else ''}
  <div class="focus-chips">{''.join(t_chips)}</div>
  {'<div class="theme-arts">' + ''.join(art_items) + '</div>' if art_items else ''}
</div>""")
        if cards:
            sections.append(
                '<div class="section-hdr">📌 跨來源共同議題</div>'
                '<div class="focus-themes">' + ''.join(cards) + '</div>'
            )

    # ── Sub-tabs: 台股 / 美股 ─────────────────────────────────────────────────
    tw_cards_html, tw_modal = _build_stock_cards(tw_list, ticker_arts, "TW")
    us_cards_html, us_modal = _build_stock_cards(us_list, ticker_arts, "US")
    all_modal_data.update(tw_modal)
    all_modal_data.update(us_modal)

    tw_count = tw_cards_html.count('class="stock-card"')
    us_count = us_cards_html.count('class="stock-card"')

    sections.append(f"""
<div class="section-hdr">📊 焦點股（有爬蟲覆蓋）</div>
<div class="sub-tabs">
  <button class="sub-tab-btn active" data-stab="tw" onclick="showSubTab('tw')">台股 ({tw_count})</button>
  <button class="sub-tab-btn" data-stab="us" onclick="showSubTab('us')">美股 ({us_count})</button>
</div>
<div id="stab-tw" class="sub-tab-pane active">
  {('<div class="stock-grid">' + tw_cards_html + '</div>') if tw_cards_html else '<p class="muted-note">尚無符合條件的台股</p>'}
</div>
<div id="stab-us" class="sub-tab-pane">
  {('<div class="stock-grid">' + us_cards_html + '</div>') if us_cards_html else '<p class="muted-note">尚無符合條件的美股</p>'}
</div>""")

    return '\n'.join(sections), all_modal_data


# ── 股市筆記 tab ──────────────────────────────────────────────────────────────

def build_notes_html(market_notes: dict | None, podcast_rows: list) -> str:
    parts = []

    if market_notes and market_notes.get("topics"):
        topic_cards = []
        for topic in market_notes["topics"]:
            t_name = html_lib.escape(topic.get("topic", ""))
            sentiment = topic.get("sentiment", "中立")
            sent_cls = "sent-bull" if "偏多" in sentiment else ("sent-bear" if "偏空" in sentiment else "sent-neu")
            sources = topic.get("sources", [])
            src_tags = "".join(f'<span class="src-tag">{html_lib.escape(s)}</span>' for s in sources)
            summary = html_lib.escape(topic.get("summary", ""))
            key_points = topic.get("key_points", [])
            kp_html = "".join(f'<li>{html_lib.escape(p)}</li>' for p in key_points[:5])
            tickers = topic.get("tickers", [])
            tk_html = "".join(f'<span class="tk-chip">{html_lib.escape(t)}</span>' for t in tickers)
            art_refs = "".join(
                f'<div class="art-ref">📰 [{a.get("date","?")} {html_lib.escape(a.get("source",""))}] '
                f'{html_lib.escape(a.get("title","")[:60])}</div>'
                for a in topic.get("articles", [])[:4]
            )
            topic_cards.append(f"""
<div class="topic-card">
  <div class="topic-head">
    <span class="topic-name">{t_name}</span>
    <span class="sent-badge {sent_cls}">{html_lib.escape(sentiment)}</span>
  </div>
  <div class="src-row">{src_tags}</div>
  {f'<p class="topic-sum">{summary}</p>' if summary else ''}
  {f'<ul class="kp-list">{kp_html}</ul>' if kp_html else ''}
  {f'<div class="tk-row">{tk_html}</div>' if tk_html else ''}
  {f'<div class="topic-arts">{art_refs}</div>' if art_refs else ''}
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

    parts.append('<div class="section-hdr" style="margin-top:1.5rem">🎙 Podcast 筆記</div>')

    pods: dict[str, list] = collections.defaultdict(list)
    for row in podcast_rows:
        pods[row["source"]].append(row)

    for src_key in PODCAST_SOURCES:
        eps = pods.get(src_key, [])
        src_name = SOURCE_NAMES.get(src_key, src_key)
        ep_count = len(eps)
        safe_key = src_key.replace("_", "-")

        ep_html_parts = []
        for i, ep in enumerate(eps[:3]):
            ep_id = f"{safe_key}-{i}"
            dt = str(ep["published_at"])[:10] if ep["published_at"] else "?"
            title = html_lib.escape(ep["title"] or "（無標題）")
            is_refined = bool(ep.get("has_refined"))
            content = ep.get("content") or ""
            content_html = podcast_content_to_html(content, is_refined=is_refined)
            ep_html_parts.append(f"""
<div class="ep-block">
  <div class="ep-hdr" onclick="toggleEl('{ep_id}')">
    <span class="ep-arrow" id="arrow-{ep_id}">▶</span>
    <span class="ep-title">{title}</span>
    <span class="ep-date">{dt}</span>
  </div>
  <div id="{ep_id}" class="ep-body hidden">
    {content_html}
  </div>
</div>""")

        ep_html = ''.join(ep_html_parts) if ep_html_parts else '<p class="muted-note">尚無資料</p>'
        parts.append(f"""
<div class="pod-source">
  <div class="pod-src-hdr" onclick="toggleEl('pod-{safe_key}')">
    <span class="pod-src-arrow" id="arrow-pod-{safe_key}">▶</span>
    <span class="pod-src-name">{html_lib.escape(src_name)}</span>
    {f'<span class="ep-cnt">{ep_count} 集</span>' if ep_count else ''}
  </div>
  <div id="pod-{safe_key}" class="pod-episodes hidden">
    {ep_html}
  </div>
</div>""")

    return '\n'.join(parts)


# ── Main generate ─────────────────────────────────────────────────────────────

async def generate():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

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
        name = json.loads(row["extra"] or "{}").get("name", row["symbol"])
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
            "SELECT rank, ticker, name, trading_value, change_pct, extra "
            "FROM trading_rankings WHERE rank_date=$1 AND market='US' ORDER BY rank LIMIT 30",
            us_rank_date,
        )]
    if tw_rank_date:
        tw_ranks = [dict(r) for r in await conn.fetch(
            "SELECT rank, ticker, name, trading_value, change_pct, is_limit_up_30m, extra "
            "FROM trading_rankings WHERE rank_date=$1 AND market='TW' ORDER BY rank LIMIT 30",
            tw_rank_date,
        )]

    # Article matching — fetch full content for relevant paragraph extraction
    all_tickers = [r["ticker"] for r in us_ranks + tw_ranks]
    all_tickers_set = set(all_tickers)
    ticker_arts: dict[str, list] = collections.defaultdict(list)
    if all_tickers:
        art_rows = await conn.fetch("""
            SELECT id, source, title, published_at, tickers,
                   COALESCE(refined_content, content) AS full_content
            FROM articles
            WHERE tickers && $1::text[]
              AND published_at >= NOW() - INTERVAL '60 days'
              AND status = 'active'
            ORDER BY published_at DESC
            LIMIT 400
        """, all_tickers)

        # Assign article to ticker only if the ticker is a PRIMARY subject:
        # (a) ticker appears in article title, OR (b) ticker is the first extracted ticker.
        # Secondary cross-mentions (e.g. VRT article mentioning NOK as a customer) are excluded.
        for row in art_rows:
            art_tickers = row["tickers"] or []
            title_upper = (row.get("title") or "").upper()
            first_ticker_assigned = False
            for i, t in enumerate(art_tickers):
                if t not in all_tickers_set:
                    continue
                in_title = t in title_upper
                is_primary = (i == 0 and not first_ticker_assigned)
                if in_title or is_primary:
                    ticker_arts[t].append(dict(row))
                    if is_primary:
                        first_ticker_assigned = True

        # Deduplicate by article id per ticker
        for ticker in ticker_arts:
            seen_ids: set = set()
            deduped = []
            for art in ticker_arts[ticker]:
                aid = art.get("id")
                if aid not in seen_ids:
                    seen_ids.add(aid)
                    deduped.append(art)
            ticker_arts[ticker] = deduped

    # Podcast notes (last 3 episodes per source)
    # Use refined_content if available (structured notes), else short raw preview
    podcast_rows = []
    for src in PODCAST_SOURCES:
        rows = await conn.fetch(
            """SELECT source, title, published_at, content, has_refined
               FROM (
                 SELECT DISTINCT ON (title)
                        source, title, published_at,
                        COALESCE(refined_content, LEFT(content, 900)) as content,
                        (refined_content IS NOT NULL) as has_refined
                 FROM articles
                 WHERE source=$1 AND status='active'
                 ORDER BY title, published_at DESC NULLS LAST
               ) deduped
               ORDER BY published_at DESC NULLS LAST
               LIMIT 3""",
            src,
        )
        podcast_rows.extend(dict(r) for r in rows)

    await conn.close()

    raw_report   = (report["raw_response"] or "") if report else ""
    report_date  = report["report_date"].strftime("%Y/%m/%d") if report else "—"
    market_notes = None
    if report and report["market_notes_json"]:
        mn = report["market_notes_json"]
        market_notes = mn if isinstance(mn, dict) else json.loads(mn)

    directions  = parse_directions(raw_report)
    report_html = md_to_html(raw_report)
    updated_at  = datetime.now(timezone.utc).strftime("%m/%d %H:%M UTC")
    focus_html, modal_data = build_focus_html(us_ranks, tw_ranks, ticker_arts, market_notes)
    notes_html  = build_notes_html(market_notes, podcast_rows)

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
        ("VIX",         "^VIX",     False),
        ("10Y 殖利率",  "^TNX",     False),
        ("DXY",         "DX-Y.NYB", True),
        ("恐慌貪婪",    "FEAR_GREED", False),
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
<style>
:root {{
  --bg:#0f1117; --card:#1a1d26; --border:#2a2e40;
  --text:#e2e8f0; --muted:#7a8ba0;
  --up:#26a69a; --down:#ef5350; --accent:#6c8ef5;
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
       height:30px;overflow:hidden;display:flex;align-items:center;
       white-space:nowrap}}
.tape-track{{display:inline-block;
             animation:tape-scroll 90s linear infinite;
             will-change:transform}}
.tape-item{{display:inline-block;padding:0 .5rem;font-size:.8rem;color:var(--muted)}}
.tape-item b{{color:var(--text)}}
.tape-up{{color:var(--up)}} .tape-down{{color:var(--down)}}
@keyframes tape-scroll{{0%{{transform:translateX(0)}}100%{{transform:translateX(-50%)}}}}


/* ── Header ── */
header{{background:var(--card);border-bottom:1px solid var(--border);
        padding:.55rem 1.5rem}}
header h1{{font-size:1rem;font-weight:700;color:var(--accent)}}

/* ── Tabs ── */
.wrap{{max-width:1120px;margin:0 auto;padding:1.25rem 1.1rem}}
.tabs{{display:flex;gap:.4rem;margin-bottom:1.1rem;
       border-bottom:1px solid var(--border);padding-bottom:.55rem}}
.tab-btn{{background:transparent;color:var(--muted);padding:.42rem .9rem;
          border-radius:6px;font-size:.88rem;font-weight:500;transition:.15s}}
.tab-btn:hover{{background:var(--card);color:var(--text)}}
.tab-btn.active{{background:var(--accent);color:#fff}}
.tab-pane{{display:none}}
.tab-pane.active{{display:block}}

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
.sent-bull{{background:#1a3a2a;color:#4caf82}}
.sent-bear{{background:#2a1a1a;color:#b05050}}
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
.art-ref{{color:var(--muted);font-size:.75rem;margin:.2rem 0}}

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

/* ── Cross-source topics (tab 3) ── */
.topics-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));
              gap:.85rem;margin-bottom:1.25rem}}
.topic-card{{background:#12151f;border-radius:10px;padding:1rem 1.1rem;
             border-left:3px solid var(--up)}}
.topic-head{{display:flex;align-items:center;gap:.5rem;margin-bottom:.4rem;flex-wrap:wrap}}
.topic-name{{font-size:.9rem;font-weight:700}}
.src-row{{display:flex;gap:.3rem;flex-wrap:wrap;margin-bottom:.4rem}}
.src-tag{{font-size:.65rem;padding:.15rem .4rem;background:#1e2235;
          border-radius:4px;color:var(--muted)}}
.topic-sum{{font-size:.84rem;color:#b0bfcf;margin:.35rem 0}}
.tk-row{{display:flex;flex-wrap:wrap;gap:.3rem;margin:.35rem 0}}
.tk-chip{{font-size:.75rem;font-weight:600;padding:.15rem .4rem;
          background:#1e2235;border-radius:4px}}
.topic-arts{{margin-top:.4rem;font-size:.75rem}}

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
.pod-notes{{font-size:.82rem;color:#b0bfcf;line-height:1.75;white-space:pre-wrap;overflow-wrap:break-word}}
.pod-notes h4{{color:var(--accent);font-size:.82rem;font-weight:700;
               margin:.7rem 0 .2rem;border-bottom:1px solid var(--border);padding-bottom:.2rem}}
.muted-note{{color:var(--muted);font-size:.85rem;padding:.5rem 0}}

.up{{color:var(--up)}} .down{{color:var(--down)}} .neutral{{color:var(--muted)}}
footer{{text-align:center;color:var(--muted);font-size:.75rem;
        padding:1.5rem 1rem;border-top:1px solid var(--border);margin-top:.5rem}}
</style>
</head>
<body>

<!-- Ticker tape -->
<div class="tape">{tape_html}</div>

<header>
  <h1>IIA 投資情報</h1>
</header>

<div class="wrap">
  <nav class="tabs">
    <button class="tab-btn active" data-tab="market" onclick="showTab('market')">市場行情</button>
    <button class="tab-btn"        data-tab="focus"  onclick="showTab('focus')">焦點股</button>
    <button class="tab-btn"        data-tab="notes"  onclick="showTab('notes')">股市筆記</button>
  </nav>

  <!-- Tab 1: 市場行情 -->
  <div id="tab-market" class="tab-pane active">
    <div class="card">
      <div class="sec">每日分析報告（{report_date}）</div>
      <div class="report">{report_html or '<p style="color:var(--muted)">今日報告尚未生成</p>'}</div>
    </div>
    <div class="ranks">
      <div class="card">
        <div class="sec">美股 成交值前 30（{str(us_rank_date) if us_rank_date else '—'}）</div>
        <table>
          <thead><tr><th>#</th><th>代號</th><th>名稱</th>
            <th style="text-align:right">成交值</th>
            <th style="text-align:right">漲跌</th></tr></thead>
          <tbody>{rank_rows_html(us_ranks, 'US')}</tbody>
        </table>
      </div>
      <div class="card">
        <div class="sec">台股 成交值前 30（上市+上櫃）（{str(tw_rank_date) if tw_rank_date else '—'}）</div>
        <table>
          <thead><tr><th>#</th><th>代號</th><th>名稱</th>
            <th style="text-align:right">成交值</th>
            <th style="text-align:right">漲跌</th></tr></thead>
          <tbody>{rank_rows_html(tw_ranks, 'TW')}</tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Tab 2: 焦點股 -->
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

<footer>IIA Investment Intelligence Analyst &nbsp;·&nbsp; 資料僅供參考，不構成投資建議</footer>

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
  document.getElementById('modal-title').textContent = ticker + ' ' + name + ' — 相關文章';
  document.getElementById('modal-body').innerHTML = artModalData[ticker] || '<p style="color:#7a8ba0">無相關文章資料</p>';
  modal.showModal();
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
