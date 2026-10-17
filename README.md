# Highlight Agent

Turn a long video and a prompt into a 30-second compilation of the best matching moments.

Built for [Real-Time Video Agents Hack — London](https://luma.com/vastlondon), part of the VAST Builders Challenge, on 17 October 2026.

## The idea

Give the agent a video and a request such as “best fails” or “every high-five.” It finds relevant visual moments, ranks them, and assembles a short compilation. The focus is on actions that transcript-based clipping can miss.

## Status

Project setup and planning. The application and sponsor integrations are not implemented yet. See [HACK_PLAN.md](HACK_PLAN.md) for the proposed architecture, prompts, milestones, and demo plan.

## Planned workflow

1. Ingest a video into VAST and caption segments with NVIDIA Cosmos.
2. Expand the user's prompt into concrete visual search queries using W&B Inference.
3. Search for matching segments, merge nearby hits, and rank candidates.
4. Verify selected moments with Cosmos and render a compilation with FFmpeg.
5. Show the result in a Gradio UI, with Weave traces explaining the selections.

Optional: use YOLO to frame vertical clips.

## Planned stack

| Tool | Intended role |
| --- | --- |
| VAST AI OS / VastDB | Video ingestion, segment storage, and semantic search |
| NVIDIA Cosmos | Visual captions and candidate verification |
| W&B Inference | Query expansion, ranking, and titles |
| W&B Weave | Agent and model-call tracing |
| FFmpeg | Trimming, normalization, and compilation rendering |
| Gradio | Video upload, prompt input, and playback |
| YOLO (optional) | Subject detection for vertical framing |

## Development

The planned local requirements are Python 3.11+ and FFmpeg. Setup and run instructions will be added with the first working implementation.

Keep API keys in a local `.env` file and source footage and generated clips in `data/` or `outputs/`. These paths are ignored by Git. Commit only footage you have permission to share.
