# Demo recorder — captioned walkthrough video

Records a narrated (captioned) screen walkthrough of the Provider Prior
Authorization app with Playwright, then converts it to MP4. Produces the videos
in [`docs/videos/`](../../docs/videos/) and the narration script at
[`docs/walkthrough-script.md`](../../docs/walkthrough-script.md).

Everything (captions + script) is driven from a single file, **`scenes.mjs`**, so
the burned-in captions and the written script never drift.

## Prerequisites

- Node 18+ and the Playwright browsers (already cached on the build machine; if
  not, `npx playwright install chromium`).
- `ffmpeg` on `PATH` (for WebM → MP4 and the teaser cut). Install with
  `winget install -e --id Gyan.FFmpeg` or `choco install ffmpeg`.

## Record

```bash
cd scripts/demo
npm install
# defaults to the live deployment; override for local docker-compose:
TARGET_URL=https://<frontend-fqdn>/ npm run record
```

This drives the Orthopedics sample end to end (~5 min, incl. the live ~90s
assessment) and writes:

- `docs/videos/_raw/walkthrough.webm` — the master recording (gitignored)
- `docs/videos/_raw/chapters.json` — chapter → timestamp map (used by the teaser cut)
- `docs/videos/_downloads/*.pdf` — the report + provider letter it downloaded (gitignored)

## Convert + build the teaser (ffmpeg)

```bash
cd docs/videos
# Deep-dive MP4
ffmpeg -y -i _raw/walkthrough.webm -c:v libx264 -pix_fmt yuv420p -crf 23 -movflags +faststart walkthrough-deepdive.mp4
# Teaser: see chapters.json for offsets; trim to highlights and speed up the assess wait, then concat.
```

(The repo's video PR was produced with the helper steps in the walkthrough PR
description; re-run `npm run record` then re-encode to refresh.)

## Regenerate just the narration script

```bash
cd scripts/demo && npm run script   # writes docs/walkthrough-script.md
```

## Editing the walkthrough

Edit **`scenes.mjs`** — each scene has `caption` (burned into the video),
`narration` (the script line), `action` (what happens on screen), and `holdMs`
(how long the caption stays up). The director (`record-walkthrough.mjs`) contains
the actual Playwright steps and pulls caption text by scene id.
