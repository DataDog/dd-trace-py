from ...internal.logger import get_logger


log = get_logger(__name__)


def resource_from_cache_prefix(resource, cache):
    """
    Combine the resource name with the cache prefix (if any)
    """
    if getattr(cache, "key_prefix", None):
        name = "{} {}".format(resource, cache.key_prefix)
    else:
        name = resource

    # enforce lowercase to make the output nicer to read
    return name.lower()


def quantize_key_values(key):
    """
    Used in the Django trace operation method, it ensures that if a dict
    with values is used, we removes the values from the span meta
    attributes. For example::

        >>> quantize_key_values({'key': 'value'})
        # returns ['key']
    """
    if isinstance(key, dict):
        return key.keys()

    return key


def get_django_2_route(resolver, resolver_match):
    # Try to use `resolver_match.route` if available
    # Otherwise, look for `resolver.pattern.regex.pattern`
    route = resolver_match.route
    if not route:
        # DEV: Use all these `getattr`s to protect against changes between versions
        pattern = getattr(resolver, "pattern", None)
        if pattern:
            regex = getattr(pattern, "regex", None)
            if regex:
                route = getattr(regex, "pattern", "")

    return route


def set_tag_array(span, prefix, value):
    """Helper to set a span tag as a single value or an array"""
    if not value:
        return

    if len(value) == 1:
        span.set_tag(prefix, value[0])
    else:
        for i, v in enumerate(value, start=0):
            span.set_tag("{0}.{1}".format(prefix, i), v)
