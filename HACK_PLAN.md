# Highlight Agent — build and demo plan

Real-Time Video Agents Hack · London · 17 October 2026

## Requirements and success criteria

The workshop presentation establishes a **4:30 PM BST submission deadline**, **two-minute first round**, and **three-minute top-five final at 6 PM**. List all team members. Confirm recording requirements in the submission form before uploading. Judging covers idea, intuitive design, technical quality, reproducibility, documented inputs/outputs and evaluation, and effective use of at least three sponsor tools. Demonstrate working software before explaining slides. Workshop photographs stay local because they include access details.

Turn one gameplay recording into contrasting compilations: **“Make me look like a pro”** finds clean landings followed by continued driving; **“Now roast my driving”** finds crashes, rollovers, and failed landings. Both reuse one index. Changed timestamps, original evidence, and downloadable reels make the difference visible.

Default footage: about ten minutes of permitted continuous GTA V driving/stunts, daylight and third-person, with at least four clear examples of each event and ordinary driving between them. Footage and sponsor access are not bundled.

## Implementation

One Python/Gradio application coordinates hosted VAST APIs, Cosmos ingestion captions, W&B Inference query/ranking, Weave traces, and FFmpeg output. Inspect the assigned API schema first; the [upstream backend](https://github.com/vast-data/vss-blueprint/blob/main/source-code/retrieval/video-backend/README.md) is a reference, not proof of the workshop contract.

- **Register/index:** preserve exact local MP4, duration, VAST source, and status. Describe vehicle motion, impact, orientation, landing, and aftermath; ignore HUD text. Confirm the deployed Cosmos model from execution evidence. Distinguish configured model hints from actual reported segment IDs.
- **Search/rank:** produce one concrete visual query, retrieve up to 16 same-source hits, and allow one alternate query when fewer than three useful events remain. Deduplicate and rank known IDs using captions. Retry malformed model JSON once; show a clear failure if still invalid.
- **Select:** application code controls timestamps. Add two seconds before and three after an event, clamp to source, merge overlaps, and fit up to four complete events in 30 seconds. Put the strongest last. Return a shorter reel when necessary. Preserve enough aftermath to establish clean landings; exclude ambiguous outcomes.
- **Render:** one `render_edit_plan(plan)` for product and demo. Normalize to 720p, 30 fps, H.264/AAC, `yuv420p`, `+faststart`; supply silence for sources without audio. Save manifests and measured timings.
- **Present:** result player, two brief cards, actual source duration/status, source timeline, original previews, progress/elapsed time, traces, and download. Reveal changed selections when the brief changes.

Shared interfaces: `VideoRecord`, `Moment`, `EditPlan`, `make_highlight(video_id, prompt, max_duration=30)`, and `render_edit_plan(plan)`. Models select IDs, not timestamps. Label caption-based evidence accurately. Direct Cosmos rewatching waits until video inference access is proven.

After both briefs work, add **“Remove clip two and put the highest-ranked crash first.”** Preserve source windows and the preceding plan for undo. Defer revision if the core pipeline is not working by 2 PM. Defer vertical framing, slow motion, audio-event search, streaming ingestion, and public hosting.

## Access and execution

Start through Cosmos Community → workshop instructions → Builders Challenge → assigned team VM → desktop. Keep workshop passwords, personal API keys, and Wi-Fi credentials out of docs, recordings, and artifacts.

| Owner | Responsibility |
| --- | --- |
| Builder A | VM, sponsor adapters, ingestion, model confirmation, timestamp mapping |
| Builder B | Ranking, intervals, shared renderer, bounded revision |
| Builder C | Footage, Gradio experience, recording, docs, submission |

First 20 minutes: prove an accessible index, matching local media, captions, source-relative intervals, and one actual W&B call with a Weave trace. Test two crashes, two clean landings, and two ordinary-driving negatives before full ingestion. Require five of six correct captions, both event types recognized, and no crash labeled clean. This is a human review gate.

| Original checkpoint, BST | Required result |
| --- | --- |
| 12:30 PM | Real search → playable compilation |
| 1:30 PM | Both briefs produce different edits from one index |
| 2:30 PM | Source evidence, timeline changes, traces, and evaluation |
| 3:15 PM | Feature freeze |
| 3:45 PM | Two-minute recording secured |
| 4:00 PM | Both exports, README, accessible repo, shareable video |
| 4:15 PM | Submit with all team members |
| After submission | Rehearse three-minute final for 6 PM |

If full ingestion is slow, build against the completely indexed short sample. Organizer footage is useful only with its exact matching media/timestamps. Explicit offline sample mode validates local plumbing and cannot substitute for sponsor execution.

## Demo production

Use the [two-minute script and final extension](docs/DEMO_SCRIPT.md). Run fresh requests only when measured latency fits the presentation slot. Otherwise label genuine saved/recorded runs and disclose shortened waits. Keep real outputs, manifests, and recordings offline.

Record a browser rehearsal at 16:9, manually choose scenes, and save intervals in separate demo manifests. Assemble both cuts with the product renderer, add human narration, and mix quieter original audio. Replace illustrative example windows with actual recording times. Capture title/end cards in the browser; extra caption/effect tooling waits until the primary export is secured.

After the actual rehearsal is assembled this way, say: **“The highlights—and the assembly of this demo—were made with our tool.”** Automatic semantic selection from screen recordings requires separate validation and is deferred.

## Acceptance and delivery

- Both briefs select different outcomes from one index. Verify a crash and clean landing in the local source at returned timestamps.
- Windows preserve setup/aftermath, do not duplicate/overlap, and fit the budget. Test missing audio, endpoints, segment boundaries, empty results, malformed output, and timeouts.
- Real traces show each sponsor contribution. Record actual model IDs and label configured hints. Use generated artifacts for indexing/request/render times rather than invented estimates.
- Both demo exports play correctly within two/three minutes and remain readable/audible. Verify the upload link without the team’s login.
- A fresh checkout documents setup, source preparation, input/output formats, tests, and repeatable human evaluation. Add measured results when live access is demonstrated.
- Submit the accessible repo, shareable video, build/tool description, and all team names/contact emails before 4:30 PM BST.

## Connection evidence

VAST authentication/configuration, a real W&B inference request, and remote Weave trace delivery/read-back now pass. The W&B project must belong to an accessible workspace; an inaccessible project returned HTTP 401 even with a valid key. The verified setup/inference/trace check took 4.93 seconds. Gameplay ingestion, caption execution, both briefs, and full reel latency need their own checks; see [validation results](docs/VALIDATION.md).
