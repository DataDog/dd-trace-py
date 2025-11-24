import importlib.metadata


try:
    __version__ = importlib.metadata.version("ddtrace.testing")
except Exception:
    __version__ = "0.0.0"
