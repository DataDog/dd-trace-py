import struct
import threading

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.process_tags import process_tags_config
from ddtrace.internal.utils.cache import callonce
from ddtrace.internal.utils.fnv import fnv1_64

from . import process_tags as process_tags_module
from .constants import INFO_RETRY_MAX_ATTEMPTS


log = get_logger(__name__)


@callonce
def _retrieve_container_tags_hash() -> None:
    if not process_tags_config.enabled:
        return

    def _fetch():
        from ddtrace.internal import agent
        from ddtrace.internal.utils.retry import retry

        @retry(after=[0.5] * (INFO_RETRY_MAX_ATTEMPTS - 1))
        def _fetch_info():
            agent.info()

        try:
            _fetch_info()
        except Exception:
            log.debug("failed to fetch container tags hash from agent /info endpoint", exc_info=True)

    t = threading.Thread(target=_fetch, daemon=True)
    t.start()


def _recompute_base_hash() -> None:
    if not process_tags_config.enabled:
        return

    global base_hash
    global base_hash_bytes

    process_tags = process_tags_module.process_tags
    if process_tags is None:
        return

    b = bytes(process_tags, encoding="utf-8") + bytes(_container_tags_hash, encoding="utf-8")
    base_hash = fnv1_64(b)
    base_hash_bytes = struct.pack("<Q", base_hash)


def compute_base_hash(container_tags_hash):
    if not process_tags_config.enabled:
        return

    global _container_tags_hash
    if _container_tags_hash == container_tags_hash:
        return
    _container_tags_hash = container_tags_hash
    _recompute_base_hash()


base_hash, base_hash_bytes = None, b""
_container_tags_hash = ""

core.on("container_tags_hash.retrieved", compute_base_hash)
