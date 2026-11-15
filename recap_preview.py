"""Generate and serve a narrated panel preview."""
import asyncio
import json
import logging
import re
import shutil
import subprocess
from contextlib import ExitStack
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from narration import accepted_selection, digest
from panel_review import latest_strip, open_strip, read_review
from renumber import chapter_folder, chapter_images, directories
from studio_bridge import local_request

VOICE = "en-US-SteffanNeural"
SPEED = 1.2
FPS = 30
SIZE = (1280, 720)
BELL = Path(__file__).resolve().parent / "data" / "episode-one-bell.mp3"
MOVES = ("left", "right", "in", "up", "out", "down", "right", "up", "left", "out", "down", "in")
SLIDES = {"left": (-110, 0), "right": (110, 0), "up": (0, -65), "down": (0, 65)}
RENDER_VERSION = 10


class PreviewRequest(BaseModel):
    series: str
    chapter: str
    end_chapter: str | None = None
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


def panel_layers(crop, size=SIZE):
    width, height = size
    # Stretch a small copy across the frame and soften detail before enlarging.
    background = crop.resize((320, 180), Image.Resampling.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(12)).resize(size, Image.Resampling.BICUBIC)
    canvas = Image.blend(background, Image.new("RGB", size, "#f6f4f1"), 0.34)
    foreground = ImageOps.contain(crop, (width - 112, height), Image.Resampling.LANCZOS)
    left = (width - foreground.width) // 2
    top = (height - foreground.height) // 2
    shadow = Image.new("L", size)
    ImageDraw.Draw(shadow).rectangle(
        (left, top + 10, left + foreground.width - 1, top + foreground.height + 9), fill=158
    )
    layer = Image.new("RGBA", size, "#17131a")
    layer.putalpha(shadow.filter(ImageFilter.GaussianBlur(16)))
    layer.paste(foreground, (left, top))
    return canvas, layer


@lru_cache(maxsize=1)
def watermark_overlay():
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 22)
    except OSError:
        font = ImageFont.load_default(size=22)
    overlay = Image.new("RGBA", (280, 44))
    ImageDraw.Draw(overlay).text(
        (278, 42), "UNLIMITED RECAPS", font=font, anchor="rb",
        fill=(255, 255, 255, 100), stroke_width=1, stroke_fill=(0, 0, 0, 70),
    )
    return overlay


def panel_frame(background, layer, move, phase):
    width, height = background.size
    dx, dy = SLIDES.get(move, (0, 0))
    scale = {"in": 1 + 0.045 * phase, "out": 1.05 - 0.05 * phase, "tall": 1 + 0.24 * phase}.get(move, 1)
    inverse = 1 / scale
    shifted = layer.transform(
        background.size, Image.Transform.AFFINE,
        (inverse, 0, width * (1 - inverse) / 2 - dx * (1 - phase) * inverse,
         0, inverse, height * (1 - inverse) / 2 - dy * (1 - phase) * inverse),
        Image.Resampling.BICUBIC,
    )
    canvas = background.copy()
    canvas.paste(shifted, (0, 0), shifted)
    apply_watermark(canvas)
    return canvas


def contained_pan(crop, backdrop, aperture, move, phase):
    left, top, width, height = aperture
    dx, dy = SLIDES[move]
    # A small overscan gives the pan room to move while the panel edges stay put.
    scale = 1.08 * max(width / crop.width, height / crop.height)
    spare_x = (crop.width - width / scale) / 2
    spare_y = (crop.height - height / scale) / 2
    source_x = spare_x - (spare_x if dx > 0 else -spare_x) * (1 - phase) if dx else spare_x
    source_y = spare_y - (spare_y if dy > 0 else -spare_y) * (1 - phase) if dy else spare_y
    with crop.transform((width, height), Image.Transform.AFFINE,
                        (1 / scale, 0, source_x, 0, 1 / scale, source_y),
                        Image.Resampling.BILINEAR) as foreground:
        frame = backdrop.copy()
        frame.paste(foreground, (left, top))
    apply_watermark(frame)
    return frame


def apply_watermark(canvas):
    width, height = canvas.size
    watermark = watermark_overlay()
    canvas.paste(watermark, (width - watermark.width - 22, height - watermark.height - 18), watermark)


