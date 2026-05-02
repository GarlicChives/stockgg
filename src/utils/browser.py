"""Browser utility: ensures Chrome is running on the debug port before connecting."""
import asyncio
import os
import subprocess
import urllib.request
from dotenv import load_dotenv

load_dotenv()

CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_USER_DATA = "/tmp/chrome-iia"


def _chrome_alive(port: int = CDP_PORT) -> bool:
    try:
        urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=2)
        return True
    except Exception:
        return False


async def ensure_chrome(port: int = CDP_PORT) -> None:
    """Launch Chrome with remote debugging if not already running."""
    if _chrome_alive(port):
        return

    print(f"Chrome not detected on port {port} — launching...")
    subprocess.run(["pkill", "-f", f"remote-debugging-port={port}"], capture_output=True)
    await asyncio.sleep(1)

    subprocess.Popen([
        CHROME_BIN,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={CHROME_USER_DATA}",
        "--no-first-run",
        "--no-default-browser-check",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(15):
        await asyncio.sleep(1)
        if _chrome_alive(port):
            print(f"Chrome ready on port {port}")
            return

    raise RuntimeError(f"Chrome failed to start on port {port} after 15 s")


async def connect_browser(playwright, port: int = CDP_PORT):
    """Connect to Chrome via CDP, launching it first if necessary."""
    await ensure_chrome(port)
    return await playwright.chromium.connect_over_cdp(f"http://localhost:{port}")
