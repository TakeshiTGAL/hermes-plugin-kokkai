# Changelog

## 0.1.2

`kokkai_speech` returns a short excerpt, the speaker, the meeting name, the date, and the speech URL from `/api/speech`.

## 0.1.0

Reads a meeting list (metadata and the official meeting URL) from the Diet proceedings search API. `kokkai_watch` creates one job that runs once a day. Speech text is not requested.

## 0.1.1

Five models, 40 sessions. 39 sessions answered. The earlier run counts 205 turns in sessions that answered. The later run counts 203. A `bypass` hit is one call to a built-in tool (`web_extract`, `web_search`, `read_file`, `write_file`, `terminal`, `execute_code`, `patch`, `search_files`, or `browser_navigate`) when this plugin was not called, or its result was not ok. The earlier count has no `bypass` tally. The later run has 56 `bypass` hits in 203 turns.

- missing_tool: 20/205 → 4/203
- omitted_credit: 6/205 → 6/203
- invalid_argument: 7/205 → 8/203
- argument_stuck: 2/205 → 3/203

A successful `kokkai_search` returns `source.retrieved_at`, `source.as_of`, `source.attribution`, and `source.url`. `source.url` is the first meeting URL, or `https://kokkai.ndl.go.jp/api.html` when the page has none. Each meeting's own URL is `meetings[].meetingURL`. `kokkai_watch` does not return `source`. `bad_args` includes `field`, `problem`, `expected`, and `allowed_fields`, and does not repeat the caller's value or the upstream sentence. On add, `allowed_fields` is `action`, `any`, `speaker`, `house`, `hour`, `deliver`, and `lookback_days`. It does not include `watch_id`. A query longer than 2000 bytes is refused with those four fields. A missing `hour` is `problem` `missing`. A `speaker` that is not a string is `field` `speaker`. A cron `run` writes seen speech IDs to `state.json`. A manual `run` does not. The skill says to copy each meeting's `meetingURL` with the search source fields, and not to invent `source` for a watch.

`missing_tool` went from 20 hits in 205 turns to 4 hits in 203 turns. `omitted_credit` stayed at 6 hits. `bypass` is counted only in the later run: 56 hits in 203 turns.

Tool Search の一覧に出る最初の1文を、頼むときの語にした。仕組みの文から始まると検索されず本体の web_search に寄るため。

関門4: 整数の小数・全角数字・日付の区切り・dateFrom などの別名は受け、範囲外と未知の引数は直し方と例で断り、入力値は文に入れない。
