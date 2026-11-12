"""Split a chapter strip at full-width black or white gutters."""
from bisect import bisect_left, bisect_right
from hashlib import sha256
from heapq import heappop, heappush
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import ImageDraw, ImageFont

from image_stitch import PAGE_WIDTH

GUTTER_MARGIN = 4
PANEL_MARGIN = 8
SCAN_ROWS = 2048
OUTPUT_DIR = "Split Strip"


def safe_rows(strip, boxes):
    black = bytes(PAGE_WIDTH * 3)
    white = bytes([255]) * len(black)
    near_black = bytes(range(17))
    near_white = bytes(range(239, 256))
    protected = bytearray(strip.height + 1)
    for _, top, _, bottom in boxes:
        start = max(0, top - PANEL_MARGIN)
        end = min(strip.height, bottom + PANEL_MARGIN)
        protected[start:end + 1] = b"\x01" * (end - start + 1)

    tones = bytearray(strip.height)
    exact = bytearray(strip.height)
    stride = PAGE_WIDTH * 3
    for top in range(0, strip.height, SCAN_ROWS):
        with strip.image(top, min(top + SCAN_ROWS, strip.height)) as image:
            pixels = image.tobytes()
        for offset in range(0, len(pixels), stride):
            row = pixels[offset:offset + stride]
            y = top + offset // stride
            if row == black or row == white:
                exact[y] = tones[y] = 1 if row == black else 2
            elif not row.translate(None, near_black):
                tones[y] = 1
            elif not row.translate(None, near_white):
                tones[y] = 2

    def cuts_in_bands(rows):
        start = 0
        previous = 0
        for end, tone in enumerate([*rows, 0]):
            if tone != previous:
                if previous:
                    yield from (cut for cut in range(start + GUTTER_MARGIN, end - GUTTER_MARGIN + 1)
                                if not protected[cut])
                start, previous = end, tone

    return list(cuts_in_bands(tones)), set(cuts_in_bands(exact))


def choose_cuts(rows, exact, height, minimum, maximum):
    # Prefer a complete route within both limits, using as few near-color cuts as possible.
    points = [0, *rows, height]
    costs = {0: (0, 0)}
    parents = {}
    eligible = []
    cursor = 0
    for row in points[1:]:
        while points[cursor] <= row - minimum:
            candidate = points[cursor]
            if candidate in costs:
                heappush(eligible, (*costs[candidate], -candidate))
            cursor += 1
        while eligible and -eligible[0][2] < row - maximum:
            heappop(eligible)
        if eligible:
            near_count, count, previous = eligible[0]
            costs[row] = (near_count + (row != height and row not in exact), count + 1)
            parents[row] = -previous
    if height in parents:
        route = [height]
        while route[-1]:
            route.append(parents[route[-1]])
        return list(reversed(route))

    # Preserve artwork when the height limits cannot both be satisfied.
    exact_rows = sorted(exact)
    cuts = [0]
    while height - cuts[-1] > maximum:
        top = cuts[-1]
        ceiling = min(top + maximum, height - minimum)
        last_exact = bisect_right(exact_rows, ceiling) - 1
        if last_exact >= 0 and exact_rows[last_exact] >= top + minimum:
            cuts.append(exact_rows[last_exact])
            continue
        last = bisect_right(rows, ceiling) - 1
        if last >= 0 and rows[last] >= top + minimum:
            cuts.append(rows[last])
            continue
        following = bisect_left(rows, top + minimum)
        if following == len(rows) or height - rows[following] < minimum:
            break
        cuts.append(rows[following])
    return [*cuts, height]


def split_plan(strip, source, boxes, minimum, maximum):
    if minimum > maximum:
        raise ValueError("Minimum height must be at or below maximum height.")
    rows, exact = safe_rows(strip, boxes)
    cuts = choose_cuts(rows, exact, strip.height, minimum, maximum)
    sections = []
    for index, (top, bottom) in enumerate(zip(cuts, cuts[1:]), 1):
        height = bottom - top
        suffix = "jpg" if height <= 65000 else "png"
        sections.append({
            "file": f"{index:03d}.{suffix}", "top": top, "bottom": bottom, "height": height,
            "outside_limits": not minimum <= height <= maximum,
            "cut": None if top == 0 else "exact" if top in exact else "near",
            "panels": [f"P{number:03d}" for number, (_, y1, _, y2) in enumerate(boxes, 1)
                       if top <= y1 and y2 <= bottom],
        })
    fingerprint = sha256(json.dumps([source, boxes, minimum, maximum, sections]).encode()).hexdigest()
    return {"id": fingerprint, "source": source, "height": strip.height, "width": PAGE_WIDTH,
            "minimum": minimum, "maximum": maximum, "sections": sections}


def save_split(folder, strip, boxes, plan):
    root = folder / OUTPUT_DIR
    root.mkdir(exist_ok=True)
    revision = root / plan["id"][:16]
    if revision.is_dir():
        return revision
    font = ImageFont.load_default(size=22)
    with TemporaryDirectory(prefix=".split-", dir=root) as temporary:
        staging = Path(temporary) / "images"
        staging.mkdir()
        for section in plan["sections"]:
            top, bottom = section["top"], section["bottom"]
            with strip.image(top, bottom) as image:
                draw = ImageDraw.Draw(image)
                for panel_id in section["panels"]:
                    x1, y1, x2, y2 = boxes[int(panel_id[1:]) - 1]
                    y = y1 - top
                    bounds = (x1, y, x2 - 1, y2 - top - 1)
                    draw.rectangle(bounds, outline="black", width=6)
                    draw.rectangle(bounds, outline="#fde047", width=3)
                    label_width = int(draw.textlength(panel_id, font=font)) + 12
                    label_x = min(x1, PAGE_WIDTH - label_width)
                    label_y = min(y, image.height - 28)
                    draw.rectangle((label_x, label_y, label_x + label_width - 1, label_y + 27), fill="black")
                    draw.text((label_x + 6, label_y + 3), panel_id, fill="#fde047", font=font)
                output = staging / section["file"]
                if output.suffix == ".jpg":
                    image.save(output, quality=95, subsampling=0)
                else:
                    image.save(output)
        (staging / "manifest.json").write_text(json.dumps(plan, indent=2) + "\n")
        staging.rename(revision)
    return revision
