# Highlight Agent — Hack Plan

*Real-Time Video Agents Hack · London · Sat 17 Oct 2026 · working name, rename freely*

> **Give it a 1–2 hour video and a prompt ("best fails", "funniest moments"). Get back a 30-second compilation of the best matching moments.**

---

## ⏰ Must-knows

- **Deadline: 4:30 PM BST.** Demos also start at 4:30, so submit *before* you present. Late entries are cut off.
- **Submit here:** https://tokensand.com/vastlondon/submit
- **Submission must include:**
  - GitHub repo the reviewers can access
  - Short demo video with a shareable link
  - What you built + the tools you used
  - Team names + contact emails
  - *(Optional: working website, screenshot)*
- **Rules:** at least 3 sponsor tools · 3-minute demo, recorded · built today only · max 4 people · one project per team
- **Prizes:** 1st NVIDIA DGX Spark · 2nd Hugging Face Microduck (credits and gift cards split across the team)

| Time | |
|---|---|
| 9:30 | Build begins |
| 12:30 | Lunch |
| **4:30** | **Submission deadline + demos** |
| 6:30 | Closing & awards |

### Judges and what each likely cares about
| Judge | Role | Show them |
|---|---|---|
| Abubakr Karali | Senior Solutions Architect, NVIDIA | Real use of Cosmos (custom captions + verification) |
| Ram B. | Sr. Developer Advocate, VAST Data | VAST ingestion + semantic search at the core |
| Brian Verkley | Director, AI Data Platform, VAST Data | "Video sitting in storage, unwatched" now has value |
| Junaid Butt | AI Solutions Engineer, W&B | W&B Inference + Weave traces |

---

## ✅ Setup checklist

- [ ] Join the Cosmos Community → access to the VAST build environment (first 100 people only): https://community.vastdata.com/?utm_campaign=event26-builders-challenge
- [ ] Cursor account with the **same email you registered with** (for credits): https://cursor.com
- [ ] W&B account → inference access is handed out at kickoff: https://wandb.ai/site
- [ ] Discord for sponsor help: https://discord.gg/rPBMhMzRU
- [ ] Local: Python 3.11+, `ffmpeg`, `pip install openai weave ultralytics gradio`

---

## 💡 The project

**Problem.** Long videos (streams, GoPro sessions, sports, events) hide a few seconds worth sharing. Finding them means scrubbing through hours of footage by hand. Most footage is never watched again.

**Who has it.** Compilation and fails channels, streamers clipping VODs, sports clubs, creators, event and marketing teams.

**Why current tools fall short.** Most clipping tools rely mostly on what's *said* (the transcript), so they miss moments with no words: a fall, a crash, a near-miss. Stock tools only search their own libraries.

**Why now.** Video models like NVIDIA Cosmos can describe every 5 seconds of footage automatically, so an archive becomes searchable by meaning without any manual tagging.

**Pitch line.**
> "Nobody watches 2 hours of footage to find the 30 seconds worth sharing. Tell our agent what you want — fails, funny moments, every time the cat knocks something over — and it cuts the clip for you."

---

## 🧱 How it works

```
INGEST (once per video)
long video ─► VAST pipeline ─► 5-s segments ─► Cosmos caption (CUSTOM prompt) ─► embeddings in VastDB

QUERY (any number of times, fast)
"best fails" ─► W&B LLM: 8 concrete search queries
             ─► VAST semantic search (top-k per query)
             ─► dedupe, merge neighbours, pad −2 s / +1 s
             ─► W&B LLM rerank by caption
             ─► Cosmos re-watches top ~15, scores each, finds the peak second
             ─► pick 5–7 moments ≈ 30 s, BEST ONE LAST
             ─► ffmpeg: trim, titles ("FAIL #3"), slow-mo replay, concat
             ─► 30-s MP4  (+ optional 9:16 vertical, auto-framed with YOLO)
```

| Sponsor tool | What it does here |
|---|---|
| **VAST AI OS** | Ingests the video, stores captions + vectors, semantic search |
| **NVIDIA Cosmos** | Captions each segment with the custom prompt; verifies and scores candidates |
| **W&B Inference** | LLM for query expansion, reranking, titles |
| **W&B Weave** | Traces every step: queries → candidates → why each moment was picked |
| **YOLO** | Tracks the person to auto-crop vertical clips (optional) |
| **Cursor** | Where you build it |

---

## 🔍 Verified tech facts

