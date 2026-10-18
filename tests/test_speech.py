"""kokkai_speech against copies of the speech API, plus each failure shape."""
from __future__ import annotations

import json
from pathlib import Path

import client
import service
from test_plugin import Transport, body, make_deps, zero_payload

FIXTURES = Path(__file__).parent / "fixtures"
SPEAKER = "岸田文雄"
WORD = "予算"


def _row(text="予算について述べます。", speaker=SPEAKER, url="https://kokkai.ndl.go.jp/txt/s1"):
    return {
        "speechID": "121305261X00120240205_001",
        "issueID": "121305261X00120240205",
        "session": 213,
        "nameOfHouse": "衆議院",
        "nameOfMeeting": "予算委員会",
        "issue": "第1号",
        "date": "2024-02-05",
        "speechOrder": 1,
        "speaker": speaker,
        "speakerPosition": "内閣総理大臣",
        "speech": text,
        "speechURL": url,
        "meetingURL": "https://kokkai.ndl.go.jp/txt/m1",
    }


def _page(records, rows, returned=None):
    returned = len(rows) if returned is None else returned
    return {
        "numberOfRecords": records,
        "numberOfReturn": returned,
        "startRecord": 1,
        "nextRecordPosition": None if records == returned else returned + 1,
        "speechRecord": rows,
    }


def _speech(tmp_path, args, responses):
    transport = Transport(responses)
    deps = make_deps(tmp_path, transport)
    return body(service.speech(deps, args)), transport


def test_excerpt_total_and_url(tmp_path):
    long_text = "予算" * 200
    result, transport = _speech(
        tmp_path,
        {"speaker": SPEAKER, "any": WORD, "from": "2024-02-01", "until": "2024-02-28", "maximum_records": 5},
        [(200, _page(4, [_row(long_text), _row("短い発言です。", url="https://kokkai.ndl.go.jp/txt/s2")], returned=2))],
    )
    assert result["ok"] is True
    assert result["message"].startswith("4 speeches in total. Showing 2. truncated.")
    assert result["speech_count"] == 4
    assert result["shown"] == 2
    assert result["truncated"] is True
    assert result["speeches"][0]["excerpt"].endswith("…")
    assert result["speeches"][0]["excerpt_truncated"] is True
    assert len(result["speeches"][0]["excerpt"]) == service.EXCERPT_CHARS + 1
    assert long_text not in json.dumps(result, ensure_ascii=False)
    assert result["speeches"][1]["excerpt"] == "短い発言です。"
    assert result["speeches"][1]["excerpt_truncated"] is False
    assert result["speeches"][0]["speaker"] == SPEAKER
    assert result["speeches"][0]["nameOfMeeting"] == "予算委員会"
    assert result["speeches"][0]["date"] == "2024-02-05"
    assert result["speeches"][0]["speechURL"] == "https://kokkai.ndl.go.jp/txt/s1"
    assert "出典：国立国会図書館「国会会議録検索システム」" in result["message"]
    assert result["source"]["url"] == "https://kokkai.ndl.go.jp/txt/s1"
    assert "/api/speech?" in transport.calls[0]
    assert "maximumRecords=5" in transport.calls[0]
    assert "recordPacking=json" in transport.calls[0]


def test_zero_without_array_and_with_empty_array(tmp_path):
    bare = {"numberOfRecords": 0, "numberOfReturn": 0, "startRecord": 1, "nextRecordPosition": None}
    empty = dict(bare, speechRecord=[])
    transport = Transport([(200, bare), (200, empty)])
    deps = make_deps(tmp_path, transport)
    first = body(service.speech(deps, {"speaker": SPEAKER, "any": WORD}))
    deps.clock["t"] += 5
    second = body(service.speech(deps, {"speaker": SPEAKER, "any": WORD}))
    for result in (first, second):
        assert result["ok"] is True
        assert result["message"].startswith("0 speeches in total. Showing 0. not truncated.")
        assert result["speeches"] == []
        assert result["source"]["url"] == "https://kokkai.ndl.go.jp/api.html"


