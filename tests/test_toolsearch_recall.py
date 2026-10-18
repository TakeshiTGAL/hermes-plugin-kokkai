"""Ten English requests rank this book in Tool Search top 5, beside Hermes core tools."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

QUERIES = ['Japan Diet speech', 'parliament minutes', 'Diet meeting hearings', 'National Diet of Japan', 'Diet speaker search', 'parliamentary hearing Japan', 'new Diet meetings', 'House of Representatives meeting', 'Diet session minutes', 'Japan parliament meeting URL']

_VENV = Path.home() / "takeshitgal/.harness/venv/v0222/bin/python"
_HERMES = Path.home() / "takeshitgal/.harness/src/v0222"


class _Ctx:
    def __init__(self):
        self.tools = []

    def register_tool(self, *args, **kwargs):
        schema = kwargs.get("schema")
        name = kwargs.get("name")
        toolset = kwargs.get("toolset")
        if args:
            name = name or args[0]
            if len(args) >= 2 and toolset is None:
                toolset = args[1]
            if len(args) >= 3 and schema is None:
                schema = args[2]
        if isinstance(schema, dict):
            name = name or schema.get("name")
            schema = dict(schema)
            schema["name"] = name
        if name and isinstance(schema, dict):
            self.tools.append({"name": name, "toolset": toolset or "", "schema": schema})

    def __getattr__(self, _name):
        def _noop(*_a, **_k):
            return None
        return _noop


def _load():
    root = Path(__file__).resolve().parents[1]
    key = "recall_" + root.name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(
        key, root / "__init__.py", submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    ctx = _Ctx()
    module.register(ctx)
    return ctx.tools


def _measure():
    os.environ.setdefault(
        "HERMES_HOME", str(Path.home() / "takeshitgal/ship/_toolsearch/_empty_home"))
    sys.path.insert(0, str(_HERMES))
    from toolsets import _HERMES_CORE_TOOLS
    import model_tools  # noqa: F401
    from tools.registry import registry
    from tools.tool_search_catalog import build_catalog, search_catalog

    tools = _load()
    core_names = set(_HERMES_CORE_TOOLS)
    core = []
    for entry in registry.get_all_entries():
        if entry.name in core_names:
            core.append({"type": "function", "function": {**entry.schema, "name": entry.name}})
    for tool in tools:
        registry.register(
            name=tool["name"], toolset=tool["toolset"] or "plugin", schema=tool["schema"],
            handler=lambda **_k: "", override=True)
    catalog = build_catalog(
        core + [{"type": "function", "function": tool["schema"]} for tool in tools])
    own = {tool["name"] for tool in tools}
    hits = 0
    for query in QUERIES:
        ranked = [item.name for item in search_catalog(catalog, query, limit=5)]
        ok = any(name in own for name in ranked)
        hits += int(ok)
        print(("HIT" if ok else "MISS"), query, ranked)
    print(f"{hits}/10")
    return hits


def test_ten_queries_in_top_five():
    proc = subprocess.run([str(_VENV), str(Path(__file__).resolve())], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "10/10"


if __name__ == "__main__":
    raise SystemExit(0 if _measure() == 10 else 1)
