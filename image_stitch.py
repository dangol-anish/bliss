from itertools import count
from tempfile import TemporaryDirectory
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

from renumber import chapter_images


PAGE_WIDTH = 720
MAX_PAGE_HEIGHT = 14_000
TILE_HEIGHT = 14_000


def scaled_height(width, height):
    return max(1, round(height * PAGE_WIDTH / width))


def strip_height(images):
    height = 0
    for source in images:
        with Image.open(source) as image:
            width, rows = image.size
            if image.getexif().get(274) in (5, 6, 7, 8):
                width, rows = rows, width
            height += scaled_height(width, rows)
    return height


def insert_tile(page, sheet, top, height, scale):
    with sheet.crop((0, 0, PAGE_WIDTH, height)) as strip:
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, PAGE_WIDTH, height, strip.tobytes(), False)
        bounds = pymupdf.Rect(0, top * scale, PAGE_WIDTH * scale, (top + height) * scale)
        page.insert_image(bounds, pixmap=pixmap, keep_proportion=False)


def stitch_chapter(folder, *, report_progress=None):
    if any(folder.glob(".bliss-renumber-*")):
        raise ValueError("Finish recovering the interrupted renumber before stitching this chapter.")
    images = chapter_images(folder)
    if not images:
        raise ValueError("This chapter has no images to stitch.")

    total_height = strip_height(images)
    # Scale the PDF coordinates to fit standard readers while retaining the strip pixels.
    scale = min(1, MAX_PAGE_HEIGHT / total_height)
    with TemporaryDirectory(prefix=".bliss-stitch-", dir=folder) as temp:
        partial = Path(temp) / "strip.pdf"
        with pymupdf.open() as document, Image.new("RGB", (PAGE_WIDTH, TILE_HEIGHT)) as sheet:
            page = document.new_page(width=PAGE_WIDTH * scale, height=total_height * scale)
            written = 0
            used = 0
            for index, source in enumerate(images, 1):
                with Image.open(source) as original:
                    image = ImageOps.exif_transpose(original).convert("RGB")
                height = scaled_height(image.width, image.height)
                with image, image.resize((PAGE_WIDTH, height), Image.Resampling.LANCZOS) as scaled:
                    top = 0
                    while top < height:
                        rows = min(height - top, TILE_HEIGHT - used)
                        with scaled.crop((0, top, PAGE_WIDTH, top + rows)) as segment:
                            sheet.paste(segment, (0, used))
                        top += rows
                        used += rows
                        if used == TILE_HEIGHT:
                            insert_tile(page, sheet, written, used, scale)
                            written += used
                            used = 0
                if report_progress is not None:
                    report_progress(index / len(images))
            if written + used != total_height:
                raise ValueError("Chapter image dimensions changed during stitching. Try again.")
            if used:
                insert_tile(page, sheet, written, used, scale)
            document.save(partial, deflate=True)

        stem = f"{folder.name[:65]} - Image Strip"
        for number in count(1):
            filename = f"{stem}.pdf" if number == 1 else f"{stem} ({number}).pdf"
            target = folder / filename
            try:
                target.hardlink_to(partial)
                return filename
            except FileExistsError:
                continue