def test_failure_shapes(tmp_path):
    cases = [
        (400, {"message": "(19011)検索条件の入力に誤りがあります。", "details": ["(19012)from:開会日付をYYYY-MM-DD形式で入力してください。"]}),
        (200, {"message": "(19007)検索条件を指定してください。", "details": []}),
        (200, b""),
        (200, b"not-json"),
        (301, b"<html>redirect</html>"),
        (200, {"numberOfRecords": 2, "numberOfReturn": 1, "startRecord": 1, "nextRecordPosition": 2}),
        (200, _page(1, ["not-an-object"])),
        (200, b"x" * (client.BODY_CAP + 1)),
    ]
    transport = Transport(cases)
    deps = make_deps(tmp_path, transport)

    def boom(url):
        raise TimeoutError("timed out")

    http = body(service.speech(deps, {"speaker": SPEAKER, "any": WORD}))
    assert http["ok"] is False
    assert "(19011)" in http["message"] and "(19012)" in http["message"]
    assert "0 speeches" not in http["message"]
    deps.clock["t"] += 5
    message_body = body(service.speech(deps, {"speaker": SPEAKER, "any": WORD}))
    assert message_body["ok"] is False and "(19007)" in message_body["message"]
    deps.clock["t"] += 5
    empty = body(service.speech(deps, {"speaker": SPEAKER, "any": WORD}))
    assert empty["ok"] is False and "empty body" in empty["message"]
    deps.clock["t"] += 5
    bad_json = body(service.speech(deps, {"speaker": SPEAKER, "any": WORD}))
    assert bad_json["ok"] is False and "not JSON" in bad_json["message"]
    deps.clock["t"] += 5
    redirect = body(service.speech(deps, {"speaker": SPEAKER, "any": WORD}))
    assert redirect["ok"] is False and "301" in redirect["message"]
    deps.clock["t"] += 5
    mismatch = body(service.speech(deps, {"speaker": SPEAKER, "any": WORD}))
    assert mismatch["error"] == "partial" and "not 0 speeches" in mismatch["message"]
    deps.clock["t"] += 5
    bad_row = body(service.speech(deps, {"speaker": SPEAKER, "any": WORD}))
    assert bad_row["ok"] is False and "not an object" in bad_row["message"]
    deps.clock["t"] += 5
    oversize = body(service.speech(deps, {"speaker": SPEAKER, "any": WORD}))
    assert oversize["ok"] is False and "2000000" in oversize["message"]
    deps.clock["t"] += 5
    timed = body(service.speech(make_deps(tmp_path, boom), {"speaker": SPEAKER, "any": WORD}))
    assert timed["error"] == "timeout"
    assert "may have" not in timed["message"].lower()


def test_missing_speech_text_is_not_invented(tmp_path):
    row = _row()
    del row["speech"]
    result, _transport = _speech(tmp_path, {"speaker": SPEAKER, "any": WORD}, [(200, _page(1, [row]))])
    assert result["ok"] is True
    assert result["speeches"][0]["excerpt"] == ""
    assert "1 row had no speech text" in result["message"]
    assert "Do not invent a quote" in result["message"]


def test_argument_refusals_name_the_fix(tmp_path):
    transport = Transport([])
    deps = make_deps(tmp_path, transport)
    unknown = body(service.speech(deps, {"query": "衆議院", "limit": 101}))
    assert unknown["problem"] == "unknown_field"
    assert "query" in unknown["next_step"] and "limit" in unknown["next_step"]
    assert "岸田文雄" in unknown["next_step"]
    missing = body(service.speech(deps, {"house": "衆議院", "from": "2024-06-03", "until": "2024-06-03"}))
    assert missing["problem"] == "missing_criteria"
    assert "speaker" in missing["next_step"] and "予算" in missing["next_step"]
    inverted = body(service.speech(deps, {"speaker": SPEAKER, "from": "2024-06-10", "until": "2024-06-03"}))
    assert inverted["problem"] == "inverted" and "Swap the dates" in inverted["next_step"]
    one_date = body(service.speech(deps, {"any": WORD, "from": "2024/06/03"}))
    assert one_date["problem"] == "not_a_pair"
    too_many = body(service.speech(deps, {"any": WORD, "maximum_records": 11}))
    assert too_many["problem"] == "out_of_range" and "1 to 10" in too_many["next_step"]
    zero = body(service.speech(deps, {"any": WORD, "maximum_records": 0}))
    assert zero["problem"] == "out_of_range"
    assert transport.calls == []
    sent, calls = _speech(tmp_path, {"speaker": SPEAKER, "any": WORD, "maximum_records": 5.0}, [(200, _page(1, [_row("予算")]))])
    assert sent["ok"] is True
    assert "maximumRecords=5" in calls.calls[0]


