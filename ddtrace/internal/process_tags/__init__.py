import os
from pathlib import Path
import re
import sys
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._config import config


log = get_logger(__name__)

ENTRYPOINT_NAME_TAG = "entrypoint.name"
ENTRYPOINT_WORKDIR_TAG = "entrypoint.workdir"
ENTRYPOINT_TYPE_TAG = "entrypoint.type"
ENTRYPOINT_TYPE_SCRIPT = "script"
ENTRYPOINT_BASEDIR_TAG = "entrypoint.basedir"

_INVALID_CHARS_PATTERN = re.compile(r"[^a-z0-9/._-]")
_CONSECUTIVE_UNDERSCORES_PATTERN = re.compile(r"_{2,}")


def normalize_tag(value: str) -> str:
    normalized = _INVALID_CHARS_PATTERN.sub("_", value.lower())
    normalized = _CONSECUTIVE_UNDERSCORES_PATTERN.sub("_", normalized)
    return normalized.strip("_")


def generate_process_tags() -> Optional[str]:
    if not config._process_tags_enabled:
        return None

    try:
        return ",".join(
            f"{key}:{normalize_tag(value)}"
            for key, value in sorted(
                [
                    (ENTRYPOINT_WORKDIR_TAG, os.path.basename(os.getcwd())),
                    (ENTRYPOINT_BASEDIR_TAG, Path(sys.argv[0]).resolve().parent.name),
                    (ENTRYPOINT_NAME_TAG, os.path.splitext(os.path.basename(sys.argv[0]))[0]),
                    (ENTRYPOINT_TYPE_TAG, ENTRYPOINT_TYPE_SCRIPT),
                ]
            )
        )
    except Exception as e:
        log.debug("failed to get process_tags: %s", e)
        return None


def update_base_hash(new_base_hash):
    global base_hash
    if process_tags:
        base_hash = new_base_hash


base_hash = 0
process_tags = generate_process_tags()
