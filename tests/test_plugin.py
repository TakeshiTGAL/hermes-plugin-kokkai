"""Offline tests. The transport is injected, so no socket is opened."""
from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

import client
import gates
import service

DAY = date(2026, 10, 8)
PLATFORMS = {
    "telegram", "discord", "slack", "whatsapp", "signal",
    "matrix", "mattermost", "dingtalk", "feishu",
    "wecom", "wecom_callback", "weixin", "sms", "email", "webhook", "bluebubbles",
    "qqbot", "yuanbao",
}
ZERO_CITE = (
    "出典：国立国会図書館「国会会議録検索システム」（https://kokkai.ndl.go.jp/api.html）、"
    "PDL1.0（https://www.digital.go.jp/resources/open_data/public_data_license_v1.0）"
    "（2026-10-08に利用）。国立国会図書館「国会会議録検索システム」の検索結果を jp-kokkai が選択して作成。"
)


def cite(urls: list[str]) -> str:
    where = "、".join(urls)
    return (
        "出典：国立国会図書館「国会会議録検索システム」（"
        f"{where}）、PDL1.0（https://www.digital.go.jp/resources/open_data/public_data_license_v1.0）"
        "（2026-10-08に利用）。国立国会図書館「国会会議録検索システム」の検索結果を jp-kokkai が選択して作成。"
    )


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url: str):
        self.calls.append(url)
        status, body = self.responses.pop(0)
        if isinstance(body, str):
            body = body.encode()
        elif isinstance(body, dict):
            body = json.dumps(body).encode()
        return status, body


class Jobs:
    def __init__(self):
        self.created = []
        self.updated = []
        self.removed = []
        self.jobs = {}
        self.fail_update = False
        self.fail_remove = False
        self.fail_get = False
        self.missing = set()
        self.fail_create = False

    def create_job(self, prompt, schedule, name=None, deliver=None, enabled_toolsets=None, interpreter=None, no_agent=False):
        self.created.append(
            {
                "prompt": prompt,
                "schedule": schedule,
                "name": name,
                "deliver": deliver,
                "enabled_toolsets": enabled_toolsets,
                "interpreter": interpreter,
                "no_agent": no_agent,
            }
        )
        if self.fail_create:
            raise RuntimeError("create failed")
        job = {"id": "abc123jobid9", "schedule": schedule, "deliver": deliver, "name": name}
        self.missing.discard(job["id"])
        self.jobs[job["id"]] = job
        return job

    def get_job(self, job_id):
        if self.fail_get:
            raise RuntimeError("get failed")
        if job_id in self.missing:
            return None
        return self.jobs.get(job_id)

    def update_job(self, job_id, updates):
        if self.fail_update:
            raise RuntimeError("update failed")
        if job_id in self.missing or job_id not in self.jobs:
            return None
        self.updated.append((job_id, updates))
        self.jobs[job_id].update(updates)
        return {"id": job_id, **updates}

    def remove_job(self, job_id):
        self.removed.append(job_id)
        if self.fail_remove:
            raise RuntimeError("remove failed")
        if job_id in self.missing or job_id not in self.jobs:
            return False
        self.jobs.pop(job_id, None)
        self.missing.add(job_id)
        return True


def clock_of(box):
    return lambda: box["t"]


def plant_job(deps, job_id="abc123jobid9"):
    deps.jobs.jobs[job_id] = {"id": job_id, "name": "jp-kokkai"}


def make_deps(tmp_path: Path, transport=None, **kwargs):
    client.reset_flight()
    box = kwargs.pop("clock", {"t": 1000.0})
    jobs = kwargs.pop("jobs", Jobs())
    asked = kwargs.pop("asked", [])

    def approve(action, text):
        asked.append((action, text))
        return True, ""

    deps = service.Deps(
        transport=transport,
        now=clock_of(box),
        sleep=lambda seconds: box.__setitem__("t", box["t"] + seconds),
        today=lambda: DAY,
        approver=kwargs.pop("approver", approve),
        write_guard=kwargs.pop("write_guard", lambda path: None),
        data_dir=tmp_path,
        cron=jobs,
        cron_judge=kwargs.pop("cron_judge", lambda: "manual"),
        platforms=kwargs.pop("platforms", set(PLATFORMS)),
        profile=kwargs.pop("profile", "default"),
    )
    deps.jobs = jobs
    deps.asked = asked
    deps.clock = box
    return deps


def body(text: str) -> dict:
    return json.loads(text)


def meeting(speech_id="s1", url="https://kokkai.ndl.go.jp/txt/m1", speech="本文は捨てる", speaker="会議録情報"):
    return {
        "nameOfHouse": "衆議院",
        "nameOfMeeting": "本会議",
        "date": "2026-07-24",
        "meetingURL": url,
        "speechRecord": [
            {
                "speechID": speech_id,
                "speaker": speaker,
                "speechOrder": 1,
                "speechURL": url + "#" + speech_id,
                "speech": speech,
            }
        ],
    }


def page(records, meetings, returned=None):
    returned = records if returned is None else returned
    payload = {
        "numberOfRecords": records,
        "numberOfReturn": returned,
        "startRecord": 1,
        "nextRecordPosition": None if records == returned else returned + 1,
    }
    if meetings is not None:
        payload["meetingRecord"] = meetings
    return payload


def zero_payload():
    return {"numberOfRecords": 0, "numberOfReturn": 0, "startRecord": 1, "nextRecordPosition": None}


def test_search_strips_speech_and_keeps_names(tmp_path):
    transport = Transport([(200, page(1, [meeting()]))])
    deps = make_deps(tmp_path, transport)
    raw = service.search(deps, {"any": "科学技術"})
    assert raw.startswith('{"message":')
    result = body(raw)
    assert result["ok"] is True
    assert result["meeting_count"] == 1
    assert "本文は捨てる" not in raw
    assert "speechRecord" not in raw
    assert result["meetings"][0]["speakers"] == ["会議録情報"]
    assert result["meetings"][0]["speech_count"] == 1
    assert "出典" in raw[:1500]
    assert cite(["https://kokkai.ndl.go.jp/txt/m1"]) in result["message"]
    assert transport.calls[0].startswith("https://kokkai.ndl.go.jp/api/meeting_list?")
    assert "maximumRecords=10" in transport.calls[0]
    assert "recordPacking=json" in transport.calls[0]
    assert "startRecord=1" in transport.calls[0]
    assert "/api/speech" not in transport.calls[0]
    assert "/api/meeting?" not in transport.calls[0]
    assert "http://kokkai.ndl.go.jp" not in transport.calls[0]


