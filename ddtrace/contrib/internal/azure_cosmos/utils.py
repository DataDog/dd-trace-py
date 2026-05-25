"""Utilities for Azure CosmosDB instrumentation."""

_KEEP_ID_KEYS = frozenset({"dbs", "colls"})


def normalize_resource_uri(resource_uri: str) -> str:
    """Redact CosmosDB resource ids to reduce span resource cardinality.

    Preserves database (``dbs``) and collection (``colls``) ids; replaces every
    other id segment with ``?``.
    """
    if not resource_uri:
        return resource_uri

    parts = resource_uri.split("/")
    changed = False
    for i in range(2, len(parts), 2):
        if not parts[i] or parts[i - 1].lower() in _KEEP_ID_KEYS:
            continue
        parts[i] = "?"
        changed = True

    return "/".join(parts) if changed else resource_uri
