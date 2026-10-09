"""HTTPS GET client for the Diet proceedings meeting list.

The only network call is GET {API_BASE}/api/meeting_list. API_BASE is a module
attribute so a test harness can point it at a local server. Callers cannot
pass a host. Redirects are not followed. One request is in flight at a time,
and the next send waits until 5 seconds after the previous response.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

API_BASE = "https://kokkai.ndl.go.jp"
MEETING_LIST_PATH = "/api/meeting_list"
USER_AGENT = "jp-kokkai/0.1.0"
TIMEOUT_SECONDS = 30
BODY_CAP = 2_000_000
GAP_SECONDS = 5.0
REDIRECTS = frozenset({301, 302, 303, 307, 308})

_guard = threading.Lock()
_busy = False
_last_done: float | None = None
_clock: Callable[[], float] = time.monotonic


class ClientError(Exception):
    def __init__(self, code: str, message: str, next_step: str, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_step = next_step
        self.status = status


def set_clock(fn: Callable[[], float]) -> None:
    global _clock
    _clock = fn


def reset_flight() -> None:
    """Test helper. A clock reading 0 must not block the first send."""
    global _busy, _last_done
    with _guard:
        _busy = False
        _last_done = None


def gap_remaining() -> float | None:
    """Seconds until another send is allowed. None when a request is in flight."""
    with _guard:
        if _busy:
            return None
        if _last_done is None:
            return 0.0
        return max(0.0, GAP_SECONDS - (_clock() - _last_done))


def _begin() -> None:
    global _busy
    with _guard:
        if _busy:
            raise ClientError(
                "busy",
                "Another request is still in flight, so this one was not sent.",
                "Wait until that request finishes, then try again. Nothing was saved.",
            )
        if _last_done is not None and (_clock() - _last_done) < GAP_SECONDS:
            raise ClientError(
                "too_soon",
                "The previous response was less than 5 seconds ago, so this request was not sent.",
                "Wait at least 5 seconds after a response. The gap cannot be set shorter. Nothing was saved.",
            )
        _busy = True


def _end() -> None:
    global _busy, _last_done
    with _guard:
        _last_done = _clock()
        _busy = False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


def _origin() -> str:
    parsed = urllib.parse.urlsplit(API_BASE)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ClientError(
            "bad_base",
            "The meeting list URL is not a usable origin, so nothing was requested.",
            "Nothing was saved.",
        )
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return f"{parsed.scheme}://{host}"


def build_url(pairs: list[tuple[str, str]]) -> str:
    query = urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote)
    return f"{_origin()}{MEETING_LIST_PATH}?{query}" if query else f"{_origin()}{MEETING_LIST_PATH}"


def _urllib_transport(url: str) -> tuple[int, bytes]:
    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            status = int(getattr(response, "status", 0) or 0)
            body = response.read(BODY_CAP + 1)
            return status, body
    except urllib.error.HTTPError as exc:
        body = exc.read(BODY_CAP + 1) if exc.fp is not None else b""
        return int(exc.code), body


def fetch(pairs: list[tuple[str, str]], transport: Callable[[str], tuple[int, bytes]] | None = None) -> dict[str, Any]:
    """Send one GET. Raises ClientError before a send when the flight gate refuses."""
    url = build_url(pairs)
    parsed = urllib.parse.urlsplit(url)
    if parsed.path != MEETING_LIST_PATH:
        raise ClientError(
            "bad_path",
            "Only /api/meeting_list is requested, so this call was not sent.",
            "Nothing was saved.",
        )
    _begin()
    sent = True
    try:
        try:
            status, body = (transport or _urllib_transport)(url)
        except (TimeoutError, TimeoutError) as exc:
            raise ClientError(
                "timeout",
                "The request timed out, so the meetings were not fetched. Saved state is unchanged.",
                "Try again later. This plugin does not retry in the same call.",
            ) from exc
        except ClientError:
            raise
        except Exception as exc:
            if isinstance(exc, (TimeoutError, OSError)) and "timed out" in str(exc).lower():
                raise ClientError(
                    "timeout",
                    "The request timed out, so the meetings were not fetched. Saved state is unchanged.",
                    "Try again later. This plugin does not retry in the same call.",
                ) from exc
            raise ClientError(
                "network",
                "The meeting list could not be fetched. Saved state is unchanged.",
                "Try again later. This plugin does not retry in the same call.",
            ) from exc
        return _parse(status, body, url)
    finally:
        if sent:
            _end()


def _parse(status: int, body: bytes, url: str) -> dict[str, Any]:
    if status in REDIRECTS:
        raise ClientError(
            "redirect",
            f"The server returned HTTP {status} and the redirect was not followed. Nothing was saved.",
            "The https meeting list URL is the only one this plugin requests.",
            status=status,
        )
    if len(body) > BODY_CAP:
        raise ClientError(
            "oversize",
            f"The response was larger than {BODY_CAP} bytes, so it was not read as a result. Nothing was saved.",
            "Narrow the query. Tool arguments cannot raise this limit.",
            status=status,
        )
    if status >= 400:
        message, details = _error_fields(body)
        extra = _format_api_error(message, details)
        raise ClientError(
            f"http_{status}",
            f"The meeting list returned HTTP {status}. Do not read this as an empty meeting list. Nothing was saved.{extra}",
            "Change the query and try again. Seen speech IDs were not changed.",
            status=status,
        )
    if status != 200:
        raise ClientError(
            f"http_{status}",
            f"The meeting list returned HTTP {status}. Do not read this as an empty meeting list. Nothing was saved.",
            "Try again later. Seen speech IDs were not changed.",
            status=status,
        )
    if not body or not body.strip():
        raise ClientError(
            "bad_body",
            "The meeting list returned an empty body. Do not read this as an empty meeting list. Nothing was saved.",
            "Try again later. Seen speech IDs were not changed.",
            status=status,
        )
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClientError(
            "bad_body",
            "The meeting list body was not JSON. The HTTP status was 200. Do not read this as an empty meeting list. Nothing was saved.",
            "Try again later. Seen speech IDs were not changed.",
            status=status,
        ) from exc
    if not isinstance(payload, dict):
        raise ClientError(
            "bad_body",
            "The meeting list JSON was not an object. Do not read this as an empty meeting list. Nothing was saved.",
            "Try again later. Seen speech IDs were not changed.",
            status=status,
        )
    if "message" in payload and "numberOfRecords" not in payload:
        extra = _format_api_error(payload.get("message"), payload.get("details"))
        raise ClientError(
            "message",
            f"The meeting list returned an error message. Do not read this as an empty meeting list. Nothing was saved.{extra}",
            "Change the query and try again. Seen speech IDs were not changed.",
            status=status,
        )
    return _page(payload, url)


def _error_fields(body: bytes) -> tuple[Any, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    return payload.get("message"), payload.get("details")


def _format_api_error(message: Any, details: Any) -> str:
    parts: list[str] = []
    if message not in (None, ""):
        parts.append(str(message))
    if details not in (None, "", []):
        if isinstance(details, list):
            parts.extend(str(item) for item in details)
        else:
            parts.append(str(details))
    if not parts:
        return ""
    return " " + " ".join(parts)


def _page(payload: dict[str, Any], url: str) -> dict[str, Any]:
    records = payload.get("numberOfRecords")
    returned = payload.get("numberOfReturn")
    if isinstance(records, bool) or not isinstance(records, int) or isinstance(returned, bool) or not isinstance(returned, int):
        raise ClientError(
            "bad_body",
            "The meeting list did not include integer record counts. Nothing was saved.",
            "Seen speech IDs were not changed.",
        )
    has_array = "meetingRecord" in payload
    meetings = payload.get("meetingRecord")
    zero_shape = (
        records == 0
        and returned == 0
        and not has_array
        and payload.get("nextRecordPosition") is None
        and payload.get("startRecord") == 1
    )
    if zero_shape:
        return {
            "url": url,
            "zero": True,
            "complete": True,
            "can_baseline": True,
            "number_of_records": 0,
            "number_of_return": 0,
            "meetings": [],
            "speech_ids": [],
            "missing_speech_id": False,
        }
    if records == 0 or returned == 0 or not has_array or not isinstance(meetings, list):
        raise ClientError(
            "partial",
            (
                f"The meeting list count does not match one complete page "
                f"(numberOfRecords {records}, numberOfReturn {returned}). "
                "Nothing was saved, and this is not an empty meeting list."
            ),
            "Narrow the query so one response can hold it. Seen speech IDs were not changed.",
        )
    if records != returned or len(meetings) != returned or records > returned:
        raise ClientError(
            "partial",
            (
                f"The meeting list does not fit in one response "
                f"(numberOfRecords {records}, numberOfReturn {returned}, meetings {len(meetings)}). "
                "Nothing was saved."
            ),
            "Narrow the query. Seen speech IDs were not changed. The next page is not requested.",
        )
    parsed, speech_ids, missing = _meetings(meetings)
    return {
        "url": url,
        "zero": False,
        "complete": True,
        "can_baseline": not missing,
        "number_of_records": records,
        "number_of_return": returned,
        "meetings": parsed,
        "speech_ids": speech_ids,
        "missing_speech_id": missing,
    }


def _meetings(meetings: list[Any]) -> tuple[list[dict[str, Any]], list[str], bool]:
    parsed: list[dict[str, Any]] = []
    speech_ids: list[str] = []
    missing = False
    for meeting in meetings:
        if not isinstance(meeting, dict):
            raise ClientError(
                "bad_body",
                "A meeting record was not an object. Nothing was saved.",
                "Seen speech IDs were not changed.",
            )
        speeches_in = meeting.get("speechRecord") or []
        if not isinstance(speeches_in, list):
            raise ClientError(
                "bad_body",
                "speechRecord was not a list. Nothing was saved.",
                "Seen speech IDs were not changed.",
            )
        speeches: list[dict[str, Any]] = []
        for speech in speeches_in:
            if not isinstance(speech, dict):
                missing = True
                continue
            speech_id = speech.get("speechID")
            if not isinstance(speech_id, str) or not speech_id:
                missing = True
            else:
                speech_ids.append(speech_id)
            speeches.append(
                {
                    "speaker": speech.get("speaker"),
                    "speechID": speech_id if isinstance(speech_id, str) else None,
                    "speechOrder": speech.get("speechOrder"),
                    "speechURL": speech.get("speechURL"),
                }
            )
        parsed.append(
            {
                "nameOfHouse": meeting.get("nameOfHouse"),
                "nameOfMeeting": meeting.get("nameOfMeeting"),
                "date": meeting.get("date"),
                "meetingURL": meeting.get("meetingURL"),
                "issue": meeting.get("issue"),
                "session": meeting.get("session"),
                "speechRecord": speeches,
            }
        )
    return parsed, speech_ids, missing
