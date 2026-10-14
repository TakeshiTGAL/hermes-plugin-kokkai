# Changelog

## 0.1.0

Reads a meeting list (metadata and the official meeting URL) from the Diet proceedings search API. `kokkai_watch` creates one job that runs once a day. Speech text is not requested.

## 0.1.1

Five free models, 40 sessions. 39 sessions answered. The raw count of turns in answered sessions is 205 before the wording change and 203 after it. The before/after rates use only scene-and-model pairs present in both runs: 199 turns before, 198 after. The record names a missing credit line `omitted_credit`.

- bypass: 119/199 (59.8%) → 56/198 (28.3%). Those 56 are also 56/203 (27.6%) of the raw after total.
- missing_tool: 8/199 (4.0%) → 4/198 (2.0%)
- omitted_credit: 6/199 (3.0%) → 6/198 (3.0%)
- invalid_argument: 7/199 (3.5%) → 8/198 (4.0%)
- argument_stuck: 2/199 (1.0%) → 3/198 (1.5%)

A successful `kokkai_search` returns `source.retrieved_at`, `source.as_of`, `source.attribution`, and `source.url`. `source.url` is the first meeting URL, or `https://kokkai.ndl.go.jp/api.html` when the page has none. Each meeting's own URL is `meetings[].meetingURL`. `kokkai_watch` does not return `source`. `bad_args` includes `field`, `problem`, `expected`, and `allowed_fields`, and does not repeat the caller's value or the upstream sentence. On add, `allowed_fields` is `action`, `any`, `speaker`, `house`, `hour`, `deliver`, and `lookback_days`. It does not include `watch_id`. A query longer than 2000 bytes is refused with those four fields. A missing `hour` is `problem` `missing`. A `speaker` that is not a string is `field` `speaker`. A cron `run` writes seen speech IDs to `state.json`. A manual `run` does not. The skill says to copy each meeting's `meetingURL` with the search source fields, and not to invent `source` for a watch.

Bypass and answers that never called the tool went down. `omitted_credit` stayed at 6. The extra `invalid_argument` was one session repeating `maximum_records` outside 1 to 30. That wording change was not run again. The cron job created for the session run was not left in that Hermes profile afterward. The skill asks the model not to quote a speech from memory. It does not stop a model from answering without the tool: 56 of the 198 paired turns still bypassed the plugin.
