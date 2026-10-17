# Implementation validation — 17 October 2026

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

## Verified live connections

The assigned VAST backend accepted the configured credentials and returned authenticated configuration, an upload bucket, and the configured synthesis model `nvidia/cosmos3-nano-reasoner`. Its `/openapi.json` route returned non-JSON content; this connection check does not establish the deployed ingestion/search schema or prove Cosmos caption execution.

A real W&B inference request returned the validated JSON result `{"ok": true}`. Its Weave call was flushed and read back from the remote service with a completed timestamp. Workspace initialization, inference, and trace verification together took 4.93 seconds; this is not a highlight-generation latency measurement. The credential-free local report and real trace URL are saved at `outputs/validation/live-connection-check.json`.

The initial HTTP 401 came from an inaccessible `WANDB_PROJECT`, despite a valid API key. Switching to the authenticated account's default workspace fixed both inference and Weave. API keys remain in the ignored local `.env`.

## Still requires gameplay validation

The connection check does not verify live ingestion/search, executed Cosmos identity, gameplay caption quality, or complete highlight latency. Provider unit tests exercise HTTP contracts and do not count as sponsor execution evidence.

Configure `.env`, run `highlight-agent doctor --remote`, register the exact source, upload or attach its index, and refresh until ready. Review the six actual gameplay examples in `examples/evaluation.json`; do not treat synthetic panels as gameplay-quality evidence. Record the genuine two-/three-minute presentation only after the live flow works.

