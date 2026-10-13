"""Application code that IAST AST-patches, mirroring the monitor_app_web crash sites (APPSEC-70496)."""


def concat(prefix, suffix):
    return prefix + suffix


def join_items(separator, items):
    return separator.join(items)


def subscript(container, key):
    return container[key]
