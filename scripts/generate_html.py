#!/usr/bin/env python3
"""Generate docs/index.html from latest DB data for GitHub Pages."""
import asyncio
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


def fmt_pct(v) -> tuple[str, str]:
    if v is None:
        return "N/A", "neutral"
    css = "up" if v >= 0 else "down"
    return f"{'+' if v >= 0 else ''}{v:.2f}%", css


def md_to_html(text: str) -> str:
    """Convert the Gemini report markdown to HTML."""
    text = html_lib.escape(text)
    # Headers
    text = re.sub(r'^### (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Bullet lists — group consecutive list items into <ul>
    def wrap_list(m):
        items = re.sub(r'^[\*\-]\s+(.+)$', r'<li>\1</li>', m.group(0), flags=re.MULTILINE)
        return f'<ul>{items}</ul>'
    text = re.sub(r'(?m)(^[\*\-] .+\n?)+', wrap_list, text)
    # Paragraphs
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


async def generate():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    # Latest report
    report = await conn.fetchrow(
        "SELECT report_date, raw_response FROM analysis_reports ORDER BY report_date DESC LIMIT 1"
    )

    # Market snapshots — each symbol uses its own latest non-null trading day
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
            "close": float(row["close_price"] or 0),
            "chg": float(row["change_pct"]) if row["change_pct"] is not None else None,
        }
        snap_dates[row["symbol"]] = row["snapshot_date"]

    # Use the latest date of the key US index as the section header date
    snap_date = snap_dates.get("^GSPC") or snap_dates.get("^IXIC") or (
        max(snap_dates.values()) if snap_dates else None
    )

    # Rankings — US and TW may have different latest dates
    us_rank_date = await conn.fetchval(
        "SELECT MAX(rank_date) FROM trading_rankings WHERE market='US'"
    )
    tw_rank_date = await conn.fetchval(
        "SELECT MAX(rank_date) FROM trading_rankings WHERE market='TW'"
    )
    us_ranks, tw_ranks = [], []
    if us_rank_date:
        us_ranks = [dict(r) for r in await conn.fetch(
            "SELECT rank, ticker, name, trading_value, change_pct "
            "FROM trading_rankings WHERE rank_date=$1 AND market='US' ORDER BY rank LIMIT 30",
            us_rank_date,
        )]
    if tw_rank_date:
        tw_ranks = [dict(r) for r in await conn.fetch(
            "SELECT rank, ticker, name, trading_value, change_pct, is_limit_up_30m "
            "FROM trading_rankings WHERE rank_date=$1 AND market='TW' ORDER BY rank LIMIT 30",
            tw_rank_date,
        )]

    await conn.close()

    report_date = report["report_date"].strftime("%Y/%m/%d") if report else "—"
    report_html = md_to_html(report["raw_response"] or "") if report else "<p>尚無報告</p>"
    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ── Market indicator cards ──────────────────────────────────────────────────
    def ind(sym):
        d = snaps.get(sym, {})
        return d.get("close", 0), d.get("chg")

    INDICATORS = [
        ("S&amp;P 500", "^GSPC", True),
        ("NASDAQ",       "^IXIC", True),
        ("SOX 半導體",   "^SOX",  True),
        ("日經 N225",    "^N225", True),
        ("台股 TWII",    "^TWII", True),
        ("VIX",          "^VIX",  False),
        ("10Y 殖利率",   "^TNX",  False),
        ("DXY 美元",     "DX-Y.NYB", True),
        ("恐慌貪婪指數", "FEAR_GREED", False),
    ]

    ind_cards = []
    for label, sym, show_pct in INDICATORS:
        close, chg = ind(sym)
        val = f"{close:,.0f}" if sym not in ("^VIX", "^TNX", "FEAR_GREED") else f"{close:.2f}"
        if not close:
            val = "N/A"
        pct_html = ""
        if show_pct:
            pct_text, css = fmt_pct(chg)
            pct_html = f'<div class="change {css}">{pct_text}</div>'
        ind_cards.append(f"""
        <div class="market-item">
          <div class="label">{label}</div>
          <div class="value">{val}</div>
          {pct_html}
        </div>""")

    # ── Ranking rows ───────────────────────────────────────────────────────────
    us_rows = []
    for r in us_ranks:
        chg = float(r["change_pct"]) if r["change_pct"] is not None else None
        pct, css = fmt_pct(chg)
        val = f"${float(r['trading_value'] or 0)/1e9:.1f}B"
        us_rows.append(
            f'<tr><td class="rank">{r["rank"]}</td>'
            f'<td class="ticker">{html_lib.escape(r["ticker"])}</td>'
            f'<td class="name">{html_lib.escape((r["name"] or "")[:16])}</td>'
            f'<td class="num">{val}</td>'
            f'<td class="num {css}">{pct}</td></tr>'
        )

    tw_rows = []
    for r in tw_ranks:
        chg = float(r["change_pct"]) if r["change_pct"] is not None else None
        pct, css = fmt_pct(chg)
        val = f"{float(r['trading_value'] or 0)/1e8:.0f}億"
        flag = " ⬆" if r.get("is_limit_up_30m") else ""
        tw_rows.append(
            f'<tr><td class="rank">{r["rank"]}</td>'
            f'<td class="ticker">{html_lib.escape(r["ticker"])}</td>'
            f'<td class="name">{html_lib.escape((r["name"] or "")[:8])}{flag}</td>'
            f'<td class="num">{val}</td>'
            f'<td class="num {css}">{pct}</td></tr>'
        )

    # ── HTML ───────────────────────────────────────────────────────────────────
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
body{{background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.65;font-size:15px}}
a{{color:var(--accent)}}

