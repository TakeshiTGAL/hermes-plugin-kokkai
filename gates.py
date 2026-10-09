"""Fail-closed approval, delivery, profile, and file gates."""
from __future__ import annotations

import importlib
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

PLUGIN_NAME = "jp-kokkai"
HOST_PROCESS_ENV = "HERMES_PLUGIN_HOST_PROCESS"
REASON_BUDGET = 300
PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
NEVER_DELIVER = frozenset({"cli", "cron", "api_server"})
DELIVER_EXACT = frozenset({"origin", "local", "all"})
SCHEDULE_WORDS = frozenset({
    "every", "in", "at", "on", "daily", "hourly", "weekly", "monthly", "yearly", "annually",
    "once", "now", "today", "tonight", "tomorrow", "midnight", "noon", "minute", "minutes",
    "hour", "hours", "day", "days", "week", "weeks", "weekdays", "weekends", "weekday", "weekend",
})
WEEKDAYS = frozenset({
    "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
})
_DURATION = re.compile(r"(\d*)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\Z", re.IGNORECASE)


def in_plugin_host_process() -> bool:
    """True when the variable is set to anything other than empty or 0."""
    return os.environ.get(HOST_PROCESS_ENV, "").strip() not in {"", "0"}


def _load(module: str, name: str) -> tuple[str, Any]:
    try:
        mod = importlib.import_module(module)
    except Exception:
        return "failed", None
    if not hasattr(mod, name):
        return "missing", None
    try:
        return "ok", getattr(mod, name)
    except Exception:
        return "failed", None


def reason_fits(text: str) -> bool:
    if len(text) > REASON_BUDGET:
        return False
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return len(escaped) <= REASON_BUDGET


def one_call_rule_key(action: str) -> str:
    return f"kokkai_watch:{action}:{uuid.uuid4()}"


def approval_block_reason() -> str | None:
    if in_plugin_host_process():
        return (
            "BLOCKED: this plugin is running in a separate plugin-host process "
            "(plugins.isolation: host). Adding or removing a watch is refused there."
        )
    checks = (
        (
            "tools.approval_context",
            "_is_cron_approval_context",
            "a cron job",
            "BLOCKED: a cron job cannot add or remove a watch.",
        ),
        (
            "tools.approval",
            "_yolo_active",
            "yolo",
            "BLOCKED: Hermes yolo is on, so the approval gate would not ask a person.",
        ),
        (
            "tools.approval_context",
            "_get_approval_mode",
            "approvals.mode",
            "BLOCKED: Hermes approvals are off, so nobody would be asked.",
        ),
        (
            "tools.approval_context",
            "_is_single_query_approval_context",
            "a single-query session",
            "BLOCKED: a single-query session (hermes -q) cannot add or remove a watch.",
        ),
        (
            "tools.approval_context",
            "_is_unattended_platform_approval_context",
            "an unattended session",
            "BLOCKED: an unattended session cannot add or remove a watch.",
        ),
    )
    for module, name, label, blocked in checks:
        status, fn = _load(module, name)
        if status != "ok":
            return f"BLOCKED: could not check {label}, so the watch was not changed."
        try:
            value = fn()
        except Exception:
            return f"BLOCKED: the check for {label} failed, so the watch was not changed."
        if name == "_get_approval_mode":
            if value == "off":
                return blocked
        elif value:
            return blocked
    return None


def request_watch_approval(action: str, text: str) -> tuple[bool, str]:
    reason = approval_block_reason()
    if reason:
        return False, reason
    if not reason_fits(text):
        return False, (
            "BLOCKED: the approval question would not fit in one message, so it was not sent "
            "and the watch was not changed."
        )
    status, fn = _load("tools.approval", "request_tool_approval")
    if status != "ok":
        return False, "BLOCKED: Hermes approval could not be loaded, so the watch was not changed."
    rule_key = one_call_rule_key(action)
    try:
        result = fn("kokkai_watch", text, rule_key=rule_key)
    except Exception:
        return False, "BLOCKED: the Hermes approval request failed, so the watch was not changed."
    if not isinstance(result, dict) or result.get("approved") is not True:
        message = str(result.get("message") or "") if isinstance(result, dict) else ""
        return False, message or "BLOCKED: the watch change was not approved."
    return True, ""


def judge_cron() -> str:
    """Read the cron helper once. host, unreadable, cron, or manual."""
    if in_plugin_host_process():
        return "host"
    status, fn = _load("tools.approval_context", "_is_cron_approval_context")
    if status != "ok":
        return "unreadable"
    try:
        value = fn()
    except Exception:
        return "unreadable"
    return "cron" if value else "manual"


def write_guard_error(path: str) -> str | None:
    status, fn = _load("agent.file_safety", "get_write_denied_error")
    if status != "ok":
        return "BLOCKED: Hermes's write guard could not be loaded, so the file was not saved."
    try:
        denied = fn(path)
    except Exception:
        return "BLOCKED: Hermes's write guard raised, so the file was not saved."
    if denied:
        return str(denied)
    return None


