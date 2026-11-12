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

Use `npm run dev:css` to rebuild CSS as you edit the frontend. The frontend uses shadcn/ui components, Tailwind, and JavaScript for the downloader. Run `npm run build:js` after editing frontend scripts or components. The backend uses FastAPI and Uvicorn.

Install the detector runtime with `uv sync --locked --extra detection`. The PyTorch and Ultralytics versions match the earlier crop experiments. Keep local model weights in `models/` and chapter files, crops, narration, and renders in `data/`. Both directories are ignored by Git.

## Panel review

Install the detection extra, then put the WTN model files and their matching D2 runtime in `models/`:

```text
models/
  Manhwa Style.pt
  Manhua Style.pt
  character-face-efficientdet-c2.pth
  animecv/
  bounding_box/
```

The `animecv` folder includes its bundled EfficientDet code. Use the matching local runtime and weights from the WTN crop experiments. Bliss runs both panel models and D2 on the Mac.

After stitching, open **Panels**, choose a series and chapter, and click **Detect Panels**. Click a box, drag it or any handle to adjust it, and use **Delete** or **Undo** as needed. Save Edits keeps the reviewed geometry with the stitched chapter. Panel IDs follow top-to-bottom reading order with left-to-right ordering for boxes at the same height.

## Narration

Open **Narration** after saving a split. In Chrome or Brave, load the folder from **Open Extension Folder** as an unpacked extension from the browser's Extensions page. Open Google AI Studio in that browser, sign in, and use **Get Pairing Code** in Bliss to connect the AI Studio tab through the Bliss Narration extension popup.

Select a chapter and choose **Start Narration** to send its saved images to AI Studio in batches. Review the finished script and choose **Accept Chapter** to update the story checkpoint. The story library is stored at `~/Library/Application Support/Bliss/Story/story.sqlite3`, outside the downloaded chapters. Bliss uses the latest accepted checkpoint before the selected chapter to build its prompt. A local test installation can set `BLISS_STORY_LIBRARY` to a separate directory.

### Preview

Install FFmpeg (including ffprobe), then open **Preview** and select an accepted chapter. **Generate Preview** uses Edge TTS online for Steffan at 1.2× and saves the voiceover and word timings locally for reuse. Play the resulting MP4 in Bliss or choose **Download** to save a copy.

Episode 1 opens with [Bell Notification by ALEXIS_GAMING_CAM](https://pixabay.com/sound-effects/film-special-effects-bell-notification-337658/), under the Pixabay Content License.
