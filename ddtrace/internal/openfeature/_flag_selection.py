"""
Flag selection for the agentless Feature Flagging configuration source.

A provider can ask the configuration endpoint for only the flags that carry
some tags or match some key patterns. The selection is canonicalized and
checked against the endpoint's limits before it is sent, the same way the other
Datadog SDKs do, so an invalid selection loads the full configuration instead.

Every provider applies its configuration to one process-wide snapshot, so a
process can serve only one selection: the first provider to start fetching
claims it, and the claim is released when the last provider shuts down.
"""

from collections.abc import Mapping
import typing
from urllib.parse import quote
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.logger import set_tag_rate_limit
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


log = get_logger(__name__)

# Limits enforced by the flag configuration endpoint, shared by every SDK.
MAX_VALUES = 31
MAX_KEY_PATTERN_BYTES = 200
MAX_TAG_BYTES = 200
# Fastly answers URLs over 8 KB with 414 URI Too Long:
# https://docs.fastly.com/products/network-services-resource-limits
MAX_REQUEST_URL_BYTES = 8192

FLAG_SELECTION_IGNORED_HEADER = "DD-Flag-Selection-Ignored"

MALFORMED_SELECTION = "malformed_selection"
TOO_MANY_VALUES = "too_many_values"
INVALID_KEY_PATTERN = "invalid_key_pattern"
INVALID_TAG = "invalid_tag"
TAG_LOAD_FAILED = "tag_load_failed"
UNKNOWN_REASON = "unknown"
_SERVER_REASONS = frozenset((MALFORMED_SELECTION, TOO_MANY_VALUES, INVALID_KEY_PATTERN, INVALID_TAG, TAG_LOAD_FAILED))

_LIMITS = {
    MALFORMED_SELECTION: "flag_selection must be a dict with optional tags and key_patterns string arrays",
    TOO_MANY_VALUES: f"each list accepts at most {MAX_VALUES} values",
    INVALID_KEY_PATTERN: f"each key pattern is at most {MAX_KEY_PATTERN_BYTES} UTF-8 bytes",
    INVALID_TAG: f"each tag is at most {MAX_TAG_BYTES} UTF-8 bytes",
}
_REQUEST_URL_LIMIT = f"the request URL is at most {MAX_REQUEST_URL_BYTES} bytes"

DROPPED_METRIC = "flag_selection.dropped"
IGNORED_METRIC = "flag_selection.ignored"

