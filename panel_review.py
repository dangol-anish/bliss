"""Read stitched chapters and save or export reviewed panel geometry."""
from bisect import bisect_right
from contextlib import contextmanager
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
import json
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

import pymupdf
from PIL import Image

from image_stitch import PAGE_WIDTH
from renumber import directories

PREVIEW_ROWS = 1600


def latest_strip(folder):
    stem = f"{folder.name[:65]} - Image Strip"
    pattern = re.compile(re.escape(stem) + r"(?: \((\d+)\))?\.pdf")
    versions = []
    for path in folder.glob("*.pdf"):
        match = pattern.fullmatch(path.name)
        if match and not path.is_symlink() and path.is_file():
            versions.append((int(match[1] or 1), path))
    return max(versions, default=(0, None))[1]


def stitched_series(root):
    result = []
    for series in directories(root):
        chapters = [folder.name for folder in directories(series) if latest_strip(folder)]
        if chapters:
            result.append({"name": series.name, "chapters": chapters})
    return result


class Strip:
    def __init__(self, pdf, exact):
        self.pdf = pdf
        self.pages = []
        self.starts = [0]
        self.native = []
        self.load_tile = lru_cache(maxsize=2)(self._load_tile)
        for page in pdf:
            rect = page.rect
            if not rect.width or not rect.height:
                continue
            scale = PAGE_WIDTH / rect.width
            height = round(rect.height * scale)
            if height < 1:
                continue
            self.pages.append((page.number, scale))
            start = self.starts[-1]
            self.starts.append(start + height)
            tiles = []
            row = 0
            for info in sorted(page.get_image_info(xrefs=True) if exact else (), key=lambda item: item["bbox"][1]):
                left, top, right, bottom = (value * scale for value in info["bbox"])
                if (abs(left) > .02 or abs(right - PAGE_WIDTH) > .02
                        or abs(top - row) > .02 or abs(bottom - row - info["height"]) > .02
                        or info["width"] != PAGE_WIDTH or not info["xref"]):
                    break
                tiles.append((start + row, start + row + info["height"], info["xref"]))
                row += info["height"]
            else:
                if row == height:
                    self.native.extend(tiles)
                    continue
            self.native.append((start, start + height, None))
        self.tile_starts = [top for top, _, _ in self.native]
        self.height = self.starts[-1]
        if not self.height:
            raise ValueError("This PDF has no chapter artwork.")

    def _load_tile(self, xref):
        pixmap = pymupdf.Pixmap(self.pdf, xref)
        if pixmap.alpha or pixmap.n != 3:
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
            if pixmap.alpha:
                pixmap = pymupdf.Pixmap(pixmap, 0)
        return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

    def image(self, top, bottom, left=0, right=PAGE_WIDTH):
        if not (0 <= top < bottom <= self.height and 0 <= left < right <= PAGE_WIDTH):
            raise ValueError("The panel extends beyond the chapter strip.")
        result = Image.new("RGB", (right-left, bottom-top), "white")
        for index in range(bisect_right(self.tile_starts, top)-1, len(self.native)):
            tile_top, tile_bottom, xref = self.native[index]
            if tile_top >= bottom:
                break
            first, last = max(tile_top, top), min(tile_bottom, bottom)
            if xref is not None:
                with self.load_tile(xref).crop((left, first-tile_top, right, last-tile_top)) as crop:
                    result.paste(crop, (0, first-top))
                continue
            page_index = bisect_right(self.starts, tile_top) - 1
            number, scale = self.pages[page_index]
            clip = pymupdf.Rect(left/scale, (first-tile_top)/scale, right/scale, (last-tile_top)/scale)
            pixmap = self.pdf[number].get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False)
            with Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples) as tile:
                x = left - pixmap.x
                y = first - tile_top - pixmap.y
                with tile.crop((x, y, min(tile.width, x+right-left), min(tile.height, y+last-first))) as crop:
                    result.paste(crop, (0, first-top))
        return result


def strip_source(path):
    stat = path.stat()
    return sha256(f"{path.name}/{stat.st_size}/{stat.st_mtime_ns}".encode()).hexdigest()[:20]


@contextmanager
def open_strip(folder, expected=None, *, exact=False):
    path = latest_strip(folder)
    if path is None:
        raise ValueError("Stitch this chapter first.")
    source = strip_source(path)
    if expected is not None and expected != source:
        raise ValueError("The stitched chapter changed. Reopen it to continue.")
    with pymupdf.open(path) as pdf:
        strip = Strip(pdf, exact)
        try:
            yield strip, source
        finally:
            strip.load_tile.cache_clear()


def read_review(folder, source):
    path = folder / ".bliss-panels.json"
    if path.exists():
        review = json.loads(path.read_text())
        if review.get("source") == source:
            return review["boxes"]
    return None


def save_review(folder, source, boxes):
    path = folder / ".bliss-panels.json"
    with NamedTemporaryFile(mode="w", dir=folder, prefix=".bliss-panels-", delete=False) as file:
        temp = Path(file.name)
        try:
            json.dump({"source": source, "boxes": boxes}, file)
            file.close()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def image_bytes(image, format, **options):
    output = BytesIO()
    image.save(output, format, **options)
    return output.getvalue()
