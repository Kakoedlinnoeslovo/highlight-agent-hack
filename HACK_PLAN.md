# Corner Scout — build and demo plan

Real-Time Video Agents Hack · London · 17 October 2026

**Corner Scout answers “Where should I open my…?”** Type any business. It works out who the customers are, compares reviewed street recordings within the selected city for those people, vehicles, and animals, ranks the corners, and shows the video evidence behind the ranking. When the footage cannot support an answer, it says **“Not enough evidence.”** This is the product we demo, document, and submit. The [README](README.md) is the front door; the [demo plan](docs/DEMO_PLAN.md) is the presentation script.

**Current data:** all **55 recordings, 612 chunks, and 3,537 supplied captions** are searchable and playable. Audience scouting uses **937 valid observations / 78.05 minutes** from SF cameras 3/4/5 and three distinct NYC recordings. The full SF camera-1 and camera-2 recordings move and stay explorer-only. The 82-second demo and 108-second proof video document the earlier 15-minute sample; current examples are in the [full-collection walkthrough](docs/DEMO_PLAN.md#full-collection-walkthrough).

## Requirements and success criteria

The workshop presentation establishes a **4:30 PM BST submission deadline**, **two-minute first round**, and **three-minute top-five final at 6 PM**. List all team members. Confirm recording requirements in the submission form before uploading. Judging covers idea, intuitive design, technical quality, reproducibility, documented inputs/outputs and evaluation, and effective use of at least three sponsor tools. Demonstrate working software before explaining slides. Workshop photographs stay local because they include access details.

Corner Scout succeeds when:

- **Any business works.** Free text such as *dog grooming salon* or *luxury car detailing* produces core and helpful audiences, and an answer arrives in a few seconds.
- **Different businesses pick different corners** from the same footage, for reasons a viewer can check.
- **Every recommendation links to evidence:** the sampled frames that were counted and the surrounding source clip.
- **It abstains honestly.** A recommendation requires three core-audience frames, 180 analyzed seconds, and 90% valid coverage at the leading recording.
- **Judges can try it** at the [live demo](https://corner-scout-rouge.vercel.app) without an account.

Use only the organizer's supplied datasets. Footage and sponsor access are not bundled.

## Implementation

The original prototype used five SF camera streams and six indexed chunks each (three minutes per corner, 15 minutes total). The current collection imports every supplied recording and preserves stream identities; only the six motion-reviewed fixed-view recordings enter scouting. Inspect the assigned API schema first; the [upstream backend](https://github.com/vast-data/vss-blueprint/blob/main/source-code/retrieval/video-backend/README.md) is a reference, not proof of the workshop contract.

- **Import (VAST):** browse the supplied camera streams and download the exact original objects. The shared FFmpeg renderer prepares each indexed `[0, chunk_duration_sec]` window as H.264, 720p, 30 fps, excluding uncaptioned trailing frames, and concatenates the excerpts. Reuse the existing captions without uploads or reindexing. Preserve each `original_video` URI, `source_duration`, `source_sha256`, measured excerpt `duration`, and cumulative `start_seconds`.
- **Who passes (W&B Inference, vision):** send each eligible indexed segment's midpoint frame to Gemma 4: 937 valid observations in the full-recording release, versus 180 in the historical sample. It estimates counts for a fixed vocabulary: office workers, students and young adults, families with children, older adults, workers in uniform, premium, mid-range and budget cars, taxis, buses, delivery vehicles, motorcycles and scooters, cyclists, and dogs. Cache the observations; failed observations stay gaps, not zeros.
- **Business profile (W&B Inference, text):** turn the business description into one or two **core** audiences and up to three **helpful** ones from the same vocabulary. Audiences named in the text (“dog”, “luxury”, “student”, “kids”, “bike”) are always core.
- **Ranking (application code):** score duration-weighted positive audience presence relative to other recordings in the same city. Core audiences carry 75% of the match. Ignore categories present everywhere. Require three core-audience frames, 180 analyzed seconds, and 90% valid coverage before recommending.
- **Evidence:** the hosted app shows the matching sampled frames and plays the surrounding five-second clip; the local app renders evidence reels with the shared renderer and saves an `edit_plan.json`.
- **Hosting:** the web app on Vercel serves the caption catalog and scouting observations, with sampled images and full-recording previews in Vercel Blob. Each search makes a fresh W&B text call through `/api/profile`; the browser scores with the same rules.
- **Traces (W&B Weave):** local provider and application calls are traced when configured. The hosted app does not create traces.
- **Captions (NVIDIA Cosmos):** the dataset's existing Cosmos captions power the caption-based preset and city reports. They are also the comparison that shows why frames matter: in the historical camera-1 sample, captions mention a bicycle in 25 of 36 segments, while the frames show a parked kick scooter and zero cyclists.

Models estimate what is visible and which audiences matter; application code ranks the corners and controls every timestamp.

### Shared engine and secondary tools

Corner Scout runs on the **Highlight Agent** engine: `VideoRecord`, `Moment`, `EditPlan`, `make_highlight(video_id, prompt, max_duration=30)`, and `render_edit_plan(plan)`, with one renderer for product clips and the demo video. The same engine powers caption-based preset and city reports and a conversational highlight editor (custom briefs, revision, undo). These are documented in the [operating guide](docs/GUIDE.md) and are not part of the demo.

## Access and execution

Start through Cosmos Community → workshop instructions → Builders Challenge → assigned team VM → desktop. Keep workshop passwords, personal API keys, and Wi-Fi credentials out of docs, recordings, and artifacts.

**Evaluation.** Spot-check current positive and negative audience frames using `outputs/full-example-rankings.json`. The original sample's 13-frame plan covered three dog frames, five premium-car frames, and five young-adult frames; those counts belong to the historical videos. Extract each frame, confirm it by eye, and report only the reviewed agreement count. This human review gate is **pending**; saved runs and automated tests do not establish accuracy. The procedure is in the [guide](docs/GUIDE.md#prove-the-result).

| Checkpoint, BST | Required result |
| --- | --- |
| 3:15 PM | Feature freeze |
| 3:45 PM | Two-minute recording secured |
| 4:00 PM | README, accessible repository, shareable video |
| 4:15 PM | Submit with all team members |
| After submission | Rehearse the three-minute final for 6 PM |

## Demo production

Present the hosted app and follow the [demo plan](docs/DEMO_PLAN.md). Run fresh requests when measured latency fits the slot; otherwise label saved runs and disclose shortened waits.

The submitted video is the [82-second Corner Scout demo](https://drive.google.com/file/d/1zZgiQSG6cm3ZWqlijK4rIugzymr1lPrq/view?usp=sharing). An earlier version of [`scripts/record_demo.py`](scripts/record_demo.py) recorded real searches in the web app in one take and measured each beat ([`examples/demo-plan.json`](examples/demo-plan.json)); `highlight-agent demo` assembled the cut with the product renderer. The retained script targets the historical frame-proof walkthrough and requires the original 180-frame web snapshot and its retired clip assets to be restored. The presenter is AI-generated (Nano Banana Pro portrait, Veo 3.1 voice and lip-sync), and the music was synthesized; both are disclosed in the README. A separate [108-second frame-proof walkthrough](docs/PROOF_REVEAL.md) is a deep dive, not the submission video.

## Acceptance and delivery

- Current measured examples include SF dog grooming at camera 5 (10/180 core samples), NYC luxury watches at camera 2 (49/180), and NYC dog-grooming abstention (at most 2/180). The original video's camera-1 luxury win and toy-store abstention remain historical.
- Each recommendation's counts open the matching sampled frames, and each clip plays from its exact source.
- The live demo and the video link open without the team's login.
- `uv run pytest -q`, `uv run ruff check .`, and `npm test` in `web/` pass.
- A fresh checkout documents setup, dataset import, inputs and outputs, tests, and the human evaluation procedure.
- Submit the accessible repository, shareable video, build/tool description, and all team names and contact emails before 4:30 PM BST.

## Connection evidence

VAST authentication/configuration, a real W&B inference request, and remote Weave trace delivery/read-back pass. The W&B project must belong to an accessible workspace; an inaccessible project returned HTTP 401 even with a valid key. The verified setup/inference/trace check took 4.93 seconds.

Earlier read-only dataset inspection found 122 public SF Streets chunks across five camera streams. SF5 has 30 ordered chunks with 30-second indexed windows, and its first source is `s3://team-37-vss-chunks/team-37/20261006_123546_sf5_chunk_0000.mp4`. The media endpoint returned MP4 bytes, and caption rows reported `nvidia/cosmos3-nano-reasoner`. Measured imports, vision results, and hosted checks are in the [validation notes](docs/VALIDATION.md); earlier gameplay index checks are kept there as history.
