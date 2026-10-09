"""Search and watch for Diet meeting metadata."""
from __future__ import annotations

import inspect
import json
import os
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

if __package__:
    from . import client, gates
else:
    import client
    import gates

TOOLSET = "jp_kokkai"
STATE_NAME = "state.json"
HOUSES = ("衆議院", "参議院", "両院", "両院協議会")
WATCH_ID = re.compile(r"^[0-9a-f]{32}\Z")
DATE_TEXT = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
QUERY_LIMIT = 2000
MAX_WATCHES = 3
LOOKBACK_DEFAULT = 30
SEARCH_DEFAULT = 10
# A daily cron can fire a little early. Exactly 24 hours would stay silent on day 2.
NOTIFY_GAP = 23 * 60 * 60
SPEAKER_CAP = 30
PDL_URL = "https://www.digital.go.jp/resources/open_data/public_data_license_v1.0"
API_HTML = "https://kokkai.ndl.go.jp/api.html"
INVERTED_MEASURED = "(0) (19014)fromはuntil以下、もしくはuntilはfrom以上の西暦年で入力してください。"
CRON_PROMPT = (
    'Call the kokkai_watch tool with {"action":"run"} only. '
    "If the tool result has notify true, reply with its message field and nothing else. "
    "If notify is false, reply with [SILENT]. "
    "If the tool is missing, reply that the watch did not run. "
    "Do not call kokkai_search. Do not add or remove a watch. Do not quote speeches. "
    "A model may still add words; that limit is not enforceable here."
)
SEARCH_KEYS = {"any", "speaker", "house", "from", "until", "maximum_records"}
ADD_KEYS = {"action", "any", "speaker", "house", "hour", "deliver", "lookback_days"}
REMOVE_KEYS = {"action", "watch_id"}
LIST_KEYS = {"action"}
RUN_KEYS = {"action"}

Transport = Callable[[str], tuple[int, bytes]]


class Deps:
    def __init__(
        self,
        *,
        transport: Transport | None = None,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        today: Callable[[], date | None] | None = None,
        approver: Callable[[str, str], tuple[bool, str]] | None = None,
        write_guard: Callable[[str], str | None] | None = None,
        data_dir: Path | None = None,
        cron: Any = None,
        cron_judge: Callable[[], str] | None = None,
        platforms: set[str] | None = None,
        profile: str | None = None,
        load_platforms: Callable[[], set[str] | None] | None = None,
    ):
        self.transport = transport
        self.now = now
        self.sleep = sleep or time_sleep
        self.today = today
        self.approver = approver
        self.write_guard = write_guard
        self.data_dir = data_dir
        self.cron = cron
        self.cron_judge = cron_judge
        self.platforms = platforms
        self.profile = profile
        self.load_platforms = load_platforms


def time_sleep(seconds: float) -> None:
    import time
    time.sleep(seconds)


def dumps(body: dict[str, Any], *, ordered: bool = False) -> str:
    return json.dumps(body, ensure_ascii=False, sort_keys=not ordered)


def _fail(code: str, message: str, next_step: str, *, notify: bool = True, **extra: Any) -> str:
    # message stays first. sort_keys would hide it behind a large body.
    body: dict[str, Any] = {"message": message, "ok": False, "error": code, "next_step": next_step, "notify": notify}
    body.update(extra)
    return dumps(body, ordered=True)


def _ok(message: str, *, notify: bool, **extra: Any) -> str:
    body: dict[str, Any] = {"message": message, "ok": True, "notify": notify}
    body.update(extra)
    return dumps(body, ordered=True)


def tokyo_today() -> date | None:
    try:
        tz = ZoneInfo("Asia/Tokyo")
    except Exception:
        return None
    try:
        return datetime.now(tz).date()
    except Exception:
        return None


def attribution(urls: list[str], day: date) -> str:
    where = "、".join(urls) if urls else API_HTML
    return (
        "出典：国立国会図書館「国会会議録検索システム」（"
        f"{where}）、PDL1.0（{PDL_URL}）（{day.isoformat()}に利用）。"
        "国立国会図書館「国会会議録検索システム」の検索結果を jp-kokkai が選択して作成。"
    )


def _with_attribution(english: str, urls: list[str], day: date) -> str:
    return english + "\n" + attribution(urls, day)


def _commands(deps: Deps) -> list[str]:
    return gates.hermes_cron_lines(deps.profile)


def _today(deps: Deps) -> date | None:
    if deps.today is not None:
        return deps.today()
    return tokyo_today()


def _bind_clock(deps: Deps) -> None:
    if deps.now is not None:
        client.set_clock(deps.now)


def _platforms(deps: Deps) -> set[str] | None:
    if deps.platforms is not None:
        return deps.platforms
    if deps.load_platforms is not None:
        return deps.load_platforms()
    return gates.load_platforms()


def _guard(deps: Deps, path: str) -> str | None:
    if deps.write_guard is not None:
        return deps.write_guard(path)
    return gates.write_guard_error(path)


def _data_dir(deps: Deps) -> Path:
    if deps.data_dir is not None:
        return deps.data_dir
    return gates.plugin_data_dir()


def _approve(deps: Deps, action: str, text: str) -> tuple[bool, str]:
    if deps.approver is not None:
        if not gates.reason_fits(text):
            return False, (
                "BLOCKED: the approval question would not fit in one message, so it was not sent "
                "and the watch was not changed."
            )
        return deps.approver(action, text)
    return gates.request_watch_approval(action, text)