- **VAST video pipeline** (from VAST's own video-search blog post):
  - Uploads are cut into **5-second segments** by default, adjustable with the `segment_duration` env var
  - Each segment is captioned by **Cosmos Reason** (`cosmos-reason1-7b`)
  - Embeddings: `nvidia/nv-embedqa-e5-v5`, stored as vectors in VastDB
  - Optional LLM answer step: `meta/llama-3.1-8b-instruct` via NVIDIA NIM
  - Users can upload their own videos
- **Not confirmed:** whether search results include the caption text, source file and timestamps → ask VAST staff.
- **W&B Inference** works with the standard OpenAI client. Base URL: `https://api.inference.wandb.ai/v1`. The docs example uses `meta-llama/Llama-3.1-8B-Instruct`; check the models page for bigger ones. No speech or audio models confirmed.
- **YOLO** nano models run at roughly 40 ms per frame on CPU, so they're fine on a laptop.
- **Cosmos only sees frames, not sound.** Visual moments (fails, crashes, reactions) work; jokes in dialogue won't be found.

---

## ❓ Ask the VAST team first

1. Can we upload our own video?
2. Can we change the caption prompt and segment length?
3. How long does ingestion take per hour of video?
4. Does search return **caption text + source file + start/end timestamps**, or only matches?
5. Can we call Cosmos directly on a clip (for the verify step)?

---

## 📝 Prompts (copy-paste)

### 1. Ingestion caption prompt (Cosmos) — the most important one
The default captions are dry ("a man walks across a room") and never say "fail" or "funny", so search can't find those moments.

```
You are describing a 5-second video clip for a highlights editor.
1. WHAT HAPPENS: people, actions, objects, setting.
2. UNEXPECTED: any fail, fall, slip, trip, collision, dropped object,
   near-miss, crash, or surprising or funny event. Write "none" if nothing.
3. REACTIONS: laughing, shock, cheering, pain, celebration.
4. HIGHLIGHT SCORE: 1-10, how share-worthy this moment is.
Use concrete plain words, e.g. "man slips on ice and falls on his back".
```

### 2. Query expansion (W&B LLM)
```
A user wants a 30-second highlight clip of: "{user_prompt}".
Write 8 short, concrete search queries describing what such moments
LOOK like on camera (visible actions, not feelings).
Example for "fails": "person slips and falls", "drops phone",
"bike crashes", "trips over step".
Return raw JSON only: {"queries": ["...", "..."]}
```

### 3. Rerank by caption (W&B LLM)
```
User wants: "{user_prompt}".
Here are candidate clips with their captions:
{numbered_captions}
Score each 1-10 for how well it matches AND how entertaining it is.
Return raw JSON only: {"scores": [{"id": 1, "score": 8, "reason": "..."}]}
```

### 4. Verify (Cosmos, on the padded clip)
```
Watch this clip. Does it contain: "{user_prompt}"?
Return raw JSON only:
{"match": true/false, "score": 1-10,
 "peak_second": <second where the key moment happens>,
 "title": "<3-5 word on-screen title>"}
```

---

## 🛠 Code snippets

### W&B Inference + Weave tracing
```python
import json, os
import weave
from openai import OpenAI

weave.init("highlight-agent")  # after this, every LLM call is traced automatically

client = OpenAI(
    base_url="https://api.inference.wandb.ai/v1",
    api_key=os.environ["WANDB_API_KEY"],
    # project="<team>/<project>",  # optional, for usage tracking
)
MODEL = "meta-llama/Llama-3.1-8B-Instruct"  # check the models page for bigger ones

@weave.op
def expand_prompt(user_prompt: str) -> list[str]:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": EXPAND_PROMPT.format(user_prompt=user_prompt)}],
    )
    return json.loads(resp.choices[0].message.content)["queries"]
```

### ffmpeg: trim, normalise, title, slow-mo, join
```bash
# Trim one moment (start 125.0 s, 6 s long) and normalise so every clip can be joined
ffmpeg -y -ss 125.0 -i input.mp4 -t 6 \
  -vf "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=30" \
  -c:v libx264 -preset veryfast -crf 20 -c:a aac -ar 48000 -ac 2 clip_01.mp4

# Burn in a title (if your ffmpeg has no drawtext, show titles in the UI instead)
-vf "drawtext=fontfile='/System/Library/Fonts/Supplemental/Arial Bold.ttf':text='FAIL #3':fontsize=72:fontcolor=white:borderw=5:bordercolor=black:x=(w-text_w)/2:y=h*0.08"

# Slow-motion replay of the best moment (half speed)
ffmpeg -y -i clip_07.mp4 -vf "setpts=2.0*PTS" -af "atempo=0.5" \
  -c:v libx264 -preset veryfast -c:a aac -ar 48000 -ac 2 clip_07_slow.mp4

# Join all clips (they share the same settings, so no re-encode)
printf "file 'clip_01.mp4'\nfile 'clip_02.mp4'\n" > list.txt
ffmpeg -y -f concat -safe 0 -i list.txt -c copy highlight.mp4
```

### YOLO: auto-crop to vertical 9:16 (optional)
```python
import statistics
from ultralytics import YOLO

model = YOLO("yolo11n.pt")

def person_center_x(path: str) -> float | None:
    """Median x-centre of the biggest person across the clip."""
    xs = []
    for r in model.predict(path, stream=True, classes=[0], verbose=False):  # class 0 = person
        if r.boxes is not None and len(r.boxes):
            areas = r.boxes.xywh[:, 2] * r.boxes.xywh[:, 3]
            xs.append(float(r.boxes.xywh[areas.argmax(), 0]))
    return statistics.median(xs) if xs else None

# crop_w = ih*9/16; X = clamp(center_x - crop_w/2, 0, w - crop_w)
# ffmpeg -i clip.mp4 -vf "crop=ih*9/16:ih:X:0,scale=1080:1920" ... vertical.mp4
```

---

## 📁 Repo layout

```
highlight-agent/
  app.py            # Gradio UI: pick video, type prompt, play result
  agent/
    expand.py       # prompt -> search queries (W&B LLM)
    search.py       # VAST search (stub with hand-picked timestamps until ready)
    rank.py         # LLM rerank + Cosmos verify
    edit.py         # ffmpeg trim / titles / slow-mo / concat
    reframe.py      # YOLO vertical crop (optional)
  prompts/          # the 4 prompts above
  README.md         # problem, demo GIF, architecture, sponsor tools used
```

---

## ⚠️ Risks → fixes

| Risk | Fix |
|---|---|
| Default captions don't mention "fail" or "funny" | Re-ingest with the custom caption prompt above |
| Cosmos can't hear audio | Focus on visual moments. Extra: use volume spikes (laughter, shouting) as a bonus signal |
| 5-s segments cut moments in half | Pad −2 s / +1 s, merge neighbouring hits, use Cosmos `peak_second` |
| A 2-hour video means ~1,440 Cosmos calls, which may be slow | Demo on a 15–30 min video, say it scales. Start ingesting first thing |
| Search might not return timestamps | Ask VAST staff. Otherwise map segment ID → file + offset yourself |
| YouTube fail compilations are copyrighted | Use your own copyright-free footage or the organizers' videos |
| Venue wifi dies during the demo | Pre-render 2–3 outputs and keep them on the laptop as backup |

---

## 🗓 Build plan (milestones)

| By | Done means |
|---|---|
| **ASAP** | VAST questions answered · source video picked (15–30 min) · **ingestion started** with custom prompt · ffmpeg edit works on hand-picked timestamps |
| **12:30 (lunch)** | Ugly v1 end-to-end: prompt → queries → VAST search → 30-s MP4 |
| **2:30** | LLM rerank + Cosmos verify + best-last ordering + titles · Weave tracing on |
| **3:30** | Gradio UI · second prompt works on the same video · optional vertical / slow-mo · **feature freeze** |
| **3:30–4:15** | Record demo video · write README · push to GitHub · upload the video |
| **4:15–4:30** | Submit. Don't cut it close |

---

## 🎤 Demo script (3 min)

| Time | What |
|---|---|
| 0:00–0:20 | **Problem:** "This is 2 hours of footage. 30 seconds are worth sharing. Finding them takes hours." |
| 0:20–1:30 | **Live:** type "best fails" → show the picked moments with scores → play the 30-s clip |
| 1:30–2:10 | **Same video, new prompt** ("every high-five") → new clip in seconds. *Ingest once, search forever.* |
| 2:10–2:40 | **Under the hood:** VAST ingest with a custom Cosmos prompt → W&B LLM queries → Weave trace showing why each moment was picked |
| 2:40–3:00 | **Who it's for + what's next** (streamers, sports clubs, compilation channels) |

---

## 📤 Submission checklist

- [ ] GitHub repo public (or reviewers added)
- [ ] README: one-liner, problem, architecture diagram, sponsor tools used, how to run
- [ ] Demo video uploaded with a shareable link (YouTube unlisted / Loom / Drive with link access)
- [ ] Description: what you built + tools used
- [ ] Team names + contact emails
- [ ] Submitted at https://tokensand.com/vastlondon/submit **before 4:30 PM BST**

---

## 🔗 Links

- Hack page: https://tokensand.com/vastlondon · Submit: https://tokensand.com/vastlondon/submit · Gallery: https://tokensand.com/vastlondon/projects
- Video search overview (organizers): https://drive.google.com/file/d/1qUrT0QaBGS6mXxHTEG2vnLM2pT1KY4RD/view
- VAST video search example: https://www.vastdata.com/blog/unlock-video-intelligence-build-real-time-video-search-and-ai-summary
- VAST AI OS: https://www.vastdata.com/platform/ai-os
- NVIDIA VSS Blueprint docs: https://docs.nvidia.com/vss/latest/
- NVIDIA Cosmos: https://www.nvidia.com/en-us/ai/cosmos/
- W&B Inference docs: https://docs.wandb.ai/inference
- W&B Weave: https://wandb.ai/site/weave/
- YOLO docs: https://docs.ultralytics.com/models/yolo11
- Discord: https://discord.gg/rPBMhMzRU
