"""
Fowler/Noll/Vo hash algorithm, implemented natively.
See http://isthe.com/chongo/tech/comp/fnv/
"""

from ddtrace.internal.native import fnv1_64


__all__ = ["fnv1_64"]
