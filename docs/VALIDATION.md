# Implementation validation — 17 October 2026

## Current PR verification

- `uv run pytest -q`: **225 passed in 9.74 seconds**. Ruff passed, and `uv build` produced the source distribution and wheel for **0.1.0**.
- Browser checks of the business report regenerated from saved artifacts confirmed all four tabs, the bike-shop and café winners, the transit-kiosk tie, and the dog-shop abstention. No page overflow or console errors were observed. This reused saved results rather than making new provider requests.
- Added regression checks cover recovery of registered dataset media, invalid or failed vision observations and cache retries, report count wording, tied rankings, and complete provider failure.

Human review of captions and vision estimates, the submission recording, and submission itself remain pending. Automated checks and saved-report playback do not complete those steps.

## Current SF Streets workflow

The application now uses the supplied public SF Streets dataset. The live picker excludes earlier gameplay recordings, and the former YouTube downloader exits without downloading or uploading anything.

- Live discovery returned 122 indexed chunks across five SF street cameras. Camera 5's first six chunks were imported without uploading or reindexing.
- Exact original objects were downloaded and fingerprinted. Their indexed 30-second windows were prepared with the shared renderer, excluding uncaptioned encoder tails. The registered timeline is 180 seconds; the assembled MP4 probes at 180.021 seconds, within one frame of that timeline.
- All 36 indexed caption segments passed source-boundary, ordering, and coverage checks. Returned rows report `nvidia/cosmos3-nano-reasoner`.
- “Show me pedestrians and cyclists.” produced three windows at 23–33, 33–43, and 43–53 seconds: a playable 30-second MP4 in 23.427 seconds, including 2.045 seconds rendering.
- “Show me buses and street traffic.” produced different windows at 0–8, 18–28, and 8–18 seconds: a playable 28-second MP4 in 18.634 seconds, including 1.931 seconds rendering. This run completed through the browser UI.
- The browser revision removed clip two, moved the highest-ranked remaining moment first, and rendered an 18-second result with windows 8–18 and 0–8 seconds.
- Browser undo restored the original 28-second traffic reel. Selecting its first evidence row loaded a playable eight-second source preview; both video players reported ready state 4 with no media errors. The 390-pixel mobile viewport had no page overflow.
- At this earlier SF Streets checkpoint, **192 tests** and Ruff passed. Those checks cover dataset browsing/downloads, chunk provenance and timestamp mapping, actual normalized assembly/rendering, omitted tails, partial failures, separate street selections, and stale sessions accessing legacy sources.

Local artifacts are `outputs/validation/sf-import.json`, `sf-live-report.json`, `sf-people.json`, and `sf-traffic.json`, with per-edit MP4s, plans, evidence, and actual Weave trace links in `outputs/<edit_id>/`. These artifacts and all footage remain ignored by Git.

The six-case human caption-quality evaluation remains unreviewed. `examples/evaluation-sf-streets.json` is an illustrative template, not a passing quality result. The earlier connection and synthetic checks below are historical evidence.

## Verified locally

- Automated tests exercise real FFmpeg exports and mocked provider HTTP contracts, selection, source integrity, persistence, revisions/undo, model-output repair, human evaluation, and CLI failures.
- Browser checks cover the original source player, both creative briefs, changed selections, source evidence, revision, undo, file registration, source switching, missing configuration, and desktop/mobile layouts.
- A 60-second synthetic recording produced two different 20-second reels; removing one clip produced a 10-second revision, and undo restored the original edit.
- The recorded browser walkthrough was assembled through `highlight-agent demo`, using the same renderer as the compilations. The short validation video is a local sample, not the submission recording.

The local environment used Python 3.13.12, Gradio 6.30.0, and FFmpeg 9.0.2. Repeat automated checks with:

```sh
uv sync --extra dev
uv run ruff check .
uv run pytest -q
```

Generated screenshots, the browser report, and the assembled walkthrough are under `outputs/validation/` on this workspace and are excluded from Git.

## Earlier PR verification

The earlier local suite passed **122 tests**, with Ruff checks and both source/wheel builds passing. An isolated offline smoke check constructed the UI, rendered different Pro/Roast reels, and exercised revision and undo.

Pre-landing regression checks cover upload source hashes, recovery of registered media, feasible overlapping selections, bounded alternate search, saved timing provenance, delayed audio/video, and video bounds independent of audio tails. Live gameplay validation remains separate from these local checks.

## Verified live connections

The assigned VAST backend accepted the configured credentials and returned authenticated configuration, an upload bucket, and the configured synthesis model `nvidia/cosmos3-nano-reasoner`. Its `/openapi.json` route returned non-JSON content; this connection check does not establish the deployed ingestion/search schema or prove Cosmos caption execution.

A real W&B inference request returned the validated JSON result `{"ok": true}`. Its Weave call was flushed and read back from the remote service with a completed timestamp. Workspace initialization, inference, and trace verification together took 4.93 seconds; this is not a highlight-generation latency measurement. The credential-free local report and real trace URL are saved at `outputs/validation/live-connection-check.json`.

The initial HTTP 401 came from an inaccessible `WANDB_PROJECT`, despite a valid API key. Switching to the authenticated account's default workspace fixed both inference and Weave. API keys remain in the ignored local `.env`.

A subsequent live source-index check found nine of ten gameplay recordings ready with all 24 declared segments and continuous coverage of their 120-second local sources. Fortnite and both GTA V recordings passed; Modern Warfare 4 had 23 of 24 segments at that check. Returned caption rows report `nvidia/cosmos3-nano-reasoner`. The local snapshot is `outputs/validation/index-status.json`. The remaining rows arrived during investigation; no worker restart or deployment change was made.

## Historical gameplay validation boundary

The index check does not verify live search/ranking, gameplay caption quality, or complete highlight latency. Reported caption model IDs are recorded evidence; provider unit tests exercise HTTP contracts and do not count as sponsor execution evidence.

Configure `.env`, run `highlight-agent doctor --remote`, register the exact source, upload or attach its index, and refresh until ready. Replace the placeholder video ID and intervals in `examples/evaluation.json` with six actual gameplay examples, then review them against the indexed captions; do not treat synthetic panels as gameplay-quality evidence. Record the genuine two-/three-minute presentation only after the live flow works.
