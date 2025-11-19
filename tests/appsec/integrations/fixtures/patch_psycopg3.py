def adapt_list(obj_list):
    """
    Adapt a list to PostgreSQL array format for psycopg3.
    This mimics the behavior for taint tracking testing.
    """
    items = []
    for item in obj_list:
        if isinstance(item, str):
            items.append(f"'{item}'")
        elif isinstance(item, bool):
            items.append(str(item).lower())
        else:
            items.append(str(item))

    array_str = "{" + ",".join(items) + "}"
    return b"r-" + array_str.encode()