def render(folder, source, beats, target, report, *, opening_bell=False, size=SIZE, panel_image=None, continuous_motion=True):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise ValueError("Install FFmpeg to generate a preview.")
    if opening_bell and not BELL.is_file():
        raise ValueError("Copy episode-one-bell.mp3 into the Bliss data folder to render Episode 1.")
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
        sound_options = ["-map", "0:v", "-map", "1:a"]
        if opening_bell:
            sound_options = [
                "-i", str(BELL), "-filter_complex",
                "[2:a]volume=0.65[bell];[1:a][bell]amix=inputs=2:duration=first:normalize=0,"
                "alimiter=limit=0.95:level=false:latency=true[a]",
                "-map", "0:v", "-map", "[a]",
            ]
        with (work / "ffmpeg.log").open("w+") as errors:
            with subprocess.Popen([
                "ffmpeg", "-v", "error", "-nostdin",
                "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{size[0]}x{size[1]}",
                "-framerate", str(FPS), "-i", "pipe:0", "-i", str(audio),
                *sound_options,
                "-t", str(duration), "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "20", "-g", str(FPS * 2), "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-movflags", "+faststart", "preview.mp4",
            ], cwd=work, stdin=subprocess.PIPE, stderr=errors) as encoder, ExitStack() as stack:
                if panel_image is None:
                    strip, current = stack.enter_context(open_strip(folder, source, exact=True))
                    boxes = read_review(folder, current)
                    if boxes is None:
                        raise ValueError("Save the chapter panels first.")

                    def panel_image(panel):
                        left, top, right, bottom = boxes[int(panel[1:]) - 1]
                        return strip.image(top, bottom, left, right)

                total_frames = max(frames[-1][1] + 1, round(duration * FPS))
                try:
                    for index, (panel, frame) in enumerate(frames):
                        next_frame = frames[index + 1][1] if index + 1 < len(frames) else total_frames
                        count = next_frame - frame
                        move = "in" if index == 0 else MOVES[(index - 1) % len(MOVES)]
                        with ExitStack() as images:
                            crop = images.enter_context(panel_image(panel))
                            tall = crop.height >= 1.5 * crop.width
                            if continuous_motion and tall:
                                if move in ("in", "out"):
                                    move = "tall"
                                elif move == "left":
                                    move = "up"
                                elif move == "right":
                                    move = "down"
                            background, layer = panel_layers(crop, size)
                            images.enter_context(background)
                            images.enter_context(layer)
                            use_pan = continuous_motion and move in SLIDES
                            if use_pan:
                                with ImageOps.contain(crop, (size[0] - 112, size[1])) as foreground:
                                    width, height = foreground.size
                                aperture = ((size[0] - width) // 2, (size[1] - height) // 2, width, height)
                                # Preserve the static shadow underneath the fixed panel window.
                                background.paste(layer, (0, 0), layer)
                            travel = count - 1 if continuous_motion else min(count - 1, 6 if index == 0 else 8)
                            for step in range(count):
                                if step <= travel:
                                    phase = step / travel if travel else 1
                                    if not continuous_motion:
                                        phase = phase * phase * (3 - 2 * phase)
                                    if use_pan:
                                        canvas = contained_pan(crop, background, aperture, move, phase)
                                    else:
                                        canvas = panel_frame(background, layer, move, phase)
                                    with canvas:
                                        pixels = canvas.tobytes()
                                encoder.stdin.write(pixels)
                                if step % FPS == 0:
                                    report(45 + 54 * (frame + step) // total_frames)
                    encoder.stdin.close()
                except BrokenPipeError:
                    pass
                except BaseException:
                    encoder.kill()
                    raise
                if encoder.wait():
                    errors.seek(0)
                    raise RuntimeError(errors.read() or "FFmpeg could not encode the preview.")
        (work / "preview.mp4").replace(target)
    report(100)
    return [(panel, frame / FPS) for panel, frame in frames]


def chapter_range(downloads, series, chapter, end_chapter):
    first = chapter_folder(downloads, series, chapter)
    if not end_chapter or end_chapter == chapter:
        return [first]
    last = chapter_folder(downloads, series, end_chapter)
    chapters = [folder for folder in directories(first.parent)
                if latest_strip(folder) or chapter_images(folder)]
    if first not in chapters or last not in chapters:
        raise ValueError("Choose available chapters.")
    start, end = chapters.index(first), chapters.index(last)
    if start > end:
        raise ValueError("Choose an end chapter at or after the start chapter.")
    return chapters[start:end + 1]


def join_previews(clips, target):
    if not shutil.which("ffmpeg"):
        raise ValueError("Install FFmpeg to generate a preview.")
    with TemporaryDirectory(dir=target.parent) as temporary:
        work = Path(temporary)
        # Use local aliases so chapter titles never need ffconcat escaping.
        for index, clip in enumerate(clips):
            alias = work / f"{index}.mp4"
            try:
                alias.hardlink_to(clip)
            except OSError:
                shutil.copyfile(clip, alias)
        (work / "chapters.ffconcat").write_text(
            "ffconcat version 1.0\n" + "".join(f"file {index}.mp4\n" for index in range(len(clips)))
        )
        subprocess.run([
            "ffmpeg", "-v", "error", "-nostdin", "-f", "concat", "-i", "chapters.ffconcat",
            "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-movflags", "+faststart", "preview.mp4",
        ], cwd=work, check=True, capture_output=True, text=True)
        (work / "preview.mp4").replace(target)


def install(app, downloads, *, lock=None):
    if lock is None:
        lock = Lock()
    progress = None
    percent = 0

    def ready(series, chapter, *, first):
        selected = accepted_selection(downloads, series, chapter)
        manifest = json.loads((selected["directory"] / "manifest.json").read_text())
        settings = [selected["key"], VOICE, SPEED, SIZE, FPS, RENDER_VERSION]
        episode_one = selected["number"] == 1
        if episode_one and not first:
            settings.append("no opening bell")
        key = digest(settings)
        folder = selected["folder"]
        target = folder / "Preview" / f"{key}.mp4"
        return selected, manifest["source"], key, target, first and episode_one

    def prepare(series, chapter, end_chapter):
        chapters = chapter_range(downloads, series, chapter, end_chapter)
        clips = []
        for index, folder in enumerate(chapters):
            try:
                clips.append(ready(series, folder.name, first=index == 0))
            except (ValueError, OSError) as exc:
                raise ValueError(f"{folder.name}: {exc}") from exc
        _, _, key, target, _ = clips[0]
        if len(clips) > 1:
            key = digest(["chapter range", 1, [clip[2] for clip in clips]])
            target = target.with_name(f"{key}.mp4")
        return clips, key, target

    @app.get("/api/preview/chapter")
    def preview_chapter(series: str, chapter: str, request: Request, end_chapter: str | None = None):
        local_request(request)
        try:
            _, key, target = prepare(series, chapter, end_chapter)
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
            raise HTTPException(409, "Wait for the current chapter operation to finish.")
        try:
            clips, key, target = prepare(body.series, body.chapter, body.end_chapter)
            if key != body.key:
                raise HTTPException(409, "The selected chapters changed. Select them again.")
            percent = 0
            progress = body.progress_id

            def report(value):
                nonlocal percent
                percent = max(percent, value)

            if not target.is_file():
                limit = 95 if len(clips) > 1 else 100
                for index, (selected, source, _, clip, opening_bell) in enumerate(clips):
                    if not clip.is_file():
                        beats = [beat for reply in selected["replies"] for beat in reply["beats"]]
                        render(selected["folder"], source, beats, clip,
                               lambda value: report(limit * (index * 100 + value) // (len(clips) * 100)), opening_bell=opening_bell)
                    report(limit * (index + 1) // len(clips))
                if len(clips) > 1:
                    join_previews([clip[3] for clip in clips], target)
            report(100)
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
    def preview_video(key: str, series: str, chapter: str, request: Request,
                      download: bool = False, end_chapter: str | None = None):
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
        title = f"{chapter} to {end_chapter}" if end_chapter and end_chapter != chapter else chapter
        return FileResponse(path, media_type="video/mp4",
                            filename=f"{series} - {title}.mp4" if download else None)
