import os
import re
from pathlib import Path
from tempfile import mkdtemp


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def natural_key(name):
    parts = re.split(r"([0-9]+)", name.casefold())
    return tuple(int(part) if index % 2 else part for index, part in enumerate(parts))


def directories(parent):
    if not parent.is_dir():
        return []
    return sorted(
        (item for item in parent.iterdir()
         if not item.name.startswith(".") and not item.is_symlink() and item.is_dir()),
        key=lambda item: (natural_key(item.name), item.name),
    )


def chapter_images(folder):
    return sorted(
        (item for item in folder.iterdir()
         if not item.name.startswith(".") and item.suffix.lower() in IMAGE_EXTENSIONS
         and not item.is_symlink() and item.is_file()),
        key=lambda item: (natural_key(item.name), item.name),
    )


def downloaded_series(root):
    series_list = []
    for series in directories(root):
        chapters = [chapter.name for chapter in directories(series) if chapter_images(chapter)]
        if chapters:
            series_list.append({"name": series.name, "chapters": chapters})
    return series_list


def chapter_folder(root, series, chapter):
    for name in (series, chapter):
        if not name or name.startswith(".") or Path(name).name != name:
            raise ValueError("Choose a downloaded series and chapter.")
        root = root / name
        if root.is_symlink() or not root.is_dir():
            raise ValueError("The selected folder is no longer available.")
    return root


def move_exclusive(source, target):
    os.link(source, target, follow_symlinks=False)
    try:
        source.unlink()
    except OSError:
        target.unlink()
        raise


def renumber_chapter(folder):
    if any(folder.glob(".bliss-renumber-*")):
        raise ValueError("An interrupted renumber left original files in this chapter’s hidden .bliss-renumber folder.")
    pages = chapter_images(folder)
    if not pages:
        raise ValueError("This chapter has no images to renumber.")
    plan = [(page, folder / f"{index:04d}{page.suffix}") for index, page in enumerate(pages)]
    changes = [(source, target) for source, target in plan if source != target]
    if not changes:
        return {"images": len(pages), "renamed": 0}
    source_names = {source.name for source, _ in changes}
    for _, target in changes:
        if target.is_symlink() or (target.exists() and target.name not in source_names):
            raise ValueError(f"{target.name} already exists.")

    staging = Path(mkdtemp(prefix=".bliss-renumber-", dir=folder))
    staged = []
    published = []
    try:
        for source, target in changes:
            temporary = staging / source.name
            move_exclusive(source, temporary)
            staged.append((source, temporary, target))
        for _, temporary, target in staged:
            move_exclusive(temporary, target)
            published.append((temporary, target))
    except OSError as exc:
        try:
            for temporary, target in reversed(published):
                move_exclusive(target, temporary)
            for source, temporary, _ in reversed(staged):
                move_exclusive(temporary, source)
        except OSError as recovery_error:
            raise ValueError(f"Renumbering was interrupted. Check {staging.name} for original files.") from recovery_error
        raise exc
    finally:
        if not any(staging.iterdir()):
            staging.rmdir()
    return {"images": len(pages), "renamed": len(changes)}
