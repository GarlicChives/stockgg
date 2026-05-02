#!/usr/bin/env python3
"""Test mlx-whisper transcription on a short podcast clip."""
import subprocess
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET

# Use 股海飯桶 (shortest episodes, ~30-40 min)
FEED_URL = "https://feeds.soundon.fm/podcasts/537b7401-756c-4d0d-b1df-36a49e2793d3.xml"
MODEL = "mlx-community/whisper-large-v3-mlx-4bit"

def get_latest_audio_url(feed_url: str) -> tuple[str, str]:
    req = urllib.request.Request(feed_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as r:
        root = ET.fromstring(r.read())
    item = root.find('channel/item')
    title = item.findtext('title', '').strip()
    enc = item.find('enclosure')
    return title, enc.get('url', '') if enc is not None else ''

print("Getting latest episode URL...")
title, audio_url = get_latest_audio_url(FEED_URL)
print(f"Episode: {title}")
print(f"URL: {audio_url[:80]}")

# Download first 3 minutes only (3*60*128000/8 bytes ≈ 2.8 MB for 128kbps)
with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
    tmp_mp3 = f.name

with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
    tmp_clip = f.name

print("\nDownloading full episode (needed for ffmpeg clip)...")
req = urllib.request.Request(audio_url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=60) as r, open(tmp_mp3, 'wb') as f:
    chunk = r.read(5 * 1024 * 1024)  # download first 5MB only
    f.write(chunk)

print(f"Downloaded {len(chunk)//1024}KB")

# Cut to first 3 minutes with ffmpeg
print("Cutting to first 3 minutes...")
subprocess.run([
    'ffmpeg', '-y', '-i', tmp_mp3,
    '-t', '180',  # 3 minutes
    '-acodec', 'copy',
    tmp_clip
], capture_output=True)

print(f"\nTranscribing with {MODEL}...")
print("(Model will download on first run ~3GB, subsequent runs are fast)")

import mlx_whisper
t0 = time.time()
result = mlx_whisper.transcribe(
    tmp_clip,
    path_or_hf_repo=MODEL,
    language="zh",
    verbose=False,
)
elapsed = time.time() - t0

text = result.get('text', '')
print(f"\nTranscription time: {elapsed:.1f}s for 3min audio ({180/elapsed:.1f}x realtime)")
print(f"Transcript ({len(text)} chars):")
print(text[:1000])
