# Corner Scout proof and walkthrough

[Watch the historical 108-second captioned proof walkthrough](assets/corner-scout-proof-reveal.mp4). It uses the original five-camera, 15-minute SF sample, as does the [82-second demo](https://drive.google.com/file/d/1zZgiQSG6cm3ZWqlijK4rIugzymr1lPrq/view?usp=sharing).

The hosted interface connects audience counts to the actual sampled frames.
Click a count to inspect its audience, select any matching thumbnail or sample
marker, and use **Play this moment** to watch the surrounding five-second clip.
The top corner and runner-up share one inspector; changing the selection stops
previous playback.

The current full-recording Scout contains **937 valid observations across six
reviewed recordings**. In San Francisco, dog grooming leads at camera 5 with dogs
in **10/180** samples; its retained **Detail crop** opens at 0:07.5. **Full frame**
restores street context, and all ten matching samples remain inspectable. New York
dog grooming abstains because the strongest recording has only **2/180** dog
samples. NYC camera 1's two recordings stay distinct. See the [current measured
examples](DEMO_PLAN.md#current-measured-examples) for all six business examples.

The older proof video shows only three dog samples, camera 1's 28 luxury-car
matches, and a toy-store abstention. Those results belong to the historical
snapshot; the current SF toy-store example recommends camera 5 with families in
**8/180** samples.

## Current full-recording export

After importing, motion-reviewing, and tagging full recordings, export collection previews before scouting observations:

```sh
uv run python scripts/export_collection.py
uv run python scripts/export_vercel.py
```

Each observation publishes its `sampledAt`, original-source JPEG `image`, and
existing clip/source reference. Optional `focus` coordinates describe reviewed
presentation crops, bound to the original content hash and segment. They are
separate from model detections. The exporter uses the vision extractor's original
timestamp and settings, verifies source recordings, and publishes the snapshot
after every referenced sampled image and recording preview exists.

## Historical recording recipe

`scripts/record_demo.py` targets the original 180-frame web snapshot and references retired individual clip assets. Restore that historical snapshot before using the recipe below; the script does not replay the current full-collection app. The published videos remain historical artifacts. Present the [current full-collection walkthrough](DEMO_PLAN.md#full-collection-walkthrough) live.

With the original snapshot restored, start it locally with provider settings in `.env`:

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

The historical recording shows dog proof, luxury-car matches, insufficient evidence for a
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
