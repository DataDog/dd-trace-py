from .registry import hooks
from .decorator import register_module_hook
from .patch import patch, unpatch

__all__ = ['hooks', 'register_module_hook', 'patch', 'unpatch']

# Make sure we always patch
patch()
