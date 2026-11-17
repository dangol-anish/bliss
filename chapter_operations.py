"""Coordinate access to chapter files during downloads and processing."""
from threading import Lock

from fastapi import HTTPException


class ChapterOperations:
    def __init__(self):
        self._lock = Lock()
        self._exclusive = None
        self._downloading = False
        self._download_series = set()
        self._detection = None

    def acquire(self, description):
        with self._lock:
            blocker = self._blocker()
            if blocker:
                raise HTTPException(409, f"Cannot start {description} while {blocker}.")
            self._exclusive = description

    def release(self):
        with self._lock:
            self._exclusive = None

    def acquire_download(self):
        with self._lock:
            if self._exclusive:
                raise HTTPException(409, f"Cannot start a download while Bliss is {self._exclusive}.")
            if self._downloading:
                raise HTTPException(409, "Bliss is already downloading a batch.")
            self._downloading = True

    def reserve_download_series(self, folder):
        with self._lock:
            if self._detection and self._detection[0] == folder:
                raise HTTPException(409, f"Cannot download {folder.name} while {self._detection_label()}.")
            self._download_series.add(folder)

    def release_download(self):
        with self._lock:
            self._download_series.clear()
            self._downloading = False

    def acquire_detection(self, folder, chapter):
        with self._lock:
            blocker = self._blocker(ignore_download=folder not in self._download_series)
            if blocker:
                raise HTTPException(409, f"Cannot detect panels while {blocker}.")
            self._detection = (folder, chapter)

    def release_detection(self):
        with self._lock:
            self._detection = None

    def _detection_label(self):
        folder, chapter = self._detection
        return f"Bliss is detecting panels for {folder.name}, {chapter}"

    def _blocker(self, *, ignore_download=False):
        if self._exclusive:
            return f"Bliss is {self._exclusive}"
        if self._detection:
            return self._detection_label()
        if self._downloading and not ignore_download:
            return "Bliss is downloading chapters"
        return None
