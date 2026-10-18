"""Argument shapes from real sessions, plus the forms those sessions used."""
import json

import service
from test_plugin import Transport, body, make_deps, zero_payload


def _search(tmp_path, args, responses=None):
    transport = Transport(responses if responses is not None else [(200, zero_payload())])
    deps = make_deps(tmp_path, transport)
    return body(service.search(deps, args)), transport


def test_integral_float_and_fullwidth_maximum_records_are_sent(tmp_path):
    # refuse_imperial_ja / search_polite_long_ja sent 10.0 and 30.0
    result, transport = _search(tmp_path, {"any": "帝国", "maximum_records": 10.0})
    assert result["ok"] is True
    assert "maximumRecords=10" in transport.calls[0]
    result, transport = _search(tmp_path, {"any": "予算", "maximum_records": "１０"})
    assert result["ok"] is True
    assert "maximumRecords=10" in transport.calls[0]


def test_slash_and_fullwidth_dates_are_sent_as_iso(tmp_path):
    result, transport = _search(
        tmp_path,
        {"house": "衆議院", "from": "2024/06/03", "until": "２０２４-０６-０３"},
    )
    assert result["ok"] is True
    assert "from=2024-06-03" in transport.calls[0]
    assert "until=2024-06-03" in transport.calls[0]


def test_upstream_field_names_are_accepted(tmp_path):
    # search_url_only_ja sent dateFrom, dateTo, nameOfHouse
    result, transport = _search(
        tmp_path,
        {"dateFrom": "2024-06-03", "dateTo": "2024-06-03", "nameOfHouse": "参議院"},
    )
    assert result["ok"] is True, result
    sent = transport.calls[0]
    assert "from=2024-06-03" in sent
    assert "until=2024-06-03" in sent
    assert "nameOfHouse" in sent


def test_zero_and_word_maximum_records_explain_the_fix(tmp_path):
    # search_max_0_en sent 0
    fixed = "maximum_records is not accepted (out_of_range). Expected: an integer from 1 to 30."
    for bad in (0, 101, "lots"):
        result, transport = _search(tmp_path, {"any": "予算", "maximum_records": bad}, responses=[])
        text = json.dumps(result, ensure_ascii=False)
        assert result["ok"] is False
        assert result["error_type"] == "bad_args"
        assert result["message"] == fixed
        assert "10" in result["next_step"]
        assert "101" not in text
        assert "lots" not in text
        assert transport.calls == []


def test_start_date_alone_asks_for_both_dates_without_the_alias(tmp_path):
    # search_from_only_ja sent start_date and no until
    result, transport = _search(
        tmp_path,
        {"house": "衆議院", "maximum_records": 30, "start_date": "2024-06-03"},
        responses=[],
    )
    text = json.dumps(result, ensure_ascii=False)
    assert result["ok"] is False
    assert result["error_type"] == "bad_args"
    assert result["problem"] == "not_a_pair"
    assert "YYYY-MM-DD" in result["message"]
    assert "start_date" not in text
    assert "2024-06-03" not in text
    assert transport.calls == []
