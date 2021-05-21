from ddtrace.internal.compat import ensure_pep562


def __getattr__(name):
    if name == "deprecated":
        raise RuntimeError("bad module attribute!")
    return "good module attribute"


ensure_pep562(__name__)
