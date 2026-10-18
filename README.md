# jp-kokkai

Posts new Diet meeting hits as metadata and the official meeting URL, built on the National Diet Library Diet Proceedings Search System API (kokkai.ndl.go.jp).

The product name is jp-kokkai. The toolset is `jp_kokkai`. The tools are `kokkai_search`, `kokkai_speech`, and `kokkai_watch`. There is no slash command and no CLI. `kokkai_search` does not return speech text. `kokkai_speech` requests it and returns a short excerpt. Speech text is not stored or logged.

Hermes turns this plugin's tools on for every surface, including gateways, until you turn a surface off with `hermes tools`. No user list: anyone you allow on your gateway can search, read a speech excerpt, add a watch, and approve it. If a room allowlist is empty, a guest in that room can call these tools too.

## Tools

`kokkai_search` sends one HTTPS GET to `https://kokkai.ndl.go.jp` at path `/api/meeting_list`. It returns one summary per meeting, and the official `meetingURL`, only when `numberOfRecords` equals `numberOfReturn` and that count is the whole page. A summary has the house, the meeting name, the date, the meeting URL, the issue, the session, the speech count, and up to 30 speaker names. It does not return every speech row. The message, including the meeting count and the credit line, is the first field of the result. A successful search also returns `source.retrieved_at`, `source.as_of`, `source.attribution`, and `source.url`. `source.url` is the first meeting's `meetingURL`, or `https://kokkai.ndl.go.jp/api.html` when that page has no meeting URL. Name a meeting with that meeting's `meetingURL`, not with `source.url` alone. If the total is larger than the page, the call fails, returns no meetings, and does not say 0 meetings. Narrow the query until one response can hold it. It does not ask for approval and it does not mark speech IDs seen. `maximum_records` is an integer from 1 to 30 and defaults to 10.

`kokkai_speech` sends one HTTPS GET to `https://kokkai.ndl.go.jp` at path `/api/speech`. It needs `speaker` or `any`. `from` and `until` are optional and must be passed together. It returns at most 10 speeches (the default is 5). The message starts with the total `numberOfRecords`, how many rows are shown, and `truncated` or `not truncated`. Each excerpt is at most 240 characters. A longer speech ends the excerpt with `…` and sets `excerpt_truncated`. The row also has the speaker, the meeting name, the date, and `speechURL`. The credit line uses those speech URLs. `source.url` is the first speech URL, or `https://kokkai.ndl.go.jp/api.html` when the page has none. A total larger than the page is still a success: the total is stated and `truncated` is set. The speech text is not written to `plugin-data`. The same 5-second gap covers this call and `kokkai_search`.

`kokkai_watch` takes `add`, `remove`, `list`, or `run`. Calling it with no arguments is the same as `run`. Any other argument without an action is rejected. Unknown actions and arguments the action does not accept are rejected before a request. It does not return `source`. `list` reads the saved watches and does not call the meeting API. `add` and `remove` write `<HERMES_HOME>/plugin-data/jp-kokkai/state.json` and ask for approval. A cron `run` writes seen speech IDs into that file. A manual `run` does not.

`any` is a space-separated AND. `speaker` is a space-separated OR. Spaces are not removed. `house` accepts only `衆議院`, `参議院`, `両院`, and `両院協議会`. The API specification says `両院` and `両院協議会` return the same meetings. This plugin stores them as different watches, so saving both can announce the same new speech twice. Any other house, including `下院`, is rejected before a request, because the API silently drops an unknown house and returns the same count as a search with no house. `from` and `until` on a search must be passed together as `YYYY-MM-DD`. A range whose `from` is after `until` is rejected before a request. The measured details line for that case is `(0) (19014)fromはuntil以下、もしくはuntilはfrom以上の西暦年で入力してください。` The specification table uses 日付 for that number. A search needs `any`, `speaker`, a house, or both dates. A watch needs `any` or `speaker`. A date range alone is not a watch.

Japanese names are copied as the API sent them. A `speaker` value of `会議録情報` is metadata and is passed through. `speakerRole`, `issueID`, supplement flags, and `nameOfMeeting` are not arguments.

