---
name: read-meetings
description: List Diet meetings from the National Diet Library and quote them only with the tool's source fields.
---

条文や発言を伝えるときは、この欄を一緒に伝える。道具を呼んでいないときは、取得していないと言う。記憶から条文を書かない。

When you mention a meeting, a count, or a speaker, copy `source.attribution`, `source.retrieved_at`, `source.url`, and `source.as_of` into the reply. This plugin does not return speech text. If you did not call `kokkai_search` or `kokkai_watch`, say you did not fetch the meetings. Do not quote a speech from memory.

`from` and `until` are a pair of `YYYY-MM-DD` dates. `house` is 衆議院, 参議院, 両院, or 両院協議会. `maximum_records` is an integer from 1 to 30.
