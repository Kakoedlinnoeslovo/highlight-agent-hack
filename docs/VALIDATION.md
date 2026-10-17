# Implementation validation — 17 October 2026

## Full collection expansion

Fresh authenticated discovery reconciled to the saved baseline: **55 independent recordings, 612 unique original chunks, 3,537 caption segments, and 17,676.313 indexed seconds**. The explorer searches all captions and displays separate expected/imported/playable coverage.

Browser checks on actual inventory found the expected segment counts for all seven collections: indoor 180, Nashville 180, neighborhood 307, New York 1,005, San Francisco 725, Toronto 1,080, and warehouse 60. Case-insensitive all-token search, pagination, filter resets, empty results, lazy video loading, exact source references, and a 375-pixel viewport passed. Actual compressed recordings played across indoor, highway, neighborhood, and NYC footage, including a shortened final interval ending at 709.717 seconds. City changes cancel pending profile requests and clear stale evidence.

The full importer preserves immutable source IDs, verifies source/index membership, resumes failed work, and keeps legacy edits. Export tests exercise incomplete coverage, changed media, reversed membership, invalid intervals, quota preflight, resumable uploads, atomic manifest replacement, and real FFmpeg preview encoding. Scoring tests cover duration weighting, shortened intervals, missing vision observations, coverage thresholds, city isolation, and Python/browser parity.

**Data preparation and Blob publication are complete.** Expected, imported, and playable totals all reconcile to **55 recordings / 612 chunks / 3,537 caption intervals**, with no missing sources or failures. The source union contains every supplied public chunk exactly once. Indexed source duration is 17,676.313 seconds; normalized recordings total 17,676.400 seconds because of frame rounding. The final cached import pass completed without downloading originals again.

All 55 compressed recordings independently passed H.264, 480p, 24 fps, measured-duration, and fast-start checks. Videos total **616,555,844 bytes**; the 937 sampled JPEGs total **64,685,004 bytes**. The complete Blob export contains **992 objects / 681,240,848 bytes**, below the 800 MB budget. No original download caches remain locally; originals remain in VAST and verified indexed recordings remain local. Upload credentials are local and are excluded from the hosted app's environment.

