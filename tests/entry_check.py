"""Call kokkai tools through model_tools.handle_function_call. No socket."""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path


def main() -> None:
    home = Path(sys.argv[1])
    plugin = Path(sys.argv[2])
    source = Path(sys.argv[3])
    os.environ["HERMES_HOME"] = str(home)
    os.environ["HERMES_DISABLE_UPDATE_CHECK"] = "1"
    sys.path.insert(0, str(plugin))
    sys.path.insert(1, str(source))

    # __init__.py loaded as a package would set __package__ and break the flat import.
    mod = types.ModuleType("jp_kokkai_entry")
    mod.__package__ = ""
    mod.__file__ = str(plugin / "__init__.py")
    sys.modules["jp_kokkai_entry"] = mod
    code = compile((plugin / "__init__.py").read_text(encoding="utf-8"), mod.__file__, "exec")
    exec(code, mod.__dict__)

    from tools.registry import registry
    import model_tools
    import client

    skills: list[dict[str, str]] = []

    class Ctx:
        def register_skill(self, name, path, description=""):
            skills.append({"name": name, "path": str(path), "description": description})

        def register_tool(self, name, toolset, schema, handler, **kwargs):
            registry.register(name=name, toolset=toolset, schema=schema, handler=handler)

    mod.register(Ctx())
    if not skills or skills[0]["name"] != "read-meetings":
        raise SystemExit("skill was not registered")
    if "does not return source" not in skills[0]["description"]:
        raise SystemExit("skill description still promises source on every tool")
    if "meetingURL" not in skills[0]["description"]:
        raise SystemExit("skill description does not name meetingURL")
    skill_text = Path(skills[0]["path"]).read_text(encoding="utf-8")
    if "条文" in skill_text or "does not return `source`" not in skill_text:
        raise SystemExit("skill file does not match the registered text")

    watch = registry.get_entry("kokkai_watch")
    search_entry = registry.get_entry("kokkai_search")
    if watch is None or search_entry is None:
        raise SystemExit("tools were not registered")
    watch_text = watch.schema["description"]
    search_text = search_entry.schema["description"]
    if "does not return source" not in watch_text or "state.json" not in watch_text:
        raise SystemExit("watch description does not match the write")
    if "read-only except" in watch_text or "source.retrieved_at and source.url" in watch_text:
        raise SystemExit("watch description still promises source or read-only")
    if "source.url is only the first meeting URL" not in search_text:
        raise SystemExit("search description does not limit source.url")

    def call(name: str, args: dict) -> dict:
        raw = model_tools.handle_function_call(name, args)
        if not isinstance(raw, str):
            raise SystemExit(f"{name} returned {type(raw).__name__}")
        return json.loads(raw)

    long_query = call("kokkai_search", {"any": "あ" * 2000})
    if long_query.get("field") != "arguments" or long_query.get("problem") != "too_long":
        raise SystemExit("2000-byte refusal missing field or problem: " + json.dumps(long_query, ensure_ascii=False)[:300])
    if long_query.get("expected") != "at most 2000 bytes":
        raise SystemExit("2000-byte expected mismatch")
    if long_query.get("allowed_fields") != ["any", "speaker", "house", "from", "until", "maximum_records"]:
        raise SystemExit("2000-byte allowed_fields mismatch")
    if "あ" in json.dumps(long_query, ensure_ascii=False):
        raise SystemExit("2000-byte refusal echoed the query")

    extra = call("kokkai_watch", {
        "action": "add", "any": "科学技術", "hour": 9, "deliver": "local", "watch_id": "a" * 32,
    })
    allowed = extra.get("allowed_fields")
    if allowed != ["action", "any", "speaker", "house", "hour", "deliver", "lookback_days"]:
        raise SystemExit("add allowed_fields mismatch: " + json.dumps(allowed, ensure_ascii=False))
    if "watch_id" in allowed or "a" * 32 in json.dumps(extra, ensure_ascii=False):
        raise SystemExit("add refusal still accepts or echoes watch_id")
    missing = call("kokkai_watch", {"action": "add", "any": "科学技術", "deliver": "local"})
    if missing.get("field") != "hour" or missing.get("problem") != "missing":
        raise SystemExit("missing hour: " + json.dumps(missing, ensure_ascii=False)[:300])
    if missing.get("allowed_fields") != allowed:
        raise SystemExit("missing hour allowed_fields mismatch")
    speaker = call("kokkai_watch", {
        "action": "add", "any": "科学技術", "speaker": 7, "hour": 9, "deliver": "local",
    })
    if speaker.get("field") != "speaker" or speaker.get("problem") != "not_a_string":
        raise SystemExit("speaker type: " + json.dumps(speaker, ensure_ascii=False)[:300])

    listed = call("kokkai_watch", {"action": "list"})
    if "source" in listed:
        raise SystemExit("list returned source")

    def fake(pairs, transport=None):
        return {
            "zero": False,
            "missing_speech_id": False,
            "number_of_records": 2,
            "meetings": [
                {
                    "nameOfHouse": "衆議院",
                    "nameOfMeeting": "本会議",
                    "date": "2024-06-03",
                    "meetingURL": "https://kokkai.ndl.go.jp/txt/first",
                    "issue": "1",
                    "session": "213",
                    "speechRecord": [{"speaker": "山田", "speechID": "s1"}],
                },
                {
                    "nameOfHouse": "衆議院",
                    "nameOfMeeting": "委員会",
                    "date": "2024-06-03",
                    "meetingURL": "https://kokkai.ndl.go.jp/txt/second",
                    "issue": "2",
                    "session": "213",
                    "speechRecord": [{"speaker": "佐藤", "speechID": "s2"}],
                },
            ],
            "speech_ids": ["s1", "s2"],
            "can_baseline": True,
        }

    client.fetch = fake
    found = call("kokkai_search", {"any": "科学技術"})
    if found.get("ok") is not True or "source" not in found:
        raise SystemExit("search success missing source")
    if found["source"]["url"] != "https://kokkai.ndl.go.jp/txt/first":
        raise SystemExit("source.url is not the first meeting")
    urls = [row.get("meetingURL") for row in found["meetings"]]
    if urls != ["https://kokkai.ndl.go.jp/txt/first", "https://kokkai.ndl.go.jp/txt/second"]:
        raise SystemExit("meetings did not keep their own URLs")
    if not str(found["source"]["retrieved_at"]).endswith("Z") or not found["source"].get("as_of"):
        raise SystemExit("source time fields missing")
    print("ok")


if __name__ == "__main__":
    main()
