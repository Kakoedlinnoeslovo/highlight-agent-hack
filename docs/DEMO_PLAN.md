# Demo plan — Corner Scout

Final plan · Real-Time Video Agents Hack · London · 17 October 2026
Submission closes **4:30 PM BST** (target **4:15 PM**). Target recording deadline: **3:45 PM**. First round: **2 minutes**. Top-five final: **3 minutes at 6 PM**.

Corner Scout leads the demo, matching the [README walkthrough](../README.md#a-2-minute-walkthrough). The caption-based [preset business report](../README.md#4-preset-business-report-caption-based) is the backup, and the city report is the three-minute finale. Frame estimates, rankings, and timings below come from saved runs on 17 October; rerun them after changing the vision cache or scoring code. The human spot-check and the submission recording remain pending.

## The pitch

> **"Opening a shop is a bet on one corner. Type any business — Corner Scout watches the street footage, sees who actually passes, and ranks every corner with video evidence."**

- **Who it's for:** a small-business owner choosing where to sign a lease.
- **The problem:** owners guess, or pay for foot-traffic data that says how many people pass but not who.
- **What we show:** any business type in, a ranked corner out in about 4 seconds, backed by clips that show the people, cars, and dogs that drove the ranking.

## What the system does

| Step | Who does it | Detail |
| --- | --- | --- |
| Footage | VAST | Organizer's SF Streets dataset: 5 corners, first 3 minutes each (15 min total). |
| Who passes | Gemma 4 vision on W&B Inference | One frame per 5-second segment, 180 frames. Counts people types (office workers, students, families, older adults, workers in uniform), car price tiers, taxis, buses, delivery vehicles, cyclists, dogs. Cached in `data/vision/`. |
| Business → audience | Llama 3.3 on W&B Inference | Free text becomes one or two **core** audiences plus **helpful** ones. An audience named in the text ("dog", "luxury", "student", "kids", "bike"…) is always core. |
| Ranking | Code | Each audience scored by how often it appears at a corner, relative to the best corner. Core audiences are 75% of the match. Audiences present everywhere (ordinary adults, mid-range cars) are ignored. Fewer than 3 core-audience frames at the best corner → **"Not enough evidence"**. |
| Evidence | Shared renderer | Strongest matching frames at the top two corners cut into a reel; each clip names what was counted and its exact source chunk. |
| Trace | Weave | When configured and available, a run's trace link appears on the result. |

The text model chooses audience weights; code ranks corners using the vision model's frame estimates. Existing Cosmos captions separately power the city report (finale).

## Corners

| Corner | Landmark (from city report) | Recorded | What the vision pass saw most (frames of 36) |
| --- | --- | --- | --- |
| Camera 1 | Tall buildings, downtown | 1 Oct, 09:41 | Premium cars 28 · delivery vehicles 24 · motorcycles 16 · office workers 6 |
| Camera 2 | Fidelity building | 1 Oct, 09:51 | Buses 17 · delivery vehicles 15 · office workers 12 · young adults 10 |
| Camera 3 | Bank of America at Stockton | 1 Oct, 10:04 | Taxis 9 · cyclists 8 · motorcycles 8 |
| Camera 4 | Transamerica Pyramid in view | 1 Oct, 10:07 | Buses 16 · premium cars 10 · motorcycles 10 |
| Camera 5 | Condor sign | 6 Oct, 12:35 | Premium cars 20 · cyclists 13 · motorcycles 7 · **dogs 3** (Camera 4 has 1; no other corner has any) |

No tourists and no older adults were detected anywhere.

## What to click

All verified twice with identical results (1.4–5.7 s each).

| Business | How | Core audience | Result | Use it for |
| --- | --- | --- | --- | --- |
| **Dog grooming salon** | Example button | Dogs | **Camera 5**, 80/100 vs 25 | Opening: clear winner, the dog in the evidence reel |
| **Luxury car detailing** | Type it | Premium cars | **Camera 1**, 85/100 vs 62 | Proves free text works; different corner |
| **Student bookshop** | Type it | Young adults | **Camera 2**, 92/100 vs 33 | Backup typed example |
| **Kids' toy store** | Example button | Families | **Not enough evidence** (one stroller) | Honesty beat |
| Luxury watch boutique | Example button | Premium cars | Camera 1, 79/100 | Backup |
| Bike repair shop | Example button | Cyclists | Camera 5, 83/100 | Backup |

**Avoid on stage:** businesses that name no audience ("vegan bakery", "restaurant"). The model picks their audiences and its picks vary between runs ("vegan bakery" scored 89 then 63). If a judge suggests one, run it and say the chips show which audiences the model chose.

## Two-minute script (first round)

| Time | Screen | Say |
| --- | --- | --- |
| 0:00–0:12 | Corner Scout home page | "Opening a shop is a bet on one corner. Foot-traffic data tells you how many people pass — not who. We watch the street and tell you who." |
| 0:12–0:35 | Click **Dog grooming salon** → result in ~4 s; point at "Dogs · core" and "Dogs in 3/36 frames" | "Dog grooming. It knows dogs are the customers, checks every corner, and picks Camera 5 — where dogs actually walked past. Three of the other four corners had none." |
| 0:35–0:50 | Play the #1 evidence reel | "And it shows you the evidence: the actual moments, linked to the exact source footage." |
| 0:50–1:10 | Type **Luxury car detailing**, Enter → Camera 1 | "Any business. Luxury car detailing: premium cars in 28 of 36 frames downtown. Different business, different corner — same footage." |
| 1:10–1:25 | Click **Kids' toy store** → "Not enough evidence" | "Kids' toy store: one stroller in fifteen minutes. It refuses to guess." |
| 1:25–1:45 | Scroll to ranking; open the Weave trace | "Under the hood: a Gemma 4 vision model on W&B counted people types, car tiers and dogs in 180 frames from the VAST dataset. Code does the ranking, and every run is traced in Weave." |
| 1:45–2:00 | Back to the verdict | "Today it's a three-minute scouting sample per corner. Point it at a week of footage and it's a lease decision. Corner Scout: see who walks past before you sign." |

**Recorded round:** do it exactly like this in one take; the waits are short enough to keep real.
**If a run is slow (>10 s):** cut to a saved run and say "previous run, same footage".

## Three-minute final additions

Insert before the close:

- **+20 s · A judge's business.** Ask a judge for any business and type it. If it names no audience, point at the chips: "these are the audiences the model chose."
- **+20 s · Why vision, not captions.** "Our first version counted caption words. At Camera 1, 25 segments mention a bicycle — it would have looked like a cycling hotspot. Vision shows a parked kick scooter and zero riders." Show a Camera 1 evidence frame with the scooter.
- **+20 s · Zoom out.** Open the city report: "five locations summarized in 41 seconds," the 50-second best-of reel, and the activity table.

## What to say about limits

Say these out loud — they make the result credible:

- "It's a scouting sample: one three-minute visit per corner, recorded at different times of day."
- "Premium car and people type are the vision model's judgement from single frames — that's why every clip links back to the source."
- "Scores count frames where an audience appears, not unique people."

Never say: foot-traffic numbers, revenue, "guaranteed best location", or anything about identifying individuals.

## Prep checklist (in order)

1. **Keep one writer (1 min).** The ChatGPT agent has been told to stop editing docs. Before any further change, agree which agent commits, so the README, this plan, and PR #3 stay consistent.
2. **Start the app (1 min).** SSH tunnel up (see README), then:
   ```sh
   uv run python scripts/corner_scout.py
   ```
   Open **http://127.0.0.1:7861**. Vision tags are cached in `data/vision/`; rerun `uv run python scripts/vision_tags.py` only after importing new footage (about 3 minutes).
3. **Warm-up (2 min).** Click every example once and type the two typed examples. Each should finish in under 6 s. Results are saved under `outputs/corner-scout/` as fallbacks.
4. **Spot-check the vision counts (10 min) — this is the evaluation.** For each core audience on stage, open the frames and confirm by eye:
   ```sh
   uv run python -c "import json; d=json.load(open('data/vision/096e3f61cb8540a0b011b489ea48de62.json')); print([f['seconds'] for f in d['frames'] if f['counts']['dog']])"
   ffmpeg -loglevel error -ss SECONDS -i data/media/096e3f61cb8540a0b011b489ea48de62.mp4 -frames:v 1 check.jpg
   ```
   Check the 3 dog frames (Camera 5), 5 premium-car frames (Camera 1, `c0cc58e7d4da405eaf1e9bb01c83fa55`), and 5 young-adult frames (Camera 2, `32f844ce3dc4472c9d4596ad8afe90a0`). Record "X of 13 confirmed" and put only that reviewed number in the README.
5. **Record the two-minute run (by 3:45 PM).** 16:9 browser, text readable at 720p. Hide `.env`, tunnel commands, keys, and personal tabs.
6. **Record the spot-check in the README (2 min).** Replace “Status: pending human review” in [Prove the result](../README.md#prove-the-result) with the reviewed count. Judges read the README.
7. **Submit (by 4:15 PM).** Repo, shareable video, tool description, all team names and emails. Open the video link without logging in.

## Fallbacks

| If… | Then… |
| --- | --- |
| W&B Inference is down or slow | Play the recorded run; ranking needs the text model for each new business. |
| Corner Scout can't run at all | Open the saved preset business report (`outputs/business-report/RUN/index.html`, served on port 8765) and walk the bike-shop and dog-shop tabs. |
| A result looks wrong on stage | Open its evidence clips and say what the frames show — that is the point of evidence. |
| The app won't start | Check port 7861 is free; restart with the command above. |
| W&B returns HTTP 401 | Check `WANDB_PROJECT` points to your own workspace. |

## Don't

- **Don't add features before the recording.** Freeze at 3:15 PM.
- **Don't claim counts are people.** They are frames where an audience appears.
- **Don't demo tourist or senior businesses as wins.** Neither audience was detected; they will (correctly) refuse.

## Run commands and outputs

| What | Command | Output |
| --- | --- | --- |
| Tag frames (once) | `uv run python scripts/vision_tags.py` | `data/vision/<video_id>.json` |
| Corner Scout app | `uv run python scripts/corner_scout.py` | http://127.0.0.1:7861, reels in `outputs/corner-scout/` |
| One business, no UI | `uv run python scripts/corner_scout.py --business "Dog grooming salon"` | JSON ranking + reels |
| City report (finale) | `uv run python scripts/city_report.py` | `outputs/city-report/<run>/index.html` |
