import sys
from types import ModuleType


class SideEffectModule(ModuleType):
    def __getattribute__(self, name):
        if name in {"spec"}:
            raise RuntimeError("Attribute lookup side effect")


sys.modules[__name__].__class__ = SideEffectModule