def test_speech_shares_the_five_second_gap(tmp_path):
    transport = Transport([(200, zero_payload()), (200, _page(1, [_row()]))])
    deps = make_deps(tmp_path, transport)
    assert body(service.search(deps, {"any": WORD}))["ok"] is True
    deps.clock["t"] += 4
    early = body(service.speech(deps, {"speaker": SPEAKER, "any": WORD}))
    assert early["error"] == "too_soon"
    assert "one request" in early["next_step"]
    assert len(transport.calls) == 1
    deps.clock["t"] += 1
    assert body(service.speech(deps, {"speaker": SPEAKER, "any": WORD}))["ok"] is True


def test_search_partial_and_watch_refusals_say_how_to_fix(tmp_path):
    wide = {
        "numberOfRecords": 14,
        "numberOfReturn": 10,
        "startRecord": 1,
        "nextRecordPosition": 11,
        "meetingRecord": [],
    }
    # The meeting parser rejects a count that does not match the array before the page-size message.
    # A matching array of 10 with a total of 14 is the shape from search_inverted_ja.
    from test_plugin import meeting
    wide["meetingRecord"] = [meeting(f"s{i}") for i in range(10)]
    transport = Transport([(200, wide)])
    partial = body(service.search(make_deps(tmp_path, transport), {"house": "衆議院", "from": "2024-06-03", "until": "2024-06-10"}))
    assert partial["error"] == "partial"
    assert "not 0 meetings" in partial["message"]
    assert "maximum_records" in partial["next_step"]
    assert "Wait 5 seconds" in partial["next_step"]
    unknown = body(service.search(make_deps(tmp_path, Transport([])), {"query": "衆議院 2024年6月3日"}))
    assert "query" in unknown["next_step"]
    assert "2024-06-03" in unknown["next_step"]
    missing = body(service.watch(make_deps(tmp_path, Transport([])), {"action": "add", "deliver": "local", "hour": 9}))
    assert missing["problem"] == "missing_criteria"
    assert "予算" in missing["next_step"]
    absent = body(service.watch(make_deps(tmp_path, Transport([])), {
        "action": "remove", "watch_id": "0123456789abcdef0123456789abcdef",
    }))
    assert absent["error"] == "not_found"
    assert "action list" in absent["next_step"]

    def deny(action, text):
        return False, "BLOCKED: a single-query session (hermes -q) cannot add or remove a watch."

    blocked = body(service.watch(make_deps(tmp_path, Transport([]), approver=deny), {
        "action": "add", "deliver": "local", "hour": 9, "speaker": "山田太郎",
    }))
    assert blocked["error"] == "approval"
    assert "hermes -q" in blocked["next_step"]


def test_live_response_copies(tmp_path):
    files = [FIXTURES / f"speech_live_{i}.json" for i in (1, 2, 3)]
    assert all(path.is_file() for path in files)
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["numberOfRecords"] >= 1
        assert payload["speechRecord"]
        result, transport = _speech(tmp_path, {"speaker": SPEAKER, "any": WORD}, [(200, payload)])
        raw = payload["speechRecord"][0]
        row = result["speeches"][0]
        assert result["ok"] is True
        assert result["message"].startswith(f"{payload['numberOfRecords']} speeches in total.")
        assert row["speechURL"] == raw["speechURL"]
        assert row["speechURL"].startswith("https://kokkai.ndl.go.jp/")
        assert row["speaker"] == raw["speaker"]
        assert row["nameOfMeeting"] == raw["nameOfMeeting"]
        assert row["date"] == raw["date"]
        assert row["excerpt"]
        speech = raw["speech"]
        if len(speech) > service.EXCERPT_CHARS:
            assert row["excerpt"] == speech[:service.EXCERPT_CHARS] + "…"
            assert speech not in json.dumps(result, ensure_ascii=False)
        else:
            assert row["excerpt"] == speech
        assert "出典：国立国会図書館「国会会議録検索システム」" in result["message"]
        assert "/api/speech?" in transport.calls[0]


def test_speech_does_not_write_state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{}", encoding="utf-8")
    before = path.stat().st_mtime_ns
    service.speech(make_deps(tmp_path, Transport([(200, _page(1, [_row()]))])), {"speaker": SPEAKER, "any": WORD})
    assert path.read_text(encoding="utf-8") == "{}"
    assert path.stat().st_mtime_ns == before
