import logging
import subprocess
from pathlib import Path
from threading import Lock
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from chapter_backup import move_backup
import narration
from studio_bridge import local_request

from downloader import DownloadCancellation, DownloadCancelled, DownloadError, download_batch, download_series
from image_stitch import PAGE_WIDTH, stitch_chapter
from narration import install as install_narration
from panel_detection import detect_panels
from panel_review import PREVIEW_ROWS, image_bytes, open_strip, read_review, save_review, stitched_series, latest_strip, strip_source
from recap_preview import install as install_preview
from renumber import chapter_folder, chapter_images, directories, downloaded_series, renumber_chapter


from strip_split import OUTPUT_DIR, save_split, split_plan


ROOT = Path(__file__).resolve().parent
DOWNLOADS = ROOT / "data" / "downloads"
app = FastAPI()
download_lock = Lock()
active_progress: tuple[UUID | None, int] = (None, 0)
active_download: tuple[UUID | None, DownloadCancellation] | None = None


class DownloadEntry(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    start: int | None = Field(default=None, ge=1)
    end: int | None = Field(default=None, ge=1)



class DownloadRequest(DownloadEntry):
    progress_id: UUID | None = None


class BatchDownloadRequest(BaseModel):
    entries: list[DownloadEntry] = Field(min_length=1)
    progress_id: UUID | None = None


@app.get("/api/panels/progress/{progress_id}")
@app.get("/api/stitch/progress/{progress_id}")
@app.get("/api/download/progress/{progress_id}")
def download_progress(progress_id: UUID, response: Response):
    active_id, percent = active_progress
    response.headers["Cache-Control"] = "no-store"
    return {"percent": percent if active_id == progress_id else None}


@app.post("/api/download/cancel/{progress_id}", status_code=204)
def cancel_download(progress_id: UUID):
    download = active_download
    if download is None or download[0] != progress_id or not download[1].cancel():
        raise HTTPException(409, "This download is no longer running.")
    return Response(status_code=204)


@app.post("/api/download")
def download(request: DownloadRequest):
    return run_download(request.progress_id, lambda report, cancel: download_series(
        request.url, request.start, request.end, DOWNLOADS, report_progress=report, cancellation=cancel,
    ))


@app.post("/api/download/batch")
def batch_download(request: BatchDownloadRequest):
    def run(report, cancel):
        results = download_batch(request.entries, DOWNLOADS, report_progress=report, cancellation=cancel)
        return {"chapters": sum(result["chapters"] for result in results),
                "series": len({result["folder"] for result in results}),
                "folder": results[0]["folder"] if len(results) == 1 else None}

    return run_download(request.progress_id, run)


def run_download(progress_id, operation):
    global active_download
    if not download_lock.acquire(blocking=False):
        raise HTTPException(409, "A download is already running.")
    cancellation = DownloadCancellation()
    active_download = (progress_id, cancellation)

    def report_progress(percent):
        global active_progress
        active_progress = (progress_id, percent)

    try:
        report_progress(0)
        return operation(report_progress, cancellation)
    except DownloadCancelled:
        return {"cancelled": True}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except DownloadError as exc:
        raise HTTPException(502, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, "Could not save the downloaded chapters.") from exc
    finally:
        active_download = None
        download_lock.release()


class OpenFolderRequest(BaseModel):
    pass


@app.post("/api/download/open-folder", status_code=204)
def open_download_folder(request: OpenFolderRequest):
    folder = DOWNLOADS.resolve()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.run(["/usr/bin/open", str(folder)], check=True, capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(500, "Could not open the download folder.") from exc
    return Response(status_code=204)


class ChapterSelection(BaseModel):
    series: str
    chapter: str


class ChapterRange(ChapterSelection):
    end_chapter: str | None = None
    progress_id: UUID | None = None


def selected_chapters(request):
    start = chapter_folder(DOWNLOADS, request.series, request.chapter)
    end = chapter_folder(DOWNLOADS, request.series, request.end_chapter or request.chapter)
    chapters = [folder for folder in directories(start.parent) if chapter_images(folder)]
    if start not in chapters or end not in chapters:
        raise ValueError("Choose chapters that contain images.")
    first, last = chapters.index(start), chapters.index(end)
    if first > last:
        raise ValueError("Choose an end chapter at or after the start chapter.")
    return chapters[first:last + 1]


def process_chapters(request, action, operation, *, progress_id=None):
    global active_progress
    if not download_lock.acquire(blocking=False):
        raise HTTPException(409, "Wait for the current download or chapter operation to finish.")
    try:
        if progress_id is not None:
            active_progress = (progress_id, 0)
        chapters = selected_chapters(request)
        results = []
        for folder in chapters:
            try:
                if progress_id is None:
                    result = action(folder)
                else:
                    def report_progress(fraction):
                        global active_progress
                        # Reserve the last part of each chapter for writing its PDF.
                        percent = int(100 * (len(results) + 0.99 * fraction) / len(chapters))
                        active_progress = (progress_id, percent)

                    result = action(folder, report_progress=report_progress)
                results.append(result)
                if progress_id is not None:
                    active_progress = (progress_id, 100 * len(results) // len(chapters))
            except ValueError as exc:
                raise HTTPException(400, f"{folder.name}: {exc} ({len(results)} chapters finished.)") from exc
            except (OSError, RuntimeError) as exc:
                raise HTTPException(500, f"Could not {operation} {folder.name}. {len(results)} chapters finished.") from exc
        return results
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        download_lock.release()


@app.get("/api/stitch/series")
def stitch_series():
    return downloaded_series(DOWNLOADS)


@app.post("/api/stitch/open-folder", status_code=204)
def open_stitch_folder(request: ChapterSelection):
    try:
        folder = chapter_folder(DOWNLOADS, request.series, request.chapter)
        subprocess.run(["/usr/bin/open", str(folder)], check=True, capture_output=True, timeout=5)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(500, "Could not open the chapter folder.") from exc
    return Response(status_code=204)


@app.post("/api/stitch/renumber")
def stitch_renumber(request: ChapterRange):
    results = process_chapters(request, renumber_chapter, "renumber")
    response = {key: sum(result[key] for result in results) for key in ("images", "renamed")}
    if request.end_chapter is not None:
        response["chapters"] = len(results)
    return response


@app.post("/api/stitch/create")
def create_stitch(request: ChapterRange):
    files = process_chapters(request, stitch_chapter, "stitch", progress_id=request.progress_id)
    return {"chapters": len(files), "files": files}



class PanelDetection(ChapterSelection):
    source: str
    progress_id: UUID


class PanelEdit(ChapterSelection):
    source: str
    boxes: list[tuple[int, int, int, int]] = Field(max_length=5000)


def panel_metadata(strip, source, boxes):
    return {"source": source, "height": strip.height, "width": PAGE_WIDTH,
            "tile_height": PREVIEW_ROWS, "boxes": boxes}


@app.get("/api/panels/series")
def panel_series():
    return stitched_series(DOWNLOADS)


def ready_series(stage):
    result = []
    for series in directories(DOWNLOADS):
        chapters = []
        ranges = []
        contiguous = []
        for folder in directories(series):
            try:
                path = latest_strip(folder)
                if path is None and not chapter_images(folder):
                    continue
                source = strip_source(path) if path is not None else None
                boxes = read_review(folder, source) if source is not None else None
                if boxes is None:
                    raise ValueError("Missing panels")
                if stage != "split":
                    narration.identify(folder)
                    if not narration.available_splits(folder, source, boxes):
                        raise ValueError("Missing split images")
                if stage == "preview":
                    narration.accepted_selection(DOWNLOADS, series.name, folder.name)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                contiguous = []
                continue
            chapters.append(folder.name)
            if not contiguous:
                contiguous = []
                ranges.append(contiguous)
            contiguous.append(folder.name)
        if chapters:
            entry = {"name": series.name, "chapters": chapters}
            if stage == "preview":
                entry["ranges"] = ranges
            result.append(entry)
    return result


@app.get("/api/split/series")
def split_series():
    return ready_series("split")


@app.get("/api/narration/series")
def narration_series():
    return ready_series("narration")


@app.get("/api/preview/series")
def preview_series():
    return ready_series("preview")


@app.get("/api/panels/chapter")
def panel_chapter(series: str, chapter: str):
    try:
        folder = chapter_folder(DOWNLOADS, series, chapter)
        with open_strip(folder) as (strip, source):
            return panel_metadata(strip, source, read_review(folder, source))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/panels/tile/{index}")
def panel_tile(index: int, series: str, chapter: str, source: str):
    try:
        folder = chapter_folder(DOWNLOADS, series, chapter)
        with open_strip(folder, source) as (strip, _):
            top = index * PREVIEW_ROWS
            if not 0 <= top < strip.height:
                raise HTTPException(404)
            with strip.image(top, min(strip.height, top + PREVIEW_ROWS)) as image:
                return Response(image_bytes(image, "JPEG", quality=90), media_type="image/jpeg",
                                headers={"Cache-Control": "private, max-age=86400"})
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/panels/detect")
def panel_detect(request: PanelDetection):
    global active_progress
    if not download_lock.acquire(blocking=False):
        raise HTTPException(409, "Wait for the current chapter operation to finish.")

    def report(percent):
        global active_progress
        active_progress = (request.progress_id, percent)

    try:
        report(0)
        folder = chapter_folder(DOWNLOADS, request.series, request.chapter)
        with open_strip(folder, request.source, exact=True) as (strip, source):
            prior = read_review(folder, source)
            if prior is not None:
                return panel_metadata(strip, source, prior)
            boxes = detect_panels(strip, ROOT / "models", report)
            save_review(folder, source, boxes)
            report(100)
            return panel_metadata(strip, source, boxes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logging.getLogger(__name__).exception("Panel detection failed")
        raise HTTPException(500, "Could not detect panels in this chapter.") from exc
    finally:
        download_lock.release()


@app.put("/api/panels/chapter", status_code=204)
def panel_save(request: PanelEdit):
    if not download_lock.acquire(blocking=False):
        raise HTTPException(409, "Wait for the current chapter operation to finish.")
    try:
        folder = chapter_folder(DOWNLOADS, request.series, request.chapter)
        with open_strip(folder, request.source) as (strip, source):
            if any(not (0 <= x1 < x2 <= PAGE_WIDTH and 0 <= y1 < y2 <= strip.height)
                   for x1, y1, x2, y2 in request.boxes):
                raise ValueError("One or more panels extend beyond the strip.")
            boxes = sorted(request.boxes, key=lambda box: (box[1], box[0]))
            save_review(folder, source, boxes)
            return Response(status_code=204)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        logging.getLogger(__name__).exception("Could not write panel review")
        raise HTTPException(500, "Could not save the panel review.") from exc
    finally:
        download_lock.release()


class StripSplit(ChapterSelection):
    minimum: int = Field(ge=1, le=100_000)
    maximum: int = Field(ge=1, le=100_000)
    preview_id: str | None = None


def process_split(request: StripSplit, *, save=False):
    if not download_lock.acquire(blocking=False):
        raise HTTPException(409, "Wait for the current chapter operation to finish.")
    try:
        folder = chapter_folder(DOWNLOADS, request.series, request.chapter)
        with open_strip(folder, exact=True) as (strip, source):
            boxes = read_review(folder, source)
            if boxes is None:
                raise ValueError("Detect and save the chapter panels first.")
            plan = split_plan(strip, source, boxes, request.minimum, request.maximum)
            if save:
                if plan["id"] != request.preview_id:
                    raise HTTPException(409, "The chapter or panel edits changed. Preview the cuts again.")
                plan["folder"] = str(save_split(folder, strip, boxes, plan))
            return {**plan, "boxes": boxes, "tile_height": PREVIEW_ROWS}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        logging.getLogger(__name__).exception("Could not split the chapter strip")
        raise HTTPException(500, "Could not split the chapter strip.") from exc
    finally:
        download_lock.release()


@app.post("/api/split/preview")
def preview_split(request: StripSplit):
    return process_split(request)


@app.post("/api/split/save")
def commit_split(request: StripSplit):
    return process_split(request, save=True)


class SplitFolder(ChapterSelection):
    revision: str = Field(pattern=r"^[0-9a-f]{16}$")


@app.post("/api/split/open-folder", status_code=204)
def open_split_folder(request: SplitFolder):
    try:
        chapter = chapter_folder(DOWNLOADS, request.series, request.chapter)
        root = chapter / OUTPUT_DIR
        folder = root / request.revision
        if root.is_symlink() or folder.is_symlink() or not (folder / "manifest.json").is_file():
            raise ValueError("Save the split images first.")
        subprocess.run(["/usr/bin/open", str(folder)], check=True, capture_output=True, timeout=5)
        return Response(status_code=204)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(500, "Could not open the split image folder.") from exc


class BackupBatch(BaseModel):
    entries: list[ChapterRange] = Field(min_length=1)


@app.post("/api/backup/create")
def backup_chapters(body: ChapterRange, request: Request):
    result = run_backup([body], request)
    return result[0] if isinstance(result, list) else result


@app.post("/api/backup/batch")
def backup_batch(body: BackupBatch, request: Request):
    return run_backup(body.entries, request)


def run_backup(entries, request):
    local_request(request)
    try:
        choice = subprocess.run(["/usr/bin/osascript", "-e", """
            try
                return POSIX path of (choose folder with prompt "Choose where to move the chapter backup")
            on error number -128
                return ""
            end try
        """], check=True, capture_output=True, text=True).stdout.rstrip("\n")
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(500, "Could not open the destination picker.") from exc
    if not choice:
        return Response(status_code=204)
    destination = Path(choice).resolve()
    if destination.is_relative_to(DOWNLOADS.resolve()):
        raise HTTPException(400, "Choose a folder outside Bliss downloads.")
    if not download_lock.acquire(blocking=False):
        raise HTTPException(409, "Wait for the current chapter operation to finish.")
    try:
        with narration.state_lock:
            if narration.active and narration.active["running"]:
                raise HTTPException(409, "Wait for the current narration to finish.")
            selections = [selected_chapters(entry) for entry in entries]
            seen = set()
            for chapters in selections:
                if seen.intersection(chapters):
                    raise ValueError("Backup ranges overlap. Select each chapter once.")
                seen.update(chapters)
            results = []
            for entry, chapters in zip(entries, selections):
                try:
                    target, checkpoint = move_backup(chapters, destination)
                except (ValueError, OSError) as exc:
                    raise ValueError(f"{entry.series}: {exc} ({len(results)} archives completed.)") from exc
                results.append({"file": target.name, "location": str(target.parent), "checkpoint": checkpoint})
        return results
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        logging.exception("Could not archive the selected chapters")
        raise HTTPException(500, "Could not finish moving the chapters. Check the destination and available space.") from exc
    finally:
        download_lock.release()


install_narration(app, DOWNLOADS)
install_preview(app, DOWNLOADS, lock=download_lock)
app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True))
