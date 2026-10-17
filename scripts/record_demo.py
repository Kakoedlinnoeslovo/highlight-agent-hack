"""Record a genuine Corner Scout walkthrough for the demo video.

Drives the real web app in headless Chromium, captures every painted frame over
the DevTools screencast, and writes ``data/rehearsal.mp4`` plus measured beat
timestamps in ``data/rehearsal.json``. Searches hit the live ``/api/profile``
endpoint; nothing is mocked. Captions, the pointer, and title cards are
in-browser overlays because the minimal FFmpeg build has no ``drawtext``.
The accompanying ``*-plan.json`` contains the measured, unshortened scenes
for the product's demo assembler. The walkthrough targets 110 seconds and
rejects an assembly plan longer than the two-minute submission ceiling.

    cd web && npm run dev            # in another terminal
    uv run python scripts/record_demo.py --manifest examples/demo-plan.json
    uv run highlight-agent demo examples/demo-plan.json --output-dir outputs/demo
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import bisect
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from playwright.async_api import Page, async_playwright

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 1280, 720
LIVE_URL = "corner-scout-rouge.vercel.app"

OVERLAY = r"""
(() => {
  const css = `
  #demo-cursor{position:fixed;left:0;top:0;width:26px;height:26px;z-index:2147483647;pointer-events:none;
    transform:translate(-100px,-100px);filter:drop-shadow(0 2px 3px rgba(2,6,23,.45))}
  .demo-ripple{position:fixed;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;z-index:2147483646;
    pointer-events:none;border:3px solid #f59e0b;animation:demo-ripple .55s ease-out forwards}
  @keyframes demo-ripple{from{transform:scale(.3);opacity:1}to{transform:scale(1.4);opacity:0}}
  #demo-caption{position:fixed;left:50%;bottom:26px;z-index:2147483645;pointer-events:none;max-width:1060px;
    transform:translate(-50%,14px);opacity:0;transition:opacity .35s ease,transform .35s ease;
    background:rgba(11,16,32,.93);color:#fff;padding:13px 24px 14px;border-radius:14px;text-align:center;
    font:600 22px/1.38 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;letter-spacing:-.01em;
    box-shadow:0 14px 34px rgba(2,6,23,.32);width:max-content;text-wrap:balance}
  #demo-caption.show{opacity:1;transform:translate(-50%,0)}
  #demo-caption small{display:block;font-size:12.5px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
    color:#a5b4fc;margin-bottom:3px}
  #demo-caption b{color:#fcd34d;font-weight:700}
  .demo-ring{position:fixed;z-index:2147483644;pointer-events:none;border:3px solid #f59e0b;border-radius:12px;
    box-shadow:0 0 0 6px rgba(245,158,11,.22);opacity:0;transition:opacity .3s ease}
  .demo-ring.show{opacity:1;animation:demo-pulse 1.2s ease-in-out infinite}
  @keyframes demo-pulse{50%{box-shadow:0 0 0 11px rgba(245,158,11,.10)}}
  #demo-pip{position:fixed;left:56px;top:96px;width:560px;z-index:2147483643;pointer-events:none;opacity:0;
    transform:translateY(10px);transition:opacity .4s ease,transform .4s ease;background:#0b1020;border-radius:16px;
    padding:10px;box-shadow:0 24px 60px rgba(2,6,23,.45);color:#e2e8f0;
    font:600 15px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
  #demo-pip.show{opacity:1;transform:none}
  #demo-pip video{display:block;width:100%;border-radius:10px}
  #demo-pip p{margin:8px 6px 2px}#demo-pip span{color:#fcd34d}
  #demo-card{position:fixed;inset:0;z-index:2147483642;pointer-events:none;opacity:0;transition:opacity .6s ease;
    display:flex;flex-direction:column;justify-content:center;padding:0 110px;color:#fff;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
    background:radial-gradient(900px 520px at 12% 8%,rgba(70,95,255,.55),transparent 62%),
      radial-gradient(760px 480px at 96% 100%,rgba(70,95,255,.32),transparent 60%),#0a0f1f}
  #demo-card.show{opacity:1}
  #demo-card .brand{display:flex;align-items:center;gap:14px;font-size:24px;font-weight:700;margin-bottom:34px}
  #demo-card .logo{width:46px;height:46px;border-radius:12px;background:#465fff;display:grid;place-items:center;font-size:30px}
  #demo-card h1{font-size:60px;line-height:1.06;letter-spacing:-.035em;margin:0 0 22px;max-width:1000px}
  #demo-card h1 em{font-style:normal;color:#fcd34d}
  #demo-card p{font-size:25px;line-height:1.45;color:#cbd5e1;margin:0;max-width:960px}
  #demo-card .row{display:flex;gap:10px;flex-wrap:wrap;margin-top:34px}
  #demo-card .row span{border:1px solid rgba(165,180,252,.35);background:rgba(70,95,255,.14);border-radius:999px;
    padding:8px 16px;font-size:17px;color:#e0e7ff}
  #demo-card .steps{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:8px}
  #demo-card .step{background:rgba(255,255,255,.06);border:1px solid rgba(165,180,252,.22);border-radius:18px;padding:22px}
  #demo-card .step b{display:block;color:#a5b4fc;font-size:15px;letter-spacing:.12em;text-transform:uppercase;margin-bottom:10px}
  #demo-card .step h3{font-size:25px;margin:0 0 10px;letter-spacing:-.02em}
  #demo-card .step p{font-size:18px;line-height:1.45}
  #demo-card .url{font-size:30px;font-weight:700;color:#fff;margin-top:30px}
  #demo-card .url span{color:#fcd34d}
  #demo-card .fine{font-size:17px;color:#94a3b8;margin-top:26px}
  `;
  function install() {
    if (document.querySelector('#demo-cursor')) return;
    const style = document.createElement('style');
    style.textContent = css;
    document.head.append(style);
    const cursor = document.createElement('div');
    cursor.id = 'demo-cursor';
    cursor.innerHTML = '<svg viewBox="0 0 26 26" width="26" height="26"><path d="M4 2.5 21 14.2l-7.3 1.3 4.4 8.1-3.3 1.6-4.3-8.2L4.8 22z" fill="#fff" stroke="#0b1020" stroke-width="1.8" stroke-linejoin="round"/></svg>';
    const caption = document.createElement('div');
    caption.id = 'demo-caption';
    const card = document.createElement('div');
    card.id = 'demo-card';
    const pip = document.createElement('div');
    pip.id = 'demo-pip';
    document.body.append(card, pip, caption, cursor);
    document.addEventListener('mousemove', event => {
      cursor.style.transform = `translate(${event.clientX - 4}px,${event.clientY - 3}px)`;
    }, true);
    document.addEventListener('mousedown', event => {
      const ripple = document.createElement('div');
      ripple.className = 'demo-ripple';
      ripple.style.left = `${event.clientX}px`;
      ripple.style.top = `${event.clientY}px`;
      document.body.append(ripple);
      setTimeout(() => ripple.remove(), 700);
    }, true);
  }
  window.__demo = {
    caption(html) {
      const node = document.querySelector('#demo-caption');
      node.classList.remove('show');
      setTimeout(() => { node.innerHTML = html; node.classList.add('show'); }, node.innerHTML ? 260 : 0);
    },
    hideCaption() { document.querySelector('#demo-caption').classList.remove('show'); },
    card(html) { const node = document.querySelector('#demo-card'); node.innerHTML = html; node.classList.add('show');
      document.querySelector('#demo-cursor').style.opacity = '0'; },
    hideCard() { document.querySelector('#demo-card').classList.remove('show');
      document.querySelector('#demo-cursor').style.opacity = '1'; },
    pip(html) { const node = document.querySelector('#demo-pip'); node.innerHTML = html; node.classList.add('show');
      node.querySelector('video')?.play(); },
    hidePip() { document.querySelector('#demo-pip').classList.remove('show'); },
    ring(selector, pad = 6) {
      const target = document.querySelector(selector);
      if (!target) return false;
      const box = target.getBoundingClientRect();
      const ring = document.createElement('div');
      ring.className = 'demo-ring';
      Object.assign(ring.style, { left: `${box.left - pad}px`, top: `${box.top - pad}px`,
        width: `${box.width + pad * 2}px`, height: `${box.height + pad * 2}px` });
      document.body.append(ring);
      requestAnimationFrame(() => ring.classList.add('show'));
      return true;
    },
    unring() { document.querySelectorAll('.demo-ring').forEach(ring => { ring.classList.remove('show'); setTimeout(() => ring.remove(), 320); }); },
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install); else install();
})();
"""

TITLE_CARD = """
<div class="brand"><span class="logo">⌖</span>Corner Scout</div>
<h1>Where should I open<br>my <em>shop</em>?</h1>
<p>See <b style="color:#fff">who passes your corner</b> — and open the footage behind every match.</p>
<div class="row"><span>Any business, in plain words</span><span>5 San Francisco corners</span><span>Every answer backed by video</span></div>
"""

HOW_CARD = """
<div class="brand"><span class="logo">⌖</span>How it works</div>
<div class="steps">
  <div class="step"><b>01 · Footage</b><h3>Real street video</h3><p>SF Streets recordings and NVIDIA Cosmos captions, served by VAST. 5 corners, 15 minutes.</p></div>
  <div class="step"><b>02 · Who passes</b><h3>Vision reads frames</h3><p>Gemma 4 vision on W&amp;B Inference reads one frame every 5 s: people types, car tiers, cyclists, dogs.</p></div>
  <div class="step"><b>03 · Your business</b><h3>Code ranks corners</h3><p>A W&amp;B text model picks your core customers. Code scores every corner and cuts the evidence.</p></div>
