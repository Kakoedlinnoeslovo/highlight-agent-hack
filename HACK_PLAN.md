# Highlight Agent — build and demo plan

Real-Time Video Agents Hack · London · 17 October 2026

The current jury walkthrough is the [business-location example in the README](README.md#a-60-second-jury-walkthrough). This build plan also retains the conversational editor's implementation milestones and presentation checks.

## Requirements and success criteria

The workshop presentation establishes a **4:30 PM BST submission deadline**, **two-minute first round**, and **three-minute top-five final at 6 PM**. List all team members. Confirm recording requirements in the submission form before uploading. Judging covers idea, intuitive design, technical quality, reproducibility, documented inputs/outputs and evaluation, and effective use of at least three sponsor tools. Demonstrate working software before explaining slides. Workshop photographs stay local because they include access details.

Turn the supplied SF Streets footage into contrasting compilations: **“Show me pedestrians and cyclists.”** finds people walking, crossing, and riding bicycles; **“Show me buses and street traffic.”** finds buses and vehicles moving through intersections. Both reuse the organizer's existing index. Changed timestamps, original evidence, and downloadable reels make the difference visible.

Default footage: up to six contiguous chunks from one supplied SF Streets camera stream. Download exact original objects, normalize their indexed windows, and assemble those excerpts into a local recording. Six indexed 30-second windows provide about three minutes; measure the prepared excerpts and preserve original source provenance. Use only the organizer's supplied datasets. Footage and sponsor access are not bundled.

## Implementation

One Python/Gradio application coordinates hosted VAST APIs, Cosmos ingestion captions, W&B Inference query/ranking, Weave traces, and FFmpeg output. Inspect the assigned API schema first; the [upstream backend](https://github.com/vast-data/vss-blueprint/blob/main/source-code/retrieval/video-backend/README.md) is a reference, not proof of the workshop contract.

- **Import/index:** browse supplied SF Streets camera streams and download the exact original objects for up to six contiguous source chunks. Use the shared FFmpeg renderer to prepare each indexed `[0, chunk_duration_sec]` window as H.264, 720p, 30 fps, excluding uncaptioned trailing frames in the original HEVC media; concatenate the prepared excerpts. Reuse existing captions without uploads or reindexing. Preserve each exact `original_video` URI, original `source_duration` and `source_sha256`, and prepared excerpt's measured `duration` and cumulative `start_seconds`. Confirm the deployed Cosmos model from returned segment rows; configured model hints are not execution evidence.
- **Search/rank:** produce one concrete visual query, retrieve exact-source hits for the imported chunks, and allow one alternate query when fewer than three useful events remain. Validate each remote interval within its indexed source window, then shift it by the prepared excerpt's cumulative start in the assembled local recording. Deduplicate and rank known IDs using captions. Retry malformed model JSON once; show a clear failure if still invalid.
- **Select:** application code controls timestamps. Add two seconds before and three after an event, clamp to the assembled recording, skip scenes overlapping higher-ranked selections, and fit up to four complete events in 30 seconds. Put the strongest last. Return a shorter reel when necessary. Preserve enough context to see the crossing or vehicle movement; exclude ambiguous actions.
- **Render:** one `render_edit_plan(plan)` for product and demo. Normalize to 720p, 30 fps, H.264/AAC, `yuv420p`, `+faststart`; supply silence for sources without audio. Save manifests and measured timings.
- **Present:** result player, two brief cards, actual source duration/status, source timeline, original previews, progress/elapsed time, traces, and download. Reveal changed selections when the brief changes.

Shared interfaces: `VideoRecord`, `Moment`, `EditPlan`, `make_highlight(video_id, prompt, max_duration=30)`, and `render_edit_plan(plan)`. Models select IDs, not timestamps. Label caption-based evidence accurately. Direct Cosmos rewatching waits until video inference access is proven.

Revision: **“Remove clip two and put the highest-ranked moment first.”** Preserve source windows and the preceding plan for undo. Defer vertical framing, slow motion, audio-event search, streaming ingestion, and public hosting. Keep the current output at 16:9, 720p.

## Access and execution

Start through Cosmos Community → workshop instructions → Builders Challenge → assigned team VM → desktop. Keep workshop passwords, personal API keys, and Wi-Fi credentials out of docs, recordings, and artifacts.

| Owner | Responsibility |
| --- | --- |
| Builder A | VM, sponsor adapters, supplied dataset import, model confirmation, timestamp mapping |
| Builder B | Ranking, intervals, shared renderer, bounded revision |
| Builder C | Dataset selection, Gradio experience, recording, docs, submission |

Prove an accessible supplied index, matching downloaded media, caption alignment across chunk joins, and one actual W&B call with a Weave trace. Review two people-focused moments, two traffic-focused moments, and two ordinary street moments using the `sf-streets` profile in `examples/evaluation-sf-streets.json` (`people`, `traffic`, and `ordinary` labels). Require five of six correct captions and both positive categories recognized. This is a human review gate; illustrative or unreviewed cases do not establish quality.

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

Use the organizer's existing captions and the corresponding indexed visual windows from its supplied media. The CLI lists camera entries with `highlight-agent datasets` and imports a selected source with `highlight-agent import-dataset SOURCE_URI`; the UI uses **Browse SF Streets** and **Use dataset video**. Explicit offline sample mode validates local plumbing with legacy synthetic fixtures and cannot substitute for SF Streets or sponsor-execution evidence.

## Demo production

Use the [two-minute script and final extension](docs/DEMO_SCRIPT.md). Run fresh requests only when measured latency fits the presentation slot. Otherwise label genuine saved/recorded runs and disclose shortened waits. Keep real outputs, manifests, and recordings offline.

Record a browser rehearsal at 16:9, manually choose scenes, and save intervals in separate demo manifests. Assemble both cuts with the product renderer, add human narration, and mix quieter original audio. Replace illustrative example windows with actual recording times. Capture title/end cards in the browser; extra caption/effect tooling waits until the primary export is secured.

After the actual rehearsal is assembled this way, say: **“The highlights—and the assembly of this demo—were made with our tool.”** Automatic semantic selection from screen recordings requires separate validation and is deferred.

## Acceptance and delivery

- Both briefs select different visible actions from the same imported recording and existing index. Verify people/cyclists and buses/traffic in the local source at the returned timestamps.
- Windows preserve setup/aftermath, do not duplicate/overlap, and fit the budget. Test missing audio, endpoints, segment boundaries, empty results, malformed output, and timeouts.
- Real traces show each sponsor contribution. Record actual model IDs and label configured hints. Use generated artifacts for import/request/render times rather than invented estimates.
- Both demo exports play correctly within two/three minutes and remain readable/audible. Verify the upload link without the team’s login.
- A fresh checkout documents setup, supplied dataset import, source-chunk mapping, input/output formats, tests, and repeatable human evaluation. Add measured results when live access is demonstrated.
- Submit the accessible repo, shareable video, build/tool description, and all team names/contact emails before 4:30 PM BST.

## Connection evidence

VAST authentication/configuration, a real W&B inference request, and remote Weave trace delivery/read-back now pass. The W&B project must belong to an accessible workspace; an inaccessible project returned HTTP 401 even with a valid key. The verified setup/inference/trace check took 4.93 seconds. These connection checks do not establish the SF Streets end-to-end flow; see [validation results](docs/VALIDATION.md).

Read-only dataset inspection found 122 public SF Streets chunks across five camera streams. SF5 has 30 ordered chunks with 30-second indexed windows, and its first source is `s3://team-37-vss-chunks/team-37/20261006_123546_sf5_chunk_0000.mp4`. The media endpoint returned MP4 bytes, and caption rows reported `nvidia/cosmos3-nano-reasoner`. Subsequent checks of import, source alignment, both briefs, rendering, and latency are recorded in the [validation results](docs/VALIDATION.md). Human caption review and the submission recording remain pending.

### Historical index evidence

The following earlier gameplay index check is preserved as connection history. Those sources are outside the supplied SF Streets workflow and are not the current demo or evaluation dataset.

A subsequent live index check verified complete 24-segment coverage for nine of ten 120-second gameplay sources, including Fortnite and both GTA recordings. Their returned caption rows identify `nvidia/cosmos3-nano-reasoner`. One source remained at 23/24; the local snapshot is `outputs/validation/index-status.json`. Both briefs, caption quality, live search/ranking, and full reel latency still need validation.
