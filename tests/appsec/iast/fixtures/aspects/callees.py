def extend(a, b, dry_run=False):
    if not dry_run:
        a.extend(b)
    return a
