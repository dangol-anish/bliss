"""Download publicly available chapters from Webtoon and Weeb Central."""
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from html.parser import HTMLParser
from io import BytesIO
from itertools import count
from pathlib import Path
from shutil import rmtree
from tempfile import TemporaryDirectory
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image, UnidentifiedImageError


WEBTOON = frozenset({"webtoon.com", "www.webtoon.com", "webtoons.com",
                     "www.webtoons.com", "m.webtoons.com"})
WEEB = frozenset({"weebcentral.com", "www.weebcentral.com"})
WEBTOON_IMAGES = WEBTOON | {"webtoon-phinf.pstatic.net"}
WEEB_IMAGES = WEEB | {"hot.planeptune.us", "scans.lastation.us", "official.lowee.us"}
WEEB_ID = r"[0-9A-HJKMNP-TV-Z]{26}"
WEBTOON_ROOT = "https://www.webtoons.com"
WEEB_ROOT = "https://weebcentral.com"


class DownloadError(Exception):
    pass


class DownloadCancelled(Exception):
    pass


class DownloadCancellation:
    def __init__(self):
        self.lock = Lock()
        self.cancelled = False
        self.finished = False

    def cancel(self):
        with self.lock:
            if self.finished:
                return False
            self.cancelled = True
            return True

    def check(self):
        with self.lock:
            if self.cancelled:
                raise DownloadCancelled

    def finish(self):
        with self.lock:
            self.finished = True
            if self.cancelled:
                raise DownloadCancelled


@contextmanager
def download_files(cancellation):
    created = []
    try:
        try:
            yield created
        finally:
            cancellation.finish()
    except DownloadCancelled:
        for path in reversed(created):
            rmtree(path)
        raise


def allowed_url(url, hosts):
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in hosts
            or parts.username is not None or parts.password is not None
            or parts.port not in (None, 443)):
        raise ValueError("Use a supported HTTPS series link.")
    return parts


