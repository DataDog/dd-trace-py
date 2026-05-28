"""Maintain a separate module for the version to avoid circular imports."""

import importlib.metadata


__all__ = ["__version__"]

__version__: str

try:
    distributions = importlib.metadata.packages_distributions().get(__package__ or __name__)
except Exception:
    distributions = None

try:
    __version__ = importlib.metadata.version(distributions[0] if distributions else "ddtrace")
except Exception:
    __version__ = "0.0.0"



# DO NOT EDIT -- AUTOMATICALLY GENERATED
__build_sha_full__ = "eefc3a2cb2179c818011b700d7f27631b1ef12ed"
__build_sha_short__ = "eefc3a"
__build_timestamp__ = 1779981596
__build_pipeline_id__ = 115664932
# whatever else we want here