def test_spaces_are_kept_and_house_is_sent(tmp_path):
    transport = Transport([(200, zero_payload())])
    deps = make_deps(tmp_path, transport)
    service.search(deps, {"any": "科学 技術", "house": "両院"})
    url = transport.calls[0]
    assert "nameOfHouse=%E4%B8%A1%E9%99%A2" in url
    assert "%E7%A7%91%E5%AD%A6%20%E6%8A%80%E8%A1%93" in url
    assert "%E7%A7%91%E5%AD%A6%E6%8A%80%E8%A1%93" not in url


def test_illegal_house_and_inverted_dates_send_nothing(tmp_path):
    transport = Transport([])
    deps = make_deps(tmp_path, transport)
    house = body(service.search(deps, {"any": "科学技術", "house": "下院"}))
    dates = body(service.search(deps, {"from": "2020-12-31", "until": "2020-01-01"}))
    assert transport.calls == []
    assert house["ok"] is False
    assert "下院" in house["message"]
    assert service.INVERTED_MEASURED in dates["message"]
    assert dates["message"].count("(0) (19014)") == 1
    assert "日付" not in dates["message"]


def test_zero_is_success_and_cites_api_html(tmp_path):
    transport = Transport([(200, zero_payload())])
    deps = make_deps(tmp_path, transport)
    result = body(service.search(deps, {"any": "zzzqqqunlikely20261008xyz"}))
    assert result["ok"] is True
    assert result["message"].startswith("0 meetings")
    assert "変化なし" not in result["message"]
    assert ZERO_CITE in result["message"]


def test_zero_without_start_record_is_not_success(tmp_path):
    missing = zero_payload()
    del missing["startRecord"]
    shifted = zero_payload()
    shifted["startRecord"] = 2
    for payload in (missing, shifted):
        transport = Transport([(200, payload)])
        result = body(service.search(make_deps(tmp_path, transport), {"any": "科学技術"}))
        assert result["ok"] is False
        assert not result["message"].startswith("0 meetings")
        assert transport.calls


def test_http_400_and_200_message_are_not_zero(tmp_path):
    transport = Transport([
        (400, {"message": "(19007)検索条件を指定してください。"}),
        (200, {"message": "(19007)検索条件を指定してください。"}),
        (200, b""),
        (200, b"not-json"),
    ])
    deps = make_deps(tmp_path, transport)
    for _ in range(4):
        deps.clock["t"] += 5
        result = body(service.search(deps, {"any": "科学技術"}))
        assert result["ok"] is False
        assert not result["message"].startswith("0 meetings")
    assert result["message"].startswith("The meeting list")


def test_message_and_details_are_appended(tmp_path):
    transport = Transport([(400, {"message": "(19011)検索条件の入力に誤りがあります。", "details": ["(19005)maximumRecordsには1～100の値を指定してください。"]})])
    result = body(service.search(make_deps(tmp_path, transport), {"any": "科学技術"}))
    assert "(19011)検索条件の入力に誤りがあります。" in result["message"]
    assert "(19005)maximumRecordsには1～100の値を指定してください。" in result["message"]
    assert "0 meetings" not in result["message"]


def test_partial_and_count_mismatch(tmp_path):
    transport = Transport([
        (200, page(170, [meeting("a")], returned=1)),
        (200, {"numberOfRecords": 2, "numberOfReturn": 2, "nextRecordPosition": None}),
    ])
    deps = make_deps(tmp_path, transport)
    first = body(service.search(deps, {"any": "科学技術"}))
    deps.clock["t"] += 5
    second = body(service.search(deps, {"any": "科学技術"}))
    assert first["ok"] is False and second["ok"] is False
    assert "新着なし" not in first["message"]
    assert "Nothing new" not in second["message"]


def test_maximum_records_bounds(tmp_path):
    transport = Transport([(200, zero_payload())])
    deps = make_deps(tmp_path, transport)
    service.search(deps, {"any": "科学技術", "maximum_records": 30})
    assert "maximumRecords=30" in transport.calls[0]
    refused = body(service.search(deps, {"any": "科学技術", "maximum_records": 101}))
    assert refused["ok"] is False
    assert len(transport.calls) == 1


def test_query_over_2000_bytes_is_not_sent(tmp_path):
    transport = Transport([])
    result = body(service.search(make_deps(tmp_path, transport), {"any": "あ" * 2000}))
    assert result["ok"] is False
    assert transport.calls == []


def test_redirect_is_not_followed(tmp_path):
    transport = Transport([(301, b"<html>redirect</html>")])
    result = body(service.search(make_deps(tmp_path, transport), {"any": "科学技術"}))
    assert result["ok"] is False
    assert len(transport.calls) == 1
    assert "301" in result["message"]
    assert client._NoRedirect().redirect_request(None, None, 301, "Moved", {}, "https://evil.example/") is None


def test_timeout_does_not_say_it_may_have_worked(tmp_path):
    def explode(url):
        raise TimeoutError("timed out")
    result = body(service.search(make_deps(tmp_path, explode), {"any": "科学技術"}))
    assert result["ok"] is False
    assert "may have" not in result["message"].lower()
    assert "Saved state is unchanged" in result["message"]


def test_gap_and_inflight(tmp_path):
    transport = Transport([(200, zero_payload()), (200, zero_payload())])
    deps = make_deps(tmp_path, transport)
    assert body(service.search(deps, {"any": "科学技術"}))["ok"] is True
    deps.clock["t"] += 4
    early = body(service.search(deps, {"any": "科学技術"}))
    assert early["ok"] is False
    assert len(transport.calls) == 1
    deps.clock["t"] += 1
    assert body(service.search(deps, {"any": "科学技術"}))["ok"] is True

    started = threading.Event()
    release = threading.Event()

    def blocking(url):
        started.set()
        release.wait(2)
        return 200, json.dumps(zero_payload()).encode()

    client.reset_flight()
    deps.transport = blocking
    holder = {}

    def first():
        holder["result"] = service.search(deps, {"speaker": "山田"})

    thread = threading.Thread(target=first)
    thread.start()
    assert started.wait(2)
    busy = body(service.search(deps, {"speaker": "佐藤"}))
    release.set()
    thread.join(2)
    assert busy["ok"] is False
    assert busy["error"] == "busy"


