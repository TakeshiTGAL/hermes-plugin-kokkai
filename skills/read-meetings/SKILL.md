---
name: read-meetings
description: "Before web_search, use tool_search to find Japan Diet and parliament meeting tools and tool_call to run them. Diet meeting metadata from kokkai_search and kokkai_watch. Who said what, with a quote, comes from kokkai_speech. A search result includes each meeting's meetingURL, source.retrieved_at, source.as_of, and source.attribution. source.url is the first meeting URL, or the API page when that page has no meeting URL. kokkai_watch does not return source."
---

国会の会議を聞かれたら、web_search の前に tool_search で kokkai の道具を探し、tool_call で呼ぶ。

When you mention a meeting from `kokkai_search`, copy that meeting's `meetingURL` from `meetings`, plus `source.retrieved_at`, `source.as_of`, and `source.attribution`. `source.url` is only the first meeting URL, or `https://kokkai.ndl.go.jp/api.html` when the page has no meeting URL. Do not use `source.url` as the URL of every meeting.

`kokkai_watch` does not return `source`. `list` and `remove` do not fetch meetings. Do not invent `retrieved_at` or a URL for them. When a fetch happened, the credit line is inside `message`.

Who said what, with a quote: call `kokkai_speech` with `speaker` or `any`. A meeting list without the words spoken: call `kokkai_search`. A saved daily check: call `kokkai_watch`. `kokkai_search` does not return speech text. `kokkai_speech` returns an excerpt, the speaker, the meeting name, the date, and `speechURL`. The total count and the truncated mark come first. Search tool_search in English, for example "Japan Diet speeches who said what". If you did not call the tool, say you did not fetch it. Do not quote a speech from memory.

`from` and `until` are a pair of `YYYY-MM-DD` dates. `house` is 衆議院, 参議院, 両院, or 両院協議会. `maximum_records` is an integer from 1 to 30.