def plugin_data_dir() -> Path:
    status, fn = _load("plugins.plugin_storage", "plugin_data_dir")
    if status != "ok":
        raise RuntimeError("plugin_data_dir is not available")
    path = fn(PLUGIN_NAME)
    if not isinstance(path, Path):
        path = Path(path)
    return path


# Same files Hermes uses, on v0.21.4 and main, to decide a profiles/<name>
# directory is a profile (hermes_constants._PROFILE_IDENTITY_MARKERS).
_PROFILE_IDENTITY_MARKERS = (
    "config.yaml",
    ".env",
    "SOUL.md",
    "profile.yaml",
    "auth.json",
    "state.db",
)


def _has_profile_identity(home: Path) -> bool:
    try:
        resolved = home.resolve()
    except OSError:
        return False
    for marker in _PROFILE_IDENTITY_MARKERS:
        path = resolved / marker
        try:
            if path.is_symlink() or path.is_file():
                return True
        except OSError:
            continue
    return False


def named_profile_for_commands(reported: str, home: Path, profiles_root: Path) -> str:
    """Map Hermes's label onto a name `hermes -p` can select.

    `get_active_profile_name` returns ``custom`` for a real ``profiles/custom``
    directory and also for every home that is not a profile directory. The
    second one is not a ``-p`` argument. ``hermes -p custom`` then exits with
    "Profile 'custom' does not exist" unless that directory already has an
    identity file.
    """
    if not isinstance(reported, str) or not PROFILE_NAME.fullmatch(reported.strip()):
        return "default"
    name = reported.strip()
    if name != "custom":
        return name
    try:
        parts = home.resolve().relative_to(profiles_root.resolve()).parts
    except Exception:
        return "default"
    if len(parts) == 1 and parts[0] == "custom" and _has_profile_identity(home):
        return "custom"
    return "default"


def active_profile_name() -> str:
    try:
        from hermes_cli.profiles import get_active_profile_name
        reported = get_active_profile_name()
    except Exception:
        return "default"
    if not isinstance(reported, str) or not PROFILE_NAME.fullmatch(reported.strip()):
        return "default"
    name = reported.strip()
    if name != "custom":
        return name
    try:
        from hermes_constants import get_hermes_home
        from hermes_cli.profiles import _get_profiles_root
        return named_profile_for_commands(name, get_hermes_home(), _get_profiles_root())
    except Exception:
        return "default"


def hermes_cron_lines(profile: str | None = None) -> list[str]:
    name = active_profile_name() if profile is None else profile
    prefix = "hermes" if name == "default" else f"hermes -p {name}"
    return [f"{prefix} cron {verb}" for verb in ("create", "run", "list", "status", "remove")]


def load_platforms() -> set[str] | None:
    """None when the Hermes delivery list cannot be imported. Registry errors are ignored."""
    status, raw = _load("cron.scheduler_delivery", "_KNOWN_DELIVERY_PLATFORMS")
    if status != "ok":
        return None
    try:
        names = {str(item).strip().lower() for item in raw if str(item).strip()}
    except Exception:
        return None
    try:
        from gateway.platform_registry import platform_registry
        for entry_name in platform_registry.registered_names():
            text = str(entry_name).strip().lower()
            entry = platform_registry.get(text)
            if text and entry is not None and getattr(entry, "cron_deliver_env_var", None):
                names.add(text)
    except Exception:
        pass
    return names - NEVER_DELIVER


def _duration(text: str) -> bool:
    return _DURATION.fullmatch(text.strip()) is not None


def _looks_like_schedule(target: str) -> bool:
    word = target.strip().lower()
    if not word:
        return False
    if word in SCHEDULE_WORDS or word in WEEKDAYS or word.rstrip("s") in WEEKDAYS:
        return True
    if word[0].isdigit() or word[0] in "*@":
        return True
    if "/" in word or _duration(word):
        return True
    try:
        datetime.fromisoformat(word.replace("z", "+00:00"))
    except ValueError:
        return False
    return True


def canonical_deliver(deliver: str, platforms: set[str] | None = None) -> str | None:
    if platforms is None:
        platforms = load_platforms()
    if platforms is None:
        return None
    canon: list[str] = []
    for part in str(deliver).split(","):
        piece = part.strip()
        if not piece or any(ch.isspace() for ch in piece) or _looks_like_schedule(piece):
            return None
        low = piece.lower()
        if low in NEVER_DELIVER:
            return None
        if low in DELIVER_EXACT or low == "bot-chat":
            canon.append(low)
            continue
        if low.startswith("bot-chat:"):
            chat = piece.split(":", 1)[1].strip()
            if not chat or any(ch.isspace() for ch in chat):
                return None
            canon.append(f"bot-chat:{chat}")
            continue
        platform, sep, chat = piece.partition(":")
        key = platform.lower()
        if key not in platforms:
            return None
        if not sep:
            canon.append(key)
            continue
        if not chat or any(ch.isspace() for ch in chat):
            return None
        canon.append(f"{key}:{chat}")
    return ",".join(canon) if canon else None