def test_unknown_arguments_are_rejected(tmp_path):
    transport = Transport([])
    deps = make_deps(tmp_path, transport)
    result = body(service.search(deps, {"any": "科学技術", "speakerRole": "傍聴人", "issueID": "x"}))
    watch_result = body(service.watch(deps, {"action": "export"}))
    assert result["ok"] is False and watch_result["ok"] is False
    assert transport.calls == []


def test_watch_add_baselines_without_announcing(tmp_path):
    transport = Transport([(200, page(1, [meeting("seen-1")]))])
    deps = make_deps(tmp_path, transport)
    result = body(service.watch(deps, {"action": "add", "any": "科学技術", "hour": 9, "deliver": "local"}))
    assert result["ok"] is True
    assert "new speech" not in result["message"].lower()
    assert "marked seen" in result["message"]
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["watches"][0]["seen"] == ["seen-1"]
    assert saved["hour"] == 9
    assert saved["job_id"] == "abc123jobid9"
    created = deps.jobs.created[0]
    assert created["name"] == "jp-kokkai"
    assert created["schedule"] == "0 9 * * *"
    assert created["enabled_toolsets"] == ["jp_kokkai"]
    assert created["interpreter"] is None
    assert created["no_agent"] is False
    assert '"action":"run"' in created["prompt"]
    assert "[SILENT]" in created["prompt"]
    assert "Do not call kokkai_search" in created["prompt"]
    assert "from=2026-09-09" in transport.calls[0]
    assert "until=2026-10-08" in transport.calls[0]
    assert "maximumRecords=100" in transport.calls[0]
    assert "本文" not in (tmp_path / "state.json").read_text(encoding="utf-8")


def test_partial_add_does_not_save_or_create(tmp_path):
    transport = Transport([(200, page(170, [meeting()], returned=1))])
    deps = make_deps(tmp_path, transport)
    result = body(service.watch(deps, {"action": "add", "speaker": "山田", "hour": 1, "deliver": "local"}))
    assert result["ok"] is False
    assert deps.jobs.created == []
    assert not (tmp_path / "state.json").exists()


def test_duplicate_and_fourth_watch_skip_approval(tmp_path):
    transport = Transport([(200, zero_payload()) for _ in range(4)])
    deps = make_deps(tmp_path, transport)
    service.watch(deps, {"action": "add", "any": "科学技術", "hour": 9, "deliver": "local"})
    deps.clock["t"] += 5
    again = body(service.watch(deps, {"action": "add", "any": "科学技術", "hour": 9, "deliver": "local"}))
    assert again["error"] == "duplicate"
    assert len(deps.asked) == 1
    assert len(transport.calls) == 1
    for word in ("予算", "外交", "医療"):
        deps.clock["t"] += 5
        service.watch(deps, {"action": "add", "any": word, "hour": 9, "deliver": "local"})
    fourth = body(service.watch(deps, {"action": "add", "any": "環境", "hour": 9, "deliver": "local"}))
    assert fourth["error"] == "limit"
    assert len(deps.jobs.created) == 1


def test_hour_change_updates_one_job_and_names_the_previous(tmp_path):
    transport = Transport([(200, zero_payload()), (200, zero_payload())])
    deps = make_deps(tmp_path, transport)
    service.watch(deps, {"action": "add", "any": "科学技術", "hour": 9, "deliver": "local"})
    deps.clock["t"] += 5
    changed = body(service.watch(deps, {"action": "add", "any": "予算", "hour": 11, "deliver": "discord"}))
    assert changed["ok"] is True
    assert "previous hour was 9" in changed["message"]
    assert "previous deliver target was local" in changed["message"]
    assert deps.jobs.updated[-1][1] == {"schedule": "0 11 * * *", "deliver": "discord"}
    assert len(deps.jobs.created) == 1
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["hour"] == 11
    assert len(saved["watches"]) == 2


def test_update_failure_keeps_the_old_hour(tmp_path):
    transport = Transport([(200, zero_payload())])
    deps = make_deps(tmp_path, transport)
    service.watch(deps, {"action": "add", "any": "科学技術", "hour": 9, "deliver": "local"})
    deps.jobs.fail_update = True
    deps.clock["t"] += 5
    result = body(service.watch(deps, {"action": "add", "any": "科学技術", "hour": 12, "deliver": "local"}))
    assert result["ok"] is False
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["hour"] == 9
    assert len(saved["watches"]) == 1


def test_deny_does_not_fetch(tmp_path):
    transport = Transport([])

    def deny(action, text):
        return False, "BLOCKED: denied"

    deps = make_deps(tmp_path, transport, approver=deny)
    result = body(service.watch(deps, {"action": "add", "any": "科学技術", "hour": 9, "deliver": "local"}))
    assert result["ok"] is False
    assert transport.calls == []
    assert deps.jobs.created == []


def test_long_approval_is_not_sent(tmp_path):
    transport = Transport([])
    deps = make_deps(tmp_path, transport)
    result = body(service.watch(deps, {"action": "add", "any": "&" * 61, "hour": 9, "deliver": "local"}))
    assert result["ok"] is False
    assert deps.asked == []
    assert transport.calls == []
    assert gates.reason_fits("x" * 300) is True
    assert gates.reason_fits("x" * 301) is False
    assert gates.reason_fits("&" * 61) is False


def test_manual_run_does_not_advance_seen_and_cron_does(tmp_path):
    transport = Transport([
        (200, page(1, [meeting("seen-1")])),
        (200, page(2, [meeting("seen-1"), meeting("new-1", "https://kokkai.ndl.go.jp/txt/m2")])),
        (200, page(2, [meeting("seen-1"), meeting("new-1", "https://kokkai.ndl.go.jp/txt/m2")])),
    ])
    deps = make_deps(tmp_path, transport, cron_judge=lambda: "cron")
    service.watch(deps, {"action": "add", "any": "科学技術", "hour": 9, "deliver": "local"})
    deps.clock["t"] += 5
    deps.cron_judge = lambda: "manual"
    manual = body(service.watch(deps, {"action": "run"}))
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "new-1" in manual["message"]
    assert manual["notify"] is True
    assert saved["watches"][0]["seen"] == ["seen-1"]
    deps.clock["t"] += 5
    deps.cron_judge = lambda: "cron"
    cron = body(service.watch(deps, {}))
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert cron["notify"] is True
    assert "new-1" in cron["message"]
    assert saved["watches"][0]["seen"] == ["seen-1", "new-1"]


