"""Diet meeting metadata for Hermes. Speech text is not requested or returned."""


def register(ctx) -> None:
    if __package__:
        from .service import TOOLSET, search, watch
        from .service import Deps
    else:
        from service import TOOLSET, search, watch
        from service import Deps

    def _search(args, **_kwargs):
        return search(Deps(), args or {})

    def _watch(args, **_kwargs):
        return watch(Deps(), args or {})

    ctx.register_tool(
        name="kokkai_search",
        toolset=TOOLSET,
        schema={
            "name": "kokkai_search",
            "description": (
                "Fetch one summary per Diet meeting and the official meeting URL only when the whole result fits in one page. "
                "The count and the credit line come first. Speech rows are not returned. "
                "If numberOfRecords is greater than the page, the call fails and returns no meetings. "
                "That failure is not 0 meetings. Narrow the query. "
                "Speech text is not requested, returned, stored, or logged. "
                "Japanese names are copied as the API sent them. "
                "maximum_records is 1 to 30 and defaults to 10. "
                "house must be 衆議院, 参議院, 両院, or 両院協議会. "
                "The API returns the same meetings for 両院 and 両院協議会. "
                "from and until must be passed together as YYYY-MM-DD. "
                "any keeps spaces and is an AND search. speaker keeps spaces and is an OR search. "
                "This tool does not ask for approval and does not mark speech IDs seen."
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
        name="kokkai_watch",
        toolset=TOOLSET,
        schema={
            "name": "kokkai_watch",
            "description": (
                "Watch Diet meeting metadata. action is add, remove, list, or run. "
                "Calling the tool with no arguments is the same as action run. "
                "Any other argument without an action is rejected. "
                "add and remove ask Hermes for approval on every call. "
                "A cron run marks new speech IDs seen. A manual run returns the same new IDs and does not mark them seen. "
                "If the saved cron job is gone, list, run, and remove say the job is gone and do not show that id as running. "
                "add creates a new job and says the saved one was gone. "
                "Under a plugin host process, or when the cron check cannot be read, every run requests nothing and says seen IDs cannot be saved. "
                "That notice is sent every time and is not held for 23 hours. "
                "Saving both 両院 and 両院協議会 can announce the same new speech twice. "
                "Unknown actions and extra arguments are rejected before any request. "
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