def _judge(deps: Deps) -> str:
    if deps.cron_judge is not None:
        return deps.cron_judge()
    return gates.judge_cron()


def _cron_module(deps: Deps) -> Any:
    if deps.cron is not None:
        return deps.cron
    status, mod = gates._load("cron.jobs", "create_job")
    if status != "ok":
        raise RuntimeError("cron.jobs is not available")
    import cron.jobs as jobs
    return jobs


def _job_presence(deps: Deps, job_id: Any) -> str:
    """live, missing, or unreadable. A stored id is not live until get_job finds it."""
    if not isinstance(job_id, str) or not job_id:
        return "missing"
    try:
        jobs = _cron_module(deps)
        found = getattr(jobs, "get_job")(job_id)
    except Exception:
        return "unreadable"
    if found is None:
        return "missing"
    return "live"


def _ensure_job(deps: Deps, state: dict[str, Any], hour: int, deliver: str) -> tuple[str, str | None]:
    """Keep, update, or create the one job. Does not write the state file.

    Returns status and, on create_failed, the error text.
    status is kept, updated, created, recreated, unreadable, update_failed, or create_failed.
    created and recreated store the new id on state.
    """
    stored = state.get("job_id")
    presence = _job_presence(deps, stored)
    if presence == "unreadable":
        return "unreadable", None
    slot_differs = state.get("hour") != hour or state.get("deliver") != deliver
    if presence == "live" and not slot_differs:
        return "kept", None
    if presence == "live":
        if not _update_job(deps, str(stored), hour, deliver):
            return "update_failed", None
        return "updated", None
    job_id, error = _create_job(deps, hour, deliver)
    if error or not job_id:
        return "create_failed", error or "The cron job did not return an id."
    state["job_id"] = job_id
    if isinstance(stored, str) and stored:
        return "recreated", None
    return "created", None


def _text(value: Any) -> str | None:
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    return value


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _unknown(keys: set[str], allowed: set[str]) -> str | None:
    extra = sorted(keys - allowed)
    if not extra:
        return None
    return (
        "These arguments are not accepted, so nothing was requested: " + ", ".join(extra) + "."
    )


def _parse_date(value: str) -> date | None:
    if not DATE_TEXT.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _house(value: str) -> str | None:
    if value == "":
        return ""
    if value in HOUSES:
        return value
    return None


def _pairs(fields: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(key, value) for key, value in fields if value != ""]


def _query_too_long(pairs: list[tuple[str, str]]) -> bool:
    encoded = urllib_query(pairs)
    return len(encoded.encode("utf-8")) > QUERY_LIMIT


def urllib_query(pairs: list[tuple[str, str]]) -> str:
    import urllib.parse
    return urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote)


def _window(day: date, lookback: int) -> tuple[str, str]:
    start = day - timedelta(days=lookback - 1)
    return start.isoformat(), day.isoformat()


def search(deps: Deps, args: dict[str, Any] | None) -> str:
    _bind_clock(deps)
    args = args or {}
    if not isinstance(args, dict):
        return _fail("bad_args", "Arguments must be an object. Nothing was requested.", "Pass a JSON object.")
    unknown = _unknown(set(args), SEARCH_KEYS)
    if unknown:
        return _fail("bad_args", unknown, "Remove the extra arguments and try again.", notify=False)
    built = _search_query(args)
    if isinstance(built, str):
        return _fail("bad_args", built, "Fix the arguments. Nothing was requested.", notify=False)
    day = _today(deps)
    if day is None:
        return _fail(
            "timezone",
            "Asia/Tokyo could not be resolved, so the meeting list was not requested.",
            "Nothing was saved.",
            notify=False,
        )
    pairs, _meta = built
    if _query_too_long(pairs):
        return _fail(
            "bad_args",
            "The query is longer than 2000 bytes, so it was not sent.",
            "Shorten the words and try again.",
            notify=False,
        )
    try:
        page = client.fetch(pairs, deps.transport)
    except client.ClientError as exc:
        return _fail(exc.code, exc.message, exc.next_step, notify=False)
    return _ok(
        _with_attribution(_search_english(page), _meeting_urls(page), day),
        notify=False,
        meeting_count=page["number_of_records"],
        meetings=_meeting_summaries(page["meetings"]),
    )


def _meeting_summaries(meetings: list[Any]) -> list[dict[str, Any]]:
    """One short object per meeting. Speech rows are what made 7 meetings 80000 characters."""
    summaries: list[dict[str, Any]] = []
    for meeting in meetings:
        if not isinstance(meeting, dict):
            continue
        speeches = meeting.get("speechRecord") or []
        if not isinstance(speeches, list):
            speeches = []
        speakers: list[str] = []
        seen_names: set[str] = set()
        for speech in speeches:
            if not isinstance(speech, dict):
                continue
            name = speech.get("speaker")
            if isinstance(name, str) and name and name not in seen_names:
                seen_names.add(name)
                if len(speakers) < SPEAKER_CAP:
                    speakers.append(name)
        summaries.append(
            {
                "nameOfHouse": meeting.get("nameOfHouse"),
                "nameOfMeeting": meeting.get("nameOfMeeting"),
                "date": meeting.get("date"),
                "meetingURL": meeting.get("meetingURL"),
                "issue": meeting.get("issue"),
                "session": meeting.get("session"),
                "speech_count": len(speeches),
                "speaker_count": len(seen_names),
                "speakers": speakers,
            }
        )
    return summaries