A watch GET always sends `maximumRecords=100`, `recordPacking=json`, and `startRecord=1`. It never sends 101. A condition query longer than 2000 bytes is rejected before the call. A response larger than 2000000 bytes is a failure and nothing is saved. Tool arguments cannot raise that limit. One response is not auto-paged. A result that does not fit in one response is not marked seen, and that watch is not saved.

`lookback_days` is 1 to 30 and defaults to 30. `until` is today in Asia/Tokyo. `from` is that day minus (`lookback_days` minus 1), so 1 day is today only. If Asia/Tokyo cannot be resolved, the API is not called. The plugin does not invent a UTC+9 offset.

## Approval

`add` and `remove` ask Hermes for approval on every call. The question is a new `rule_key` each time, so an earlier session or an "always" answer does not approve the next call. Answering "always" adds one unused `command_allowlist` line in `config.yaml`. That line remains after you remove the plugin. Choose once.

The question is sent only when the whole text is still within 300 characters after HTML escaping of `&`, `<`, and `>`. 300 is the budget this plugin uses so the question is not cut off. It is not a measured Discord limit. A question that would be cut is not sent, and nothing is requested or saved.

Adding or removing a watch is refused, with no request and no job change, under cron, yolo, approvals off, `hermes -q`, an unattended session, a plugin host process (`plugins.isolation: host`, which sets `HERMES_PLUGIN_HOST_PROCESS` to anything other than empty or `0`), and when those checks cannot be loaded.

The remove question states the result. When this is the last watch and the cron job still exists, it says this removes the cron job. When other watches remain, it says this removes one watch only and the cron job stays. When the job is already gone, it says that and does not say a job will be created. The add question is not reused.

## Cron

The `hour` is read in Hermes's own timezone (`HERMES_TIMEZONE`, then the `timezone` in config.yaml, or the computer's clock if neither is set), not Asia/Tokyo. Only the search window uses Asia/Tokyo.

The first saved watch creates one Hermes cron job named `jp-kokkai`. The schedule is `0 <hour> * * *` only. `hour` is 0 through 23 and `deliver` is required. At most three watches share that one job, one hour, and one deliver target. A later add that changes the hour or the deliver target updates that job and names the previous hour and deliver target. The plugin does not create a second job and does not remove a job by name. If the update fails, the stored hour and deliver target stay as they were. Before add, list, run, or remove treats a stored job id as present, it checks that the job still exists. If the job was removed outside this plugin, including by `hermes cron remove` or by installing the plugin again, list says the cron job is gone and does not return that id as a live job. run says the cron job is gone, does not request the meeting list, and does not change seen speech IDs. remove says the cron job is already gone. add creates a new job and says the saved cron job was gone. It does not say the watch was saved while no job exists. If that check cannot be made, nothing is saved and list does not show the id as running. One run makes up to three meeting-list requests with a 5 second wait between them. Each request stops after 30 seconds with no data. That is not a limit on the whole run, so a server that keeps sending slowly can make one run last longer. The watch uses private parts of Hermes, so a Hermes update can stop it.

Each run is at least one model turn. A turn that calls the tool makes two or more model requests. The job runs once a day. There is no daily cap. `bot-chat` spends another model turn on the delivered text. `local` is not sent to a chat. This is not model-free.

The installed prompt tells the model to call `kokkai_watch` with `{"action":"run"}` only, to relay `message` when `notify` is true, to reply `[SILENT]` when `notify` is false, and to say the watch did not run when the tool is missing. It tells the model not to call `kokkai_search`, not to add or remove a watch, and not to quote speeches. A model can still add words. That limit is not enforceable here.

`hermes plugins remove` leaves the job in place. After that, `kokkai_watch` is gone, so the job stays until you run `hermes cron remove`. While the plugin is still installed, removing the last watch with `kokkai_watch` also removes that job. When the Hermes profile is not default, add `-p <name>` to that `hermes cron remove`, and the printed `hermes cron create`, `run`, `list`, `status`, and `remove` lines include `-p <name>`. Hermes uses the name `custom` for a home that is not `~/.hermes` and not `profiles/<name>`. That name is not a profile, so those lines then omit `-p`. A directory named `custom` gets `-p custom` only when it is `profiles/custom` and it already has `config.yaml`, `.env`, `SOUL.md`, `profile.yaml`, `auth.json`, or `state.db`. A directory with none of those does not get `-p`. This plugin does not take `-p` or `--profile` as its own flag.

