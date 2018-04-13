# [Backward compatibility]: keep importing modules functions
from .utils.deprecation import deprecated
from .utils.formats import asbool, deep_getattr, get_env
from .utils.wrappers import safe_patch, unwrap


__all__ = [
    'deprecated',
    'asbool',
    'deep_getattr',
    'get_env',
    'safe_patch',
    'unwrap',
]
