"""Archive selected chapter folders and their saved narration."""
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory, mkdtemp
from uuid import uuid4
from zipfile import ZIP_STORED, ZipFile

from narration import identify, library


def story_backup(chapters):
    series_id, _ = identify(chapters[0])
    _, end = identify(chapters[-1])
    names = {folder.name for folder in chapters}
    with library() as db:
        db.execute("BEGIN")
        rows = [dict(row) for row in db.execute(
            "SELECT * FROM checkpoints WHERE series=? ORDER BY revision DESC", (series_id,)
        )]
        checkpoint_ids = {row["draft"] for row in rows if row["chapter"] in names}
        by_revision = {row["revision"]: row for row in rows}
        head = rows[0] if rows else None
        while head and Decimal(head["number"]) > end:
            head = by_revision.get(head["parent"])
        chain = []
        current = head
        while current:
            chain.append({**current, "payload": json.loads(current["payload"])})
            checkpoint_ids.add(current["draft"])
            current = by_revision.get(current["parent"])
        drafts = []
        for row in db.execute("SELECT * FROM drafts"):
            replies = json.loads(row["replies"])
            receipt = replies[0].get("batch_receipt", {}) if replies else {}
            if row["id"] in checkpoint_ids or (
                receipt.get("manga_id") == series_id and receipt.get("chapter") in names
            ):
                drafts.append({**dict(row), "replies": replies})
    return {
        "version": 1,
        "series_id": series_id,
        "chapters": [folder.name for folder in chapters],
        "checkpoint": head["chapter"] if head else None,
        "checkpoints": chain[::-1],
        "drafts": drafts,
    }


def move_backup(chapters, destination):
    story = story_backup(chapters)
    series = chapters[0].parent
    title = chapters[0].name
    if len(chapters) > 1:
        title += f" to {chapters[-1].name}"
    stem = f"{series.name} - {title}".encode("utf-8")[:210].decode("utf-8", "ignore")
    filename = f"{stem} - {uuid4().hex[:12]}.zip"
    with TemporaryDirectory(dir=destination) as temporary:
        staged = Path(temporary) / "backup.zip"
        with ZipFile(staged, "w", compression=ZIP_STORED) as archive:
            archive.writestr("Bliss Story.json", json.dumps(story, ensure_ascii=False, indent=2))
            marker = series / ".bliss-series"
            if marker.is_file() and not marker.is_symlink():
                archive.write(marker, Path(series.name) / marker.name)
            for chapter in chapters:
                for root, _, files in os.walk(chapter, onerror=raise_walk_error, followlinks=False):
                    for name in files:
                        path = Path(root) / name
                        if name != ".DS_Store" and path.is_file() and not path.is_symlink():
                            archive.write(path, path.relative_to(series.parent))
        with ZipFile(staged) as archive:
            if archive.testzip() is not None:
                raise OSError("Backup verification failed.")
        target = destination / filename
        staged.replace(target)
    # Publish the verified ZIP before moving any working files.
    staging = Path(mkdtemp(prefix=".bliss-archived-", dir=series))
    moved = []
    try:
        for chapter in chapters:
            chapter.rename(staging / chapter.name)
            moved.append(chapter)
    except OSError:
        for chapter in reversed(moved):
            (staging / chapter.name).rename(chapter)
        staging.rmdir()
        raise
    try:
        shutil.rmtree(staging)
    except OSError as exc:
        raise ValueError(f"Backup saved to {target}. Cleanup could not finish at {staging}.") from exc
    return target, story["checkpoint"]


def raise_walk_error(error):
    raise error
