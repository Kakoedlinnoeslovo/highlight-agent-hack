"""Browser history restores the explorer with a reusable, empty media player."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

PUBLIC = Path(__file__).resolve().parents[1] / "web/public"
MEDIA_URL = "/recordings/" + "1" * 64 + ".mp4"


def test_cached_explorer_clears_playback_and_can_play_again(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("Explorer playback requires FFmpeg.")
    movie = tmp_path / "fixture.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=0x1f2f46:s=32x18:r=2", "-t", "3",
        "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-g", "2", "-movflags", "+faststart", str(movie),
    ], check=True, capture_output=True, timeout=15)
    video_bytes = movie.read_bytes()
    totals = {"recordings": 1, "chunks": 1, "segments": 1, "seconds": 3}
    catalog = {
        "coverage": {"expected": totals, "imported": totals, "playable": totals,
                     "complete": True, "failures": []},
        "recordings": [{"id": "fixture", "location": "San Francisco",
                        "capture_type": "streets", "duration": 3,
                        "media": {"url": MEDIA_URL},
                        "segments": [{"id": "sample", "start": 0, "end": 3,
                                      "caption": "Browser test sample", "source": "fixture.mp4"}]}],
    }
    scouting = {"audiences": {"dog": "Dogs"}, "corners": [
        {"id": "fixture", "camera": "Fixture", "frames": []},
    ]}

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(PUBLIC), **kwargs)

        def log_message(self, *_args):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in {"/catalog.json", "/corners.json"}:
                body = json.dumps(catalog if path == "/catalog.json" else scouting).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == MEDIA_URL:
                start, end, status = 0, len(video_bytes) - 1, 200
                if requested := self.headers.get("Range"):
                    match = re.fullmatch(r"bytes=(\d+)-(\d*)", requested)
                    if not match or int(match[1]) >= len(video_bytes):
                        self.send_response(416)
                        self.end_headers()
                        return
                    start = int(match[1])
                    end = min(int(match[2]), end) if match[2] else end
                    status = 206
                self.send_response(status)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Accept-Ranges", "bytes")
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(video_bytes)}")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                self.wfile.write(video_bytes[start:end + 1])
            else:
                super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with sync_playwright() as playwright:
            bundled = Path(playwright.chromium.executable_path)
            chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
            executable = bundled if bundled.is_file() else chrome
            if not executable.is_file():
                pytest.skip("Install Playwright Chromium or Google Chrome.")
            browser = playwright.chromium.launch(
                executable_path=str(executable),
                ignore_default_args=["--disable-back-forward-cache"],
            )
            page = browser.new_page()
            base = f"http://127.0.0.1:{server.server_port}"
            page.goto(base + "/explorer.html?q=Browser")
            page.locator(".ex-result").click()
            page.wait_for_function("document.querySelector('video').readyState >= 2")
            page.evaluate("window.addEventListener('pageshow', e => window.restored = e.persisted)")
            page.locator('.cs-navigation a[href="/"]').click()
            page.locator("#business").wait_for()
            page.go_back(wait_until="commit")
            page.wait_for_function("window.restored === true")
            expect(page.locator("#search")).to_have_value("Browser")
            expect(page.locator("#recording-video")).to_be_hidden()
            assert page.locator("#recording-video").get_attribute("src") is None
            expect(page.locator('.ex-result[aria-pressed="true"]')).to_have_count(0)
            expect(page.locator("#player-title")).to_have_text("Choose a caption interval")
            page.locator(".ex-result").click()
            page.wait_for_function("document.querySelector('video').readyState >= 2")
            expect(page.locator("#recording-video")).to_be_visible()
            expect(page.locator("#recording-video")).to_have_attribute("src", MEDIA_URL)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
