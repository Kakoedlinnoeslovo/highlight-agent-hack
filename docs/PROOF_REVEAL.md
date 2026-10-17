# Corner Scout proof and walkthrough

[Watch the 108-second captioned proof walkthrough](assets/corner-scout-proof-reveal.mp4).

The hosted interface connects audience counts to the actual sampled frames.
Click a count to inspect its audience, select any matching thumbnail or sample
marker, and use **Play this moment** to watch the surrounding five-second clip.
The top corner and runner-up share one inspector; changing the selection stops
previous playback.

For dog grooming, Camera 5 opens at 0:07.5 with a manually selected **Detail crop**.
**Full frame** restores the street context. The other positive samples are at
0:02.5 and 0:12.5. These can show the same dog passing; the count describes three
sampled frames, rather than three distinct dogs. Luxury car searches expose all
28 positive samples at Camera 1 through the scrolling thumbnail rail.

## Refresh the sample

After importing and tagging footage:

```sh
uv run python scripts/export_vercel.py
```

Each observation publishes its `sampledAt`, original-source JPEG `image`, and
existing clip/source reference. Optional `focus` coordinates describe reviewed
presentation crops, bound to the original content hash and segment. They are
separate from model detections. The exporter uses the vision extractor's original
timestamp and settings, verifies source recordings, and publishes the snapshot
after every referenced image and clip exists.

## Run and record

Start the hosted application locally with provider settings in `.env`:

```sh
cd web
npm run dev
```

From the repository root, capture genuine searches and a measured assembly plan:

```sh
uv run python scripts/record_demo.py \
  --output outputs/demo/proof-reveal/rehearsal.mp4 \
  --manifest outputs/demo/proof-reveal/demo-plan.json
uv run highlight-agent demo outputs/demo/proof-reveal/demo-plan.json \
  --output-dir outputs/demo/proof-reveal/final
```

The recording shows dog proof, luxury-car matches, insufficient evidence for a
toy store, and the bicycle-caption comparison. It uses the real profile endpoint;
browser-test fixtures are restricted to QA. Assembly boundaries come from the
recorded beats, with a 120-second maximum.

## Validate

```sh
uv run pytest -q tests/test_vercel_export.py tests/test_hosted_proof.py
cd web
npm test
```

Browser tests use real exported images and clips with deterministic profile
responses. They cover keyboard controls, negative samples, all matches, playback
cleanup, media failures and delayed images, desktop/mobile layout, and reduced
motion.
