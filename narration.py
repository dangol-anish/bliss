"""Prepare saved split images, validate replies, and retain accepted story revisions."""
import base64
from contextlib import closing, contextmanager
from decimal import Decimal
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import sys
from threading import Event, Lock, Thread
from time import monotonic

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from desktop import play_alert
from panel_review import open_strip, read_review
from renumber import chapter_folder
from strip_split import OUTPUT_DIR
import studio_bridge

ROOT = Path(__file__).resolve().parent
if sys.platform == "win32":
    app_support = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
else:
    app_support = Path.home() / "Library/Application Support"
LIBRARY = Path(os.environ.get("BLISS_STORY_LIBRARY", app_support / "Bliss" / "Story"))
PROMPT = ROOT / "prompts/narration_master.md"
SCHEMA = ROOT / "browser-extension/narration-schema.json"
MAX_BATCH_BYTES = 32_000_000
router = APIRouter(prefix="/api/narration")
state_lock = Lock()
cancel = Event()
active = None
ACTIVITY = {
    "opening_tab": "Opening AI Studio", "configuring": "Configuring AI Studio",
    "checking_schema": "Verifying Structured outputs", "inserting_prompt": "Adding the prompt",
    "attaching_photos": "Attaching images", "waiting_upload": "Waiting for uploads",
    "submitting": "Submitting the batch",
}


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=True, sort_keys=True).encode()).hexdigest()


