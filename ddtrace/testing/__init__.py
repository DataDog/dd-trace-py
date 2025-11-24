import importlib.metadata


try:
    __version__ = importlib.metadata.version("ddtestpy")
except Exception:
    __version__ = "0.0.0"
