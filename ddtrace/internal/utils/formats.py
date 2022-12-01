import logging
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Text
from typing import Tuple
from typing import TypeVar
from typing import Union

import six

from ddtrace.constants import USER_ID_KEY
from ddtrace.internal.constants import _W3C_TRACESTATE_ORIGIN_KEY
from ddtrace.internal.constants import _W3C_TRACESTATE_SAMPLING_PRIORITY_KEY
from ddtrace.internal.sampling import SAMPLING_DECISION_TRACE_TAG_KEY

from ..compat import binary_type
from ..compat import ensure_text
from ..compat import stringify
from ..compat import text_type


VALUE_PLACEHOLDER = "?"
VALUE_MAX_LEN = 100
VALUE_TOO_LONG_MARK = "..."
CMD_MAX_LEN = 1000
_W3C_TRACESTATE_INVALID_CHARS_REGEX = r",|;|:|[^\x20-\x7E]+"

T = TypeVar("T")

log = logging.getLogger(__name__)


def deep_getattr(obj, attr_string, default=None):
    # type: (Any, str, Optional[Any]) -> Optional[Any]
    """
    Returns the attribute of `obj` at the dotted path given by `attr_string`
    If no such attribute is reachable, returns `default`

    >>> deep_getattr(cass, 'cluster')
    <cassandra.cluster.Cluster object at 0xa20c350

    >>> deep_getattr(cass, 'cluster.metadata.partitioner')
    u'org.apache.cassandra.dht.Murmur3Partitioner'

    >>> deep_getattr(cass, 'i.dont.exist', default='default')
    'default'
    """
    attrs = attr_string.split(".")
    for attr in attrs:
        try:
            obj = getattr(obj, attr)
        except AttributeError:
            return default

    return obj


def asbool(value):
    # type: (Union[str, bool, None]) -> bool
    """Convert the given String to a boolean object.

    Accepted values are `True` and `1`.
    """
    if value is None:
        return False

    if isinstance(value, bool):
        return value

    return value.lower() in ("true", "1")


def parse_tags_str(tags_str):
    # type: (Optional[str]) -> Dict[str, str]
    """Parse a string of tags typically provided via environment variables.

    The expected string is of the form::
        "key1:value1,key2:value2"
        "key1:value1 key2:value2"

    :param tags_str: A string of the above form to parse tags from.
    :return: A dict containing the tags that were parsed.
    """
    if not tags_str:
        return {}

    TAGSEP = ", "

    def parse_tags(tags):
        # type: (List[str]) -> Tuple[List[Tuple[str, str]], List[str]]
        parsed_tags = []
        invalids = []

        for tag in tags:
            key, sep, value = tag.partition(":")
            if not sep or not key or "," in key:
                invalids.append(tag)
            else:
                parsed_tags.append((key, value))

        return parsed_tags, invalids

    tags_str = tags_str.strip(TAGSEP)

    # Take the maximal set of tags that can be parsed correctly for a given separator
    tag_list = []  # type: List[Tuple[str, str]]
    invalids = []
    for sep in TAGSEP:
        ts = tags_str.split(sep)
        tags, invs = parse_tags(ts)
        if len(tags) > len(tag_list):
            tag_list = tags
            invalids = invs
        elif len(tags) == len(tag_list) > 1:
            # Both separators produce the same number of tags.
            # DEV: This only works when checking between two separators.
            tag_list[:] = []
            invalids[:] = []

    if not tag_list:
        log.error(
            (
                "Failed to find separator for tag string: '%s'.\n"
                "Tag strings must be comma or space separated:\n"
                "  key1:value1,key2:value2\n"
                "  key1:value1 key2:value2"
            ),
            tags_str,
        )

    for tag in invalids:
        log.error("Malformed tag in tag pair '%s' from tag string '%s'.", tag, tags_str)

    return dict(tag_list)


def stringify_cache_args(args):
    # type: (List[Any]) -> Text
    """Convert a list of arguments into a space concatenated string

    This function is useful to convert a list of cache keys
    into a resource name or tag value with a max size limit.
    """
    length = 0
    out = []  # type: List[Text]
    for arg in args:
        try:
            if isinstance(arg, (binary_type, text_type)):
                cmd = ensure_text(arg, errors="backslashreplace")
            else:
                cmd = stringify(arg)

            if len(cmd) > VALUE_MAX_LEN:
                cmd = cmd[:VALUE_MAX_LEN] + VALUE_TOO_LONG_MARK

            if length + len(cmd) > CMD_MAX_LEN:
                prefix = cmd[: CMD_MAX_LEN - length]
                out.append("%s%s" % (prefix, VALUE_TOO_LONG_MARK))
                break

            out.append(cmd)
            length += len(cmd)
        except Exception:
            out.append(VALUE_PLACEHOLDER)
            break

    return " ".join(out)


def _w3c_format_unknown_propagated_tags(current_tags, potential_tags):
    # type: (List[str], dict) -> List[str]
    current_tags_len = sum(len(i) for i in current_tags)
    for k, v in potential_tags.items():
        if (
            isinstance(k, six.string_types)
            and k.startswith("_dd.p")
            # we've already added sampling decision and user id
            and k not in [SAMPLING_DECISION_TRACE_TAG_KEY, USER_ID_KEY]
        ):
            # for key replace ",", "=", and characters outside the ASCII range 0x20 to 0x7E
            # for value replace ",", ";", ":" and characters outside the ASCII range 0x20 to 0x7E
            next_tag = "{}:{}".format(
                re.sub("_dd.p.", "t.", re.sub(r",| |=|[^\x20-\x7E]+", "_", k)),
                re.sub(_W3C_TRACESTATE_INVALID_CHARS_REGEX, "_", v),
            )
            # we need to keep the total length under 256 char
            current_tags_len += len(next_tag)
            if not current_tags_len > 256:
                current_tags.append(next_tag)
            else:
                log.debug("tracestate would exceed 256 char limit with tag: %s. Tag will not be added.", next_tag)
    return current_tags


def _w3c_format_known_propagated_tags(context):
    # Context -> List[str]
    tags = []
    if context.sampling_priority:
        tags.append("{}:{}".format(_W3C_TRACESTATE_SAMPLING_PRIORITY_KEY, context.sampling_priority))
    if context.dd_origin:
        # the origin value has specific values that are allowed.
        tags.append("{}:{}".format(_W3C_TRACESTATE_ORIGIN_KEY, re.sub(r",|;|=|[^\x20-\x7E]+", "_", context.dd_origin)))
    sampling_decision = context._meta.get(SAMPLING_DECISION_TRACE_TAG_KEY)
    if sampling_decision:
        tags.append("t.dm:{}".format(re.sub(_W3C_TRACESTATE_INVALID_CHARS_REGEX, "_", sampling_decision)))
    # since this can change, we need to grab the value off the current span
    usr_id_key = context._meta.get(USER_ID_KEY)
    if usr_id_key:
        tags.append("t.usr.id:{}".format(re.sub(_W3C_TRACESTATE_INVALID_CHARS_REGEX, "_", usr_id_key)))

    return tags
