"""Generate and serve a narrated panel preview."""
import asyncio
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image, ImageOps

from narration import digest, selection
from panel_review import open_strip, read_review
from renumber import chapter_folder
from studio_bridge import local_request

VOICE = "en-US-SteffanNeural"
SPEED = 1.2
FPS = 30
SIZE = (1280, 720)


class PreviewRequest(BaseModel):
    series: str
    chapter: str
    progress_id: UUID
    key: str = Field(pattern=r"^[0-9a-f]{64}$")


def spoken_track(beats):
    lines = []
    cues = []
    offset = 0
    for beat in beats:
        line = beat["narration"]
        if not line.strip():
            continue
        for anchor in beat["anchors"]:
            cues.append((anchor["panel_id"], offset + line.index(anchor["phrase"])))
        lines.append(line)
        offset += len(line) + 2
    if not lines or not cues:
        raise ValueError("This narration has no spoken panel cues.")
    return "\n\n".join(lines), cues


async def capture(script, target):
    from edge_tts import Communicate

    words = []
    with target.open("wb") as audio:
        async for event in Communicate(script, VOICE, boundary="WordBoundary",
                                       connect_timeout=10, receive_timeout=30).stream():
            if event["type"] == "audio":
                audio.write(event["data"])
            elif event["type"] == "WordBoundary":
                words.append([event["text"], event["offset"] / 10_000_000])
    if not words or not target.stat().st_size:
        raise ValueError("Speech generation returned no audio or timing data.")
    return words


def match_cues(script, cues, words):
    # Compare letters and numbers so punctuation and contractions do not shift word offsets.
    characters = list(re.finditer(r"[^\W_]", script))
    spoken = [(char.lower(), time) for word, time in words for char in word if char.isalnum()]
    if "".join(item.group().lower() for item in characters) != "".join(char for char, _ in spoken):
        raise ValueError("The voiceover word timings did not match the script.")
    frames = []
    cursor = 0
    for panel, offset in cues:
        while cursor < len(characters) and characters[cursor].start() < offset:
            cursor += 1
        if cursor == len(characters):
            raise ValueError(f"Missing voiceover timing for {panel}.")
        frame = round(spoken[cursor][1] / SPEED * FPS) if frames else 0
        frames.append((panel, max(frame, frames[-1][1] + 1) if frames else 0))
    return frames


def audio_track(folder, script):
    key = digest([VOICE, SPEED, script, 1])
    cache = folder / "audio" / key
    if not (cache / "voice.wav").is_file() or not (cache / "words.json").is_file():
        cache.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=cache.parent) as temporary:
            work = Path(temporary)
            raw = work / "raw.mp3"
            words = asyncio.run(asyncio.wait_for(capture(script, raw), timeout=180))
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(raw), "-af", f"atempo={SPEED}",
                            str(work / "voice.wav")], check=True, timeout=60)
            raw.unlink()
            (work / "words.json").write_text(json.dumps(words))
            work.rename(cache)
    return cache / "voice.wav", json.loads((cache / "words.json").read_text())


