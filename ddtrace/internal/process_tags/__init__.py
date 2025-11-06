import os
from pathlib import Path
import re
import sys
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.settings._config import config

from .constants import ENTRYPOINT_BASEDIR_TAG
from .constants import ENTRYPOINT_NAME_TAG
from .constants import ENTRYPOINT_TYPE_SCRIPT
from .constants import ENTRYPOINT_TYPE_TAG
from .constants import ENTRYPOINT_WORKDIR_TAG


log = get_logger(__name__)


def normalize_tag(value: str) -> str:
    return re.sub(r"[^a-z0-9/._-]", "_", value.lower())


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


# For test purpose
def _process_tag_reload():
    global process_tags
    process_tags = generate_process_tags()

    # Force update in the processor module for testing
    import sys

    if "ddtrace._trace.processor" in sys.modules:
        processor_module = sys.modules["ddtrace._trace.processor"]
        processor_module.process_tags = process_tags


process_tags = generate_process_tags()