def _search_english(page: dict[str, Any]) -> str:
    count = page["number_of_records"]
    if page["zero"]:
        return "0 meetings. Names are copied as the API sent them. Speech text is not included."
    if page["missing_speech_id"]:
        return (
            f"{count} meetings. One or more speech rows had no speechID, so seen speech IDs were not changed. "
            "Names are copied as the API sent them. Speech text is not included."
        )
    return (
        f"{count} meetings. Names are copied as the API sent them. Speech text is not included. "
        "This search does not mark speech IDs seen."
    )


def _search_query(args: dict[str, Any]) -> str | tuple[list[tuple[str, str]], dict[str, Any]]:
    any_text = _text(args.get("any", ""))
    speaker = _text(args.get("speaker", ""))
    house = _text(args.get("house", ""))
    start = _text(args.get("from", ""))
    end = _text(args.get("until", ""))
    if None in {any_text, speaker, house, start, end}:
        return "any, speaker, house, from, and until must be strings."
    assert any_text is not None and speaker is not None and house is not None and start is not None and end is not None
    if house != "" and _house(house) is None:
        return (
            f"house {house!r} is not one of 衆議院, 参議院, 両院, 両院協議会. "
            "An unknown house is ignored by the API and would look like a search with no house, "
            "so nothing was requested."
        )
    if (start == "") != (end == ""):
        return "from and until must be given together. Nothing was requested."
    if start and end:
        start_day = _parse_date(start)
        end_day = _parse_date(end)
        if start_day is None or end_day is None:
            return "from and until must be YYYY-MM-DD. Nothing was requested."
        if start_day > end_day:
            return (
                "from is after until, so nothing was requested. "
                f"The measured API sentence is: {INVERTED_MEASURED}"
            )
    maximum = args.get("maximum_records", SEARCH_DEFAULT)
    if "maximum_records" not in args:
        maximum = SEARCH_DEFAULT
    count = _int(maximum)
    if count is None or not 1 <= count <= 30:
        return "maximum_records must be an integer from 1 to 30. Nothing was requested."
    if any_text == "" and speaker == "" and house == "" and start == "":
        return "A search needs any, speaker, a house, or both from and until. Nothing was requested."
    pairs = _pairs([
        ("any", any_text),
        ("speaker", speaker),
        ("nameOfHouse", house),
        ("from", start),
        ("until", end),
        ("maximumRecords", str(count)),
        ("recordPacking", "json"),
        ("startRecord", "1"),
    ])
    return pairs, {"maximum_records": count}


def watch(deps: Deps, args: dict[str, Any] | None) -> str:
    _bind_clock(deps)
    args = args or {}
    if not isinstance(args, dict):
        return _fail("bad_args", "Arguments must be an object. Nothing was requested.", "Pass a JSON object.")
    if "action" not in args:
        if args:
            return _fail(
                "bad_args",
                _unknown(set(args), set()) or "Arguments are not accepted without an action.",
                "Pass action run with no other arguments, or name add, remove, or list.",
            )
        action = "run"
    else:
        action = args.get("action")
    if action == "add":
        return _add(deps, args)
    if action == "remove":
        return _remove(deps, args)
    if action == "list":
        return _list(deps, args)
    if action == "run":
        unknown = _unknown(set(args), RUN_KEYS)
        if unknown:
            return _fail("bad_args", unknown, "Call run with no other arguments. Nothing was requested.")
        return _run(deps)
    shown = action if isinstance(action, str) else type(action).__name__
    return _fail(
        "bad_args",
        f"action {shown!r} is not one of add, remove, list, or run. Nothing was requested.",
        "Use one of those four actions.",
    )


def _meetings_with_new_speeches(page: dict[str, Any], fresh: list[str]) -> tuple[int, list[str]]:
    """Count meetings that contain a new speech, and return only those meeting URLs."""
    wanted = set(fresh)
    count = 0
    urls: list[str] = []
    for meeting in page["meetings"]:
        if not isinstance(meeting, dict):
            continue
        speeches = meeting.get("speechRecord") or []
        if not isinstance(speeches, list):
            continue
        if not any(
            isinstance(speech, dict)
            and isinstance(speech.get("speechID"), str)
            and speech.get("speechID") in wanted
            for speech in speeches
        ):
            continue
        count += 1
        url = meeting.get("meetingURL")
        if isinstance(url, str) and url and url not in urls:
            urls.append(url)
    return count, urls


