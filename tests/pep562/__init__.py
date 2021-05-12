from ddtrace.internal.compat import EnsurePep562


def __getattr__(name):
    if name == "deprecated":
        raise RuntimeError("bad module attribute!")
    return "good module attribute"


EnsurePep562(__name__)
