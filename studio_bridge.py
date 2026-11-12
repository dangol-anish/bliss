"""Explicitly paired, loopback-only extension transport. Never handles Google login."""
import re
import secrets
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

router = APIRouter(prefix='/api/bliss-studio')
lock = threading.RLock()
pair_code = None
pair_deadline = 0
connection = None
pending = None


def local_request(request):
    if request.headers.get('host') not in {'127.0.0.1:8000', 'localhost:8000', 'testserver'}:
        raise HTTPException(403, 'Open the app at http://127.0.0.1:8000.')
    origin = request.headers.get('origin')
    if origin and origin not in {'http://127.0.0.1:8000', 'http://localhost:8000'}:
        raise HTTPException(403, 'Use the local Narration page for this action.')
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Use the local Narration page for this action.')


def extension_id(request):
    ident = request.headers.get('x-narration-extension', '')
    origin = request.headers.get('origin')
    if not re.fullmatch('[a-p]{32}', ident) or origin not in (None, 'chrome-extension://' + ident):
        raise HTTPException(403, 'Only the paired narration extension can connect.')
    if request.headers.get('host') not in {'127.0.0.1:8000', 'testserver'}:
        raise HTTPException(403, 'Use the loopback app address.')
    return ident


def authenticate(request):
    ident = extension_id(request)
    supplied = request.headers.get('authorization', '')
    with lock:
        if not connection or connection['id'] != ident or not secrets.compare_digest(supplied, 'Bearer ' + connection['token']):
            raise HTTPException(403, 'Pair the extension again from the Narration page.')
        connection['seen'] = time.monotonic()


@router.get('/status')
def status(request: Request):
    local_request(request)
    return connection_view()


def connection_view():
    with lock:
        return {'connected': bool(connection and time.monotonic() - connection['seen'] < 15),
                'paired': bool(connection)}


@router.post('/heartbeat')
def heartbeat(request: Request):
    authenticate(request)
    return {'alive':True}


class Progress(BaseModel):
    id:str=Field(pattern='^[a-f0-9]{32}$')
    step:str=Field(max_length=40)


@router.post('/progress')
def progress(body:Progress,request:Request):
    authenticate(request)
    with lock:
        if not pending or pending['id']!=body.id or not pending['claimed'] or 'result' in pending:
            raise HTTPException(409,'This browser action is no longer active.')
        pending['progress']=body.step
    return {'saved':True}


@router.post('/code')
def code(request: Request):
    global pair_code, pair_deadline
    local_request(request)
    with lock:
        pair_code = secrets.token_urlsafe(24)
        pair_deadline = time.monotonic() + 300
        return {'code': pair_code, 'expires_seconds': 300}


class Pair(BaseModel):
    code: str = Field(min_length=1, max_length=100)


@router.post('/pair')
def pair(body: Pair, request: Request):
    global connection, pair_code
    ident = extension_id(request)
    with lock:
        if not pair_code or time.monotonic() > pair_deadline or not secrets.compare_digest(body.code, pair_code):
            raise HTTPException(403, 'Get a new pairing code from the local Narration page.')
        if pending and pending.get('claimed') and 'result' not in pending:
            raise HTTPException(409, 'Stop the current queue before changing its browser connection.')
        connection = {'id': ident, 'token': secrets.token_urlsafe(32), 'seen': time.monotonic()}
        pair_code = None
        return {'token': connection['token']}


@router.post('/poll')
def poll(request: Request):
    authenticate(request)
    with lock:
        if not pending or pending['claimed'] or pending['cancel'].is_set() or time.time() * 1000 > pending['expiresAt']:
            return {'command': None}
        pending['claimed'] = True  # One delivery only, including after worker/browser crashes.
        return {'command': {k: pending[k] for k in ('id', 'action', 'payload', 'expiresAt')}}


class Result(BaseModel):
    id: str = Field(pattern='^[a-f0-9]{32}$')
    value: dict = Field(default_factory=dict)


class Permit(BaseModel):
    id: str = Field(pattern='^[a-f0-9]{32}$')


@router.post('/permit')
def permit(body: Permit, request: Request):
    authenticate(request)
    with lock:
        if not pending or pending['id'] != body.id or pending['action'] != 'send' or not pending['claimed'] or pending.get('permitted') or pending['cancel'].is_set() or time.time()*1000 > pending['expiresAt']:
            raise HTTPException(409, 'This submission was stopped or expired. Nothing should be sent.')
        pending['permitted'] = True
    return {'permitted': True}


@router.post('/result')
async def result(request: Request):
    authenticate(request)
    # Bound snapshots before decoding; photos travel in the opposite direction.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8_000_000: raise HTTPException(413, 'Browser response is too large; inspect the last reply manually.')
    try: body = Result.model_validate_json(raw)
    except ValidationError: raise HTTPException(400, 'The browser acknowledgement is malformed. Inspect the last action before resuming.') from None
    with lock:
        if not pending or pending['id'] != body.id or not pending.get('claimed'):
            raise HTTPException(409, 'This browser command expired. It will not be resent.')
        if 'result' not in pending: pending['result'] = body.value
    return {'saved': True}


@router.post('/open-folder')
def open_folder(request: Request):
    local_request(request)
    import os
    import subprocess
    import sys
    path = Path(__file__).resolve().parent / 'browser-extension'
    if sys.platform == 'darwin': subprocess.run(['open', str(path)], check=True, timeout=10)
    elif sys.platform == 'win32': os.startfile(str(path))
    else: raise HTTPException(400, 'Use the browser-extension folder inside this repository.')
    return {'opened': True}


def command(action, payload, cancel, *, timeout=35, report=None):
    global pending
    with lock:
        if not connection or time.monotonic() - connection['seen'] > 15:
            raise RuntimeError('The browser extension is disconnected. Open its popup in your usual browser, connect it to this app, then Resume. Nothing was resent.')
        if pending is not None:
            raise RuntimeError('An earlier browser command is still unresolved. Stop the queue and inspect AI Studio before resuming.')
        entry = {'id': secrets.token_hex(16), 'action': action, 'payload': payload or {}, 'claimed': False,
                 'expiresAt': int((time.time()+timeout)*1000), 'cancel': cancel}
        pending = entry
    deadline = time.monotonic() + timeout
    reported=None
    try:
        while time.monotonic() < deadline:
            if cancel and cancel.is_set(): raise InterruptedError('Stopped. Inspect AI Studio before retrying an unfinished request.')
            with lock:
                activity=entry.get('progress')
                if 'result' in entry:
                    value = entry['result']
                    if value.get('error'):
                        raise RuntimeError(str(value['error'])[:1800])
                    return value
            if report and activity and activity != reported:
                report(activity)
                reported = activity
            time.sleep(.1)
        raise RuntimeError('The extension did not confirm its last action. Check the AI Studio tab before resuming; the app will not automatically repeat that action.')
    finally:
        with lock:
            if pending is entry: pending = None
