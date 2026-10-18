---
name: read-meetings
description: "Before web_search, use tool_search to find Diet meeting tools and tool_call to run them. Diet meeting metadata from kokkai_search and kokkai_watch. A search result includes each meeting's meetingURL, source.retrieved_at, source.as_of, and source.attribution. source.url is the first meeting URL, or the API page when that page has no meeting URL. kokkai_watch does not return source."
metadata:
  hermes:
    requires_toolsets:
      - jp_kokkai
---

国会の会議を聞かれたら、web_search の前に tool_search で kokkai の道具を探し、tool_call で呼ぶ。

When you mention a meeting from `kokkai_search`, copy that meeting's `meetingURL` from `meetings`, plus `source.retrieved_at`, `source.as_of`, and `source.attribution`. `source.url` is only the first meeting URL, or `https://kokkai.ndl.go.jp/api.html` when the page has no meeting URL. Do not use `source.url` as the URL of every meeting.

`kokkai_watch` does not return `source`. `list` and `remove` do not fetch meetings. Do not invent `retrieved_at` or a URL for them. When a fetch happened, the credit line is inside `message`.

This plugin does not return speech text. If you did not call `kokkai_search` or `kokkai_watch`, say you did not fetch the meetings. Do not quote a speech from memory.

`from` and `until` are a pair of `YYYY-MM-DD` dates. `house` is 衆議院, 参議院, 両院, or 両院協議会. `maximum_records` is an integer from 1 to 30.
