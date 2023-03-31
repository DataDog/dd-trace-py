def inner():
    return 42


def traceme():
    cake = "🍰"  # noqa
    return 42 + inner()
