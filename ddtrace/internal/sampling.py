from hashlib import sha256
import re
from typing import TYPE_CHECKING

from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.cache import cached


log = get_logger(__name__)

if TYPE_CHECKING:
    from typing import Dict
    from typing import Optional
    from typing import Text

    from ddtrace.context import Context


class SamplingMechanism(object):
    UNKNOWN = -1
    DEFAULT = 0
    AGENT_RATE = 1
    REMOTE_RATE = 2
    TRACE_SAMPLING_RULE = 3
    MANUAL = 4
    APPSEC = 5
    REMOTE_RATE_USER = 6
    REMOTE_RATE_DATADOG = 7


SERVICE_HASH_MAX_LENGTH = 10
SAMPLING_DECISION_TRACE_TAG_KEY = "_dd.p.dm"

# Use regex to validate trace tag value
TRACE_TAG_RE = re.compile(r"^(|[0-9a-f]{10})-([0-8])$")


@cached()
def _hash_service(service):
    # type: (Text) -> Optional[Text]
    try:
        return sha256(service.encode("utf-8")).hexdigest()[:SERVICE_HASH_MAX_LENGTH]
    except Exception:
        log.warning("failed to hash service", exc_info=True)

    return None


def _set_trace_tag(
    context,  # type: Context
    service,  # type: Optional[Text]
    sampling_mechanism,  # type: int
):
    # type: (...) -> Optional[Text]

    value = "-%d" % sampling_mechanism

    if service and config._propagate_service is True:
        _ = _hash_service(service)
        if _ is not None:
            value = _ + value

    context._meta[SAMPLING_DECISION_TRACE_TAG_KEY] = value

    return value


def _unset_trace_tag(
    context,  # type: Context
):
    # type: (...) -> Optional[Text]
    if SAMPLING_DECISION_TRACE_TAG_KEY not in context._meta:
        return None

    value = context._meta[SAMPLING_DECISION_TRACE_TAG_KEY]
    del context._meta[SAMPLING_DECISION_TRACE_TAG_KEY]
    return value


def validate_sampling_decision(
    meta,  # type: Dict[str, str]
):
    # type: (...) -> Dict[str, str]
    newmeta = meta.copy()
    value = meta.get(SAMPLING_DECISION_TRACE_TAG_KEY)
    if value:
        # Skip propagating invalid sampling mechanism trace tag
        if TRACE_TAG_RE.match(value) is None:
            del newmeta[SAMPLING_DECISION_TRACE_TAG_KEY]
            newmeta["_dd.propagation_error"] = "decoding_error"
            log.warning("failed to decode _dd.p.dm: %r", value, exc_info=True)
    return newmeta


def update_sampling_decision(
    context,  # type: Context
    service,  # type: Optional[Text]
    sampling_mechanism,  # type: int
    sampled,  # type: bool
):
    # type: (...) -> Optional[Text]
    # When sampler keeps trace, we need to set sampling decision trace tag.
    # If sampler rejects trace, we need to remove sampling decision trace tag to avoid unnecessary propagation.
    if sampled:
        return _set_trace_tag(context, service, sampling_mechanism)
    else:
        return _unset_trace_tag(context)
