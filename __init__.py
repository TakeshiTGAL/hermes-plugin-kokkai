"""Diet meeting metadata and speech excerpts for Hermes. Speech text is not stored."""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SKILL = Path(__file__).resolve().parent / "skills" / "read-meetings" / "SKILL.md"
_SKILL_DESCRIPTION = (
    "Before web_search, use tool_search to find Japan Diet and parliament meeting tools and tool_call to run them. "
    "Diet meeting metadata from kokkai_search and kokkai_watch. "
    "Who said what, with a quote, comes from kokkai_speech. "
    "A search result includes each meeting's meetingURL, source.retrieved_at, source.as_of, and source.attribution. "
    "source.url is the first meeting URL, or the API page when that page has no meeting URL. "
    "kokkai_watch does not return source."
)
# Hermes filters <available_skills> from this argument, not from the SKILL.md file.
_SKILL_FRONTMATTER = {"metadata": {"hermes": {"requires_toolsets": ["jp_kokkai"]}}}


def register(ctx) -> None:
    if __package__:
        from .service import TOOLSET, search, speech, watch
        from .service import Deps
    else:
        from service import TOOLSET, search, speech, watch
        from service import Deps

    def _search(args, **_kwargs):
        return search(Deps(), args or {})

    def _speech(args, **_kwargs):
        return speech(Deps(), args or {})

    def _watch(args, **_kwargs):
        return watch(Deps(), args or {})

    ctx.register_tool(
        name="kokkai_search",
        toolset=TOOLSET,
        schema={
            "name": "kokkai_search",
            "description": (
                "Japan Diet meetings: list hearings and their URLs. "
                "source.url is only the first meeting URL, or the API page when the page has none. "
                "Read-only. "
                "If you did not call this tool, say you did not search and do not quote speech from memory. "
                "The count and the credit line come first. Speech rows are not returned. "
                "The API returns the same meetings for 両院 and 両院協議会. "
                "If numberOfRecords is greater than the page, the call fails and returns no meetings. "
                "That failure is not 0 meetings. Narrow the query. "
                "Speech text is not requested, returned, stored, or logged. "
                "maximum_records is 1 to 30 and defaults to 10. "
                "National Diet of Japan: parliament, parliamentary House of Representatives. Rows are meetings, not minutes. "
                "house must be 衆議院, 参議院, 両院, or 両院協議会. "
                "from and until must be passed together as YYYY-MM-DD. "
                "any keeps spaces and is an AND search. speaker keeps spaces and is an OR search. "
                "This tool does not ask for approval and does not mark speech IDs seen. "
                "A speaker may be a minister in a debate."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "any": {"type": "string", "description": "Space-separated words. All words are required. Spaces are kept."},
                    "speaker": {"type": "string", "description": "Space-separated names. Any name may match. Spaces are kept."},
                    "house": {"type": "string", "description": "衆議院, 参議院, 両院, or 両院協議会."},
                    "from": {"type": "string", "description": "Start date YYYY-MM-DD. Required together with until."},
                    "until": {"type": "string", "description": "End date YYYY-MM-DD. Required together with from."},
                    "maximum_records": {"type": "integer", "description": "1 to 30. Default 10. Cannot be 101."},
                },
            },
        },
        handler=_search,
    )
    ctx.register_tool(
        name="kokkai_speech",
        toolset=TOOLSET,
        schema={
            "name": "kokkai_speech",
            "description": (
                "Japan Diet speeches: who said what, with quotes. "
                "kokkai_speech returns an excerpt, the speaker, the meeting name, the date, and the speech URL. "
                "The total count and the truncated mark come first. "
                "An excerpt is at most 240 characters, and at most 10 speeches are shown. "
                "source.url is only the first speech URL, or the API page when the page has none. "
                "Read speechURL on each speech. "
                "If you did not call this tool, say you did not search and do not quote a speech from memory. "
                "A minister, a committee remark, or what someone said uses this tool. "
                "kokkai_search lists meetings and does not return speech text. "
                "speaker keeps spaces and is an OR search. any keeps spaces and is an AND search. "
                "Pass speaker or any. from and until must be passed together as YYYY-MM-DD. "
                "house must be 衆議院, 参議院, 両院, or 両院協議会. "
                "maximum_records is 1 to 10 and defaults to 5. "
                "The next request waits 5 seconds. Speech text is not stored. "
                "Copyright in a speech stays with the speaker, except National Diet Library staff speeches."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "any": {"type": "string", "description": "Space-separated words. All words are required. Spaces are kept."},
                    "speaker": {"type": "string", "description": "Space-separated names. Any name may match. Spaces are kept."},
                    "house": {"type": "string", "description": "衆議院, 参議院, 両院, or 両院協議会."},
                    "from": {"type": "string", "description": "Start date YYYY-MM-DD. Required together with until."},
                    "until": {"type": "string", "description": "End date YYYY-MM-DD. Required together with from."},
                    "maximum_records": {"type": "integer", "description": "1 to 10. Default 5."},
                },
            },
        },
        handler=_speech,
    )
    ctx.register_tool(
        name="kokkai_watch",
        toolset=TOOLSET,
        schema={
            "name": "kokkai_watch",
            "description": (
                "Japan Diet watch: new meetings for one saved query. It does not return source. "
                "list reads saved watches and does not call the meeting API. "
                "add and remove write the watch file and ask for approval. "
                "run writes seen speech IDs to state.json when the caller is the cron job, and does not write them on a manual run. "
                "If you did not call this tool, say you did not fetch meetings. "
                "Calling the tool with no arguments is the same as action run. "
                "Any other argument without an action is rejected. "
                "add and remove ask Hermes for approval on every call. "
                "A cron run marks new speech IDs seen. A manual run returns the same new IDs and does not mark them seen. "
                "If the saved cron job is gone, list, run, and remove say the job is gone and do not show that id as running. "
                "add creates a new job and says the saved one was gone. "
                "Under a plugin host process, or when the cron check cannot be read, every run requests nothing and says seen IDs cannot be saved. "
                "Saving 両院 and 両院協議会 can announce the same speech twice. "
                "Speech text is not included."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {"type": "string", "enum": ["add", "remove", "list", "run"]},
                    "any": {"type": "string"},
                    "speaker": {"type": "string"},
                    "house": {"type": "string"},
                    "hour": {"type": "integer", "description": "0 through 23. Required for add."},
                    "deliver": {"type": "string", "description": "Required for add. Not a schedule word."},
                    "lookback_days": {"type": "integer", "description": "1 to 30. Default 30."},
                    "watch_id": {"type": "string", "description": "32 lowercase hex characters. Required for remove."},
                },
            },
        },
        handler=_watch,
    )
    try:
        ctx.register_skill(
            "read-meetings",
            _SKILL,
            description=_SKILL_DESCRIPTION,
            frontmatter=_SKILL_FRONTMATTER,
        )
    except Exception:
        logger.warning("jp-kokkai skill was not registered", exc_info=True)
