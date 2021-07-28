import os


if os.getenv("DDTRACE") == "1":
    import ddtrace  # noqa
