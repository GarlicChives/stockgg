#!/usr/bin/env python3
"""Podcast crawler — RSS + full audio transcription via mlx-whisper (M1 optimised).

Flow per episode:
  1. Parse RSS → get audio URL + metadata
  2. Download MP3 to temp file
  3. Transcribe with mlx-whisper/large-v3 (Chinese)
  4. Store full transcript as content in DB
"""
import asyncio
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import urlopen, Request
import xml.etree.ElementTree as ET
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncpg
from dotenv import load_dotenv

from src.utils.refine import refine_and_store

load_dotenv()

LOOKBACK_DAYS = 180
WHISPER_MODEL = "mlx-community/whisper-large-v3-mlx-4bit"

PODCASTS = [
    {
        "source": "podcast_gooaye",
        "name": "股癌 Gooaye",
        "rss": "https://feeds.soundon.fm/podcasts/954689a5-3096-43a4-a80b-7810b219cef3.xml",
        "author": "謝孟恭",
    },
    {
        "source": "podcast_macromicro",
        "name": "財經M平方 podcast",
        "rss": "https://feeds.soundon.fm/podcasts/d2aab16c-3a70-4023-b52b-e50f07852ecd.xml",
        "author": "財經M平方",
    },
    {
        "source": "podcast_chives_grad",
        "name": "韭菜畢業班",
        "rss": "https://feeds.soundon.fm/podcasts/70907bd6-d0ae-4b64-bc38-2bf48ae4fc36.xml",
        "author": "韭菜畢業班",
    },
    {
        "source": "podcast_stock_barrel",
        "name": "股海飯桶 WilsonRice",
        "rss": "https://feeds.soundon.fm/podcasts/537b7401-756c-4d0d-b1df-36a49e2793d3.xml",
        "author": "Wilson",
    },
]

NS = {
    'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd',
    'content': 'http://purl.org/rss/1.0/modules/content/',
}

_SKIP_TICKERS = {
    "QoQ", "YoY", "EPS", "CEO", "AI", "US", "TW", "GDP", "CPI", "PMI", "VIX",
    "ETF", "PCE", "PPI", "IPO", "GAAP", "QE", "FED", "EP", "CME", "HBM", "DDR",
    "GPU", "CPU", "FCF", "RPO", "ACV", "TAM", "GMV",
}


def strip_html(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&#?\w+;', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def extract_tickers(text: str) -> list[str]:
    tw = re.findall(r'\b(\d{4,5})\b', text)
    us = re.findall(r'\(([A-Z]{2,5})\)', text)
    all_t = list(dict.fromkeys(tw + us))
    return [t for t in all_t if t not in _SKIP_TICKERS and len(t) >= 2]


def parse_pub_date(s: str) -> Optional[datetime]:
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def transcribe_audio(audio_url: str, episode_title: str) -> str:
    """Download MP3 and transcribe with mlx-whisper (M1 optimised). Returns full transcript."""
    import mlx_whisper

    print(f"    Downloading audio…")
    req = Request(audio_url, headers={'User-Agent': 'Mozilla/5.0 IIA-Podcast-Crawler'})

    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
        tmp_path = f.name
        with urlopen(req, timeout=120) as r:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)

    size_mb = os.path.getsize(tmp_path) / 1024 / 1024
    print(f"    Downloaded {size_mb:.1f} MB — transcribing…")

    try:
        result = mlx_whisper.transcribe(
            tmp_path,
            path_or_hf_repo=WHISPER_MODEL,
            language="zh",
            verbose=False,
        )
        transcript = (result.get('text') or '').strip()
        print(f"    Transcript: {len(transcript)} chars")
        return transcript
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def fetch_rss_items(podcast: dict, cutoff: datetime) -> list[dict]:
    """Fetch RSS and return episode metadata (no transcription yet)."""
    req = Request(podcast['rss'], headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(req, timeout=15) as r:
        root = ET.fromstring(r.read())

    channel = root.find('channel')
    items = []

    for item in channel.findall('item'):
        title = (item.findtext('title') or '').strip()
        pub_date = parse_pub_date(item.findtext('pubDate', ''))

        if pub_date and pub_date < cutoff:
            break  # newest-first; stop when past window

        guid = (item.findtext('guid') or '').strip()
        link = (item.findtext('link') or '').strip()
        # Prefer SoundOn player link over bare GUID
        url = link if link.startswith('http') else guid

        enc = item.find('enclosure')
        audio_url = enc.get('url', '') if enc is not None else ''

        # RSS show notes as fallback (thin but better than nothing)
        show_notes = ''
        for field in ['description', 'itunes:summary']:
            raw = item.findtext(field, '', NS)
            if raw:
                cleaned = strip_html(raw)
                cleaned = re.sub(r'--\s*Hosting provided by.*$', '', cleaned, flags=re.S).strip()
                if len(cleaned) > len(show_notes):
                    show_notes = cleaned

        items.append({
            'source': podcast['source'],
            'url': url,
            'title': title,
            'author': podcast['author'],
            'published_at': pub_date,
            'audio_url': audio_url,
            'show_notes': show_notes,
        })

    return items


async def upsert_episode(conn, ep: dict) -> Optional[int]:
    """Insert episode; return new row id or None if already exists."""
    existing = await conn.fetchval("SELECT id FROM articles WHERE url=$1", ep['url'])
    if existing:
        return None
    row_id = await conn.fetchval(
        """INSERT INTO articles
           (source, url, title, author, published_at, content, tickers, status)
           VALUES ($1,$2,$3,$4,$5,$6,$7,'active') RETURNING id""",
        ep['source'], ep['url'], ep['title'], ep['author'],
        ep['published_at'], ep['content'], ep['tickers'],
    )
    return row_id


async def crawl(incremental: bool = False):
    global_cutoff = datetime.now(tz=timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])

    total_new = 0
    for podcast in PODCASTS:
        source = podcast['source']

        if incremental:
            latest = await conn.fetchval(
                "SELECT MAX(published_at) FROM articles WHERE source=$1", source
            )
            if latest:
                cutoff = latest if latest.tzinfo else latest.replace(tzinfo=timezone.utc)
                print(f"\n=== {podcast['name']} (after {cutoff.date()}) ===")
            else:
                cutoff = global_cutoff
                print(f"\n=== {podcast['name']} (first run) ===")
        else:
            cutoff = global_cutoff
            print(f"\n=== {podcast['name']} ===")

        try:
            items = fetch_rss_items(podcast, cutoff)
            print(f"  {len(items)} episode(s) in window")

            for ep_meta in items:
                date_str = str(ep_meta['published_at'])[:10] if ep_meta['published_at'] else '????-??-??'
                print(f"  [{date_str}] {ep_meta['title'][:60]!r}")

                # Check if already in DB
                existing = await conn.fetchval(
                    "SELECT id FROM articles WHERE url=$1", ep_meta['url']
                )
                if existing:
                    print(f"    Already saved — skipping")
                    continue

                # Transcribe audio
                content = ''
                if ep_meta['audio_url']:
                    try:
                        content = transcribe_audio(ep_meta['audio_url'], ep_meta['title'])
                    except Exception as e:
                        print(f"    Transcription error: {e}")

                # Fall back to show notes if transcription failed
                if not content:
                    content = ep_meta['show_notes'] or ep_meta['title']
                    print(f"    Using show notes as fallback ({len(content)} chars)")

                ep = {
                    **ep_meta,
                    'content': content,
                    'tickers': extract_tickers(f"{ep_meta['title']} {content}"),
                }

                new_id = await upsert_episode(conn, ep)
                if new_id:
                    print(f"    Saved id={new_id} ({len(content)} chars transcript)")
                    total_new += 1
                    await refine_and_store(conn, new_id, ep['title'], content)

        except Exception as e:
            print(f"  ERROR: {e}")

    print(f"\nDone. {total_new} new podcast episodes saved.")
    await conn.close()


