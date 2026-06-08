import os
from pathlib import Path
import re
import struct
import sys
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.process_tags import process_tags_config
from ddtrace.internal.utils.fnv import fnv1_64


log = get_logger(__name__)

ENTRYPOINT_NAME_TAG = "entrypoint.name"
ENTRYPOINT_WORKDIR_TAG = "entrypoint.workdir"
ENTRYPOINT_TYPE_TAG = "entrypoint.type"
ENTRYPOINT_TYPE_SCRIPT = "script"
ENTRYPOINT_TYPE_MODULE = "module"
ENTRYPOINT_BASEDIR_TAG = "entrypoint.basedir"
SVC_USER_TAG = "svc.user"
SVC_AUTO_TAG = "svc.auto"

_CONSECUTIVE_UNDERSCORES_PATTERN = re.compile(r"_{2,}")
_ALLOWED_CHARS = _ALLOWED_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789/._-")
MAX_LENGTH = 100


def normalize_tag_value(value: str) -> str:
    # we copy the behavior of the agent which
    # checks the size on the original value and not on
    # an intermediary normalized step
    if len(value) > MAX_LENGTH:
        value = value[:MAX_LENGTH]

    result = value.lower()

    def is_allowed_char(char: str) -> str:
        # ASCII alphanumeric and special chars: / : . _ -
        if char in _ALLOWED_CHARS:
            return char
        # Unicode letters and digits
        if char.isalpha() or char.isdigit():
            return char
        return "_"

    result = "".join(is_allowed_char(char) for char in result)
    result = _CONSECUTIVE_UNDERSCORES_PATTERN.sub("_", result)
    return result.strip("_")


def _compute_process_tag(key: str, compute_value: Callable):
    try:
        value = compute_value()
        if value and value not in ("/", "\\", "bin"):
            return normalize_tag_value(value)
        return None
    except Exception as e:
        log.debug("failed to get process tag %s : %s", key, e)
        return None


def _get_entrypoint_name() -> str:
    argv0 = sys.argv[0]
    if argv0 == "-m":
        # When executing a python program like `python -m myapp`, sys.argv
        # can be only ["-m"] so we are using sys.orig_argv when available (python3.9+)
        orig_argv = getattr(sys, "orig_argv", None)
        if isinstance(orig_argv, list):
            for i, arg in enumerate(orig_argv[:-1]):
                if arg == "-m" and orig_argv[i + 1]:
                    return orig_argv[i + 1]

        # In python3.9, without access to sys.orig_argv, we fallback to __main__
        main_module = sys.modules.get("__main__")
        return getattr(main_module, __name__, "__main__")

    # Handle compound command strings (e.g. "python -m unittest") passed as a
    # single sys.argv[0] by Docker ENTRYPOINT, subprocess, or process managers.
    if " " in argv0:
        parts = argv0.split()
        for i, part in enumerate(parts[:-1]):
            if part == "-m" and parts[i + 1]:
                return parts[i + 1]
        non_flags = [p for p in parts if not p.startswith("-")]
        if len(non_flags) > 1:  # non_flags[0] is the interpreter; [1] is the script
            return os.path.splitext(os.path.basename(non_flags[1]))[0]

    return os.path.splitext(os.path.basename(argv0))[0]


def _get_entrypoint_type() -> str:
    if sys.argv and sys.argv[0] == "-m":
        return ENTRYPOINT_TYPE_MODULE
    if sys.argv and " " in sys.argv[0]:
        parts = sys.argv[0].split()
        for part in parts[1:]:  # skip interpreter at index 0
            if part == "-m":
                return ENTRYPOINT_TYPE_MODULE
            if not part.startswith("-"):
                break  # reached the script target before seeing -m
    return ENTRYPOINT_TYPE_SCRIPT


def generate_process_tags() -> tuple[Optional[str], Optional[list[str]]]:
    if not process_tags_config.enabled:
        return None, None

    from ddtrace import config as ddtrace_config
    from ddtrace.internal.settings._inferred_base_service import detect_service

    tag_definitions = [
        (ENTRYPOINT_WORKDIR_TAG, lambda: os.path.basename(os.getcwd())),
        (ENTRYPOINT_BASEDIR_TAG, lambda: Path(sys.argv[0]).resolve().parent.name),
        (ENTRYPOINT_NAME_TAG, _get_entrypoint_name),
        (ENTRYPOINT_TYPE_TAG, _get_entrypoint_type),
        (SVC_USER_TAG, lambda: "true" if ddtrace_config._is_user_provided_service else None),
        (
            SVC_AUTO_TAG,
            lambda: (detect_service(sys.argv) or _get_entrypoint_name())
            if not ddtrace_config._is_user_provided_service
            else None,
        ),
    ]

    process_tags_list = sorted(
        [
            f"{key}:{value}"
            for key, compute_value in tag_definitions
            if (value := _compute_process_tag(key, compute_value)) is not None
        ]
    )

    # process_tags_list cannot be empty as one of the tag is a constant
    process_tags = ",".join(process_tags_list)
    return process_tags, process_tags_list


def _initialize_process_tags() -> None:
    global process_tags
    global process_tags_list
    process_tags, process_tags_list = generate_process_tags()  # type: ignore


def _recompute_base_hash() -> None:
    if not process_tags_config.enabled:
        return

    global base_hash
    global base_hash_bytes
    if "process_tags" not in globals():
        _initialize_process_tags()

    # if process tags are enabled, they cannot be None, that is why we add type: ignore
    b = bytes(process_tags, encoding="utf-8") + bytes(_container_tags_hash, encoding="utf-8")  # type: ignore
    base_hash = fnv1_64(b)
    base_hash_bytes = struct.pack("<Q", base_hash)


def compute_base_hash(container_tags_hash):
    if not process_tags_config.enabled:
        return

    global _container_tags_hash
    _container_tags_hash = container_tags_hash
    _recompute_base_hash()


base_hash, base_hash_bytes = None, b""
_container_tags_hash = ""


def __getattr__(name: str) -> Any:
    if name in ("process_tags", "process_tags_list"):
        _initialize_process_tags()
        _recompute_base_hash()
        if name == "process_tags":
            return process_tags  # type: ignore
        return process_tags_list  # type: ignore

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