@contextmanager
def library():
    LIBRARY.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(LIBRARY / "story.sqlite3")) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS checkpoints (
            revision INTEGER PRIMARY KEY, series TEXT NOT NULL, chapter TEXT NOT NULL,
            number TEXT NOT NULL, parent INTEGER, draft TEXT NOT NULL UNIQUE, payload TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS drafts (
            id TEXT PRIMARY KEY, replies TEXT NOT NULL, submitted INTEGER NOT NULL
        )""")
        yield db


def checkpoint_before(db, series, number):
    # Follow the accepted chain. Rewriting an older chapter leaves descendants archived.
    head = db.execute("SELECT * FROM checkpoints WHERE series=? ORDER BY revision DESC LIMIT 1", (series,)).fetchone()
    while head and Decimal(head["number"]) >= number:
        head = db.execute("SELECT * FROM checkpoints WHERE revision=?", (head["parent"],)).fetchone()
    if not head:
        return None
    return {"revision": head["revision"], "chapter": head["chapter"], **json.loads(head["payload"])}


def draft_state(db, key):
    row = db.execute("SELECT replies, submitted FROM drafts WHERE id=?", (key,)).fetchone()
    return (json.loads(row["replies"]), bool(row["submitted"])) if row else ([], False)


def save_draft(key, replies, submitted):
    with library() as db:
        db.execute("INSERT OR REPLACE INTO drafts VALUES (?, ?, ?)", (key, json.dumps(replies), submitted))


def identify(folder):
    match = re.search(r"\b(?:chapter|episode|ch)[ ._-]*(\d+(?:\.\d+)?)", folder.name, re.I)
    marker = folder / ".bliss-chapter"
    chapter_number = match[1] if match else (marker.read_text().strip() if marker.is_file() else folder.name)
    if not re.fullmatch(r"\d+(?:\.\d+)?", chapter_number):
        raise ValueError("Use a numbered chapter so Bliss can find its earlier story checkpoint.")
    marker = folder.parent / ".bliss-series"
    series = marker.read_text().strip() if marker.is_file() else "local:" + folder.parent.name.casefold()
    return series, Decimal(chapter_number)


def available_splits(folder, source, boxes):
    root = folder / OUTPUT_DIR
    if root.is_symlink() or not root.is_dir():
        return []
    revisions = []
    for directory in root.iterdir():
        if not re.fullmatch(r"[0-9a-f]{16}", directory.name) or directory.is_symlink():
            continue
        manifest = directory / "manifest.json"
        try:
            if manifest.is_symlink():
                continue
            raw = json.loads(manifest.read_text())
            sections = raw["sections"]
            if raw["source"] != source or not sections:
                continue
            # Match both saved Split Strip manifest formats.
            basis = [source, boxes, raw["minimum"], raw["maximum"]]
            fingerprints = {
                sha256(json.dumps([*basis, sections]).encode()).hexdigest(),
                sha256(json.dumps([*basis, [0, *(s["bottom"] for s in sections)]]).encode()).hexdigest(),
            }
            if raw["id"] not in fingerprints or not raw["id"].startswith(directory.name):
                continue
            if any(not re.fullmatch(r"\d+\.(?:jpg|png)", section["file"])
                   or (directory / section["file"]).is_symlink()
                   or not (directory / section["file"]).is_file() for section in sections):
                continue
            revisions.append((manifest.stat().st_mtime_ns, directory, raw))
        except (OSError, KeyError, TypeError, ValueError):
            continue
    return sorted(revisions, reverse=True)


def accepted_selection(downloads, series, chapter):
    selected = selection(downloads, series, chapter)
    if selected["accepted"]:
        return selected
    for revision in selected["revisions"][1:]:
        candidate = selection(downloads, series, chapter, revision["id"])
        if candidate["accepted"]:
            return candidate
    raise ValueError("Finish the chapter in Narration first.")


def selection(downloads, series, chapter, revision=None):
    folder = chapter_folder(downloads, series, chapter)
    series_id, number = identify(folder)
    with open_strip(folder) as (_, source):
        boxes = read_review(folder, source)
    if boxes is None:
        raise ValueError("Save the chapter panels and split images first.")
    revisions = available_splits(folder, source, boxes)
    if not revisions:
        raise ValueError("Save split images for the current panel edits first.")
    selected = next((item for item in revisions if item[1].name == revision), None) if revision else revisions[0]
    if selected is None:
        raise ValueError("This split no longer matches the saved panel edits.")
    _, directory, manifest = selected
    with library() as db:
        checkpoint = checkpoint_before(db, series_id, number)
        parent = checkpoint["revision"] if checkpoint else None
        prompt = PROMPT.read_text()
        schema = json.loads(SCHEMA.read_text())
        file_state = [(s["file"], (directory / s["file"]).stat().st_size,
                       (directory / s["file"]).stat().st_mtime_ns) for s in manifest["sections"]]
        key = digest([series_id, chapter, manifest["id"], parent, prompt, schema, file_state])
        replies, submitted = draft_state(db, key)
        accepted = db.execute("SELECT revision FROM checkpoints WHERE draft=?", (key,)).fetchone()
    batches = []
    group = []
    size = 0
    for section, (_, length, _) in zip(manifest["sections"], file_state):
        if length > MAX_BATCH_BYTES:
            raise ValueError(f"{section['file']} exceeds the upload limit. Save a smaller split in Split Strip.")
        if len(group) == 20 or size + length > MAX_BATCH_BYTES:
            batches.append(group)
            group, size = [], 0
        group.append(section)
        size += length
    if group:
        batches.append(group)
    return {"folder": folder, "directory": directory, "series": series, "series_id": series_id,
            "chapter": chapter, "number": number, "parent": parent, "checkpoint": checkpoint,
            "revisions": [{"id": path.name, "images": len(raw["sections"]), "minimum": raw["minimum"],
                           "maximum": raw["maximum"]} for _, path, raw in revisions],
            "key": key, "files": file_state, "batches": batches, "replies": replies, "submitted": submitted,
            "accepted": bool(accepted), "prompt": prompt, "schema": schema}


def story_context(checkpoint, chapter, replies):
    context = dict(checkpoint or {})
    cast = {(entry["name"].casefold() or entry["role"].casefold(), entry["look"].casefold() if not entry["name"] else ""): entry
            for entry in context.get("cast", [])}
    memory = list(context.get("memory", []))
    for reply in replies:
        for entry in reply["cast"]:
            key = (entry["name"].casefold() or entry["role"].casefold(), entry["look"].casefold() if not entry["name"] else "")
            previous = cast.get(key, {})
            cast[key] = {**previous, **{k: v for k, v in entry.items() if v or k == "name"}}
            cast[key]["aliases"] = list(dict.fromkeys([*previous.get("aliases", []), *entry["aliases"]]))
        memory.extend({**fact, "chapter": chapter} for fact in reply["memory"])
        context["continuity"] = reply["continuity"]
        context["chapter_summary"] = reply["chapter_summary"]
        for beat in reply["beats"]:
            if beat["narration"].strip():
                context["previous_narration_ending"] = beat["narration"]
    context.update(cast=list(cast.values()), memory=memory)
    return context


class Cast(BaseModel):
    name: str = Field(max_length=400)
    gender: str = Field(max_length=400)
    age_group: str = Field(max_length=400)
    role: str = Field(max_length=400)
    look: str = Field(max_length=800)
    aliases: list[str] = Field(default_factory=list, max_length=30)


class Anchor(BaseModel):
    panel_id: str
    phrase: str = Field(min_length=1, max_length=200)


class Beat(BaseModel):
    panel_ids: list[str]
    narration: str = Field(max_length=10000)
    unsure: str = Field(max_length=1000)
    anchors: list[Anchor]


class Fact(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    status: str
    panel_ids: list[str] = Field(min_length=1)


class Continuity(BaseModel):
    summary: str = Field(max_length=5000)
    open_threads: list[str] = Field(max_length=50)


class Reply(BaseModel):
    batch_receipt: dict[str, str]
    cast: list[Cast] = Field(max_length=100)
    beats: list[Beat] = Field(max_length=1000)
    continuity: Continuity
    chapter_summary: str = Field(max_length=1200)
    memory: list[Fact] = Field(max_length=30)


def strict_json(text):
    def unique(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError("The reply contains duplicate JSON keys.")
        return value

    def invalid_constant(value):
        raise ValueError(f"The reply contains an invalid JSON value: {value}.")

    return json.loads(text, object_pairs_hook=unique, parse_constant=invalid_constant)


def validate_reply(text, receipt, panel_ids):
    data = strict_json(text)
    if isinstance(data, dict) and set(data) == {"response"} and isinstance(data["response"], str):
        data = strict_json(data["response"])
    if isinstance(data, dict) and data.get("error"):
        raise ValueError(str(data["error"])[:500])
    try:
        reply = Reply.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        raise ValueError(f"Gemini returned an invalid narration field ({location}): {first['msg']}") from exc
    if reply.batch_receipt != receipt:
        raise ValueError("Gemini returned the wrong batch receipt.")
    order = {panel: index for index, panel in enumerate(panel_ids)}
    previous_panel = -1
    for beat in reply.beats:
        if not beat.panel_ids or [a.panel_id for a in beat.anchors] != (beat.panel_ids if beat.narration.strip() else []):
            raise ValueError("Panel cues do not match the beat's selected panels.")
        previous_end = 0
        for panel in beat.panel_ids:
            if order.get(panel, -1) <= previous_panel:
                raise ValueError("The narration uses an unknown, repeated, or out-of-order panel.")
            previous_panel = order[panel]
        if len(beat.narration.split()) > 40 * len(beat.panel_ids):
            raise ValueError("A beat exceeds its panel word limit.")
        for anchor in beat.anchors:
            phrase = anchor.phrase
            matches = list(re.finditer(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", beat.narration))
            if len(matches) != 1 or matches[0].start() < previous_end:
                raise ValueError("A cue phrase is missing, repeated, or out of order in the narration.")
            previous_end = matches[0].end()
    for fact in reply.memory:
        if fact.status not in {"confirmed", "uncertain"} or any(panel not in order for panel in fact.panel_ids):
            raise ValueError("Story memory refers to an invalid status or a panel outside this batch.")
    return reply.model_dump()


def receipt_and_prompt(selected, index, replies):
    context = story_context(selected["checkpoint"], selected["chapter"], replies)
    context["memory"] = context["memory"][-60:]
    if not replies and selected["checkpoint"]:
        context["previous_chapter"] = selected["checkpoint"]["chapter"]
    batch = selected["batches"][index]
    panel_ids = [panel for section in batch for panel in section["panels"]]
    pages = [section["file"] for section in batch]
    receipt = {
        "manga_id": selected["series_id"], "chapter": selected["chapter"],
        "batch_id": f"batch-{index + 1:03}", "fingerprint": selected["key"],
        "context_token": digest(context),
    }
    prompt = "\n\n".join([
        selected["prompt"], f"MANHWA: {selected['series']} / CHAPTER: {selected['chapter']}",
        f"CURRENT_BATCH: {receipt['batch_id']}", "FINAL_RECAP: false",
        "CURRENT_PAGES (split images in reading order):\n" + json.dumps(pages),
        "VALID_PANEL_IDS (exact IDs, in reading order):\n" + json.dumps(panel_ids),
        "Previous narration ending, saved cast and story context:\n" + json.dumps(context, ensure_ascii=False),
        "BATCH_RECEIPT (copy exactly into batch_receipt):\n" + json.dumps(receipt),
    ])
    return receipt, prompt, panel_ids


def receive(before, receipt, panel_ids, command):
    deadline = monotonic() + 600
    previous = None
    stable = 0
    while monotonic() < deadline:
        snapshot = command("snapshot")
        if snapshot.get("protocol") != 5 or not isinstance(snapshot.get("replies"), list):
            raise RuntimeError("Reload the Bliss Narration extension and reconnect the AI Studio tab.")
        latest = snapshot["replies"][-1] if snapshot["replies"] else {}
        token = (latest.get("id"), latest.get("text"))
        if (snapshot.get("visible") and snapshot.get("finished") and not snapshot.get("generating")
                and latest.get("completed") and isinstance(token[1], str) and token not in before):
            stable = stable + 1 if token == previous else 1
            previous = token
            if stable >= 3:
                return validate_reply(token[1], receipt, panel_ids)
        else:
            previous, stable = None, 0
        cancel.wait(1)
    raise RuntimeError("No complete reply arrived within 10 minutes. Check the AI Studio tab.")


def view(selected):
    checkpoint = selected["checkpoint"]
    return {"revision": selected["directory"].name, "revisions": selected["revisions"],
            "from_chapter": checkpoint["chapter"] if checkpoint else None,
            "key": selected["key"], "total": len(selected["batches"]),
            "completed": len(selected["replies"]), "submitted": selected["submitted"],
            "accepted": selected["accepted"], "script": "\n\n".join(
                beat["narration"] for reply in selected["replies"] for beat in reply["beats"] if beat["narration"].strip()
            )}


def run_queue(downloads, selected):
    global active
    replies = selected["replies"][:]
    key = selected["key"]

    def update(**changes):
        with state_lock:
            active.update(changes)

    def report(step):
        update(message=ACTIVITY.get(step, "Working in AI Studio"))

    def command(action, payload=None, timeout=35):
        return studio_bridge.command(action, payload, cancel, timeout=timeout, report=report)

    def current_selection():
        current = selection(downloads, selected["series"], selected["chapter"], selected["directory"].name)
        if current["key"] != key:
            raise ValueError("The split images, prompt, or earlier checkpoint changed. Reopen the chapter.")
        return current

    try:
        if len(replies) < len(selected["batches"]):
            hello = command("hello")
            if hello.get("protocol") != 5:
                raise RuntimeError("Reload the Bliss Narration extension and reconnect the AI Studio tab.")
        for index in range(len(replies), len(selected["batches"])):
            current_selection()
            receipt, prompt, ids = receipt_and_prompt(selected, index, replies)
            photos = [{"name": section["file"], "data": base64.b64encode(
                (selected["directory"] / section["file"]).read_bytes()
            ).decode("ascii")} for section in selected["batches"][index]]
            command("prepare", {"prompt": prompt, "photos": photos, "fresh": True, "schema": selected["schema"]}, 600)
            before = {(reply["id"], reply["text"]) for reply in command("snapshot").get("replies", [])}
            current_selection()
            if cancel.is_set():
                raise InterruptedError("Narration stopped.")
            save_draft(key, replies, True)
            command("send")
            update(message="Waiting for Gemini")
            result = receive(before, receipt, ids, command)
            current_selection()
            replies.append(result)
            save_draft(key, replies, False)
            update(completed=len(replies), message="Batch saved")
        if cancel.is_set():
            raise InterruptedError("Narration stopped.")
        save_checkpoint(current_selection())
        update(status="complete", message="Narration saved. Story checkpoint updated.")
    except InterruptedError:
        update(status="stopped", message="Narration stopped.")
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as exc:
        update(status="paused", message=str(exc))
        play_pause_sound()
    except Exception:
        logging.exception("Narration queue failed")
        update(status="paused", message="Narration paused. Check the AI Studio tab before retrying.")
        play_pause_sound()
    finally:
        with state_lock:
            active["running"] = False


def start_queue(downloads, selected, retry):
    global active
    with state_lock:
        if active and active["running"]:
            raise HTTPException(409, "A narration queue is already running.")
        if selected["accepted"]:
            raise ValueError("This chapter's narration is already complete.")
        if selected["submitted"] and not retry:
            raise ValueError("Check the last AI Studio request and confirm that you want to send this batch again.")
        if len(selected["replies"]) < len(selected["batches"]) and not studio_bridge.connection_view()["connected"]:
            raise ValueError("Connect the Bliss extension to your AI Studio tab first.")
        cancel.clear()
        active = {"running": True, "status": "running", "message": "Connecting to AI Studio",
                  "key": selected["key"], "completed": len(selected["replies"]), "total": len(selected["batches"])}
        Thread(target=run_queue, args=(downloads, selected), daemon=True).start()


def play_pause_sound():
    try:
        play_alert()
    except (OSError, RuntimeError):
        logging.warning("Could not play narration pause sound.", exc_info=True)


def save_checkpoint(selected):
    if selected["submitted"] or len(selected["replies"]) != len(selected["batches"]):
        raise ValueError("Finish all narration batches before saving the checkpoint.")
    context = story_context(selected["checkpoint"], selected["chapter"], selected["replies"])
    if not any(beat["narration"].strip() for reply in selected["replies"] for beat in reply["beats"]):
        raise ValueError("This chapter has no spoken narration to save.")
    context.pop("revision", None)
    context.pop("chapter", None)
    with library() as db:
        db.execute("BEGIN IMMEDIATE")
        current = checkpoint_before(db, selected["series_id"], selected["number"])
        if (current["revision"] if current else None) != selected["parent"]:
            raise ValueError("The earlier story checkpoint changed. Reopen this chapter before continuing.")
        db.execute("""INSERT OR IGNORE INTO checkpoints (series, chapter, number, parent, draft, payload)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (selected["series_id"], selected["chapter"], str(selected["number"]), selected["parent"],
             selected["key"], json.dumps(context)))


