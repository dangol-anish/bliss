"""Run the two WTN panel models and the D2 head detector in strip coordinates."""
from copy import deepcopy
from functools import lru_cache
import logging
import re
import sys


from image_stitch import PAGE_WIDTH


def area(box):
    left, top, right, bottom = box
    return (right - left) * (bottom - top)


def intersection(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def contains(a, b):
    return a[0] <= b[0] and a[1] <= b[1] and a[2] >= b[2] and a[3] >= b[3]


def nonoverlapping(candidates, key):
    kept = []
    for candidate in sorted(candidates, key=key, reverse=True):
        if not any(intersection(candidate[0], other[0]) for other in kept):
            kept.append(candidate)
    return kept


def choose_panels(raw, heads, strip, ocr):
    import cv2
    import numpy as np

    faces = [
        (x1 + .10 * (x2 - x1), y1 + .35 * (y2 - y1), x2 - .10 * (x2 - x1), y2)
        for (x1, y1, x2, y2), _ in heads
    ]

    def shows_face(box):
        return any(contains(box, face) for face in faces)

    manhwa, manhua = [
        nonoverlapping((row for row in raw if row[2] == model), lambda row: (area(row[0]), row[1]))
        for model in range(2)
    ]
    matches = []
    for i, a in enumerate(manhwa):
        for j, b in enumerate(manhua):
            deltas = [abs(x - y) for x, y in zip(a[0], b[0])]
            if max(deltas) <= 10:
                matches.append((max(deltas), sum(deltas), i, j))
    matched_a, matched_b, candidates = set(), set(), []
    for _, _, i, j in sorted(matches):
        if i not in matched_a and j not in matched_b:
            matched_a.add(i)
            matched_b.add(j)
            candidates.append(max((manhwa[i], manhua[j]), key=lambda row: row[1]))
    candidates += [row for i, row in enumerate(manhwa) if i not in matched_a]
    candidates += [row for j, row in enumerate(manhua) if j not in matched_b]
    kept = nonoverlapping(candidates, lambda row: (shows_face(row[0]), area(row[0]), row[1]))
    boxes = []
    for box, _, model, (window_top, window_bottom) in kept:
        left, top, right, bottom = box
        rivals = [rival for rival, _, style, _ in candidates if style != model and intersection(box, rival)]
        nearby = []
        for (head, confidence), face in zip(heads, faces):
            if (rivals and window_top <= head[1] and head[3] <= window_bottom
                    and left <= face[0] and face[2] <= right and face[1] < top and face[3] <= bottom
                    and top - face[3] <= .25 * (head[3] - head[1])
                    and not any(contains(rival, face) for rival in rivals)):
                nearby.append((abs(top - face[3]), -confidence, head[1], face[1]))
        if nearby:
            _, _, head_top, face_top = min(nearby)
            ceiling = max((other[3] for other, *_ in kept
                           if other[3] <= top and other[0] < right and left < other[2]), default=0)
            if max(head_top, ceiling) <= face_top:
                top = max(head_top, ceiling)
        if bottom - top >= 240 or shows_face((left, top, right, bottom)):
            boxes.append((left, top, right, bottom))

    def text_lines(pixels):
        lines, _ = ocr(pixels, use_cls=False)
        return " ".join(text for _, text, score in (lines or []) if score >= .45)

    without_face = [box for box in boxes if not shows_face(box)]
    bubbles = []
    for top in range(0, strip.height, 2400):
        bottom = min(strip.height, top + 2800)
        if not any(box[1] < bottom and top < box[3] for box in without_face):
            continue
        rgb = np.asarray(strip.image(top, bottom))
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        for dark in (False, True):
            mask = np.uint8(np.max(rgb, axis=2) <= 32 if dark else np.min(rgb, axis=2) >= 227)
            contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                size = cv2.contourArea(contour)
                region = (x, top + y, x + w, top + y + h)
                if not (100 <= w <= 710 and 45 <= h <= 650 and .23 <= h/w <= 1.8 and size >= .55*w*h
                        and x > 0 and y > 0 and x+w < PAGE_WIDTH and y+h < bottom-top
                        and any(intersection(region, box) for box in without_face)):
                    continue
                inside = np.zeros((h, w), np.uint8)
                cv2.drawContours(inside, [contour - np.array([[[x, y]]], dtype=np.int32)], -1, 255, -1)
                patch = gray[y:y+h, x:x+w].copy()
                if dark:
                    patch = 255 - patch
                patch[inside == 0] = 255
                if np.mean(patch[inside != 0] < 120) < .004:
                    continue
                patch = cv2.copyMakeBorder(patch, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
                words = re.findall(r"[A-Za-z]{2,}", text_lines(cv2.cvtColor(patch, cv2.COLOR_GRAY2BGR)))
                if sum(map(len, words)) >= 4:
                    bubbles.append((region, size))

    def is_fragment(box):
        if shows_face(box):
            return False
        left, top, right, bottom = box
        for region, size in bubbles:
            shared = intersection(box, region)
            if shared >= .45 * area(region) and shared * size / area(region) >= .12 * area(box):
                return True
        pixels = np.asarray(strip.image(top, bottom, left, right))
        if np.mean(np.max(pixels, axis=2) <= 18) >= .65:
            context = np.asarray(strip.image(top, bottom))
            if len(re.findall(r"[A-Za-z]{2,}", text_lines(cv2.cvtColor(context, cv2.COLOR_RGB2BGR)))) >= 2:
                return True
        gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
        edges = np.mean(cv2.Canny(gray, 45, 100) > 0)
        if np.quantile(np.std(gray, axis=1), .90) < 6 and edges < .012:
            return True
        minimum = np.min(pixels, axis=2)
        white = np.mean(minimum >= 238)
        foreground = minimum < 220
        if white >= .86 and np.mean(foreground) <= .075 and edges < .027:
            return True
        if white > .55 and np.mean(gray >= 235) > .60 and edges < .06 and foreground.any():
            red, green, blue = np.moveaxis(pixels.astype(np.int16), -1, 0)
            warm = (red-blue > 20) & (red-green > 7) & (green-blue > 7) & (red >= 75)
            if np.mean(warm[foreground]) >= .70:
                return True
        return bottom-top <= 360 and white >= .40 and any(
            region[1] < top < region[3] < bottom and intersection(box, region) >= .30 * area(region)
            for region, _ in bubbles
        )

    return sorted((box for box in boxes if not is_fragment(box)), key=lambda box: (box[1], box[0]))


def split_windows(strip):
    import numpy as np

    blank = np.zeros(strip.height, dtype=bool)
    for top in range(0, strip.height, 2400):
        bottom = min(strip.height, top + 2400)
        gray = np.asarray(strip.image(top, bottom).convert("L"), dtype=np.int16)
        blank[top:bottom] = np.all(np.abs(np.diff(gray, axis=1)) <= 25, axis=1)
    cuts = [0]
    while cuts[-1] + 1000 < strip.height:
        target = cuts[-1] + 1000
        rows = (row for row in range(target, cuts[-1] + 399, -5) if blank[row])
        cut = next(rows, None)
        if cut is None:
            cut = next((row for row in range(target + 5, strip.height, 5) if blank[row]), None)
        if cut is None:
            break
        cuts.append(cut)
    cuts.append(strip.height)
    for a, b in zip(cuts, cuts[1:]):
        # Give very long scenes bounded, overlapping inputs.
        for start in range(a, b, 6000):
            yield max(0, start - (200 if start != a else 4)), min(strip.height, b + 4, start + 6200)


def enable_mps_head(head, torch):
    if not torch.backends.mps.is_available():
        return
    cpu_model = head.model
    try:
        gpu_model = deepcopy(cpu_model).to("mps")
    except (RuntimeError, NotImplementedError):
        logging.getLogger(__name__).warning("D2 GPU initialization failed. Using CPU.", exc_info=True)
        return

    def forward(inputs):
        nonlocal gpu_model
        try:
            # Keep the existing CPU box decoding and suppression.
            _, regression, classification, anchors = gpu_model(inputs.to("mps"))
            return None, regression.cpu(), classification.cpu(), anchors.cpu()
        except (RuntimeError, NotImplementedError):
            logging.getLogger(__name__).warning("D2 GPU inference failed. Switching to CPU.", exc_info=True)
            head.model = cpu_model
            gpu_model = None
        return cpu_model(inputs)

    head.model = forward


@lru_cache(maxsize=1)
def load_models(folder):
    weights = [folder / f"{style} Style.pt" for style in ("Manhwa", "Manhua")]
    if not all(path.is_file() for path in [
        *weights, folder / "character-face-efficientdet-c2.pth", folder / "animecv" / "__init__.py",
        folder / "bounding_box" / "__init__.py",
    ]):
        raise ValueError("Install the two panel models and the D2 head detector in the Bliss models folder.")
    try:
        import torch
        from ultralytics import YOLO
        from rapidocr_onnxruntime import RapidOCR
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
        from animecv.object_detection import FaceDetector_EfficientDet
    except ImportError as exc:
        raise ValueError("Install the panel detection runtime from the Bliss setup instructions.") from exc
    torch.set_num_threads(4)
    head = FaceDetector_EfficientDet(2, use_cuda=False, base_dir=str(folder))
    head.batch_size = 4
    enable_mps_head(head, torch)
    return [YOLO(path) for path in weights], head, RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)


def detect_panels(strip, models, report):
    yolo, head, ocr = load_models(models)
    windows = list(split_windows(strip))

    def predict(bounds):
        top, bottom = bounds
        image = strip.image(top, bottom)
        for style, model in enumerate(yolo):
            boxes = model.predict(image, device="cpu", verbose=False)[0].boxes
            for (x1, y1, x2, y2), confidence in zip(boxes.xyxy.tolist(), boxes.conf.tolist()):
                box = (max(0, round(x1)), max(top, round(y1) + top),
                       min(PAGE_WIDTH, round(x2)), min(bottom, round(y2) + top))
                if box[0] < box[2] and box[1] < box[3]:
                    yield box, confidence, style, bounds

    raw = []
    for index, bounds in enumerate(windows, 1):
        raw.extend(predict(bounds))
        report(5 + 50 * index // len(windows))

    heads = []
    starts = list(range(0, strip.height, 960))
    for index in range(0, len(starts), 4):
        tops = starts[index:index+4]
        images = [strip.image(top, min(strip.height, top + 1600)) for top in tops]
        for top, image, detections in zip(tops, images, head.detect(images, threshold=.5)):
            for detection in detections:
                x1, y1, x2, y2 = map(int, detection["coordinates"])
                if x2 > x1 and y2 > y1 and not (top and y1 <= 3) and not (
                    top + image.height < strip.height and y2 >= image.height - 3
                ):
                    heads.append(((x1, y1 + top, x2, y2 + top), float(detection["score"])))
        report(55 + 35 * min(index + 4, len(starts)) // len(starts))

    # Recheck likely seam fragments using the same 450-pixel context as the trial.
    by_window = {}
    for box, _, _, bounds in raw:
        by_window.setdefault(bounds, []).append(box)
    for left, right in zip(windows, windows[1:]):
        upper = [box for box in by_window.get(left, []) if abs(box[3] - left[1]) <= 48]
        lower = [box for box in by_window.get(right, []) if abs(box[1] - right[0]) <= 48]
        if not any(min(a[2], b[2]) - max(a[0], b[0]) >= PAGE_WIDTH / 3 for a in upper for b in lower):
            continue
        bounds = max(0, left[0] - 450), min(strip.height, right[1] + 450)
        for row in predict(bounds):
            box = row[0]
            if all(any(intersection(box, fragment) >= .5 * area(fragment) for fragment in side)
                   for side in (upper, lower)):
                raw.append(row)
    report(93)
    return choose_panels(raw, heads, strip, ocr)
