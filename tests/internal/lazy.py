from ddtrace.internal.utils.lazy import lazy


print("lazy loaded")


@lazy
def _():
    new_value = 42  # noqa