header{{background:var(--card);border-bottom:1px solid var(--border);padding:1.1rem 1.5rem}}
header h1{{font-size:1.25rem;font-weight:700;color:var(--accent)}}
header p{{color:var(--muted);font-size:0.82rem;margin-top:0.15rem}}

.wrap{{max-width:1080px;margin:0 auto;padding:1.5rem 1.25rem}}

.card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:1.25rem 1.4rem;margin-bottom:1.25rem}}
.sec{{font-size:0.7rem;font-weight:700;color:var(--accent);letter-spacing:.1em;text-transform:uppercase;margin-bottom:.9rem}}

/* Market grid */
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(118px,1fr));gap:.65rem}}
.market-item{{background:#12151f;border-radius:8px;padding:.65rem .75rem}}
.market-item .label{{font-size:.7rem;color:var(--muted)}}
.market-item .value{{font-size:1.05rem;font-weight:700;margin:.15rem 0}}
.market-item .change{{font-size:.8rem}}

/* Report */
.report h2{{color:var(--accent);font-size:1rem;font-weight:600;margin:1.2rem 0 .55rem;padding-bottom:.35rem;border-bottom:1px solid var(--border)}}
.report h3{{color:#a0b0cc;font-size:.93rem;font-weight:600;margin:.95rem 0 .4rem}}
.report p{{margin-bottom:.6rem;font-size:.92rem;color:var(--text)}}
.report ul{{padding-left:1.3rem;margin-bottom:.6rem}}
.report li{{margin-bottom:.28rem;font-size:.92rem}}
.report strong{{color:#c0cfe0}}

/* Rankings */
.ranks{{display:grid;grid-template-columns:1fr 1fr;gap:1.25rem}}
@media(max-width:680px){{.ranks{{grid-template-columns:1fr}}}}
table{{width:100%;border-collapse:collapse}}
th{{color:var(--muted);font-weight:500;font-size:.72rem;text-align:left;padding:.3rem .45rem;border-bottom:1px solid var(--border)}}
td{{padding:.28rem .45rem;border-bottom:1px solid rgba(42,46,64,.5);font-size:.82rem}}
td.rank{{color:var(--muted);width:1.8rem}}
td.ticker{{font-weight:600}}
td.num{{text-align:right}}
tr:last-child td{{border-bottom:none}}

.up{{color:var(--up)}} .down{{color:var(--down)}} .neutral{{color:var(--muted)}}

footer{{text-align:center;color:var(--muted);font-size:.78rem;padding:1.75rem 1rem;border-top:1px solid var(--border);margin-top:.5rem}}
</style>
</head>
<body>
<header>
  <h1>IIA 投資情報</h1>
  <p>報告日期：{report_date}　·　資料更新：{updated_at}</p>
</header>
<div class="wrap">

<div class="card">
  <div class="sec">市場指標（{str(snap_date) if snap_date else "—"}）</div>
  <div class="grid">{''.join(ind_cards)}</div>
</div>

<div class="card">
  <div class="sec">每日分析報告</div>
  <div class="report">{report_html}</div>
</div>

<div class="ranks">
  <div class="card">
    <div class="sec">美股 成交值前 30（{str(us_rank_date) if us_rank_date else "—"}）</div>
    <table>
      <thead><tr><th>#</th><th>代號</th><th>名稱</th><th style="text-align:right">成交值</th><th style="text-align:right">漲跌</th></tr></thead>
      <tbody>{''.join(us_rows) or '<tr><td colspan="5" style="color:var(--muted)">尚無資料</td></tr>'}</tbody>
    </table>
  </div>
  <div class="card">
    <div class="sec">台股 成交值前 30（{str(tw_rank_date) if tw_rank_date else "—"}）</div>
    <table>
      <thead><tr><th>#</th><th>代號</th><th>名稱</th><th style="text-align:right">成交值</th><th style="text-align:right">漲跌</th></tr></thead>
      <tbody>{''.join(tw_rows) or '<tr><td colspan="5" style="color:var(--muted)">尚無資料</td></tr>'}</tbody>
    </table>
  </div>
</div>

</div>
<footer>IIA Investment Intelligence Analyst &nbsp;·&nbsp; 資料僅供參考，不構成投資建議</footer>
</body>
</html>"""

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(page, encoding="utf-8")
    print(f"Generated {OUT_FILE}  ({len(page):,} bytes)")


if __name__ == "__main__":
    asyncio.run(generate())
