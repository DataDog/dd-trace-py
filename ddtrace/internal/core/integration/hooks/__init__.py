"""
Integration hooks.

Hooks self-register with core.on() for specific event names.
"""

from .base import BaseHook
from .base import register_hook


__all__ = [
    "BaseHook",
    "register_hook",
]
