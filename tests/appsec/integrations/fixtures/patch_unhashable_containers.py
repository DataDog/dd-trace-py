"""Application code that IAST AST-patches, mirroring the monitor_app_web crash sites (APPSEC-70496)."""


def concat(prefix, suffix):
    return prefix + suffix


def join_items(separator, items):
    return separator.join(items)


def subscript(container, key):
    return container[key]


def enumerate_pairs(pairs):
    # A TypeError left pending by an aspect surfaced on a loop like this one in production.
    return [key for _, (key, _) in enumerate(pairs)]
