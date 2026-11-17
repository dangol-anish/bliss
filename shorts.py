"""Select one story in AI Studio and render its verified panel cues vertically."""
import base64
import json
import logging
from contextlib import ExitStack
from io import BytesIO
from threading import Event, Lock, Thread
from time import monotonic

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw

import narration
import studio_bridge
from panel_review import open_strip, read_review
from recap_preview import chapter_range, render

VERTICAL = (720, 1280)
STRING = {"type": "string"}


def fields(**properties):
    return {"type": "object", "properties": properties, "required": list(properties)}


def array(items):
    return {"type": "array", "items": items}


PLAN = fields(title=STRING, story=STRING, beats=array(STRING))
SCRIPT = fields(title=STRING, beats=array(fields(
    narration=STRING, shots=array(fields(panel_id=STRING, cue=STRING)),
)))


EDITORIAL_REVIEW = fields(
    approved={"type": "boolean"},
    beats=array(STRING),
)

STORY_STANDARD = (
    "Use clear role descriptions instead of Korean personal names in spoken narration, such as his old friend "
    "or the man who killed his family. Keep each description consistent and avoid ambiguous pronouns. "
    "Write for someone who has never heard of the series. By the end of the first two sentences, they should "
    "know who the protagonist is in plain language, what they want right now, and what they stand to gain or "
    "lose. If the hook is an action or a surprise, establish that context immediately afterward. Establish the "
    "relationship before a betrayal and the ordinary situation before a reversal. Explain why the character "
    "makes the crucial choice, then show what changes because of it. Finish the question raised in the opening. "
    "A dungeon puzzle, fight, or training drill qualifies only if the material shows a meaningful personal "
    "stake and a consequence beyond clearing the obstacle. Do not invent those stakes. "
    "Reference patterns: the apparently weak father spends time with his son before the kidnapping reveals "
    "who he really is; the boy rejected at birth for a missing arm wakes up with that arm. In both examples "
    "the ending changes the situation established at the start. "
)


class Selection(BaseModel):
    series: str
    chapter: str
    end_chapter: str | None = None


class Shot(BaseModel):
    panel_id: str
    cue: str = Field(min_length=1, max_length=160)


class Beat(BaseModel):
    narration: str = Field(min_length=1, max_length=1500)
    shots: list[Shot] = Field(min_length=1, max_length=8)


