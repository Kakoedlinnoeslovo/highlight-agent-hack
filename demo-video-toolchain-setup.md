# Demo video toolchain — minimal macOS workflow

Make the two-minute first-round demo and three-minute final from genuine browser footage using **the same FFmpeg renderer as Highlight Agent**. Secure the primary recording before optional effects or captions.

## Capture, select, render

1. Rehearse the [script](docs/DEMO_SCRIPT.md) at a fixed 16:9 size. Show both briefs, changed timestamps, source evidence, and actual traces. Hide credentials and personal tabs.
2. Record with macOS **Cmd+Shift+5** or an existing recorder. Keep the original at `data/rehearsal.mp4`; convert another capture format to MP4 if needed.
3. Measure scene intervals and replace the illustrative windows in both `examples/demo-plan.json` and `examples/demo-plan-extended.json`. Paths resolve relative to the manifest: `../data/rehearsal.mp4` means the repository’s `data/` folder.
4. Assemble with the product renderer:

   ```sh
   uv run highlight-agent demo examples/demo-plan.json --output-dir outputs/demo
   uv run highlight-agent demo examples/demo-plan-extended.json --output-dir outputs/demo-final
   ```

5. Record human narration against each assembled cut. Add `"narration_path": "../data/narration.wav"` to its manifest and rerender. Use separate narration takes for each version. The renderer mixes quieter source audio beneath narration. Capture title/end cards in the browser as part of the rehearsal.
6. Watch each export with sound and muted. Check opening impact, readable text, complete events, truthful progress labels, audible voice, and duration. Keep original footage, final MP4s, and manifests offline.

Default output: **1280×720, 30 fps, H.264/AAC, `yuv420p`, `+faststart`**. Below 50 MB is a convenient sharing target, not a confirmed submission rule. Confirm recording/link requirements before upload. Keep media in ignored `data/` and `outputs/`; Git LFS is unnecessary for this workflow.

## Manifest contract

```json
{
  "source_path": "../data/rehearsal.mp4",
  "max_duration_seconds": 120,
  "scenes": [
    {"start_seconds": 0, "end_seconds": 8, "label": "Teaser"},
    {"start_seconds": 20, "end_seconds": 30, "label": "Original source"}
  ]
}
```

Scenes play in listed order and may jump around the recording. Each interval must fit the actual source. The selected total must fit the budget; do not fabricate or stretch progress. Optional narration follows the same relative-path rule. JSON examples are templates, not evidence that live runs already succeeded.

After the renderer has assembled the real rehearsal, say: **“The highlights—and the assembly of this demo—were made with our tool.”** Presentation scene selection is manual; semantic video search has not been validated for screen recordings.

## Optional tools, after the primary export

- **Burned captions:** inspect `ffmpeg -filters` for `subtitles` and `drawtext`. The active minimal installation may lack both. `ffmpeg-full` can provide extra filters but is keg-only; follow its printed path instructions and check the resulting binary. Its Whisper.cpp dependency does not install transcription model weights: download a model separately. [Homebrew formula](https://formulae.brew.sh/formula/ffmpeg-full)
- **Synthetic narration:** ElevenLabs’ `with-timestamps` endpoint returns character alignment. Group characters into words/phrases before generating readable SRT cues; it does not directly return ready-made word/subtitle timings. [ElevenLabs reference](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps)
- **Browser automation:** optional Playwright capture depends on the installed version. `recordVideo` belongs in `browser.newContext({recordVideo: {...}})`; close the context before consuming the recording. `page.screencast` requires a version that provides that API. Manual capture is the default. [Playwright recording](https://playwright.dev/docs/videos)
- **Advanced composition:** Remotion/HyperFrames, offline TTS downloads, VHS terminal animation, and automatic zooms add setup cost. Defer them until core exports and sponsor evidence are secured.
