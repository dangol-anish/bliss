# Bliss

A local macOS workspace for making manhwa recap videos. Downloader saves chapter images from Webtoon and Weeb Central series links.

## Setup

Requires Python 3.11, uv, Node.js, npm, and FFmpeg.

```sh
uv sync --locked
npm ci
npm run build
```

## Local preview

```sh
uv run --locked uvicorn main:app --host 127.0.0.1 --port 8000
```

Open [Bliss](http://127.0.0.1:8000/) in your browser. Paste a series link and optionally enter the first and last chapter positions to download, counting from 1. Blank positions select the first or last available chapter. Images are saved under `data/downloads/` in numbered reading order within each chapter folder.

Use `npm run dev:css` to rebuild CSS as you edit the frontend. The frontend uses shadcn/ui components, Tailwind, and JavaScript for the downloader. Run `npm run build:js` after editing frontend scripts or components. The backend will use FastAPI and Uvicorn.

Install the detector runtime with `uv sync --locked --extra detection`. The PyTorch and Ultralytics versions match the earlier crop experiments. Keep local model weights in `models/` and chapter files, crops, narration, and renders in `data/`. Both directories are ignored by Git.
