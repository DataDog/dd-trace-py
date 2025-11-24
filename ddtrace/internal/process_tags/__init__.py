import os
from pathlib import Path
import re
import sys
from typing import Callable
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.process_tags import process_tags_config as config


log = get_logger(__name__)

ENTRYPOINT_NAME_TAG = "entrypoint.name"
ENTRYPOINT_WORKDIR_TAG = "entrypoint.workdir"
ENTRYPOINT_TYPE_TAG = "entrypoint.type"
ENTRYPOINT_TYPE_SCRIPT = "script"
ENTRYPOINT_BASEDIR_TAG = "entrypoint.basedir"

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


def generate_process_tags() -> Tuple[Optional[str], Optional[List[str]]]:
    if not config.enabled:
        return None, None

    tag_definitions = [
        (ENTRYPOINT_WORKDIR_TAG, lambda: os.path.basename(os.getcwd())),
        (ENTRYPOINT_BASEDIR_TAG, lambda: Path(sys.argv[0]).resolve().parent.name),
        (ENTRYPOINT_NAME_TAG, lambda: os.path.splitext(os.path.basename(sys.argv[0]))[0]),
        (ENTRYPOINT_TYPE_TAG, lambda: ENTRYPOINT_TYPE_SCRIPT),
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


process_tags, process_tags_list = generate_process_tags()
