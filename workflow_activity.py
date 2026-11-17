"""Track operations independently of the browser request that started them."""
from functools import wraps
from threading import Lock
from uuid import uuid4

_lock = Lock()
_active = {}


def track(stage, label):
    def decorator(operation):
        @wraps(operation)
        def run(*args, **kwargs):
            selection = kwargs.get("body") or kwargs.get("request")
            progress_id = getattr(selection, "progress_id", None)
            activity_id = uuid4().hex
            activity = {"id": activity_id, "stage": stage, "label": label,
                        "series": getattr(selection, "series", None),
                        "chapter": getattr(selection, "chapter", None),
                        "end_chapter": getattr(selection, "end_chapter", None),
                        "progress_id": progress_id, "percent": None}
            with _lock:
                _active[activity_id] = activity
            try:
                return operation(*args, **kwargs)
            finally:
                with _lock:
                    _active.pop(activity_id, None)
        return run
    return decorator


def report(progress_id, percent):
    if progress_id is None:
        return
    with _lock:
        for activity in _active.values():
            if activity["progress_id"] == progress_id:
                activity["percent"] = percent


def active_operations():
    with _lock:
        return [dict(activity) for activity in _active.values()]