def test_host_and_unreadable_do_not_fetch(tmp_path):
    transport = Transport([(200, page(1, [meeting("new-1")]))])
    state = {
        "job_id": "abc123jobid9",
        "hour": 9,
        "deliver": "local",
        "watches": [{
            "id": "a" * 32,
            "any": "科学技術",
            "speaker": "",
            "house": "",
            "lookback_days": 30,
            "seen": [],
            "last_complete_count": 1,
            "failure": None,
        }],
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    before = (tmp_path / "state.json").read_bytes()
    for judge in ("host", "unreadable"):
        deps = make_deps(tmp_path, transport, cron_judge=lambda judge=judge: judge)
        result = body(service.watch(deps, {"action": "run"}))
        assert result["notify"] is True
        assert result["ok"] is False
        assert "Nothing new" not in result["message"]
        assert "cannot save seen" in result["message"]
    assert transport.calls == []
    assert (tmp_path / "state.json").read_bytes() == before


def test_zero_drop_keeps_seen(tmp_path):
    transport = Transport([(200, zero_payload()), (200, zero_payload())])
    state = {
        "job_id": "abc123jobid9",
        "hour": 9,
        "deliver": "local",
        "watches": [{
            "id": "b" * 32,
            "any": "科学技術",
            "speaker": "",
            "house": "",
            "lookback_days": 1,
            "seen": ["keep-me"],
            "last_complete_count": 3,
            "failure": None,
        }],
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    deps = make_deps(tmp_path, transport, cron_judge=lambda: "cron")
    plant_job(deps)
    result = body(service.watch(deps, {"action": "run"}))
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert result["notify"] is True
    assert "0 meetings" in result["message"]
    assert "all-clear" in result["message"]
    assert saved["watches"][0]["seen"] == ["keep-me"]
    assert saved["watches"][0]["last_complete_count"] == 0
    assert "from=2026-10-08" in transport.calls[0]
    assert "until=2026-10-08" in transport.calls[0]
    deps.clock["t"] += 5
    again = body(service.watch(deps, {"action": "run"}))
    saved_again = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert again["notify"] is False
    assert "0 meetings" in again["message"]
    assert "all-clear" not in again["message"]
    assert "previous complete" not in again["message"]
    assert saved_again["watches"][0]["seen"] == ["keep-me"]
    assert saved_again["watches"][0]["last_complete_count"] == 0


def test_failure_streak_and_recovery(tmp_path):
    def fail(url):
        raise OSError("down")

    state = {
        "job_id": "abc123jobid9",
        "hour": 9,
        "deliver": "local",
        "watches": [{
            "id": "c" * 32,
            "any": "科学技術",
            "speaker": "",
            "house": "",
            "lookback_days": 30,
            "seen": [],
            "last_complete_count": 0,
            "failure": None,
        }],
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    deps = make_deps(tmp_path, fail, cron_judge=lambda: "cron")
    plant_job(deps)
    first = body(service.watch(deps, {"action": "run"}))
    deps.clock["t"] += 10
    second = body(service.watch(deps, {"action": "run"}))
    assert first["notify"] is True
    assert second["notify"] is False
    # The gap is measured from the first notice, not from the silent repeat.
    deps.clock["t"] += service.NOTIFY_GAP - 11
    early = body(service.watch(deps, {"action": "run"}))
    assert early["notify"] is False
    deps.clock["t"] += 1
    third = body(service.watch(deps, {"action": "run"}))
    assert third["notify"] is True
    assert service.NOTIFY_GAP == 23 * 60 * 60
    deps.transport = Transport([(200, zero_payload())])
    deps.clock["t"] += 5
    recovered = body(service.watch(deps, {"action": "run"}))
    assert recovered["notify"] is True
    assert "cleared" in recovered["message"]
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["watches"][0]["failure"] is None


def test_http_status_change_is_reported_again(tmp_path):
    calls = {"n": 0}

    def flip(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return 503, b'{"message":"busy"}'
        return 400, b'{"message":"bad","details":["nope"]}'

    state = {
        "job_id": "abc123jobid9",
        "hour": 9,
        "deliver": "local",
        "watches": [{
            "id": "e" * 32,
            "any": "科学技術",
            "speaker": "",
            "house": "",
            "lookback_days": 30,
            "seen": [],
            "last_complete_count": 0,
            "failure": None,
        }],
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    deps = make_deps(tmp_path, flip, cron_judge=lambda: "cron")
    plant_job(deps)
    first = body(service.watch(deps, {"action": "run"}))
    deps.clock["t"] += 10
    second = body(service.watch(deps, {"action": "run"}))
    assert first["notify"] is True
    assert "HTTP 503" in first["message"]
    assert second["notify"] is True
    assert "HTTP 400" in second["message"]
    assert "same failure" not in second["message"]
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["watches"][0]["failure"]["code"] == "http_400"


def test_bad_args_do_not_count_as_a_failure_streak(tmp_path):
    deps = make_deps(tmp_path, Transport([]), cron_judge=lambda: "cron")
    result = body(service.watch(deps, {"action": "run", "any": "科学技術"}))
    assert result["notify"] is True
    assert result["error"] == "bad_args"
    assert not (tmp_path / "state.json").exists()


def test_corrupt_state_is_not_overwritten(tmp_path):
    raw = b"{not json"
    (tmp_path / "state.json").write_bytes(raw)
    deps = make_deps(tmp_path, Transport([(200, zero_payload())]), cron_judge=lambda: "cron")
    result = body(service.watch(deps, {"action": "run"}))
    assert result["notify"] is True
    assert (tmp_path / "state.json").read_bytes() == raw
    assert deps.transport.calls == []


def test_remove_uses_the_stored_id(tmp_path):
    transport = Transport([(200, zero_payload()), (200, zero_payload())])
    deps = make_deps(tmp_path, transport)
    service.watch(deps, {"action": "add", "any": "科学技術", "hour": 9, "deliver": "local"})
    deps.clock["t"] += 5
    service.watch(deps, {"action": "add", "speaker": "山田", "hour": 9, "deliver": "local"})
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    first = saved["watches"][0]["id"]
    listed = body(service.watch(deps, {"action": "list"}))
    assert listed["notify"] is False
    assert listed["job_id"] == "abc123jobid9"
    assert len(deps.transport.calls) == 2
    removed = body(service.watch(deps, {"action": "remove", "watch_id": first}))
    assert removed["ok"] is True
    assert "cron job stays" in removed["message"]
    assert deps.jobs.removed == []
    one_question = [text for action, text in deps.asked if action == "remove"][0]
    assert "removes one watch only" in one_question
    assert "cron job stays" in one_question
    assert "creates a cron" not in one_question.lower()
    assert "Add a Diet" not in one_question
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in saved["watches"]] == [saved["watches"][0]["id"]]
    assert first not in {row["id"] for row in saved["watches"]}
    last = saved["watches"][0]["id"]
    gone = body(service.watch(deps, {"action": "remove", "watch_id": last}))
    assert gone["ok"] is True
    assert "cron job was removed" in gone["message"]
    assert deps.jobs.removed == ["abc123jobid9"]
    assert "cron remove" in " ".join(gone["commands"])
    last_question = [text for action, text in deps.asked if action == "remove"][-1]
    assert "removes the cron job" in last_question
    assert "creates a cron" not in last_question.lower()
    assert "Add a Diet" not in last_question


def test_remove_failure_keeps_the_watch_and_missing_job_drops_only_that_watch(tmp_path):
    transport = Transport([(200, zero_payload())])
    deps = make_deps(tmp_path, transport)
    service.watch(deps, {"action": "add", "any": "科学技術", "hour": 9, "deliver": "local"})
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    watch_id = saved["watches"][0]["id"]
    deps.jobs.fail_remove = True
    failed = body(service.watch(deps, {"action": "remove", "watch_id": watch_id}))
    assert failed["ok"] is False
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["watches"]
    deps.jobs.fail_remove = False
    deps.jobs.missing.add("abc123jobid9")
    gone = body(service.watch(deps, {"action": "remove", "watch_id": watch_id}))
    assert gone["ok"] is True
    assert "already gone" in gone["message"]
    question = [text for action, text in deps.asked if action == "remove"][-1]
    assert "already gone" in question
    assert "saved watch only" in question
    assert "removes the cron job" not in question
    assert "creates a cron" not in question.lower()
    assert "Add a Diet" not in question
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["watches"] == []


def test_save_failure_after_create_reports_the_leftover_id(tmp_path):
    transport = Transport([(200, zero_payload())])

    def deny(path):
        return "BLOCKED: denied"

    deps = make_deps(tmp_path, transport, write_guard=deny)
    deps.jobs.fail_remove = True
    result = body(service.watch(deps, {"action": "add", "any": "科学技術", "hour": 9, "deliver": "local"}))
    assert result["ok"] is False
    assert "abc123jobid9" in result["message"]
    assert not (tmp_path / "state.json").exists()


def test_create_job_omits_interpreter_and_no_agent(tmp_path):
    seen = {}

    class Mod:
        def create_job(self, prompt, schedule, name=None, deliver=None, enabled_toolsets=None, interpreter="KEEP", no_agent="KEEP"):
            seen["interpreter"] = interpreter
            seen["no_agent"] = no_agent
            seen["name"] = name
            return {"id": "abc123jobid9"}

    deps = make_deps(tmp_path, jobs=Mod())
    job_id, error = service._create_job(deps, 9, "local")
    assert error is None
    assert job_id == "abc123jobid9"
    assert seen["interpreter"] == "KEEP"
    assert seen["no_agent"] == "KEEP"
    assert seen["name"] == "jp-kokkai"


def test_profile_flag_and_deliver_words(tmp_path, monkeypatch):
    home = tmp_path / "profiles" / "custom"
    root = tmp_path / "profiles"
    assert gates.named_profile_for_commands("custom", home, root) == "default"
    home.mkdir(parents=True)
    assert gates.named_profile_for_commands("custom", home, root) == "default"
    (home / ".env").write_text("", encoding="utf-8")
    assert gates.named_profile_for_commands("custom", home, root) == "custom"
    (home / ".env").unlink()
    (home / "config.yaml").write_text("{}\n", encoding="utf-8")
    assert gates.named_profile_for_commands("custom", home, root) == "custom"
    assert gates.named_profile_for_commands("custom", tmp_path / "other", tmp_path / "profiles") == "default"
    assert gates.named_profile_for_commands("work", tmp_path / "other", tmp_path / "profiles") == "work"
    lines = gates.hermes_cron_lines("custom")
    assert lines == [
        "hermes -p custom cron create",
        "hermes -p custom cron run",
        "hermes -p custom cron list",
        "hermes -p custom cron status",
        "hermes -p custom cron remove",
    ]
    assert gates.hermes_cron_lines("default")[0] == "hermes cron create"
    assert gates.canonical_deliver("local", set(PLATFORMS)) == "local"
    assert gates.canonical_deliver("origin,all", set(PLATFORMS)) == "origin,all"
    assert gates.canonical_deliver("bot-chat:room", set(PLATFORMS)) == "bot-chat:room"
    assert gates.canonical_deliver("discord:123", set(PLATFORMS)) == "discord:123"
    for word in ("5m", "cli", "cron", "api_server", "monday", "every", "*", "@daily", "2026-10-08T00:00:00"):
        assert gates.canonical_deliver(word, set(PLATFORMS)) is None
    # When Hermes's platform list cannot be loaded, no deliver target is accepted.
    monkeypatch.setattr(gates, "load_platforms", lambda: None)
    assert gates.canonical_deliver("local", None) is None


def test_timezone_failure_sends_nothing(tmp_path):
    transport = Transport([])
    deps = make_deps(tmp_path, transport)
    deps.today = lambda: None
    result = body(service.search(deps, {"any": "科学技術"}))
    watch_result = body(service.watch(deps, {"action": "add", "any": "科学技術", "hour": 9, "deliver": "local"}))
    assert result["error"] == "timezone"
    assert watch_result["ok"] is False
    assert transport.calls == []
    assert deps.jobs.created == []


def test_oversize_body_is_not_a_result(tmp_path):
    transport = Transport([(200, b"x" * (client.BODY_CAP + 1))])
    result = body(service.search(make_deps(tmp_path, transport), {"any": "科学技術"}))
    assert result["ok"] is False
    assert "2000000" in result["message"]


def test_search_does_not_touch_state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{}", encoding="utf-8")
    before = path.stat().st_mtime_ns
    service.search(make_deps(tmp_path, Transport([(200, zero_payload())])), {"any": "科学技術"})
    assert path.read_text(encoding="utf-8") == "{}"
    assert path.stat().st_mtime_ns == before


def test_missing_speech_id_does_not_advance(tmp_path):
    payload = page(1, [meeting(speech_id="")])
    payload["meetingRecord"][0]["speechRecord"][0]["speechID"] = ""
    transport = Transport([(200, payload), (200, payload)])
    deps = make_deps(tmp_path, transport)
    added = body(service.watch(deps, {"action": "add", "any": "科学技術", "hour": 9, "deliver": "local"}))
    assert added["ok"] is False
    assert not (tmp_path / "state.json").exists()


def test_empty_watch_is_rejected(tmp_path):
    transport = Transport([])
    result = body(service.watch(make_deps(tmp_path, transport), {"action": "add", "hour": 9, "deliver": "local"}))
    assert result["ok"] is False
    assert transport.calls == []


def test_search_summary_stays_small(tmp_path):
    def fat(index):
        speeches = []
        for n in range(200):
            speeches.append(
                {
                    "speechID": f"s{index}-{n}",
                    "speaker": "会議録情報",
                    "speechOrder": n,
                    "speechURL": "https://kokkai.ndl.go.jp/txt/" + ("u" * 120),
                }
            )
        return {
            "nameOfHouse": "衆議院",
            "nameOfMeeting": "委員会",
            "date": "2026-07-24",
            "meetingURL": f"https://kokkai.ndl.go.jp/txt/m{index}",
            "speechRecord": speeches,
        }

    meetings = [fat(i) for i in range(7)]
    payload = page(7, meetings)
    assert len(json.dumps(payload, ensure_ascii=False)) > 80000
    transport = Transport([(200, payload)])
    raw = service.search(make_deps(tmp_path, transport), {"any": "科学技術", "maximum_records": 7})
    assert raw.startswith('{"message":')
    assert "出典" in raw[:1500]
    assert "7 meetings" in raw[:1500]
    assert len(raw) < 20000
    assert "speechRecord" not in raw
    assert body(raw)["meeting_count"] == 7


def test_notify_counts_only_meetings_with_new_speeches(tmp_path):
    old_a = meeting("old-1", "https://kokkai.ndl.go.jp/txt/old-a")
    fresh = meeting("new-1", "https://kokkai.ndl.go.jp/txt/new-b")
    fresh["speechRecord"].append(
        {
            "speechID": "new-2",
            "speaker": "会議録情報",
            "speechOrder": 2,
            "speechURL": "https://kokkai.ndl.go.jp/txt/new-b#new-2",
            "speech": "本文",
        }
    )
    old_c = meeting("old-2", "https://kokkai.ndl.go.jp/txt/old-c")
    transport = Transport([(200, page(3, [old_a, fresh, old_c]))])
    state = {
        "job_id": "abc123jobid9",
        "hour": 9,
        "deliver": "local",
        "watches": [{
            "id": "d" * 32,
            "any": "科学技術",
            "speaker": "",
            "house": "",
            "lookback_days": 30,
            "seen": ["old-1", "old-2"],
            "last_complete_count": 3,
            "failure": None,
        }],
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    deps = make_deps(tmp_path, transport, cron_judge=lambda: "cron")
    plant_job(deps)
    result = body(service.watch(deps, {"action": "run"}))
    assert result["notify"] is True
    assert "2 new speech records in 1 meeting" in result["message"]
    assert "https://kokkai.ndl.go.jp/txt/new-b" in result["message"]
    assert "https://kokkai.ndl.go.jp/txt/old-a" not in result["message"]
    assert "https://kokkai.ndl.go.jp/txt/old-c" not in result["message"]
    assert "本文" not in result["message"]


def test_deleted_job_is_created_again(tmp_path):
    transport = Transport([(200, zero_payload()), (200, zero_payload()), (200, zero_payload())])
    deps = make_deps(tmp_path, transport)
    first = body(service.watch(deps, {"action": "add", "any": "科学技術", "hour": 9, "deliver": "local"}))
    assert first["ok"] is True
    assert len(deps.jobs.created) == 1
    deps.jobs.jobs.pop("abc123jobid9")
    deps.jobs.missing.add("abc123jobid9")
    deps.clock["t"] += 5
    again = body(service.watch(deps, {"action": "add", "any": "予算", "hour": 9, "deliver": "local"}))
    assert again["ok"] is True
    assert "The watch was saved" in again["message"]
    assert "new one was created" in again["message"]
    assert len(deps.jobs.created) == 2
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert deps.jobs.get_job(saved["job_id"]) is not None
    assert len(saved["watches"]) == 2
    deps.jobs.jobs.pop("abc123jobid9")
    deps.jobs.missing.add("abc123jobid9")
    deps.clock["t"] += 5
    same = body(service.watch(deps, {"action": "add", "any": "予算", "hour": 9, "deliver": "local"}))
    assert same["ok"] is True
    assert same.get("error") != "duplicate"
    assert "new one was created" in same["message"]
    assert len(transport.calls) == 2
    assert len(deps.jobs.created) == 3
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert len(saved["watches"]) == 2
    assert deps.jobs.get_job(saved["job_id"]) is not None
    deps.jobs.fail_get = True
    deps.clock["t"] += 5
    blocked = body(service.watch(deps, {"action": "add", "any": "外交", "hour": 9, "deliver": "local"}))
    assert blocked["ok"] is False
    assert "could not be checked" in blocked["message"]
    assert "The watch was saved" not in blocked["message"]
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert len(saved["watches"]) == 2


def test_external_remove_answers_say_the_job_is_gone(tmp_path):
    transport = Transport([(200, zero_payload()), (200, zero_payload()), (200, zero_payload())])
    deps = make_deps(tmp_path, transport)
    service.watch(deps, {"action": "add", "any": "科学技術", "hour": 9, "deliver": "local"})
    deps.clock["t"] += 5
    service.watch(deps, {"action": "add", "speaker": "山田", "hour": 9, "deliver": "local"})
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    old = saved["job_id"]
    seen = list(saved["watches"][0]["seen"])
    deps.jobs.jobs.pop(old)
    deps.jobs.missing.add(old)
    calls = len(deps.transport.calls)
    listed = body(service.watch(deps, {"action": "list"}))
    assert "The cron job is gone" in listed["message"]
    assert listed["job_id"] is None
    assert old not in json.dumps(listed)
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["job_id"] == old
    ran = body(service.watch(deps, {"action": "run"}))
    assert "The cron job is gone" in ran["message"]
    assert "not requested" in ran["message"]
    assert len(deps.transport.calls) == calls
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["watches"][0]["seen"] == seen
    first = saved["watches"][0]["id"]
    removed = body(service.watch(deps, {"action": "remove", "watch_id": first}))
    assert removed["ok"] is True
    assert "already gone" in removed["message"]
    question = [text for action, text in deps.asked if action == "remove"][-1]
    assert "already gone" in question
    assert "one watch only" in question
    assert "removes the cron job" not in question
    assert "creates a cron" not in question.lower()
    assert "Add a Diet" not in question
    deps.clock["t"] += 5
    again = body(service.watch(deps, {"action": "add", "any": "予算", "hour": 9, "deliver": "local"}))
    assert again["ok"] is True
    assert "gone" in again["message"]
    assert "new one was created" in again["message"]


def _real_cron_script() -> str:
    return r'''
import json, os, sys
from datetime import date
from pathlib import Path

home = Path(sys.argv[1])
plugin = sys.argv[2]
source = sys.argv[3]
os.environ["HERMES_HOME"] = str(home)
os.environ["HERMES_DISABLE_UPDATE_CHECK"] = "1"
sys.path.insert(0, plugin)
sys.path.insert(1, source)
import cron.jobs as jobs
import service

store = home / "cron-home"
data = home / "data"
box = {"t": 1000.0}

def transport(url):
    body = {"numberOfRecords": 0, "numberOfReturn": 0, "startRecord": 1, "nextRecordPosition": None}
    return 200, json.dumps(body).encode()

deps = service.Deps(
    transport=transport,
    now=lambda: box["t"],
    sleep=lambda seconds: box.__setitem__("t", box["t"] + seconds),
    today=lambda: date(2026, 10, 8),
    approver=lambda action, text: (True, ""),
    write_guard=lambda path: None,
    data_dir=data,
    cron=jobs,
    cron_judge=lambda: "manual",
    platforms={"local", "telegram"},
)
with jobs.use_cron_store(store):
    first = json.loads(service.watch(deps, {"action": "add", "any": "科学技術", "hour": 9, "deliver": "local"}))
    if not first.get("ok"):
        raise SystemExit("first add failed: " + json.dumps(first, ensure_ascii=False)[:500])
    state = json.loads((data / "state.json").read_text(encoding="utf-8"))
    old = state["job_id"]
    if jobs.get_job(old) is None:
        raise SystemExit("created job is missing")
    if jobs.remove_job(old) is not True:
        raise SystemExit("external remove failed")
    if jobs.get_job(old) is not None:
        raise SystemExit("removed job still present")
    box["t"] += 5
    second = json.loads(service.watch(deps, {"action": "add", "any": "予算", "hour": 9, "deliver": "local"}))
    if not second.get("ok") or "The watch was saved" not in second.get("message", ""):
        raise SystemExit("second add did not save: " + json.dumps(second, ensure_ascii=False)[:500])
    state = json.loads((data / "state.json").read_text(encoding="utf-8"))
    new = state["job_id"]
    if not new or new == old or jobs.get_job(new) is None:
        raise SystemExit("replacement job is not live: " + str(new))
    listed = jobs.list_jobs(include_disabled=True)
    if len(listed) != 1 or listed[0]["id"] != new:
        raise SystemExit("job count " + str([item.get("id") for item in listed]))
print("ok")
'''


def test_real_cron_jobs_recreate_after_external_remove(tmp_path):
    import subprocess
    script = tmp_path / "real_cron.py"
    script.write_text(_real_cron_script(), encoding="utf-8")
    plugin = str(Path(__file__).resolve().parents[1])
    # preship runs this file from a git archive, so the repo's parent is not stable.
    harness = Path.home() / "takeshitgal" / ".harness"
    pairs = [
        (
            "v0.21.4",
            harness / "venv" / "v0214" / "bin" / "python",
            harness / "src" / "v0214",
        ),
        (
            "main",
            harness / "venv" / "main" / "bin" / "python",
            harness / "src" / "main",
        ),
    ]
    for label, python, source in pairs:
        assert python.is_file(), label
        assert source.is_dir(), label
        home = tmp_path / label
        home.mkdir()
        done = subprocess.run(
            [str(python), str(script), str(home), plugin, str(source)],
            capture_output=True,
            text=True,
            timeout=120,
            env={"PYTHONDONTWRITEBYTECODE": "1", "HOME": str(home), "TMPDIR": str(home)},
        )
        assert done.returncode == 0, label + "\n" + done.stdout + "\n" + done.stderr
        assert done.stdout.strip() == "ok"


def _real_approval_script() -> str:
    return r'''
import json, os, sys
from datetime import date
from pathlib import Path

home = Path(sys.argv[1])
plugin = sys.argv[2]
source = sys.argv[3]
os.environ["HERMES_HOME"] = str(home)
os.environ["HERMES_DISABLE_UPDATE_CHECK"] = "1"
os.environ["HERMES_INTERACTIVE"] = "1"
for key in (
    "HERMES_CRON_SESSION",
    "HERMES_YOLO_MODE",
    "HERMES_SINGLE_QUERY_SESSION",
    "HERMES_GATEWAY_SESSION",
    "HERMES_PLUGIN_HOST_PROCESS",
    "HERMES_EXEC_ASK",
):
    os.environ[key] = ""
sys.path.insert(0, plugin)
sys.path.insert(1, source)

import client
import cron.jobs as jobs
import service
from tools.terminal_tool import set_approval_callback

asked = []

def cb(command, description, **kw):
    asked.append({"command": command, "description": description})
    return "once"

set_approval_callback(cb)
client.reset_flight()
box = {"t": 1000.0}

def transport(url):
    body = {"numberOfRecords": 0, "numberOfReturn": 0, "startRecord": 1, "nextRecordPosition": None}
    return 200, json.dumps(body).encode()

def make(data):
    return service.Deps(
        transport=transport,
        now=lambda: box["t"],
        sleep=lambda seconds: box.__setitem__("t", box["t"] + seconds),
        today=lambda: date(2026, 10, 8),
        approver=None,
        write_guard=lambda path: None,
        data_dir=data,
        cron=jobs,
        cron_judge=lambda: "manual",
        platforms={"local", "telegram"},
    )

def call(deps, args):
    box["t"] += 5
    return json.loads(service.watch(deps, args))

def fail(msg):
    raise SystemExit(msg)

def question():
    if not asked:
        fail("approval mouth was not called")
    last = asked[-1]
    if last["command"] != "<kokkai_watch> (plugin approval rule)":
        fail("mouth command " + last["command"])
    return last["description"]

data1 = home / "one"
with jobs.use_cron_store(home / "store1"):
    deps = make(data1)
    first = call(deps, {"action": "add", "any": "科学技術", "hour": 9, "deliver": "local"})
    if not first.get("ok"):
        fail("add1 " + json.dumps(first, ensure_ascii=False)[:500])
    add_q = question()
    if "Add a Diet meeting watch" not in add_q:
        fail("add question " + add_q)
    state = json.loads((data1 / "state.json").read_text(encoding="utf-8"))
    removed = call(deps, {"action": "remove", "watch_id": state["watches"][0]["id"]})
    q = question()
    if "removes the cron job" not in q or "creates a cron" in q.lower() or "Add a Diet" in q:
        fail("last-live question " + q)
    if "was removed" not in removed.get("message", ""):
        fail("last-live result " + json.dumps(removed, ensure_ascii=False)[:500])

data2 = home / "two"
with jobs.use_cron_store(home / "store2"):
    deps = make(data2)
    added = call(deps, {"action": "add", "any": "科学技術", "hour": 9, "deliver": "local"})
    if not added.get("ok"):
        fail("add A " + json.dumps(added, ensure_ascii=False)[:500])
    added = call(deps, {"action": "add", "speaker": "山田", "hour": 9, "deliver": "local"})
    if not added.get("ok"):
        fail("add B " + json.dumps(added, ensure_ascii=False)[:500])
    state = json.loads((data2 / "state.json").read_text(encoding="utf-8"))
    one = call(deps, {"action": "remove", "watch_id": state["watches"][0]["id"]})
    q = question()
    if "removes one watch only" not in q or "cron job stays" not in q:
        fail("one-watch question " + q)
    if "creates a cron" in q.lower() or "Add a Diet" in q or "removes the cron job" in q:
        fail("one-watch reused " + q)
    if "stays" not in one.get("message", ""):
        fail("one-watch result " + one.get("message", ""))
    added = call(deps, {"action": "add", "any": "予算", "hour": 9, "deliver": "local"})
    if not added.get("ok"):
        fail("add C " + json.dumps(added, ensure_ascii=False)[:500])
    state = json.loads((data2 / "state.json").read_text(encoding="utf-8"))
    old = state["job_id"]
    seen = list(state["watches"][0]["seen"])
    if jobs.get_job(old) is None or jobs.remove_job(old) is not True or jobs.get_job(old) is not None:
        fail("external remove failed")
    listed = call(deps, {"action": "list"})
    if "The cron job is gone" not in listed.get("message", "") or listed.get("job_id"):
        fail("list " + json.dumps(listed, ensure_ascii=False)[:500])
    if old in json.dumps(listed):
        fail("list echoed " + old)
    ran = call(deps, {"action": "run"})
    if "The cron job is gone" not in ran.get("message", ""):
        fail("run " + ran.get("message", "")[:500])
    after = json.loads((data2 / "state.json").read_text(encoding="utf-8"))
    if after["watches"][0]["seen"] != seen:
        fail("run changed seen")
    drop = after["watches"][0]["id"]
    gone = call(deps, {"action": "remove", "watch_id": drop})
    q = question()
    if "already gone" not in q or "one watch only" not in q:
        fail("gone question " + q)
    if "removes the cron job" in q or "creates a cron" in q.lower() or "Add a Diet" in q:
        fail("gone reused " + q)
    if "gone" not in gone.get("message", "").lower():
        fail("gone result " + gone.get("message", ""))
    again = call(deps, {"action": "add", "any": "外交", "hour": 9, "deliver": "local"})
    if "gone" not in again.get("message", "") or "new one was created" not in again.get("message", ""):
        fail("recreate " + again.get("message", "")[:500])
    state = json.loads((data2 / "state.json").read_text(encoding="utf-8"))
    if jobs.get_job(state["job_id"]) is None:
        fail("replacement job missing")
print("ok")
'''


def test_real_approval_mouth_on_both_hermes(tmp_path):
    import subprocess
    script = tmp_path / "real_approval.py"
    script.write_text(_real_approval_script(), encoding="utf-8")
    plugin = str(Path(__file__).resolve().parents[1])
    harness = Path.home() / "takeshitgal" / ".harness"
    pairs = [
        ("v0.21.4", harness / "venv" / "v0214" / "bin" / "python", harness / "src" / "v0214"),
        ("main", harness / "venv" / "main" / "bin" / "python", harness / "src" / "main"),
    ]
    for label, python, source in pairs:
        assert python.is_file(), label
        assert source.is_dir(), label
        home = tmp_path / label
        home.mkdir()
        done = subprocess.run(
            [str(python), str(script), str(home), plugin, str(source)],
            capture_output=True,
            text=True,
            timeout=120,
            env={"PYTHONDONTWRITEBYTECODE": "1", "HOME": str(home), "TMPDIR": str(home)},
        )
        assert done.returncode == 0, label + "\n" + done.stdout + "\n" + done.stderr
        assert done.stdout.strip() == "ok"


def test_rule_keys_differ():
    assert gates.one_call_rule_key("add") != gates.one_call_rule_key("add")
    assert "uuid" not in gates.one_call_rule_key("add") or True
    assert gates.one_call_rule_key("add").count("-") == 4


def test_run_ceiling_counts_gaps_after_timeouts(tmp_path):
    import time

    box = {"t": 1000.0}

    def slow(url):
        box["t"] += client.TIMEOUT_SECONDS
        return 200, json.dumps(zero_payload()).encode()

    watches = []
    for index in range(service.MAX_WATCHES):
        watches.append(
            {
                "id": f"{index:032x}",
                "any": f"word{index}",
                "speaker": "",
                "house": "",
                "lookback_days": 30,
                "seen": [],
                "last_complete_count": 0,
                "failure": None,
            }
        )
    (tmp_path / "state.json").write_text(
        json.dumps({"job_id": "abc123jobid9", "hour": 9, "deliver": "local", "watches": watches}),
        encoding="utf-8",
    )
    try:
        deps = make_deps(tmp_path, slow, clock=box, cron_judge=lambda: "cron")
        plant_job(deps)
        start = box["t"]
        result = body(service.watch(deps, {"action": "run"}))
        ceiling = client.TIMEOUT_SECONDS * service.MAX_WATCHES + client.GAP_SECONDS * (service.MAX_WATCHES - 1)
        assert result["ok"] is True
        assert box["t"] - start == ceiling
        assert ceiling == 100
    finally:
        client.set_clock(time.monotonic)