class SourceRedirects(HTTPRedirectHandler):
    def __init__(self, hosts):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            allowed_url(newurl, self.hosts)
        except ValueError as exc:
            raise DownloadError("The source redirected to an unsupported host.") from exc
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, hosts, referer, limit=12 * 1024 * 1024, *, cancellation=None):
    try:
        allowed_url(url, hosts)
    except ValueError as exc:
        raise DownloadError("The source returned an unsupported download URL.") from exc
    if cancellation is not None:
        cancellation.check()
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": referer})
    try:
        with build_opener(SourceRedirects(hosts)).open(request, timeout=25) as response:
            if cancellation is None:
                data = response.read(limit + 1)
            else:
                data = bytearray()
                while len(data) <= limit:
                    cancellation.check()
                    chunk = response.read1(min(256 * 1024, limit + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                cancellation.check()
    except HTTPError as exc:
        if exc.code == 429:
            raise DownloadError("The source is rate limiting downloads. Try again later.") from exc
        raise DownloadError(f"The source returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise DownloadError("Could not reach the source. Try again.") from exc
    if len(data) > limit:
        raise DownloadError("The source response was too large.")
    return data


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.title = ""
        self.links = []
        self.images = []
        self.anchor = None
        self.feed(html.decode("utf-8", "replace"))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("property") == "og:title":
            self.title = attrs.get("content") or ""
        elif tag == "img":
            self.images.append(attrs)
        elif tag == "a":
            self.anchor = (attrs.get("href") or "", [])

    def handle_data(self, text):
        if self.anchor:
            self.anchor[1].append(text)

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor:
            href, text = self.anchor
            self.links.append((href, " ".join(" ".join(text).split())))
            self.anchor = None


@dataclass(frozen=True)
class Chapter:
    id: str
    title: str
    url: str


def webtoon_catalog(parts, *, cancellation=None):
    title_ids = parse_qs(parts.query).get("title_no", [])
    if not parts.path.rstrip("/").endswith("/list") or not title_ids or not title_ids[0].isascii() or not title_ids[0].isdigit():
        raise ValueError("Paste a Webtoon series link with a title_no.")
    series_id = title_ids[0]
    kind = "canvas" if "canvas" in parts.path.lower().split("/") else "webtoon"
    series = f"{WEBTOON_ROOT}{parts.path}?title_no={series_id}"
    endpoint = f"https://m.webtoons.com/api/v1/{kind}/{series_id}/episodes?pageSize=99999"
    try:
        items = json.loads(fetch(endpoint, WEBTOON, series, cancellation=cancellation))["result"]["episodeList"]
        chapters = [
            Chapter(str(row["episodeNo"]), row["episodeTitle"].strip(),
                    urljoin(WEBTOON_ROOT, row["viewerLink"]))
            for row in items
        ]
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise DownloadError("Webtoon returned an unreadable episode list.") from exc
    title = Page(fetch(series, WEBTOON, WEBTOON_ROOT, cancellation=cancellation)).title.removesuffix(" | WEBTOON")
    return f"webtoon-{kind}-{series_id}", title, chapters


def weeb_catalog(parts, *, cancellation=None):
    match = re.fullmatch(rf"/series/({WEEB_ID})(?:/[^/]+)?/?", parts.path, re.I)
    if not match:
        raise ValueError("Paste a Weeb Central series link.")
    series_id = match[1].upper()
    series = f"{WEEB_ROOT}/series/{series_id}"
    page = Page(fetch(f"{series}/full-chapter-list", WEEB, series, cancellation=cancellation))
    chapters = {}
    for href, title in page.links:
        match = re.fullmatch(rf"/chapters/({WEEB_ID})/?", href, re.I)
        if match:
            chapter_id = match[1].upper()
            label = re.search(r"(?:Chapter|Episode)\s*\d+(?:\.\d+)?", title, re.I)
            chapters.setdefault(chapter_id, Chapter(
                chapter_id, label[0] if label else title, f"{WEEB_ROOT}/chapters/{chapter_id}"
            ))
    # The fragment is newest first. Numeric sorting also handles fractional chapters.
    ordered = list(reversed(chapters.values()))
    title = Page(fetch(series, WEEB, WEEB_ROOT, cancellation=cancellation)).title.removesuffix(" | Weeb Central")
    return f"weebcentral-{series_id}", title, sorted(ordered, key=weeb_chapter_number)


def weeb_chapter_number(chapter):
    match = re.match(r"(?:(?:Chapter|Episode)\s*)?#?\s*(\d+(?:\.\d+)?)", chapter.title, re.I)
    return (0, Decimal(match[1])) if match else (1, Decimal(0))


def chapter_images(chapter, is_webtoon, *, cancellation=None):
    if is_webtoon:
        page = Page(fetch(chapter.url, WEBTOON, WEBTOON_ROOT, cancellation=cancellation))
        urls = []
        for image in page.images:
            if "data-url" in image and "_images" in (image.get("class") or "").split():
                url = urlsplit(urljoin(chapter.url, image["data-url"].strip()))
                query = [
                    (key, value) for key, value in parse_qsl(url.query, keep_blank_values=True)
                    if not (key == "type" and re.fullmatch(r"q\d{1,3}", value))
                ]
                urls.append(url._replace(query=urlencode(query), fragment="").geturl())
    else:
        endpoint = f"{chapter.url}/images?is_prev=False&current_page=1&reading_style=long_strip"
        page = Page(fetch(endpoint, WEEB, chapter.url, cancellation=cancellation))
        urls = [
            urljoin(chapter.url, (image.get("data-src") or image.get("src") or "").strip())
            for image in page.images if re.fullmatch(r"Page\s+\d+", image.get("alt") or "", re.I)
        ]
    if not urls or len(urls) != len(set(urls)):
        raise DownloadError("The source did not return a complete list of chapter images.")
    return urls


def save_image(data, stem, is_webtoon):
    try:
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > 60_000_000:
                raise DownloadError("A source image exceeds the supported size.")
            extension = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(image.format)
            if extension is None:
                raise DownloadError("The source returned an unsupported image format.")
            image.load()
            if is_webtoon and extension != ".jpg":
                image.convert("RGB").save(stem.with_suffix(".jpg"), quality=95)
            else:
                stem.with_suffix(extension).write_bytes(data)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise DownloadError("Could not read or save a chapter image.") from exc


def series_directory(root: Path, series: str, title: str, *, created=None) -> Path:
    title = re.sub(r'[/\\:\x00-\x1f\x7f]', " ", title)
    title = " ".join(title.split()).strip(". ")
    title = title.encode("utf-8")[:200].decode("utf-8", "ignore").rstrip(". ")
    if not title:
        raise DownloadError("The source did not provide a series title.")

    root.mkdir(parents=True, exist_ok=True)
    for folder in root.iterdir():
        identity = folder / ".bliss-series"
        if folder.is_dir() and not folder.is_symlink() and identity.is_file():
            if identity.read_text() == series:
                return folder

    for number in count(1):
        folder = root / (title if number == 1 else f"{title} ({number})")
        if not folder.exists() and not folder.is_symlink():
            break

    legacy = root / series
    if legacy.is_dir() and not legacy.is_symlink():
        (legacy / ".bliss-series").write_text(series)
        legacy.rename(folder)
    else:
        folder.mkdir()
        if created is not None:
            created.append(folder)
        (folder / ".bliss-series").write_text(series)
    return folder


def chapter_directory(directory: Path, identity: str, title: str, position: int) -> Path:
    label = " ".join(re.sub(r"[^\w. -]+", " ", title).split()).strip(". ")[:100].rstrip(". ")
    if not label or label == identity:
        label = f"Episode {position}"
    for folder in directory.iterdir():
        marker = folder / ".bliss-chapter"
        if folder.is_dir() and not folder.is_symlink() and marker.is_file():
            if marker.read_text() == identity:
                return folder

    for number in count(1):
        target = directory / (label if number == 1 else f"{label} ({number})")
        if not target.exists() and not target.is_symlink():
            break

    legacy_label = re.sub(r"[^\w.-]+", "-", title).strip(".-")[:70]
    legacy = directory / f"{identity}-{legacy_label or 'chapter'}"
    if legacy.is_dir() and not legacy.is_symlink():
        (legacy / ".bliss-chapter").write_text(identity)
        legacy.rename(target)
    return target


def download_series(url, start, end, root: Path, *, report_progress=None, cancellation=None):
    cancellation = cancellation or DownloadCancellation()
    with download_files(cancellation) as created:
        return _download_series(url, start, end, root, report_progress, cancellation, created)


def download_batch(entries, root, *, report_progress, cancellation):
    results = []
    with download_files(cancellation) as created:
        for index, entry in enumerate(entries):
            def report(percent):
                report_progress((100 * index + percent) // len(entries))

            try:
                result = _download_series(entry.url, entry.start, entry.end, root, report, cancellation, created)
            except (DownloadError, ValueError) as exc:
                raise type(exc)(f"Series {index + 1}: {exc}") from exc
            results.append(result)
    return results


def _download_series(url, start, end, root, report_progress, cancellation, created):
    cancellation.check()
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parts = allowed_url(url, WEBTOON | WEEB)
    is_webtoon = parts.hostname in WEBTOON
    catalog = webtoon_catalog if is_webtoon else weeb_catalog
    series, title, chapters = catalog(parts, cancellation=cancellation)
    cancellation.check()
    if not chapters:
        raise DownloadError("No chapters were found for this series.")
    first, last = start or 1, end or len(chapters)
    if not 1 <= first <= last <= len(chapters):
        raise ValueError(f"Enter a chapter position from 1 to {len(chapters)}.")
    directory = series_directory(root, series, title, created=created)
    hosts, referer = (WEBTOON_IMAGES, WEBTOON_ROOT) if is_webtoon else (WEEB_IMAGES, WEEB_ROOT)
    total = last - first + 1
    completed = 0

    def report(chapter_fraction=0):
        if report_progress is not None:
            report_progress(int(100 * (completed + chapter_fraction) / total))

    for position, chapter in enumerate(chapters[first - 1:last], first):
        cancellation.check()
        identity = chapter.id if re.fullmatch(r"[A-Za-z0-9]+", chapter.id) else str(position)
        target = chapter_directory(directory, identity, chapter.title, position)
        if target.is_dir():
            completed += 1
            report()
            continue
        try:
            with TemporaryDirectory(prefix=".chapter-", dir=directory) as temp:
                staging = Path(temp) / "pages"
                staging.mkdir()
                images = chapter_images(chapter, is_webtoon, cancellation=cancellation)
                for number, image_url in enumerate(images, 1):
                    cancellation.check()
                    data = fetch(image_url, hosts, referer, limit=40 * 1024 * 1024,
                                 cancellation=cancellation)
                    cancellation.check()
                    save_image(data, staging / f"{number:04d}", is_webtoon)
                    if number < len(images):
                        report(number / len(images))
                (staging / ".bliss-chapter").write_text(identity)
                cancellation.check()
                staging.rename(target)
                created.append(target)
        except (DownloadError, ValueError, OSError) as exc:
            raise DownloadError(f"{chapter.title or position}: {exc} ({completed} chapters already saved.)") from exc
        completed += 1
        report()
    return {"chapters": completed, "folder": str(directory)}
