import subprocess
from pathlib import Path
from threading import Lock
from uuid import UUID

from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from downloader import DownloadCancellation, DownloadCancelled, DownloadError, download_series
from image_stitch import stitch_chapter
from renumber import chapter_folder, chapter_images, directories, downloaded_series, renumber_chapter


ROOT = Path(__file__).resolve().parent
DOWNLOADS = ROOT / "data" / "downloads"
app = FastAPI()
download_lock = Lock()
active_progress: tuple[UUID | None, int] = (None, 0)
active_download: tuple[UUID | None, DownloadCancellation] | None = None


class DownloadRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    start: int | None = Field(default=None, ge=1)
    end: int | None = Field(default=None, ge=1)
    progress_id: UUID | None = None


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
    global active_download
    if not download_lock.acquire(blocking=False):
        raise HTTPException(409, "A download is already running.")
    cancellation = DownloadCancellation()
    active_download = (request.progress_id, cancellation)

    def report_progress(percent):
        global active_progress
        active_progress = (request.progress_id, percent)

    try:
        report_progress(0)
        return download_series(
            request.url, request.start, request.end, DOWNLOADS,
            report_progress=report_progress, cancellation=cancellation,
        )
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


app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True))
