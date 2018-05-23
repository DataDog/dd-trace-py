from .constants import ConfigKeys

# Precompute the list of keys
KEYS = [key for key in dir(ConfigKeys) if not key.startswith('__')]

def config_invalid_keys(config):
    """Returns a list of keys that exist in *config* and not in *keys*."""
    return [key for key in KEYS if key not in config]