async def retranscribe_latest(conn, n: int = 1):
    """Retranscribe the most recent N episodes of each podcast (update existing records).

    Matches DB records to RSS items by title prefix to handle GUID/URL format changes.
    """
    for podcast in PODCASTS:
        source = podcast['source']
        rows = await conn.fetch(
            """SELECT id, url, title, content FROM articles
               WHERE source=$1 ORDER BY published_at DESC NULLS LAST LIMIT $2""",
            source, n
        )
        if not rows:
            print(f"\n[{podcast['name']}] No records found")
            continue

        # Fetch RSS once per podcast
        req = Request(podcast['rss'], headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=15) as r:
            root = ET.fromstring(r.read())
        rss_items = root.findall('channel/item')

        # Build a title→enclosure map from RSS (first 20 items)
        title_to_audio: dict[str, str] = {}
        for item in rss_items[:20]:
            t = (item.findtext('title') or '').strip()
            enc = item.find('enclosure')
            if enc is not None and t:
                title_to_audio[t] = enc.get('url', '')

        for row in rows:
            print(f"\n[{podcast['name']}] {row['title'][:65]!r}")
            print(f"  Current content: {len(row['content'])} chars")

            # Match by exact title, then by prefix (first 20 chars)
            audio_url = title_to_audio.get(row['title'], '')
            if not audio_url:
                for rss_title, url in title_to_audio.items():
                    if rss_title[:20] == row['title'][:20]:
                        audio_url = url
                        break

            # Last resort: use the first RSS item if only 1 row requested
            if not audio_url and n == 1 and rss_items:
                enc = rss_items[0].find('enclosure')
                if enc is not None:
                    audio_url = enc.get('url', '')
                    print(f"  ⚠ Title mismatch — using latest RSS item as fallback")

            if not audio_url:
                print(f"  No audio URL found — skipping")
                continue

            try:
                transcript = transcribe_audio(audio_url, row['title'])
                if transcript:
                    tickers = extract_tickers(f"{row['title']} {transcript}")
                    await conn.execute(
                        "UPDATE articles SET content=$1, tickers=$2, updated_at=NOW() WHERE id=$3",
                        transcript, tickers, row['id']
                    )
                    print(f"  ✓ Updated — {len(transcript)} char transcript")
            except Exception as e:
                print(f"  Error: {e}")


if __name__ == '__main__':
    if '--retranscribe-latest' in sys.argv:
        async def run_retranscribe():
            conn = await asyncpg.connect(os.environ['DATABASE_URL'])
            print("Retranscribing latest episode from each podcast...")
            await retranscribe_latest(conn)
            await conn.close()
        asyncio.run(run_retranscribe())
    else:
        asyncio.run(crawl(incremental='--incremental' in sys.argv))