# The Unicode White_Space property (https://www.unicode.org/Public/UCD/latest/ucd/PropList.txt), which the
# endpoint trims. str.strip() with no argument is not equivalent: it also strips U+001C to U+001F.
_WHITE_SPACE = (
    "\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)
_LIST_TYPES = (list, tuple, set, frozenset)

_NOT_SENT_MESSAGE = "flag_selection was not sent (%s: %s). Loading the full flag configuration."
_IGNORED_MESSAGE = "flag_selection was ignored by the server (%s). Loaded the full flag configuration."
_REMOTE_CONFIG_MESSAGE = (
    "flag_selection is not supported when flags are delivered through Remote Config. "
    "Loading the full flag configuration."
)
_SECOND_SELECTION_MESSAGE = (
    "flag_selection differs from the flag selection already in use in this process. Using the first selection."
)

# One call site logs every reason, so the default per-call-site limit would swallow a second reason
# or a second provider's message. The once-per guards below bound the volume instead.
for _message in (_NOT_SENT_MESSAGE, _IGNORED_MESSAGE, _REMOTE_CONFIG_MESSAGE, _SECOND_SELECTION_MESSAGE):
    set_tag_rate_limit(_message, 0)


class FlagSelection(typing.NamedTuple):
    """A canonical selection. ``reason`` is set when the selection must not be sent.

    Two selections are the same selection exactly when they compare equal.
    """

    tags: tuple[str, ...] = ()
    key_patterns: tuple[str, ...] = ()
    reason: typing.Optional[str] = None


NO_SELECTION = FlagSelection()
_MALFORMED = FlagSelection(reason=MALFORMED_SELECTION)

_lock = forksafe.Lock()
_claimed: FlagSelection = NO_SELECTION
_claim_count = 0
# The selection under which the agentless source last applied the shared snapshot.
_loaded: typing.Optional[FlagSelection] = None
_reported: set[tuple[str, str]] = set()
_remote_config_warned = False
_second_selection_warned = False
# Why the claimed selection was not sent, so a forked child can count the drop it inherits.
_dropped_reason: typing.Optional[str] = None
_pending_fork_drop: typing.Optional[str] = None


def canonicalize_flag_selection(raw: typing.Any) -> FlagSelection:
    """Canonicalize a ``flag_selection`` option; never raises for any input."""
    try:
        return _canonicalize(raw)
    except Exception:
        return _MALFORMED


def _canonicalize(raw: typing.Any) -> FlagSelection:
    if raw is None:
        return NO_SELECTION
    if not isinstance(raw, Mapping):
        return _MALFORMED
    raw_tags = raw.get("tags")
    raw_key_patterns = raw.get("key_patterns")
    if not _is_optional_string_list(raw_tags) or not _is_optional_string_list(raw_key_patterns):
        return _MALFORMED

    tags = _canonicalize_list(raw_tags or ())
    key_patterns = _canonicalize_list(raw_key_patterns or ())
    if len(tags) > MAX_VALUES or len(key_patterns) > MAX_VALUES:
        reason: typing.Optional[str] = TOO_MANY_VALUES
    elif any(len(pattern.encode("utf-8")) > MAX_KEY_PATTERN_BYTES for pattern in key_patterns):
        reason = INVALID_KEY_PATTERN
    elif any(len(tag.encode("utf-8")) > MAX_TAG_BYTES for tag in tags):
        reason = INVALID_TAG
    else:
        reason = None
    return FlagSelection(tags, key_patterns, reason)


def _is_optional_string_list(value: typing.Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, _LIST_TYPES):
        return False
    for item in value:
        if not isinstance(item, str):
            return False
        try:
            # Fails only on surrogate code points, which cannot be sent.
            item.encode("utf-8")
        except UnicodeEncodeError:
            return False
    return True


def _canonicalize_list(values: typing.Iterable[str]) -> tuple[str, ...]:
    unique = {value.strip(_WHITE_SPACE) for value in values}
    unique.discard("")
    return tuple(sorted(unique, key=lambda value: value.encode("utf-8")))


def append_flag_selection(url: str, selection: FlagSelection) -> typing.Optional[str]:
    """Return ``url`` with the selection's query parameters, or None when it would be too long."""
    parameters = [f"selected_flag_tags={quote(tag, safe='')}" for tag in selection.tags]
    parameters += [f"selected_flag_key_patterns={quote(pattern, safe='')}" for pattern in selection.key_patterns]
    parts = urlsplit(url)
    query = "&".join(part for part in (parts.query, *parameters) if part)
    with_selection = urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    if len(with_selection.encode("utf-8")) > MAX_REQUEST_URL_BYTES:
        return None
    return with_selection


def claim_process_selection(selection: FlagSelection) -> FlagSelection:
    """Take a reference to the process selection, claiming it with ``selection`` if nobody holds it.

    Returns the selection to request. Each call must be paired with release_process_selection().
    """
    global _claimed, _claim_count
    with _lock:
        if _claim_count == 0:
            _claimed = selection
        _claim_count += 1
        return _claimed


def release_process_selection() -> None:
    """Drop a reference taken by claim_process_selection(); the last one releases the claim."""
    global _claimed, _claim_count, _dropped_reason
    with _lock:
        if _claim_count == 0:
            return
        _claim_count -= 1
        if _claim_count == 0:
            _claimed = NO_SELECTION
            _dropped_reason = None


def snapshot_from_other_selection() -> bool:
    """Whether the shared snapshot was loaded under a selection other than the one claimed now.

    True only after a claim was released and replaced by a different one, until the new
    selection's first configuration is applied.
    """
    with _lock:
        return _claim_count > 0 and _loaded is not None and _loaded != _claimed


def recording_loaded_selection(
    apply_configuration: typing.Callable[[typing.Any], None], selection: FlagSelection
) -> typing.Callable[[typing.Any], None]:
    """Wrap ``apply_configuration`` to remember which selection the applied configuration has."""

    def apply(configuration: typing.Any) -> None:
        global _loaded
        apply_configuration(configuration)
        with _lock:
            _loaded = selection

    return apply


def selection_request_url(endpoint: str, requested: FlagSelection, claimed: FlagSelection) -> tuple[str, bool]:
    """Return the URL to poll for ``claimed`` and whether it carries the selection.

    A selection equal to the claimed one is reported as the claimed one is. A different one is not
    used: it reports its own drop, and warns only if it would load other flags, since a dropped
    selection and no selection both load every flag.
    """
    url, sent = endpoint, False
    reason: typing.Optional[str] = None
    limit = ""
    if claimed.reason is None:
        if claimed.tags or claimed.key_patterns:
            with_selection = append_flag_selection(endpoint, claimed)
            if with_selection is not None:
                url, sent = with_selection, True
            else:
                reason, limit = TOO_MANY_VALUES, _REQUEST_URL_LIMIT
    else:
        reason, limit = claimed.reason, _LIMITS[claimed.reason]

    _record_dropped(reason)
    if requested == claimed:
        if reason is not None:
            _report_not_sent(reason, limit)
        return url, sent

    if requested.reason is not None:
        _report_not_sent(requested.reason, _LIMITS[requested.reason])
    requested_flags = NO_SELECTION if requested.reason is not None else requested
    if requested_flags != (claimed if sent else NO_SELECTION):
        _warn_second_selection()
    return url, sent


def _report_not_sent(reason: str, limit: str) -> None:
    # Keep the literal equal to _NOT_SENT_MESSAGE, which exempts it from the rate limiter.
    log.error("flag_selection was not sent (%s: %s). Loading the full flag configuration.", reason, limit)
    if _first_report(DROPPED_METRIC, reason):
        _count(DROPPED_METRIC, reason)


def _warn_second_selection() -> None:
    global _second_selection_warned
    with _lock:
        if _second_selection_warned:
            return
        _second_selection_warned = True
    log.warning(_SECOND_SELECTION_MESSAGE)


def sanitize_ignored_reason(value: typing.Optional[str]) -> typing.Optional[str]:
    """Map a ``DD-Flag-Selection-Ignored`` value to a known reason, ``unknown``, or None when absent.

    The comparison is exact, so trailing whitespace or a comma-joined repeated header is unknown.
    """
    if not value:
        return None
    return value if value in _SERVER_REASONS else UNKNOWN_REASON


def report_ignored(value: typing.Optional[str]) -> None:
    """Report that the server ignored the selection, once per process per reason."""
    reason = sanitize_ignored_reason(value)
    if reason is None or not _first_report(IGNORED_METRIC, reason):
        return
    # Keep the literal equal to _IGNORED_MESSAGE, which exempts it from the rate limiter.
    log.error("flag_selection was ignored by the server (%s). Loaded the full flag configuration.", reason)
    _count(IGNORED_METRIC, reason)


def warn_remote_config(selection: FlagSelection) -> None:
    """Warn once per process that Remote Config delivery does not apply a selection."""
    global _remote_config_warned
    if selection == NO_SELECTION:
        return
    with _lock:
        if _remote_config_warned:
            return
        _remote_config_warned = True
    log.warning(_REMOTE_CONFIG_MESSAGE)


def count_inherited_drop() -> None:
    """Count, once in a forked child, the dropped selection it inherited.

    It logs nothing: "not sent" is logged once per provider initialization, and no provider initializes here.
    """
    global _pending_fork_drop
    if _pending_fork_drop is None:
        return
    with _lock:
        reason, _pending_fork_drop = _pending_fork_drop, None
    if reason is not None and _first_report(DROPPED_METRIC, reason):
        _count(DROPPED_METRIC, reason)


def _record_dropped(reason: typing.Optional[str]) -> None:
    global _dropped_reason
    with _lock:
        _dropped_reason = reason


def _first_report(metric: str, reason: str) -> bool:
    with _lock:
        if (metric, reason) in _reported:
            return False
        _reported.add((metric, reason))
        return True


def _count(metric: str, reason: str) -> None:
    try:
        telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, metric, 1, (("reason", reason),))
    except Exception:
        log.debug("Feature Flags: failed to record the %s count", metric, exc_info=True)


@forksafe.register
def _reset_once_per_process_guards() -> None:
    """Let each forked worker report its own diagnostics. The claim stays with the inherited providers."""
    global _remote_config_warned, _second_selection_warned, _pending_fork_drop
    _reported.clear()
    _remote_config_warned = False
    _second_selection_warned = False
    # Telemetry drops points recorded inside a fork hook, so the inherited poller counts the drop on its next run.
    _pending_fork_drop = _dropped_reason if _claim_count > 0 else None


def _reset_flag_selection_state() -> None:
    """Forget the claim and every reported diagnostic. For tests."""
    global _claimed, _claim_count, _loaded, _dropped_reason
    with _lock:
        _claimed = NO_SELECTION
        _claim_count = 0
        _loaded = None
        _dropped_reason = None
    _reset_once_per_process_guards()
