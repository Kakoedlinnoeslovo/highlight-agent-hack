# Demo video toolchain — Corner Scout

How the [82-second Corner Scout demo](https://drive.google.com/file/d/1zZgiQSG6cm3ZWqlijK4rIugzymr1lPrq/view?usp=sharing) was made, and how to record a new take. Every take is genuine footage of the web app with real searches, assembled by **the same FFmpeg renderer as Highlight Agent**. The presentation script is the [demo plan](docs/DEMO_PLAN.md).

## Record, assemble, check

1. **Run the hosted app locally** with W&B settings in the root `.env`:

   ```sh
   cd web && npm run dev
   ```

2. **Record a take.** [`scripts/record_demo.py`](scripts/record_demo.py) drives the app in headless Chromium at 1280×720, captures every painted frame, and writes `data/rehearsal.mp4`, measured beats in `data/rehearsal.json`, and an assembly plan in `data/rehearsal-plan.json`. Each search calls the live `/api/profile` endpoint, so on-screen timings are real. Captions, the pointer, and title cards are in-browser overlays. Pass `--chromium PATH` if Playwright's bundled browser is missing.

   ```sh
   uv run python scripts/record_demo.py
   ```

   The current script records the 108-second [frame-proof walkthrough](docs/PROOF_REVEAL.md). The published 82-second cut came from an earlier, shorter version; its measured plan is [`examples/demo-plan.json`](examples/demo-plan.json). A manual capture with macOS **Cmd+Shift+5** also works: save it as `data/rehearsal.mp4` and write the plan by hand.

3. **Assemble with the product renderer:**

   ```sh
   uv run highlight-agent demo data/rehearsal-plan.json --output-dir outputs/demo
   ```

4. **Optional narration.** Add `"narration_path": "narration.wav"` (relative to the plan) and rerender. The renderer mixes the source audio at 18 percent under it. For the published cut, an AI presenter was composited onto the rehearsal (a Nano Banana Pro portrait, voiced and lip-synced with Veo 3.1), and her lines plus a synthesized music bed went in as the narration. Disclose any generated presenter or voice.
5. **Watch the export** with sound and muted. Check readable text, real timings, complete clips, audible voice, and duration. Keep footage, renders, and plans in the ignored `data/` and `outputs/` folders.

Output: **1280×720, 30 fps, H.264/AAC, `yuv420p`, `+faststart`**. The 82-second cut is 6.5 MB. Confirm recording and link requirements in the submission form, and open the shared link signed out.

## Plan contract

```json
{
  "source_path": "../data/rehearsal.mp4",
  "max_duration_seconds": 120,
  "scenes": [
    {"start_seconds": 0.2, "end_seconds": 4.81, "label": "Title: where should I open my shop?"},
    {"start_seconds": 4.81, "end_seconds": 9.41, "label": "Home page and scouting sample"}
  ]
}
```

Scenes play in listed order and may jump around the recording. Each interval must fit the actual source, and the total must fit the budget (at most 180 seconds). Paths resolve relative to the plan. Use measured beats; do not fabricate or stretch progress.

After the renderer has assembled a real take, you can say: **“This demo was assembled with our own renderer.”**

## Optional tools

- **Burned captions:** the minimal FFmpeg build has neither `subtitles` nor `drawtext`, which is why captions are browser overlays. `ffmpeg-full` provides both but is keg-only. [Homebrew formula](https://formulae.brew.sh/formula/ffmpeg-full)
- **Synthetic narration:** ElevenLabs’ `with-timestamps` endpoint returns character alignment; group characters into phrases before making subtitle cues. [ElevenLabs reference](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps)
- **Advanced composition:** Remotion, HyperFrames, and automatic zooms add setup cost; secure the primary export first.
