import os
from typing import Callable
from typing import List
from typing import Optional
from typing import Tuple
from typing import Type
from typing import TypeVar
from typing import Union


NoDefault = object()  # sentinel object
DeprecationInfo = Tuple[str, str, str]


T = TypeVar("T")


class EnvVariable(object):
    def __init__(self, type, name, parser=None, default=NoDefault, deprecations=None):
        # type: (Type[T], str, Optional[Callable[[str], T]], Union[T, NoDefault], Optional[List[DeprecationInfo]]) -> None
        if not isinstance(default, type):
            raise TypeError("default must be of type {}".format(type))
        self.type = type
        self.name = name
        self.parser = parser
        self.default = default
        self.deprecations = deprecations

    def __call__(self):
        # type: () -> T
        raw = os.getenv(self.name)
        if raw is None and self.deprecations:
            for name, deprecated_when, removed_when in self.deprecations:
                raw = os.getenv(name)
                if raw is not None:
                    # TODO: Deprecation warning
                    break

        if raw is None:
            if self.default is not NoDefault:
                return self.default

            raise KeyError("{} is not set".format(self.name))

        if self.parser is not None:
            parsed = self.parser(raw)
            if type(parsed) is not self.type:
                raise TypeError("parser returned type {} instead of {}".format(type(parsed), self.type))
            return parsed

        return self.type(raw)


class DerivedVariable(object):
    def __init__(self, type, derivation):
        self.type = type
        self.derivation = derivation

    def __call__(self, env):
        value = self.derivation(env)
        if not isinstance(value, self.type):
            raise TypeError("derivation returned type {} instead of {}".format(type(value), self.type))
        return value


class Env(object):
    def __init__(self):

        derived = []
        for name, e in self.__class__.__dict__.items():
            if isinstance(e, EnvVariable) or isinstance(e, type) and issubclass(e, Env):
                setattr(self, name, e())
            elif isinstance(e, DerivedVariable):
                derived.append((name, e))

        for n, d in derived:
            setattr(self, n, d(self))

    @classmethod
    def var(cls, type, name, parser=None, default=NoDefault, deprecations=None):
        # type: (Type[T], str, Optional[Callable[[str], T]], Union[T, NoDefault], Optional[List[DeprecationInfo]]) -> EnvVariable[T]
        return EnvVariable(type, name, parser, default, deprecations)

    @classmethod
    def der(cls, type, derivation):
        # type: (Type[T], Callable[[Env], T]) -> DerivedVariable[T]
        return DerivedVariable(type, derivation)