</div>
<p class="fine">Models estimate. Code ranks. Vision observations are cached; each business profile is generated when you search.</p>
<p class="fine">Three minutes per corner, recorded at different times. Sampled frames can show the same person or dog.</p>
"""

END_CARD = f"""
<div class="brand"><span class="logo">⌖</span>Corner Scout</div>
<h1>See who walks past<br><em>before you sign.</em></h1>
<p class="url">Try it → <span>{LIVE_URL}</span></p>
<div class="row"><span>VAST</span><span>NVIDIA Cosmos</span><span>W&amp;B Inference</span></div>
<p class="fine">Real searches, recorded live. This video was assembled with Highlight Agent's own FFmpeg renderer.</p>
"""


class Director:
    """Small helpers that keep the walkthrough readable and measure each beat."""

    def __init__(self, page: Page):
        self.page = page
        self.x, self.y = WIDTH * 0.78, HEIGHT * 0.86
        self.beats: list[dict] = []

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def beat(self, label: str, started: float) -> None:
        self.beats.append({"label": label, "start": started, "end": time.time()})
        print(f"Recorded: {label} ({time.time() - started:.1f}s)", flush=True)

    async def js(self, script: str, *args):
        return await self.page.evaluate(script, *args)

    async def caption(self, html: str, kicker: str | None = None) -> None:
        body = f"<small>{kicker}</small>{html}" if kicker else html
        await self.js("html => window.__demo.caption(html)", body)

    async def move(self, x: float, y: float, seconds: float = 0.4) -> None:
        steps = max(8, int(seconds * 60))
        x0, y0 = self.x, self.y
        for step in range(1, steps + 1):
            t = step / steps
            ease = t * t * (3 - 2 * t)
            await self.page.mouse.move(x0 + (x - x0) * ease, y0 + (y - y0) * ease)
            await asyncio.sleep(seconds / steps)
        self.x, self.y = x, y

    async def click(self, selector: str, clicks: int = 1) -> None:
        target = self.page.locator(selector).first
        await target.scroll_into_view_if_needed()
        box = await target.bounding_box()
        assert box, f"{selector} is not visible"
        await self.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await asyncio.sleep(0.1)
        await self.page.mouse.click(self.x, self.y, click_count=clicks)

    async def example(self, business: str) -> None:
        selector = f"#cs-examples button:text-is({json.dumps(business)})"
        if not await self.page.locator(selector).is_visible():
            await self.click("#edit-business")
            await self.page.locator(selector).wait_for(state="visible")
        await self.click(selector)

    async def reveal(self, selector: str, position: float = 18) -> None:
        top = await self.page.locator(selector).evaluate(
            "element => element.getBoundingClientRect().top + scrollY"
        )
        await self.scroll_to(top - position, 0.8)

    async def sample(self, seconds: float, marker: bool = False) -> None:
        group = "proof-markers" if marker else "proof-thumbnails"
        await self.click(f'#{group} button[data-sample-seconds="{seconds:g}"]')
        await self.page.wait_for_function(
            """seconds => {
              const image = document.querySelector('#proof-image');
              const time = document.querySelector('#proof-time');
              const formatted = `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(1).padStart(4, '0')}`;
              return image?.complete && image.naturalWidth > 0 && time?.textContent.includes(formatted);
            }""",
            arg=seconds,
            timeout=10_000,
        )

    async def scroll_to(self, top: float, seconds: float = 1.0) -> None:
        await self.js("top => window.scrollTo({top, behavior: 'smooth'})", top)
        await asyncio.sleep(seconds)

    async def ring(self, selector: str) -> None:
        assert await self.js("s => window.__demo.ring(s)", selector), f"{selector} not found"

    async def unring(self) -> None:
        await self.js("() => window.__demo.unring()")

    async def text(self, selector: str) -> str:
        return (await self.page.locator(selector).first.inner_text()).strip()

    async def result(self, business: str) -> None:
        await self.page.wait_for_function(
            """business => document.querySelector('#results').getAttribute('aria-busy') === 'false'
              && (document.querySelector('.cs-customers b')?.textContent === business
                || document.querySelector('.cs-error'))""",
            arg=business,
            timeout=60_000,
        )
        error = self.page.locator('.cs-error')
        if await error.count():
            raise RuntimeError(f"Search for {business!r} failed: {await error.inner_text()}")


async def walkthrough(page: Page, d: Director) -> None:
    # The title card is already showing when the screencast starts.
    started = time.time()
    await d.sleep(2.5)
    await d.js("() => window.__demo.hideCard()")
    await d.sleep(0.5)
    d.beat("Title: where should I open my shop?", started)

    started = time.time()
    await d.caption("Type any business. Corner Scout compares <b>5 San Francisco corners</b>: 15 minutes of street footage, 180&nbsp;frames read by a vision model.")
    await d.sleep(4.0)
    d.beat("Home page and scouting sample", started)

    started = time.time()
    await d.caption("Opening a <b>dog grooming salon</b>?", kicker="Example 1 · one click")
    await d.sleep(0.6)
    await d.example("Dog grooming salon")
    await d.result("Dog grooming salon")
    await d.sleep(0.5)
    core = (await d.text(".cs-chip.cs-core")).split(" · ")[0]
    await d.ring(".cs-chip.cs-core")
    await d.caption(f"A W&amp;B text model reads the business and picks the core customers: <b>{core.lower()}</b>.")
    await d.sleep(4.0)
    await d.unring()
    title = (await d.text("#result-title")).replace("Best corner: ", "")
    counts = await d.text("#proof-claim")
    await d.ring(".cs-recommendation .cs-recommendation-main")
    await d.caption(f"<b>{title}</b> leads the shortlist. {counts}.")
    await d.sleep(2.8)
    await d.unring()
    await d.sleep(0.35)
    d.beat("Dog grooming salon ranked", started)

    started = time.time()
    await d.reveal("#cs-proof")
    await page.wait_for_function(
        """() => {
          const image = document.querySelector('#proof-image');
          return image?.complete && image.naturalWidth > 0;
        }""",
        timeout=10_000,
    )
    assert "0:07.5" in await d.text("#proof-time"), "Dog proof should feature the 7.5-second sample"
    assert await page.locator("#proof-detail").is_visible(), "Reviewed dog detail should be available"
    assert "Full frame" in await d.text("#proof-detail"), "Dog proof should open in reviewed detail"
    await d.ring("#proof-viewport")
    await d.caption("There's the dog. A <b>reviewed detail crop</b> makes it easy to see.", kicker="Check the claim")
    await d.sleep(4.0)
    await d.unring()
    await d.click("#proof-detail")
    await d.caption("Open the <b>full sampled frame</b>, with its timestamp and source.")
    await d.sleep(4.0)
    await d.sample(2.5)
    await d.caption("Every matching sample is here: <b>2.5, 7.5, and 12.5 seconds</b>.")
    await d.sleep(3.2)
    await d.sample(12.5)
    await d.caption("Three sampled frames can show <b>the same dog passing</b>. These aren't three unique dogs.")
    await d.sleep(4.5)
    await d.reveal("#proof-markers", position=HEIGHT - 180)
    await d.ring("#proof-markers")
    await d.caption("All <b>36 samples</b> are inspectable. The highlighted three are dog matches.")
    await d.sleep(3.5)
    await d.unring()
    await d.sample(17.5, marker=True)
    await d.reveal("#cs-proof")
    await d.caption("Check an unhighlighted sample too: <b>no dog was detected in this frame</b>.")
    await d.sleep(3.5)
    await d.sample(7.5)
    await d.reveal("#cs-proof")
    await d.click("#proof-context")
    await page.wait_for_function(
        """() => {
          const video = document.querySelector('#proof-context-video');
          return video?.readyState >= 2 && !video.paused;
        }""",
        timeout=10_000,
    )
    await d.move(WIDTH * 0.88, HEIGHT * 0.15, 0.3)
    await d.caption("Then <b>play the surrounding moment</b>. Check the footage behind the estimate.")
    await d.sleep(5.0)
    await d.js("() => document.querySelector('#proof-context-video').pause()")
    d.beat("Inspect exact dog samples and play context", started)

    started = time.time()
    await d.caption("Any business, in your own words.", kicker="Example 2 · free text")
    await d.scroll_to(0, 0.7)
    if await page.locator('#edit-business').get_attribute('aria-expanded') != 'true':
        await d.click('#edit-business')
    await d.click("#business")
    await page.locator('#business').focus()
    await page.keyboard.press('ControlOrMeta+A')
    await page.keyboard.type("Luxury car detailing", delay=45)
    assert await page.locator('#business').input_value() == 'Luxury car detailing', "Typed business did not replace the previous query"
    await d.sleep(0.2)
    await d.click("#cs-go")
    await d.result("Luxury car detailing")
    await d.sleep(0.4)
    title = (await d.text("#result-title")).replace("Best corner: ", "")
    counts = await d.text(".cs-counts")
    seconds = await d.text(".cs-time")
    await d.ring(".cs-recommendation .cs-recommendation-main")
    await d.caption(f"Same footage, different business, <b>different corner</b>: {title}, {counts.lower().replace('/', ' of ')}.")
    await d.sleep(4.0)
    await d.unring()
    await d.ring(".cs-time")
    await d.caption(f"A fresh business profile. <b>{seconds.split()[0]}</b> actual search time.")
    await d.sleep(1.8)
    await d.unring()
    await d.reveal("#cs-proof")
    assert "28" in await d.text("#proof-positive-count"), "The car gallery must include all 28 matches"
    assert await page.locator("#proof-thumbnails button[data-sample-seconds]").count() == 28
    await d.ring("#proof-positive-count")
    await d.caption("Premium cars in <b>28 of 36 sampled frames</b>. Every match can be opened.")
    await d.sleep(4.0)
    await d.unring()
    await d.click("#proof-thumbnails button[data-sample-seconds]:nth-child(2)")
    await d.sleep(1.0)
    d.beat("Luxury car detailing typed", started)

    started = time.time()
    await d.caption("And when the footage can't support an answer?", kicker="Example 3 · honesty")
    await d.sleep(0.6)
    await d.scroll_to(0, 0.7)
    await d.example("Kids' toy store")
    await d.result("Kids' toy store")
    await d.sleep(0.4)
    counts = await d.text(".cs-counts")
    await d.ring(".cs-recommendation .cs-recommendation-main")
    await d.caption(f"{counts.replace('/', ' of ')} at the best corner. It needs at least 3, so <b>it refuses to guess</b>.")
    await d.sleep(3.5)
    await d.unring()
    d.beat("Kids' toy store refuses", started)

    started = time.time()
    await d.caption("Why read frames instead of counting caption words?", kicker="Example 4 · bike repair shop")
    await d.sleep(0.4)
    await d.example("Bike repair shop")
    await d.result("Bike repair shop")
    await d.sleep(0.4)
    ranking_top = await d.js("() => document.querySelector('.cs-ranking').getBoundingClientRect().top + scrollY - 18")
    await d.scroll_to(ranking_top, 1.0)
    assert await d.js("""() => {
      const row = [...document.querySelectorAll('.cs-ranking-row')].find(item => item.querySelector('b').textContent === 'Camera 1');
      if (row) row.dataset.demoRing = '';
      return Boolean(row) && row.querySelector('.cs-frame-count').textContent.includes('in 0 of');
    }"""), "Camera 1 should show no cyclists"
    await d.ring(".cs-ranking-row[data-demo-ring]")
    await d.js(
        """html => window.__demo.pip(html)""",
        '<video src="/media/c0cc58e7-0c9a5ccf4ddec3e5bdeb.mp4" muted playsinline></video>'
        "<p>Camera 1 · 0:20–0:25 · <span>parked red kick scooter</span></p>",
    )
    await d.caption("Camera 1's captions mention a bicycle in <b>25 of 36</b> segments. The frames show a parked kick scooter: <b>0&nbsp;cyclists</b>.")
    await d.sleep(4.9)
    await d.js("() => window.__demo.hidePip()")
    await d.unring()
    d.beat("Frames beat captions", started)

    started = time.time()
    await d.js("() => window.__demo.hideCaption()")
    await d.js("html => window.__demo.card(html)", HOW_CARD)
    await d.sleep(7.0)
    d.beat("How it works", started)

    started = time.time()
    await d.js("html => window.__demo.card(html)", END_CARD)
    await d.sleep(5.5)
    d.beat("Try it", started)


async def record(url: str, frames_dir: Path, executable: str | None, diagnostics_dir: Path | None = None) -> tuple[list[tuple[float, Path]], list[dict], float]:
    frames: list[tuple[float, Path]] = []
    offsets: list[float] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=executable)
        context = await browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, color_scheme="light")
        await context.add_init_script(OVERLAY)
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_function("!document.querySelector('#cs-go').disabled", timeout=30_000)
        await page.mouse.move(WIDTH * 0.78, HEIGHT * 0.86)
        await page.evaluate("html => window.__demo.card(html)", TITLE_CARD)
        await asyncio.sleep(1.0)
        cdp = await context.new_cdp_session(page)

        async def on_frame(params: dict) -> None:
            path = frames_dir / f"{len(frames):06d}.jpg"
            path.write_bytes(base64.b64decode(params["data"]))
            frames.append((params["metadata"]["timestamp"], path))
            offsets.append(time.time() - params["metadata"]["timestamp"])
            try:
                await cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
            except Exception:
                pass

        cdp.on("Page.screencastFrame", lambda params: asyncio.ensure_future(on_frame(params)))
        await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 94, "maxWidth": WIDTH, "maxHeight": HEIGHT})
        director = Director(page)
        try:
            await walkthrough(page, director)
        except Exception as error:
            if diagnostics_dir is not None:
                diagnostics_dir.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=diagnostics_dir / 'recording-error.png', full_page=True)
                state = await page.evaluate("""() => ({
                  business: document.querySelector('#business')?.value,
                  resultBusiness: document.querySelector('.cs-customers b')?.textContent,
                  resultTitle: document.querySelector('#result-title')?.textContent,
                  uiError: document.querySelector('.cs-error')?.textContent,
                })""")
                (diagnostics_dir / 'recording-error.json').write_text(json.dumps({
                    'error': str(error), 'state': state, 'completed_beats': director.beats,
                }, indent=2) + '\n')
            await cdp.send('Page.stopScreencast')
            await browser.close()
            raise
        stopped = time.time()
        await cdp.send("Page.stopScreencast")
        await asyncio.sleep(0.3)
        await browser.close()
    # Screencast stamps use the compositor clock; shift them onto time.time()
    # with the smallest observed delivery delay so beats line up with frames.
    offset = min(offsets)
    return [(stamp + offset, path) for stamp, path in frames], director.beats, stopped


def encode(frames: list[tuple[float, Path]], stopped: float, output: Path) -> float:
    """Resample the variable-rate screencast to constant 30 fps and encode it."""
    frames.sort()
    start = frames[0][0]
    stamps = [stamp for stamp, _ in frames]
    output.parent.mkdir(parents=True, exist_ok=True)
    encoder = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "image2pipe", "-framerate", "30", "-c:v", "mjpeg", "-i", "-",
         # Screencast JPEGs are full range; browsers and the renderer expect limited-range BT.709.
         "-vf", f"scale={WIDTH}:{HEIGHT}:flags=lanczos:in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p",
         "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         "-c:v", "libx264", "-preset", "slow", "-crf", "14", "-r", "30", "-movflags", "+faststart", str(output)],
        stdin=subprocess.PIPE,
    )
    assert encoder.stdin
    for index in range(int((stopped - start) * 30)):
        shown = max(bisect.bisect_right(stamps, start + index / 30) - 1, 0)
        encoder.stdin.write(frames[shown][1].read_bytes())
    encoder.stdin.close()
    if encoder.wait():
        raise SystemExit("FFmpeg could not encode the rehearsal.")
    return start


def assembly_manifest(source: Path, destination: Path, beats: list[dict], duration: float) -> dict:
    """Select the measured walkthrough scenes without shortening live waits."""
    # FFmpeg receives a whole number of 30 fps frames. Round down so the final
    # scene never extends beyond that encoded source, including sub-frame tails.
    source_end = math.floor(math.floor(duration * 30) / 30 * 100) / 100
    scenes = []
    for beat in beats:
        start = max(0, beat["start_seconds"])
        end = min(source_end, beat["end_seconds"])
        if end > start:
            scenes.append({"start_seconds": start, "end_seconds": end, "label": beat["label"]})
    selected_duration = sum(scene["end_seconds"] - scene["start_seconds"] for scene in scenes)
    if not scenes:
        raise ValueError("The recording did not contain any measured scenes.")
    if selected_duration > 120:
        raise ValueError(
            f"The genuine walkthrough is {selected_duration:.1f} seconds, over the 120-second "
            "ceiling. Shorten the presentation and record again; live waits were preserved."
        )
    return {
        "source_path": os.path.relpath(source.resolve(), destination.resolve().parent),
        "max_duration_seconds": 120,
        "scenes": scenes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:3000", help="Corner Scout web app to record.")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "rehearsal.mp4")
    parser.add_argument(
        "--manifest", type=Path,
        help="Measured assembly manifest; defaults to OUTPUT-plan.json beside the recording.",
    )
    parser.add_argument("--chromium", help="Chromium executable, if Playwright's bundled browser is missing.")
    args = parser.parse_args()
    if not shutil.which("ffmpeg"):
        parser.exit(1, "FFmpeg is required.\n")
    with tempfile.TemporaryDirectory(prefix="corner-scout-frames-") as temporary:
        frames, beats, stopped = asyncio.run(record(args.url, Path(temporary), args.chromium, args.output.parent))
        if not frames:
            parser.exit(1, "No frames were captured.\n")
        start = encode(frames, stopped, args.output)
    measured = [
        {"label": beat["label"], "start_seconds": round(max(beat["start"] - start, 0), 2),
         "end_seconds": round(beat["end"] - start, 2)}
        for beat in beats
    ]
    timing = args.output.with_suffix(".json")
    duration = stopped - start
    timing.write_text(json.dumps({
        "source_path": args.output.name, "frames": len(frames),
        "duration_seconds": round(duration, 2), "beats": measured,
    }, indent=2) + "\n")
    destination = args.manifest or args.output.with_name(f"{args.output.stem}-plan.json")
    try:
        manifest = assembly_manifest(args.output, destination, measured, duration)
    except ValueError as error:
        parser.exit(1, f"{error}\nRecording and measured beats remain at {args.output} and {timing}.\n")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({
        "video": str(args.output), "manifest": str(destination),
        "duration_seconds": round(duration, 2), "beats": measured,
    }, indent=2))


if __name__ == "__main__":
    main()