def render(folder, source, beats, target, report):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise ValueError("Install FFmpeg to generate a preview.")
    script, cues = spoken_track(beats)
    report(5)
    audio, words = audio_track(target.parent, script)
    frames = match_cues(script, cues, words)
    duration = float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
        "default=noprint_wrappers=1:nokey=1", str(audio)
    ], text=True, timeout=15))
    if frames[-1][1] / FPS >= duration:
        raise ValueError("A panel cue falls after the end of the voiceover.")
    report(45)
    with TemporaryDirectory(dir=target.parent) as temporary:
        work = Path(temporary)
        manifest = ["ffconcat version 1.0"]
        with open_strip(folder, source, exact=True) as (strip, current):
            boxes = read_review(folder, current)
            if boxes is None:
                raise ValueError("Save the chapter panels first.")
            for index, (panel, frame) in enumerate(frames):
                left, top, right, bottom = boxes[int(panel[1:]) - 1]
                with strip.image(top, bottom, left, right) as crop:
                    fitted = ImageOps.contain(crop, SIZE, Image.Resampling.LANCZOS)
                    with Image.new("RGB", SIZE, "#101010") as canvas:
                        canvas.paste(fitted, ((SIZE[0] - fitted.width) // 2, (SIZE[1] - fitted.height) // 2))
                        canvas.save(work / f"{index}.jpg", quality=93)
                end = frames[index + 1][1] / FPS if index + 1 < len(frames) else duration
                manifest.extend([f"file {index}.jpg", f"option framerate {FPS}",
                                 f"duration {end - frame / FPS:.9f}"])
                report(45 + 25 * (index + 1) // len(frames))
        manifest.extend([f"file {len(frames) - 1}.jpg", f"option framerate {FPS}"])
        (work / "panels.ffconcat").write_text("\n".join(manifest) + "\n")
        with (work / "ffmpeg.log").open("w+") as errors:
            with subprocess.Popen([
                "ffmpeg", "-v", "error", "-nostdin", "-progress", "pipe:1",
                "-f", "concat", "-safe", "0", "-i", "panels.ffconcat", "-i", str(audio),
                "-t", str(duration), "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "20", "-g", str(FPS * 2), "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-movflags", "+faststart", "preview.mp4",
            ], cwd=work, stdout=subprocess.PIPE, stderr=errors, text=True) as encoder:
                for line in encoder.stdout:
                    if line.startswith("out_time_us="):
                        value = line.strip().partition("=")[2]
                        if value.isdigit():
                            report(min(99, 70 + int(29 * int(value) / (duration * 1_000_000))))
                if encoder.wait():
                    errors.seek(0)
                    raise RuntimeError(errors.read())
        (work / "preview.mp4").replace(target)
    report(100)
    return [(panel, frame / FPS) for panel, frame in frames]


def install(app, downloads):
    lock = Lock()
    progress = None
    percent = 0

    def ready(series, chapter):
        selected = selection(downloads, series, chapter)
        if not selected["accepted"]:
            for revision in selected["revisions"][1:]:
                candidate = selection(downloads, series, chapter, revision["id"])
                if candidate["accepted"]:
                    selected = candidate
                    break
        if not selected["accepted"]:
            raise ValueError("Accept the chapter in Narration first.")
        manifest = json.loads((selected["directory"] / "manifest.json").read_text())
        key = digest([selected["key"], VOICE, SPEED, SIZE, FPS, 1])
        folder = selected["folder"]
        target = folder / "Preview" / f"{key}.mp4"
        return selected, manifest["source"], key, target

    @app.get("/api/preview/chapter")
    def preview_chapter(series: str, chapter: str, request: Request):
        local_request(request)
        try:
            _, _, key, target = ready(series, chapter)
            return {"key": key, "ready": target.is_file()}
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/preview/progress/{progress_id}")
    def preview_progress(progress_id: UUID, request: Request):
        local_request(request)
        return {"percent": percent if progress == progress_id else None}

    @app.post("/api/preview/generate")
    def generate(body: PreviewRequest, request: Request):
        nonlocal progress, percent
        local_request(request)
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "A preview is already rendering.")
        try:
            selected, source, key, target = ready(body.series, body.chapter)
            if key != body.key:
                raise HTTPException(409, "The chapter changed. Select it again.")
            percent = 0
            progress = body.progress_id

            def report(value):
                nonlocal percent
                percent = max(percent, value)

            if not target.is_file():
                beats = [beat for reply in selected["replies"] for beat in reply["beats"]]
                render(selected["folder"], source, beats, target, report)
            return {"key": key, "ready": True}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ImportError as exc:
            raise HTTPException(503, "Run uv sync to install the voiceover dependency.") from exc
        except Exception as exc:
            logging.exception("Preview rendering failed")
            raise HTTPException(502, "Could not generate the preview. Check the speech connection and try again.") from exc
        finally:
            progress = None
            lock.release()

    @app.get("/api/preview/video/{key}")
    def preview_video(key: str, series: str, chapter: str, request: Request, download: bool = False):
        local_request(request)
        if not re.fullmatch(r"[a-f0-9]{64}", key):
            raise HTTPException(404)
        try:
            folder = chapter_folder(downloads, series, chapter)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        path = folder / "Preview" / f"{key}.mp4"
        if not path.is_file():
            raise HTTPException(404, "Generate the preview first.")
        return FileResponse(path, media_type="video/mp4",
                            filename=f"{series} - {chapter}.mp4" if download else None)