A cron run marks new speech IDs seen only after a complete page. A manual `run` returns the same new IDs and does not mark them seen. Speech IDs already present when you add a watch are marked seen and are not announced as new. If the cron check cannot be read, or this process is the plugin host, every run skips the API and `notify` is true with the message that this form cannot save seen speech IDs. That notice is sent every time and is not held for 23 hours. Seen IDs are not changed. That message does not say there is nothing new.

`notify` is present on success and on failure. A complete run with no new speech IDs has `notify` false. The same API failure is reported on the first run, when the cause changes, and again after 23 hours. A change of HTTP status, such as 503 then 400, is a new cause. Recovery is reported once. An argument error is not counted as an API failure. A notice of new speech records counts only meetings that contain a new speech, and its credit line lists only those meeting URLs.

If the previous complete count was greater than 0 and this run is a real 0-meeting result, seen IDs are kept and the message says this is not an all-clear. On a cron run the stored complete count becomes 0. A manual run does not write that count, so it warns again until a cron run records the zero. A 0-meeting result after a stored 0 is not that warning. 0 meetings is a success, not an error. HTTP 400, and HTTP 200 whose body has `message` and no `numberOfRecords`, are failures. An empty body or a non-JSON body is a failure. Redirects (301, 302, 303, 307, 308) are not followed. The timeout is 30 seconds and one try. The plugin does not retry, including when the API reports 19001. A timeout says the meetings were not fetched and saved state is unchanged. It does not say a change may already have taken effect, because this plugin only sends GET.

After a response, the next send waits 5 seconds. A second call during a request returns immediately and does not send. The official machine-access note says to wait on the order of several seconds. It does not state an integer floor. 5 seconds is this plugin's floor, and it cannot be set shorter. It is not an official 3-second floor. One request is in flight at a time.

## Deliver

Accepted deliver targets are `origin`, `local`, `all`, `bot-chat`, `bot-chat:<profile>`, a platform from Hermes cron's `_KNOWN_DELIVERY_PLATFORMS`, `platform:chat_id`, a comma combination such as `origin,all`, and a platform added by a loaded plugin. If that Hermes list cannot be imported, every deliver target is refused. `cli`, `cron`, `api_server`, and schedule-shaped words (`every`, `in`, `at`, a weekday, `5m`, `*`, `@`, an ISO timestamp, a leading digit) are refused, and the existing job is left as it is. Replacing a deliver target names the previous one.

## Storage

State is one file, `<HERMES_HOME>/plugin-data/jp-kokkai/state.json`, one directory per profile. Writes go through Hermes's write guard and replace the file from a temporary file in that directory. If the guard cannot be loaded, raises, or denies the write, nothing is written. A corrupt state file is not overwritten with an empty file, and that run does not change seen IDs. The file holds the deliver target, the hour, the job id, watch words, seen `speechID` values, and failure times. It does not hold speech text, and this plugin does not create a key file. `hermes plugins remove` does not delete this file. Delete `state.json` in that directory yourself when you want the watch words, the deliver target, and the seen speech IDs gone. The state file is not emitted as `MEDIA:`. Error responses are not cached. Paths are not built from text you pass in.

## What a success cites

Every successful search, watch add that fetched a page, and watch run appends one credit line. The date is the Asia/Tokyo calendar date of that call. For 0 meetings the line is exactly the following, with `YYYY-MM-DD` replaced by that date:

出典：国立国会図書館「国会会議録検索システム」（https://kokkai.ndl.go.jp/api.html）、PDL1.0（https://www.digital.go.jp/resources/open_data/public_data_license_v1.0）（YYYY-MM-DDに利用）。国立国会図書館「国会会議録検索システム」の検索結果を jp-kokkai が選択して作成。

When the response has meetings, the `https://kokkai.ndl.go.jp/api.html` URL is replaced by the `meetingURL` values, joined with `、`. A watch notice of new speech records uses only the URLs of meetings that contain a new speech. The list is this plugin's selection, not an unedited library feed. `kokkai_watch` does not add a `source` object. The credit line, when a fetch happened, stays in `message`.

