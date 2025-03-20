import sys
import typing as t

from ddtrace.internal.wrapping.context import WrappingContext


class LazyWrappingContext(WrappingContext):
    def __return__(self, value: t.Any) -> t.Any:
        # Update the global (i.e. the module) scope with the local scope of the
        # wrapped function.
        self.__frame__.f_globals.update(self.__frame__.f_locals)

        return super().__return__(value)


def lazy(f: t.Callable[[], None]) -> None:
    LazyWrappingContext(t.cast(FunctionType, f)).wrap()

    _globals = sys._getframe(1).f_globals

    def __getattr__(name: str) -> t.Any:
        f()
        try:
            return _globals[name]
        except KeyError:
            h = AttributeError(f"module {_globals['__name__']!r} has no attribute {name!r}")
            h.__suppress_context__ = True
            raise h

    _globals["__getattr__"] = __getattr__