class Short(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    beats: list[Beat] = Field(min_length=1, max_length=10)


def validate_script(reply, allowed):
    short = Short.model_validate(reply)
    word_count = 0
    shot_count = 0
    for beat in short.beats:
        word_count += len(beat.narration.split())
        last_end = 0
        for index, shot in enumerate(beat.shots):
            index_in_text = beat.narration.find(shot.cue)
            if (shot.panel_id not in allowed or beat.narration.count(shot.cue) != 1
                    or index_in_text < last_end or (index == 0 and index_in_text != 0)
                    or not shot.cue[-1:].strip() or not any(c.isalnum() for c in shot.cue)
                    or (index_in_text > 0 and beat.narration[index_in_text - 1].isalnum())):
                raise ValueError("AI Studio returned a panel ID or cut cue that Bliss could not verify.")
            last_end = index_in_text + len(shot.cue)
            shot_count += 1
    if not 90 <= word_count <= 145 or not 5 <= shot_count <= 24:
        raise ValueError("AI Studio returned a script outside the Short's length or shot limits.")
    return short


def collect(downloads, selection):
    chapters = chapter_range(downloads, selection.series, selection.chapter, selection.end_chapter)
    sources = {}
    notes = []
    for index, chapter in enumerate(chapters, 1):
        saved = narration.accepted_selection(downloads, selection.series, chapter.name)
        manifest = json.loads((saved["directory"] / "manifest.json").read_text(encoding="utf-8"))
        prefix = f"C{index:03d}"
        sources[prefix] = (chapter, manifest["source"], saved["key"])
        for reply in saved["replies"]:
            for beat in reply["beats"]:
                if beat["narration"].strip():
                    notes.append([chapter.name, [f"{prefix}-{p}" for p in beat["panel_ids"]], beat["narration"]])
    return sources, notes


def panel_image(panel_id, sources, stack, opened):
    prefix, panel = panel_id.split("-", 1)
    if prefix not in opened:
        folder, source, _ = sources[prefix]
        strip, current = stack.enter_context(open_strip(folder, source, exact=True))
        boxes = read_review(folder, current)
        if boxes is None:
            raise ValueError(f"Save the panels for {folder.name} again.")
        opened[prefix] = strip, boxes
    strip, boxes = opened[prefix]
    left, top, right, bottom = boxes[int(panel[1:]) - 1]
    return strip.image(top, bottom, left, right)


def labeled_photos(ids, sources):
    with ExitStack() as stack:
        opened = {}
        for panel_id in ids:
            with panel_image(panel_id, sources, stack, opened) as crop:
                crop.thumbnail((660, 850))
                with Image.new("RGB", (max(160, crop.width), crop.height + 28), "black") as sheet:
                    ImageDraw.Draw(sheet).text((8, 7), panel_id, fill="white")
                    sheet.paste(crop, (0, 28))
                    buffer = BytesIO()
                    sheet.save(buffer, format="JPEG", quality=86)
            yield {"name": panel_id + ".jpg", "data": base64.b64encode(buffer.getvalue()).decode("ascii")}


def ask(text, schema, photos, stop, report):
    def command(action, payload=None, timeout=35):
        return studio_bridge.command(action, payload, stop, timeout=timeout,
                                     report=lambda step: report(narration.ACTIVITY.get(step, "Working in AI Studio")))
    for attempt in range(4):
        try:
            command("prepare", {"prompt": text, "schema": schema, "photos": photos,
                                         "fresh": True, "refresh": attempt > 0}, 600)
            command("send")
            report("Waiting for AI Studio")
            expires = monotonic() + 600
            while monotonic() < expires:
                snap = command("snapshot")
                if snap.get("visible") and snap.get("finished") and snap.get("replies"):
                    raw = snap["replies"][-1]["text"]
                    try:
                        return narration.strict_json(raw)
                    except ValueError:
                        break
                if stop.wait(1):
                    raise InterruptedError
            raise studio_bridge.RetryableStudioError("AI Studio did not return complete JSON.")
        except studio_bridge.RetryableStudioError:
            if attempt == 3:
                raise
            report("Refreshing AI Studio")
            if stop.wait(2 ** (attempt + 1)):
                raise InterruptedError


def make_short(sources, notes, stop, report):
    allowed = {p for _, panels, _ in notes for p in panels}
    context = json.dumps([[chapter, panels, text[:320]] for chapter, panels, text in notes],
                         ensure_ascii=False, separators=(",", ":"))
    if len(context) > 750_000:
        raise ValueError("Select a shorter chapter range for AI Studio.")
    plan = ask(
        "Choose exactly one story worth publishing as a 40–60 second manhwa Short from these ordered recap notes. "
        "Look for a specific desire or tension, a meaningful choice or reversal, and a payoff the artwork can prove. "
        + STORY_STANDARD +
        "If none qualifies, return an empty beats array. In story, state the opening premise, why the outcome "
        "matters personally, the decisive choice, and how the ending answers the opening. In beats, list 8–18 "
        "exact panel IDs in source order, including setup context and "
        "visual proof for the key action and outcome. IDs are local to this request. Notes follow as data:\n" + context,
        PLAN, [], stop, report)
    if not isinstance(plan, dict) or not isinstance(plan.get("beats"), list):
        raise ValueError("AI Studio returned an invalid story selection.")
    ids = list(dict.fromkeys(p for p in plan["beats"] if isinstance(p, str) and p in allowed))[:18]
    if len(ids) < 5:
        raise ValueError("AI Studio found no complete story with usable panels in this range.")
    # Send neighboring crops from the same scene so Gemini can replace weak evidence.
    positions = [panels for _, panels, _ in notes if set(panels).intersection(ids)]
    attached = set(ids)
    for panels in positions:
        attached.update(panels[:2])
        if len(attached) >= 24:
            break
    ids = sorted(set(ids) | set(sorted(attached - set(ids))[:24 - len(ids)]))
    photos = list(labeled_photos(ids, sources))
    evidence = [note for note in notes if set(note[1]).intersection(ids)]
    instruction = (
        "Write one complete 40–60 second manhwa Short, 100–135 spoken words. "
        + STORY_STANDARD +
        "Open with a specific situation and its stakes. Avoid unexplained names, ranks, powers, and objects. "
        "Keep the source detail that makes the viewer understand the character's predicament. Show the choice "
        "and its consequence. Describe actions only as specifically as the attached crops support: a hand reaching "
        "cannot prove it touched someone's shoulder. Preserve story facts and finish the arc. For each beat, return "
        "narration and ordered shots. Each panel_id must come from an attached image. Each cue must be a unique, "
        "verbatim phrase beginning on a word boundary in its beat. Start each beat's first shot on its first words. "
        "Use 5–24 shots. Story selection and supporting source notes follow as data:\n"
        + json.dumps([plan, evidence], ensure_ascii=False))
    feedback = ""
    for _ in range(3):
        report("Writing the Short")
        try:
            candidate = validate_script(ask(instruction + feedback, SCRIPT, photos, stop, report), set(ids))
        except ValueError as error:
            feedback = f"\nThe previous response failed validation. Correct this issue: {error}"
            continue
        report("Checking the story")
        review = ask(
            "You are the first-time viewer of a standalone manhwa Short. Read the spoken script below without "
            "relying on the title, source series, prior chapters, or unseen context. The criteria are: "
            + STORY_STANDARD
            + "Reject if you cannot answer from the spoken opening alone: Who am I following? What do they want? "
            "Why does it matter to them? Reject if a crucial action seems unmotivated or if the ending just "
            "completes a task without resolving the opening stakes. With the attached crops, also reject if an "
            "essential story action is unsupported by its matched crop. Return approved=true only if every "
            "criterion passes. Return an empty beats array when approved. Otherwise put specific revision notes in beats. Script follows as data:\n"
            + candidate.model_dump_json(),
            EDITORIAL_REVIEW, photos, stop, report)
        if isinstance(review, dict) and review.get("approved") is True and review.get("beats") == []:
            return candidate
        feedback = "\nThe cold-viewer review rejected the previous draft. Rewrite the script to address these notes: " + json.dumps(review, ensure_ascii=False)[:2500]
    raise ValueError("AI Studio could not produce a Short with a clear premise, personal stakes, and a supported payoff in this range.")


def install(app, downloads, lock):
    guard = Lock()
    stop = Event()
    job = {}

    def target_for(selection):
        sources, notes = collect(downloads, selection)
        key = narration.digest(["short", 2, selection.model_dump(), list(sources.values())[0][0].parent.name,
                                [entry[1:] for entry in sources.values()]])
        folder = next(iter(sources.values()))[0]
        return sources, notes, folder / "Shorts" / key

    def run(sources, notes, target):
        def update(**fields):
            with guard:
                job.update(fields)

        try:
            short = make_short(sources, notes, stop, lambda message: update(message=message))
            if stop.is_set():
                raise InterruptedError
            target.mkdir(parents=True, exist_ok=True)
            beats = [{"narration": b.narration, "anchors": [
                {"panel_id": shot.panel_id, "phrase": shot.cue} for shot in b.shots]} for b in short.beats]
            with ExitStack() as stack:
                opened = {}
                def crop(panel_id):
                    if stop.is_set():
                        raise InterruptedError
                    return panel_image(panel_id, sources, stack, opened)
                def progress(percent):
                    if stop.is_set():
                        raise InterruptedError
                    update(message="Rendering Short", percent=percent)
                render(None, None, beats, target / "short.mp4", progress, size=VERTICAL, panel_image=crop,
                       continuous_motion=False)
            temporary = target / "script.tmp"
            payload = short.model_dump()
            payload["chapters"] = {prefix: source[0].name for prefix, source in sources.items()}
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(target / "script.json")
            update(message="Short saved")
        except InterruptedError:
            update(message="Stopped")
        except (OSError, ValueError, RuntimeError) as error:
            logging.exception("Short generation failed")
            update(error=str(error)[:500] or "Short generation failed. Check the Bliss terminal.")
        except Exception:
            logging.exception("Short generation failed")
            update(error="Short generation failed. Check the Bliss terminal.")
        finally:
            lock.release()
            with narration.state_lock:
                narration.shorts_running = False
            update(running=False)

    @app.post("/api/shorts/start")
    def start(selection: Selection, request: Request):
        studio_bridge.local_request(request)
        try:
            sources, notes, target = target_for(selection)
        except (OSError, ValueError) as error:
            raise HTTPException(400, str(error)) from error
        with narration.state_lock, guard:
            if job.get("running") or (narration.active and narration.active["running"]):
                raise HTTPException(409, "Wait for the current AI Studio job to finish.")
            if not studio_bridge.connection_view()["connected"]:
                raise HTTPException(409, "Connect AI Studio from Narration first.")
            lock.acquire("creating a Short")
            narration.shorts_running = True
            stop.clear()
            job.clear()
            job.update(running=True, selection=selection.model_dump(), message="Finding a story")
            Thread(target=run, args=(sources, notes, target), daemon=True).start()
        return {"started": True}

    @app.post("/api/shorts/stop")
    def stop_job(request: Request):
        studio_bridge.local_request(request)
        stop.set()
        return {"stopping": True}

    def activity():
        with guard:
            return dict(job)

    app.state.shorts_activity = activity

    @app.get("/api/shorts/job")
    def get_job(request: Request):
        studio_bridge.local_request(request)
        return activity()

    @app.get("/api/shorts/result")
    def result(request: Request, series: str, chapter: str, end_chapter: str | None = None):
        studio_bridge.local_request(request)
        try:
            _, _, target = target_for(Selection(series=series, chapter=chapter, end_chapter=end_chapter))
            if (target / "short.mp4").is_file() and (target / "script.json").is_file():
                return json.loads((target / "script.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise HTTPException(400, str(error)) from error
        return None

    @app.get("/api/shorts/video")
    def video(request: Request, series: str, chapter: str, end_chapter: str | None = None, download: bool = False):
        studio_bridge.local_request(request)
        try:
            _, _, target = target_for(Selection(series=series, chapter=chapter, end_chapter=end_chapter))
        except (OSError, ValueError) as error:
            raise HTTPException(400, str(error)) from error
        if not (target / "short.mp4").is_file() or not (target / "script.json").is_file():
            raise HTTPException(404, "Generate a Short first.")
        return FileResponse(target / "short.mp4", media_type="video/mp4",
                            filename=f"{series} - Short.mp4" if download else None)