`kokkai_speech` fetches speech text and returns an excerpt. Section 4 of the API terms says copyright in National Diet Library staff speeches and in the database itself stays with the National Diet Library, and copyright in other speeches stays with the speaker. Whether a use needs permission is for you to check. `kokkai_speech` は発言の抜粋を返す。APIの利用条件の4は、国立国会図書館の職員の発言とデータベース自体の著作権は国立国会図書館にあり、その他の発言の著作権は発言者にある、と書いている。許諾が要るかは利用者が確認する。

The API page says: 「APIの利用には、手続き等は必要ありません。」 https://kokkai.ndl.go.jp/api.html

This plugin does not read an API key and does not send `Authorization`.

## Order of results

This plugin does not claim that meetings are ordered only by date, and it does not claim that plenary sittings come first. A page that does not hold every match is not treated as the whole result. Counts measured on 2026-10-08 are not used as proof of publication lag.

## Demand and Hermes

A primary request that someone asked for this on Hermes was not found. The adjacent note is a 2024-10-02 Zenn post, https://zenn.dev/coconala/articles/f120b70a719760 , and it is not a request filed against Hermes. On 2026-10-08, `origin/main` `0240fa4a84123406a0e5e6e7262e5b772b43f0bd` had no catalog entry, skill, or plugin for these proceedings. That check is `git grep` of that commit. Open pull-request titles were not stored in this repository, and `gh` was not used. Titles outside a search for these proceedings were not listed.

## What was checked, and what was not

On 2026-10-08 the meeting-list shapes were checked from outside this plugin, at least 1.2 seconds apart. The shapes were a normal page, a 400 `message` body, `maximumRecords=101`, an inverted range, a 0-meeting body, an unknown house, and `http://` returning 301 to the https URL. The record counts from that hour are not expected values in tests. This plugin was not used for those checks. The request count is not stored in this repository.

Not checked:

- Delivery into a real chat, a gateway with a person admitted, or whether a model relays `message` unchanged.
- Whether an individual speech would fall under a copyright exception. `kokkai_speech` returns an excerpt and does not decide that.
- The imperial-diet proceedings system. It is a different system and is not called.
- A trademark-only page. The ban that was read is section ２（1） of https://www.ndl.go.jp/jp/sitepolicy/terms.html (updated 2025-03-01), which says a third party may not use the library's symbols, logos, or character designs. Not finding a separate trademark page is not permission to use them. This plugin does not use them.
- Every open pull-request title. Those search responses are not stored in this repository.

The API terms that were read are section 4 of https://kokkai.ndl.go.jp/api.html . The credit line follows section 1.1 of PDL 1.0, https://www.digital.go.jp/resources/open_data/public_data_license_v1.0 .

## What the session check showed

Five models, 40 sessions. 39 sessions answered. The later run counts 203 turns in sessions that answered. The earlier run counts 205. A `bypass` hit is one call to a built-in tool (`web_extract`, `web_search`, `read_file`, `write_file`, `terminal`, `execute_code`, `patch`, `search_files`, or `browser_navigate`) on a turn where this plugin was not called, or its result was not ok. The later run has 56 of those hits in its 203 turns. The same run has 4 `missing_tool` hits, 6 `omitted_credit` hits, 8 `invalid_argument` hits, and 3 `argument_stuck` hits, each out of 203. The earlier run's count has no `bypass` tally. It has 20 `missing_tool` hits, 6 `omitted_credit` hits, 7 `invalid_argument` hits, and 2 `argument_stuck` hits, each out of 205. Offline tests inject the HTTP transport. They passed on Python 3.12.14 in the Hermes 0.21.4 environment and on Python 3.14.7 in current main.

## Requirements

`requires_hermes` is `>=0.21.4`. `manifest_version` is 2. `python_dependencies` is empty. The license is MIT. There is no NOTICE file. When `__package__` is empty, imports are flat. No hooks are registered. Private Hermes modules (`cron.jobs`, `tools.approval`, `tools.approval_context`, the write guard, and `plugin_data_dir`) fail closed when they cannot be loaded. This repository ships `tests/test_plugin.py`. Hermes does not load that file. It is not a tool, and a watch does not run it.
