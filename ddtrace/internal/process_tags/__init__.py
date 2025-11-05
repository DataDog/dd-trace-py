import os
from pathlib import Path
import re
import sys
from typing import Callable
from typing import Dict
from typing import Optional

from ddtrace.internal.forksafe import Lock
from ddtrace.internal.logger import get_logger
from ddtrace.settings._config import config

from .constants import ENTRYPOINT_BASEDIR_TAG
from .constants import ENTRYPOINT_NAME_TAG
from .constants import ENTRYPOINT_TYPE_SCRIPT
from .constants import ENTRYPOINT_TYPE_TAG
from .constants import ENTRYPOINT_WORKDIR_TAG


log = get_logger(__name__)


# outside of ProcessTags class for test purpose
def normalize_tag(value: str) -> str:
    return re.sub(r"[^a-z0-9/._-]", "_", value.lower())


class ProcessTags:
    def __init__(self) -> None:
        self._lock = Lock()
        self._serialized: Optional[str] = None
        self._enabled = config._process_tags_enabled
        self._process_tags: Dict[str, str] = {}
        self.reload()

    def _serialize_process_tags(self) -> Optional[str]:
        if self._process_tags and not self._serialized:
            serialized_tags = ",".join(f"{key}:{value}" for key, value in self._process_tags.items())
            return serialized_tags
        return None

    def get_serialized_process_tags(self) -> Optional[str]:
        if not self._enabled:
            return None

        with self._lock:
            if not self._serialized:
                self._serialized = self._serialize_process_tags()
            return self._serialized

    def add_process_tag(self, key: str, value: Optional[str] = None, compute: Optional[Callable[[], str]] = None):
        if not self._enabled:
            return

        if compute:
            try:
                value = compute()
            except Exception as e:
                log.debug("failed to set %s process_tag: %s", key, e)

        if value:
            with self._lock:
                self._process_tags[key] = normalize_tag(value)
                self._serialized = None

    def reload(self):
        if not self._enabled:
            return

        with self._lock:
            self._process_tags = {}

        self.add_process_tag(ENTRYPOINT_WORKDIR_TAG, compute=lambda: os.path.basename(os.getcwd()))
        self.add_process_tag(ENTRYPOINT_BASEDIR_TAG, compute=lambda: Path(sys.argv[0]).resolve().parent.name)
        self.add_process_tag(ENTRYPOINT_NAME_TAG, compute=lambda: os.path.splitext(os.path.basename(sys.argv[0]))[0])
        self.add_process_tag(ENTRYPOINT_TYPE_TAG, value=ENTRYPOINT_TYPE_SCRIPT)


process_tags = ProcessTags()