class NarrationSelection(BaseModel):
    series: str
    chapter: str
    revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    key: str | None = None
    retry: bool = False


def install(app, downloads):
    @router.get("/chapter")
    def chapter_view(series: str, chapter: str, request: Request, revision: str | None = None):
        studio_bridge.local_request(request)
        try:
            return view(selection(downloads, series, chapter, revision))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/job")
    def job_view(request: Request):
        studio_bridge.local_request(request)
        with state_lock:
            return dict(active) if active else None

    def load_checked(body):
        selected = selection(downloads, body.series, body.chapter, body.revision)
        if selected["key"] != body.key:
            raise HTTPException(409, "The chapter changed. Reopen it before continuing.")
        return selected

    @router.post("/start")
    def start(body: NarrationSelection, request: Request):
        studio_bridge.local_request(request)
        try:
            start_queue(downloads, load_checked(body), body.retry)
            return {"started": True}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/alert")
    def alert(request: Request):
        studio_bridge.local_request(request)
        play_pause_sound()
        return {"played": True}

    @router.post("/stop")
    def stop(request: Request):
        studio_bridge.local_request(request)
        with state_lock:
            if active and active["running"]:
                cancel.set()
        return {"stopping": True}

    app.include_router(studio_bridge.router)
    app.include_router(router)