All eight street-camera candidates were visually reviewed at their first, middle, and last views. Six are eligible: SF cameras 3/4/5 and three NYC recordings across two camera IDs. SF cameras 1/2 move and remain explorer-only. Vision analysis completed **937/937 valid observations**, with zero failures, covering **4,683.149 seconds (78.05 minutes)**. Source hashes, model and prompt fingerprints, and all 12 real-profile Python/browser rankings were verified. The new measured examples are in the [demo walkthrough](DEMO_PLAN.md#current-measured-examples).

Final local browser checks played the last caption interval in every collection, including warehouse 5–10 seconds and Toronto 895–900 seconds, and verified exact seek/stop boundaries. The explorer displays complete coverage and all 3,537 captions. City isolation, delayed requests, 180-marker filmstrips, long-recording evidence, the retained detail crop, and 375-pixel layouts passed without console errors. Local evidence is saved in `outputs/full-collection-validation.json`, `outputs/full-example-rankings.json`, and `outputs/validation/explorer-local-qa.json`.

Final PR checks: **382 Python tests passed**, including browser proof and history regressions; **51 web tests passed**; Ruff and `git diff --check` passed. `uv build` produced the 0.1.0 source distribution and wheel.

Production is live at [Corner Scout](https://corner-scout-rouge.vercel.app) and the [full explorer](https://corner-scout-rouge.vercel.app/explorer.html). The initial full-collection Node.js 24 build contained **27 static files / 5,975,650 bytes**, with local manifests and credentials excluded. Both pages and both data manifests return HTTP 200; hosted manifests exactly match the verified build. The profile endpoint rejects unsupported methods and invalid input. Two real hosted profile calls succeeded in **2,240 ms** and **2,336 ms**: SF dog grooming recommends camera 5 with 10/180 positive samples; NYC dog grooming abstains with at most 2/180. Blob images, the retained crop, full-frame and enlargement controls, keyboard navigation, and a 375-pixel filmstrip passed without console errors. Detailed evidence is in `outputs/validation/hosted-health.json` and `outputs/validation/explorer-hosted-qa.json`.

Final production explorer checks confirmed complete coverage, the expected caption counts in all seven collection filters, 431 matches for the keyword query with 24 results per page, empty/reset handling, and mobile layout. Toronto's 895–900-second interval and the warehouse's 5–10-second interval played from HTTPS Blob URLs and stopped exactly at their ends. No deployment or browser console errors were observed.

The PR preparation pass reconciled the latest main-branch branding and proof documentation, required explicit source hashes for full-recording motion reviews, and fixed explorer playback after browser Back restores a cached document. The final merged-tree checks passed **382 Python tests**, **51 web tests**, Ruff, whitespace validation, and the source/wheel build. The real-browser history regression verifies that Back preserves filters, clears stale playback, and allows selecting footage again. Final review also added process-safe import checkpoint protection, full inventory refresh after single imports, recording-specific motion review keys, bounded aggregate frame-rounding allowances, and shared independent observations for ranking and proof. Duplicate, overlapping, and nested sample windows cannot inflate recommendation counts. All 12 measured profiles and all 937 prepared observations retain their previous results and order; targeted independent review found no remaining blockers.

The authenticated one-byte-range storage preflight measured **23,990,533,959 bytes of original media**. Keeping all originals alongside assembled recordings and previews exceeded available workspace storage by roughly **9 GB**. The user selected keeping originals in VAST and retaining verified indexed footage locally. Bulk import now releases original download caches only after verifying persisted assembled media, caption completeness, source metadata, and every cached source hash. The initial release reclaimed **6,346,862,094 bytes** across 190 verified chunks; remote originals and local provenance remain intact. Detailed local evidence is `outputs/full-storage-preflight.json`, `data/catalog/coverage.json`, and `outputs/collection/published.json`.

## Historical 15-minute SF demo verification

**Automated checks on `main` (17 October, commit `28f3681`):** `uv run pytest -q` passed **279 tests in 44.93 seconds**, `uv run ruff check .` passed, and `npm test` in `web/` passed **30 of 30**. The web tests cover profile validation, provider failures, deterministic scoring, published thresholds, sparse-evidence refusal, and clip references; [`PROOF_REVEAL.md`](PROOF_REVEAL.md) lists the browser checks for the frame proof.

**Historical snapshot.** At this checkpoint, `web/public/corners.json` held five corners with 36 sampled-frame observations each (180 in total, 180 seconds of footage per corner), produced by `google/gemma-4-31B-it` on W&B Inference. The vision pass took 2.7 minutes and is cached locally in `data/vision/`. The published `minFrames` (3) and core weight (0.75) match the Python app.

**Live site, 17 October around 15:30 BST.** Real searches on [corner-scout-rouge.vercel.app](https://corner-scout-rouge.vercel.app), each with a fresh W&B profile call:

| Business | Result | Core observation | Time shown |
| --- | --- | --- | --- |
| Dog grooming salon | Camera 5 · 81/100 | Dogs in 3/36 frames | 2.9 s |
| Luxury car detailing (typed) | Camera 1 · 85/100 | Premium cars in 28/36 frames | 2.1 s |
| Kids' toy store | Not enough evidence | Families with children in 1/36 frames | 2.5 s |

The saved runs in the [README results](../README.md#results-and-validation) reached the same winners; dog grooming scored 80 there because the model added different helpful audiences.

**Demo recording.** The submitted 82-second video is one continuous take of the web app with live profile calls (2.1, 1.9, and 2.5 seconds on screen), assembled by `highlight-agent demo` from measured beats in [`examples/demo-plan.json`](../examples/demo-plan.json). The presenter and music were added in post and are disclosed in the README.

**Pending:** the human spot-check of the 13 vision estimates behind the demo (3 dog, 5 premium-car, 5 young-adult frames). Automated checks and saved runs do not establish vision accuracy.

## Shared engine checks

- An earlier run of the suite passed 225 tests; Ruff passed, and `uv build` produced the source distribution and wheel for **0.1.0**.
- Browser checks of the business report regenerated from saved artifacts confirmed all four tabs, the bike-shop and café winners, the transit-kiosk tie, and the dog-shop abstention. No page overflow or console errors were observed. This reused saved results rather than making new provider requests.
- Added regression checks cover recovery of registered dataset media, invalid or failed vision observations and cache retries, report count wording, tied rankings, and complete provider failure.

Human review of captions and vision estimates, and the submission itself, remain pending; the submission recording is done. Automated checks and saved-report playback do not complete those steps.

<a id="current-sf-streets-workflow"></a>

## SF Streets import and highlight editor

The historical import below used the same shared engine as Corner Scout; the briefs, revision, and undo belong to the secondary highlight editor, which is not part of the demo. The application uses the supplied public SF Streets dataset. The live picker excludes earlier gameplay recordings, and the former YouTube downloader exits without downloading or uploading anything.

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
