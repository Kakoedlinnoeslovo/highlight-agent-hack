# Demo script — same streets, different stories

This optional conversational-editor walkthrough complements the [current business-report jury example](../README.md#a-60-second-jury-walkthrough).

Use the same genuine rehearsal for both cuts. Measure actual scene intervals before rendering; the examples contain illustrative windows. First round: **two minutes**. Top-five final: **three minutes at 6 PM BST**. Submission closes **4:30 PM BST on 17 October 2026**.

## Two-minute core

| Time | Screen/action | Suggested narration |
| --- | --- | --- |
| 0:00–0:08 | Strong genuine generated teaser | “Same streets. Two different stories.” |
| 0:08–0:18 | Imported SF Streets recording, actual duration, index status | “This is the organizer's SF Streets dataset. We reuse its existing footage and captions.” |
| 0:18–0:40 | Submit people brief; show real progress and windows | “Show me pedestrians and cyclists.” |
| 0:40–0:55 | People crossing and cycling | Let the action play. |
| 0:55–1:20 | Traffic brief, changed timeline, buses and vehicles | “Keep the source. Change the brief. Show me buses and street traffic.” |
| 1:20–1:35 | Open original timestamp; optional revision/undo | “Every selection links to the supplied footage.” Optional: “Remove clip two and put the highest-ranked moment first.” |
| 1:35–1:52 | Actual trace, model evidence, measured elapsed time | “VAST retrieves the indexed footage, Cosmos supplied these visual captions, and W&B translates and ranks the brief. This is the actual run.” |
| 1:52–2:00 | Download; show both outputs | “Same streets, different stories. The highlights—and the assembly of this demo—were made with our tool.” |

Show exact measured source duration and request time. Use a fresh request only if latency fits its slot. Label saved runs **Previous run · same source**. Disclose shortened waits and show actual elapsed time. If live integrations are unverified, describe intended roles and show the clearly labeled local sample instead of reading sponsor-execution narration.

Before recording, use **Browse SF Streets**, select a camera stream, then **Use dataset video**. Import downloads exact original objects for up to six contiguous chunks, prepares each indexed `[0, chunk_duration_sec]` window with the shared FFmpeg renderer as H.264, 720p, 30 fps, and concatenates the excerpts. This excludes uncaptioned trailing frames in the original HEVC files and makes the assembled source browser-playable. Six indexed 30-second windows provide about 180 seconds. Prepared excerpt durations determine the cumulative offsets; original source URIs, durations, and SHA-256 hashes preserve provenance. Dataset import reuses existing captions without uploading or reindexing footage. The source player uses the assembled recording's continuous indexed timeline, while each remote caption starts on its own chunk's timeline; the prepared excerpt offsets connect them.

## Three-minute final

Retain the core scenes. Add 30 seconds for a complete compilation, 15 seconds for a second original moment, and 15 seconds for human evaluation/implementation. Place additions before the trace and closing, as in `examples/demo-plan-extended.json`.

Show actual review of two people-focused moments, two traffic-focused moments, and two ordinary street moments, using `examples/evaluation-sf-streets.json` with the `sf-streets` profile and `people`, `traffic`, and `ordinary` labels. Report counts only after human review. Explain that application code validates the exact source intervals, shifts chunk-relative captions onto the assembled timeline, and preserves setup/aftermath; caption ranking is not proof of a fresh video rewatch.

## Recording and submission checks

- Fixed 16:9 capture; text readable at 720p. Close personal tabs and hide keys, workshop passwords, and login flows.
- Keep original rehearsal, genuine reels, manifests, and available traces offline. Record human narration against each cut. Follow the [toolchain guide](../demo-video-toolchain-setup.md).
- Watch both exports end to end, with sound and muted. Check duration, evidence, boundaries, labels, readability, audio, and browser playback.
- Verify the upload link without your login. Confirm recording requirements and list all team members. Target submission by **4:15 PM BST** for a buffer.

Only claim demo assembly after the product renderer has assembled the real rehearsal. Manual presentation scene selection is sufficient; automatic semantic selection from screen recordings is deferred.