def _meeting_urls(page: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for meeting in page["meetings"]:
        url = meeting.get("meetingURL")
        if isinstance(url, str) and url:
            urls.append(url)
    return urls


def _empty_state() -> dict[str, Any]:
    return {"job_id": None, "hour": None, "deliver": None, "watches": []}


def _load_state(deps: Deps) -> tuple[str, dict[str, Any] | None, Path | None]:
    try:
        directory = _data_dir(deps)
    except Exception:
        return "unavailable", None, None
    path = directory / STATE_NAME
    if not path.exists():
        return "missing", _empty_state(), path
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "corrupt", None, path
    if not isinstance(data, dict) or not isinstance(data.get("watches"), list):
        return "corrupt", None, path
    for watch_row in data["watches"]:
        if not isinstance(watch_row, dict):
            return "corrupt", None, path
    return "ok", data, path


def _save_state(deps: Deps, path: Path, data: dict[str, Any]) -> str | None:
    directory = path.parent
    temporary = directory / ".state.json.tmp"
    for target in (path, temporary):
        denied = _guard(deps, str(target))
        if denied:
            return denied
    try:
        directory.mkdir(parents=True, exist_ok=True)
        temporary.write_text(dumps(data), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        return f"The watch state could not be saved ({exc.__class__.__name__})."
    return None


def _condition(row: dict[str, Any]) -> tuple[str, str, str, int]:
    return (
        str(row.get("any") or ""),
        str(row.get("speaker") or ""),
        str(row.get("house") or ""),
        int(row.get("lookback_days") or LOOKBACK_DEFAULT),
    )


def _add(deps: Deps, args: dict[str, Any]) -> str:
    unknown = _unknown(set(args), ADD_KEYS)
    if unknown:
        return _fail("bad_args", unknown, "Remove the extra arguments. Nothing was saved.")
    parsed = _add_fields(args)
    if isinstance(parsed, str):
        return _fail("bad_args", parsed, "Fix the arguments. Nothing was saved.")
    status, state, path = _load_state(deps)
    if status == "corrupt":
        return _fail(
            "corrupt",
            "The watch state file is unreadable, so it was not overwritten and nothing was requested.",
            "Move the state file aside if you want to start again. Seen speech IDs were not changed.",
        )
    if status == "unavailable" or state is None or path is None:
        return _fail(
            "storage",
            "The plugin data directory is not available, so the watch was not saved.",
            "Nothing was requested.",
        )
    deliver = gates.canonical_deliver(parsed["deliver"], _platforms(deps))
    if deliver is None:
        return _fail(
            "bad_args",
            "That deliver target is not accepted, so no job was created and the existing job was left as it is.",
            "Use origin, local, all, bot-chat, bot-chat:<profile>, a platform, platform:chat_id, or a comma combination.",
        )
    parsed["deliver"] = deliver
    match = next((row for row in state["watches"] if _condition(row) == _condition(parsed)), None)
    same_slot = state.get("hour") == parsed["hour"] and state.get("deliver") == deliver
    presence = _job_presence(deps, state.get("job_id"))
    if presence == "unreadable":
        return _fail(
            "cron",
            "The saved cron job could not be checked, so the watch was not saved.",
            "Nothing was requested.",
            commands=_commands(deps),
        )
    if match is not None and same_slot and presence == "live":
        return _fail(
            "duplicate",
            "That watch is already saved with the same hour and deliver target. Nothing was requested.",
            "Remove it or choose a different query, hour, or deliver target.",
        )
    if match is None and len(state["watches"]) >= MAX_WATCHES:
        return _fail(
            "limit",
            "Three watches are already saved, so this one was not added.",
            "Remove one watch first. Nothing was requested.",
        )
    text = _approval_add(parsed, state, presence)
    approved, reason = _approve(deps, "add", text)
    if not approved:
        return _fail("approval", reason, "Nothing was requested and no job was changed.", requested=False)
    if match is not None:
        return _retarget(deps, state, path, parsed)
    day = _today(deps)
    if day is None:
        return _fail(
            "timezone",
            "Asia/Tokyo could not be resolved, so the meeting list was not requested and the watch was not saved.",
            "Nothing was saved.",
        )
    start, end = _window(day, parsed["lookback_days"])
    pairs = _watch_pairs(parsed, start, end)
    if _query_too_long(pairs):
        return _fail("bad_args", "The query is longer than 2000 bytes, so it was not sent.", "Shorten the words.")
    try:
        page = client.fetch(pairs, deps.transport)
    except client.ClientError as exc:
        return _fail(exc.code, exc.message, exc.next_step)
    if not page["can_baseline"]:
        return _fail(
            "partial",
            _with_attribution(
                "The result does not fit in one response or a speech row has no speechID, "
                "so the watch was not saved and no cron job was created.",
                _meeting_urls(page),
                day,
            ),
            "Narrow the query. Seen speech IDs were not changed.",
            meeting_count=page["number_of_records"],
        )
    row = {
        "id": uuid.uuid4().hex,
        "any": parsed["any"],
        "speaker": parsed["speaker"],
        "house": parsed["house"],
        "lookback_days": parsed["lookback_days"],
        "seen": list(page["speech_ids"]),
        "last_complete_count": page["number_of_records"],
        "failure": None,
    }
    previous_hour = state.get("hour")
    previous_deliver = state.get("deliver")
    status, error = _ensure_job(deps, state, parsed["hour"], deliver)
    if status == "unreadable":
        return _fail(
            "cron",
            "The saved cron job could not be checked, so the watch was not saved.",
            "Nothing was saved.",
            commands=_commands(deps),
        )
    if status == "update_failed":
        return _fail(
            "cron",
            (
                f"The cron job could not be updated. The previous hour {previous_hour} "
                f"and deliver target {previous_deliver} are still in effect. The new watch was not saved."
            ),
            "Try again. The old schedule remains.",
            commands=_commands(deps),
        )
    if status == "create_failed":
        return _fail("cron", error or "The cron job did not return an id.", "No watch was saved.", commands=_commands(deps))
    created_now = status in {"created", "recreated"}
    state["hour"] = parsed["hour"]
    state["deliver"] = deliver
    state["watches"] = list(state["watches"]) + [row]
    saved = _save_state(deps, path, state)
    if saved:
        leftover = ""
        if created_now and state.get("job_id"):
            removed = _remove_job(deps, state["job_id"])
            if removed is not True:
                leftover = f" The cron job id {state['job_id']} is still present."
        return _fail(
            "storage",
            "The watch state could not be saved." + leftover,
            "Nothing in the state file was replaced.",
            commands=_commands(deps),
        )
    note = "The speech IDs already in this response were marked seen and were not announced as new."
    if page["zero"]:
        note = "0 meetings. No speech IDs were announced as new."
    slot = ""
    if status == "updated":
        slot = f" The previous hour was {previous_hour} and the previous deliver target was {previous_deliver}."
    elif status == "recreated":
        slot = " The saved cron job was gone, so a new one was created."
    return _ok(
        _with_attribution(
            f"The watch was saved.{slot} {note}",
            _meeting_urls(page),
            day,
        ),
        notify=True,
        watch_id=row["id"],
        meeting_count=page["number_of_records"],
        commands=_commands(deps),
    )


def _add_fields(args: dict[str, Any]) -> dict[str, Any] | str:
    any_text = _text(args.get("any", ""))
    speaker = _text(args.get("speaker", ""))
    house = _text(args.get("house", ""))
    if None in {any_text, speaker, house}:
        return "any, speaker, and house must be strings."
    assert any_text is not None and speaker is not None and house is not None
    if any_text == "" and speaker == "":
        return "A watch needs any or speaker. A from/until range alone is not a watch. Nothing was requested."
    if house != "" and _house(house) is None:
        return (
            f"house {house!r} is not one of 衆議院, 参議院, 両院, 両院協議会. "
            "Nothing was requested."
        )
    if "hour" not in args:
        return "hour is required (0 through 23). Nothing was requested."
    hour = _int(args.get("hour"))
    if hour is None or not 0 <= hour <= 23:
        return "hour must be an integer from 0 through 23. Nothing was requested."
    if "deliver" not in args or not isinstance(args.get("deliver"), str) or not args.get("deliver").strip():
        return "deliver is required. Nothing was requested."
    lookback = args.get("lookback_days", LOOKBACK_DEFAULT)
    if "lookback_days" not in args:
        lookback = LOOKBACK_DEFAULT
    days = _int(lookback)
    if days is None or not 1 <= days <= 30:
        return "lookback_days must be an integer from 1 to 30. Nothing was requested."
    return {
        "any": any_text,
        "speaker": speaker,
        "house": house,
        "hour": hour,
        "deliver": args["deliver"],
        "lookback_days": days,
    }


def _approval_add(parsed: dict[str, Any], state: dict[str, Any], presence: str) -> str:
    previous = ""
    if presence == "live" and (state.get("hour") != parsed["hour"] or state.get("deliver") != parsed["deliver"]):
        previous = f" This replaces hour {state.get('hour')} deliver {state.get('deliver')}."
    elif presence == "missing" and state.get("job_id"):
        previous = " The saved cron job is gone and a new one will be created."
    return (
        f"Add a Diet meeting watch. any={parsed['any']!r} speaker={parsed['speaker']!r} "
        f"house={parsed['house']!r} lookback_days={parsed['lookback_days']}. "
        f"Deliver to {parsed['deliver']} at hour {parsed['hour']} once a day.{previous} "
        "Choose once."
    )


def _approval_remove(row: dict[str, Any], state: dict[str, Any], presence: str, last: bool) -> str:
    """Say what remove will do. Do not reuse the add question."""
    head = (
        f"Remove Diet meeting watch {row.get('id')}. "
        f"any={row.get('any')!r} speaker={row.get('speaker')!r} house={row.get('house')!r} "
        f"lookback_days={row.get('lookback_days')}."
    )
    if presence != "live":
        if last:
            tail = " The cron job is already gone, so this removes the saved watch only."
        else:
            tail = " The cron job is already gone. This removes one watch only. The cron job is not deleted."
    elif last:
        tail = (
            f" This removes the cron job. It delivers to {state.get('deliver')} at hour {state.get('hour')}."
        )
    else:
        tail = (
            " This removes one watch only. The cron job stays. "
            f"It still delivers to {state.get('deliver')} at hour {state.get('hour')}."
        )
    return head + tail + " Choose once."


def _watch_pairs(parsed: dict[str, Any], start: str, end: str) -> list[tuple[str, str]]:
    return _pairs([
        ("any", parsed["any"]),
        ("speaker", parsed["speaker"]),
        ("nameOfHouse", parsed["house"]),
        ("from", start),
        ("until", end),
        ("maximumRecords", "100"),
        ("recordPacking", "json"),
        ("startRecord", "1"),
    ])


def _retarget(deps: Deps, state: dict[str, Any], path: Path, parsed: dict[str, Any]) -> str:
    previous_hour = state.get("hour")
    previous_deliver = state.get("deliver")
    status, error = _ensure_job(deps, state, parsed["hour"], parsed["deliver"])
    if status == "unreadable":
        return _fail(
            "cron",
            "The saved cron job could not be checked, so the schedule was not changed.",
            "Nothing was changed.",
            commands=_commands(deps),
        )
    if status == "update_failed":
        return _fail(
            "cron",
            (
                f"The cron job could not be updated. The previous hour {previous_hour} "
                f"and deliver target {previous_deliver} are still in effect."
            ),
            "The stored hour and deliver target were not changed.",
            commands=_commands(deps),
        )
    if status == "create_failed":
        return _fail(
            "cron",
            error or "The cron job did not return an id.",
            "The schedule was not changed.",
            commands=_commands(deps),
        )
    state["hour"] = parsed["hour"]
    state["deliver"] = parsed["deliver"]
    saved = _save_state(deps, path, state)
    if saved:
        leftover = ""
        if status in {"created", "recreated"} and state.get("job_id"):
            removed = _remove_job(deps, state["job_id"])
            if removed is not True:
                leftover = f" The cron job id {state['job_id']} is still present."
        return _fail(
            "storage",
            (
                f"The cron job was changed but the state file could not be saved. "
                f"The previous stored hour was {previous_hour} and the deliver target was {previous_deliver}."
                + leftover
            ),
            "Check the cron job and the state file.",
            commands=_commands(deps),
        )
    day = _today(deps)
    if status == "recreated":
        english = (
            "The watch was kept. The saved cron job was gone, so a new one was created. "
            f"It delivers to {parsed['deliver']} at hour {parsed['hour']}."
        )
    elif status == "created":
        english = (
            "The watch was kept. A cron job was created. "
            f"It delivers to {parsed['deliver']} at hour {parsed['hour']}."
        )
    else:
        english = (
            f"The existing watch kept its query. The schedule now delivers to {parsed['deliver']} "
            f"at hour {parsed['hour']}. The previous hour was {previous_hour} and the previous "
            f"deliver target was {previous_deliver}."
        )
    message = _with_attribution(english, [], day) if day is not None else english
    return _ok(message, notify=True, commands=_commands(deps))


def _remove(deps: Deps, args: dict[str, Any]) -> str:
    unknown = _unknown(set(args), REMOVE_KEYS)
    if unknown:
        return _fail("bad_args", unknown, "Pass only action and watch_id. Nothing was changed.")
    watch_id = args.get("watch_id")
    if not isinstance(watch_id, str) or not WATCH_ID.fullmatch(watch_id):
        return _fail(
            "bad_args",
            "watch_id must be 32 lowercase hex characters. Nothing was requested.",
            "Use an id from list.",
        )
    status, state, path = _load_state(deps)
    if status == "corrupt":
        return _fail(
            "corrupt",
            "The watch state file is unreadable, so it was not overwritten and the job was not changed.",
            "Seen speech IDs were not changed.",
        )
    if status == "unavailable" or state is None or path is None:
        return _fail("storage", "The plugin data directory is not available, so nothing was removed.", "Nothing was requested.")
    row = next((item for item in state["watches"] if item.get("id") == watch_id), None)
    if row is None:
        return _fail("not_found", "That watch id is not saved. Nothing was requested.", "Use an id from list.")
    others = [item for item in state["watches"] if item.get("id") != watch_id]
    last = not others
    presence = _job_presence(deps, state.get("job_id"))
    if presence == "unreadable":
        return _fail(
            "cron",
            "The saved cron job could not be checked, so the watch was not removed.",
            "Nothing was changed.",
            commands=_commands(deps),
        )
    text = _approval_remove(row, state, presence, last)
    approved, reason = _approve(deps, "remove", text)
    if not approved:
        return _fail("approval", reason, "The watch was not removed and no job was changed.")
    presence = _job_presence(deps, state.get("job_id"))
    if presence == "unreadable":
        return _fail(
            "cron",
            "The saved cron job could not be checked, so the watch was not removed.",
            "Nothing was changed.",
            commands=_commands(deps),
        )
    job_note = ""
    if last:
        job_id = state.get("job_id")
        if presence == "live" and job_id:
            removed = _remove_job(deps, job_id)
            if removed is False:
                job_note = " The cron job was already gone."
                state["job_id"] = None
                state["hour"] = None
                state["deliver"] = None
            elif removed is not True:
                return _fail(
                    "cron",
                    "The cron job could not be removed, so this watch was kept.",
                    "Try again. The watch and its seen speech IDs are unchanged.",
                    commands=_commands(deps),
                )
            else:
                job_note = " The cron job was removed."
                state["job_id"] = None
                state["hour"] = None
                state["deliver"] = None
        elif isinstance(job_id, str) and job_id:
            job_note = " The cron job was already gone."
            state["job_id"] = None
            state["hour"] = None
            state["deliver"] = None
        else:
            job_note = " No cron job is stored."
            state["job_id"] = None
            state["hour"] = None
            state["deliver"] = None
    elif presence == "live":
        job_note = " The cron job stays."
    elif state.get("job_id"):
        job_note = " The cron job is already gone."
    else:
        job_note = " No cron job is stored."
    state["watches"] = others
    saved = _save_state(deps, path, state)
    if saved:
        return _fail("storage", "The watch could not be removed from the state file. " + saved, "The file was not replaced.")
    day = _today(deps)
    english = f"The watch was removed and its seen speech IDs were removed with it. Other watches were kept.{job_note}"
    message = _with_attribution(english, [], day) if day is not None else english
    return _ok(message, notify=True, commands=_commands(deps))


def _list(deps: Deps, args: dict[str, Any]) -> str:
    unknown = _unknown(set(args), LIST_KEYS)
    if unknown:
        return _fail("bad_args", unknown, "Call list with no other arguments.")
    status, state, _path = _load_state(deps)
    if status == "corrupt":
        return _fail(
            "corrupt",
            "The watch state file is unreadable, so it was not overwritten.",
            "Seen speech IDs were not changed.",
        )
    if status == "unavailable" or state is None:
        return _fail("storage", "The plugin data directory is not available.", "Nothing was read from disk.")
    public = []
    for row in state["watches"]:
        public.append(
            {
                "id": row.get("id"),
                "any": row.get("any") or "",
                "speaker": row.get("speaker") or "",
                "house": row.get("house") or "",
                "lookback_days": row.get("lookback_days"),
                "seen_count": len(row.get("seen") or []),
                "last_complete_count": row.get("last_complete_count"),
            }
        )
    stored = state.get("job_id")
    presence = _job_presence(deps, stored)
    if presence == "unreadable":
        return _fail(
            "cron",
            "The saved cron job could not be checked, so this list does not show it as running.",
            "Nothing was changed.",
            notify=False,
        )
    if presence == "live":
        message = "Saved watches. This list does not call the meeting API and does not change seen speech IDs."
        shown_id = stored
    elif isinstance(stored, str) and stored:
        message = (
            "The cron job is gone. The saved job id is not running. "
            "This list does not call the meeting API and does not change seen speech IDs."
        )
        shown_id = None
    else:
        message = (
            "No cron job is stored. "
            "This list does not call the meeting API and does not change seen speech IDs."
        )
        shown_id = None
    return _ok(
        message,
        notify=False,
        hour=state.get("hour"),
        deliver=state.get("deliver"),
        job_id=shown_id,
        watches=public,
        commands=_commands(deps),
    )


def _run(deps: Deps) -> str:
    judge = _judge(deps)
    if judge in {"host", "unreadable"}:
        return _fail(
            "cannot_watch",
            "This form cannot save seen speech IDs. Nothing was requested, and seen speech IDs were not changed.",
            "Set plugins.isolation to in_process. This is not a report that there is nothing new.",
            notify=True,
        )
    status, state, path = _load_state(deps)
    if status == "corrupt":
        return _fail(
            "corrupt",
            "The watch state file is unreadable, so it was not overwritten and seen speech IDs were not changed.",
            "Move the state file aside only if you mean to drop the watches.",
        )
    if status == "unavailable" or state is None or path is None:
        return _fail("storage", "The plugin data directory is not available, so the watch did not run.", "Nothing was requested.")
    if not state["watches"]:
        return _ok("No watches are saved.", notify=False, commands=_commands(deps))
    presence = _job_presence(deps, state.get("job_id"))
    if presence == "unreadable":
        return _fail(
            "cron",
            "The saved cron job could not be checked, so the meeting list was not requested and seen speech IDs were not changed.",
            "Nothing was requested.",
            commands=_commands(deps),
        )
    if presence != "live":
        stored = state.get("job_id")
        if isinstance(stored, str) and stored:
            note = "The cron job is gone."
        else:
            note = "No cron job is stored."
        return _ok(
            f"{note} Seen speech IDs were not changed. The meeting list was not requested.",
            notify=True,
            commands=_commands(deps),
        )
    day = _today(deps)
    if day is None:
        return _fail(
            "timezone",
            "Asia/Tokyo could not be resolved, so the meeting list was not requested.",
            "Seen speech IDs were not changed.",
        )
    advance = judge == "cron"
    messages: list[str] = []
    notify = False
    changed = False
    for index, row in enumerate(state["watches"]):
        if index:
            remain = client.gap_remaining()
            if remain is None:
                text, row_notify, row_changed = _run_failure(
                    row, "busy", "Another request is still in flight, so this watch was not sent.", deps, advance,
                )
            elif remain > 0:
                deps.sleep(remain)
                text, row_notify, row_changed = _run_one(deps, row, day, advance)
            else:
                text, row_notify, row_changed = _run_one(deps, row, day, advance)
        else:
            text, row_notify, row_changed = _run_one(deps, row, day, advance)
        messages.append(text)
        notify = notify or row_notify
        changed = changed or row_changed
    if changed and advance:
        saved = _save_state(deps, path, state)
        if saved:
            return _fail(
                "storage",
                "Seen speech IDs could not be saved, so the state file was left unchanged. " + " ".join(messages),
                "The file was not replaced.",
            )
    return _ok(" ".join(messages), notify=notify, commands=_commands(deps))


def _run_one(deps: Deps, row: dict[str, Any], day: date, advance: bool) -> tuple[str, bool, bool]:
    start, end = _window(day, int(row.get("lookback_days") or LOOKBACK_DEFAULT))
    pairs = _watch_pairs(
        {
            "any": str(row.get("any") or ""),
            "speaker": str(row.get("speaker") or ""),
            "house": str(row.get("house") or ""),
        },
        start,
        end,
    )
    try:
        page = client.fetch(pairs, deps.transport)
    except client.ClientError as exc:
        return _run_failure(row, exc.code, exc.message, deps, advance)
    urls = _meeting_urls(page)
    if not page["can_baseline"]:
        text = _with_attribution(
            "The result does not fit in one response or a speech row has no speechID. "
            "Seen speech IDs were not changed.",
            urls,
            day,
        )
        return text, True, False
    previous = row.get("last_complete_count")
    if page["zero"] and isinstance(previous, int) and previous > 0:
        text = _with_attribution(
            (
                f"0 meetings. The previous complete result had {previous} meetings. "
                "Seen speech IDs were kept. This is not an all-clear."
            ),
            [],
            day,
        )
        recovered, recovery_note = (False, "")
        changed = False
        if advance:
            # Remember this zero so the next zero is not another drop warning.
            # Seen speech IDs stay. A manual run does not write this.
            row["last_complete_count"] = 0
            changed = True
            recovered, recovery_note = _recover(row, deps)
            changed = changed or recovered
        if recovery_note:
            text = recovery_note + " " + text
        return text, True, changed
    seen = list(row.get("seen") or [])
    fresh = [speech_id for speech_id in page["speech_ids"] if speech_id not in seen]
    changed = False
    recovery_note = ""
    if advance:
        new_seen = seen + [speech_id for speech_id in fresh]
        if new_seen != seen or row.get("last_complete_count") != page["number_of_records"]:
            row["seen"] = new_seen
            row["last_complete_count"] = page["number_of_records"]
            changed = True
        recovered, recovery_note = _recover(row, deps)
        changed = changed or recovered
    if page["zero"]:
        english = "0 meetings. Names are copied as the API sent them."
    elif fresh:
        new_meetings, urls = _meetings_with_new_speeches(page, fresh)
        record_word = "speech record" if len(fresh) == 1 else "speech records"
        meeting_word = "meeting" if new_meetings == 1 else "meetings"
        english = (
            f"{len(fresh)} new {record_word} in {new_meetings} {meeting_word}. "
            "Names are copied as the API sent them. Speech text is not included. "
            + ("Seen speech IDs were updated." if advance else "Seen speech IDs were not updated because this call is not the cron job.")
        )
    else:
        english = (
            "No new speech records. "
            + ("Seen speech IDs are unchanged." if advance else "Seen speech IDs were not updated because this call is not the cron job.")
        )
    if recovery_note:
        english = recovery_note + " " + english
    notify = bool(fresh) or bool(recovery_note) or (page["zero"] and isinstance(previous, int) and previous > 0)
    if page["zero"] and not (isinstance(previous, int) and previous > 0) and not fresh and not recovery_note:
        notify = False
    if not fresh and not recovery_note and not page["zero"]:
        notify = False
    text = _with_attribution(english, urls, day)
    if fresh:
        text += "\n" + "\n".join(fresh)
    return text, notify, changed


def _run_failure(row: dict[str, Any], code: str, message: str, deps: Deps, advance: bool) -> tuple[str, bool, bool]:
    if not advance:
        return "The meeting list could not be checked. " + message, True, False
    now = deps.now() if deps.now is not None else __import__("time").time()
    failure = row.get("failure") if isinstance(row.get("failure"), dict) else None
    notify = True
    if failure and failure.get("code") == code:
        notified_at = failure.get("notified_at") or 0
        notify = (now - float(notified_at)) >= NOTIFY_GAP
    row["failure"] = {
        "code": code,
        "at": now,
        "notified_at": now if notify else (failure or {}).get("notified_at"),
    }
    prefix = "The meeting list could not be checked. " if notify else "The same failure is still present and was already reported. "
    return prefix + message, notify, True


def _recover(row: dict[str, Any], deps: Deps) -> tuple[bool, str]:
    if not row.get("failure"):
        return False, ""
    row["failure"] = None
    return True, "The earlier failure cleared on this check."


def _create_job(deps: Deps, hour: int, deliver: str) -> tuple[str | None, str | None]:
    try:
        jobs = _cron_module(deps)
        create = getattr(jobs, "create_job")
    except Exception:
        return None, "cron.jobs could not be loaded, so no job was created."
    schedule = f"0 {hour} * * *"
    kwargs = {
        "prompt": CRON_PROMPT,
        "schedule": schedule,
        "name": "jp-kokkai",
        "deliver": deliver,
        "enabled_toolsets": [TOOLSET],
    }
    try:
        accepted = {key: value for key, value in kwargs.items() if key in inspect.signature(create).parameters}
        if "interpreter" in accepted or "no_agent" in accepted:
            return None, "The cron job was not created because the call would have passed interpreter or no_agent."
        created = create(**accepted)
    except Exception:
        return None, "The cron job could not be created, so the watch was not saved."
    job_id = created.get("id") if isinstance(created, dict) else None
    if not job_id:
        return None, "The cron job did not return an id, so the watch was not saved."
    return str(job_id), None


def _update_job(deps: Deps, job_id: str, hour: int, deliver: str) -> bool:
    try:
        jobs = _cron_module(deps)
        update = getattr(jobs, "update_job")
        result = update(job_id, {"schedule": f"0 {hour} * * *", "deliver": deliver})
    except Exception:
        return False
    return result is not None


def _remove_job(deps: Deps, job_id: str) -> bool | None:
    try:
        jobs = _cron_module(deps)
        remove = getattr(jobs, "remove_job")
        return bool(remove(job_id))
    except Exception:
        return None
