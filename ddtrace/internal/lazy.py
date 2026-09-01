import sys
from types import FrameType
from types import FunctionType
import typing as t

from ddtrace.internal.compat import NEXT_PY_VERSION_INFO
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.wrapping.context import WrappingContext


class _LazyModuleLoadingContext(WrappingContext):
    def __return__(self, value: t.Any) -> t.Any:
        # Update the global (i.e. the module) scope with the local scope of the
        # wrapped function.
        self.__frame__.f_globals.update(self.__frame__.f_locals)

        return super().__return__(value)


def _exec_lazy_init(f: FunctionType, module_globals: dict[str, t.Any]) -> None:
    """Run a @lazy initializer body in module scope without bytecode wrapping."""
    fn = FunctionType(f.__code__, module_globals, f.__name__, f.__defaults__, f.__closure__)
    frame_locals: dict[str, t.Any] = {}
    code = fn.__code__

    old_trace = sys.gettrace()

    def _trace(frame: FrameType, event: str, arg: t.Any) -> t.Optional[t.Callable[[FrameType, str, t.Any], t.Any]]:
        if frame.f_code is code and event in ("line", "return"):
            # Materialize a snapshot (PEP 667) so locals survive under pytest/coverage.
            frame_locals.update(dict(frame.f_locals))
        # Do not forward to old_trace here: pytest/coverage often reinstall their
        # own tracer on "call" and would prevent us from seeing the return event.
        return _trace

    sys.settrace(_trace)
    try:
        fn()
    finally:
        sys.settrace(old_trace)

    module_globals.update(frame_locals)


def lazy(f: t.Callable[[], None]) -> None:
    _globals = sys._getframe(1).f_globals
    _initialized = False

    if PYTHON_VERSION_INFO < NEXT_PY_VERSION_INFO:
        _LazyModuleLoadingContext(t.cast(FunctionType, f)).wrap()

    def __getattr__(name: str) -> t.Any:
        nonlocal _initialized
        if PYTHON_VERSION_INFO >= NEXT_PY_VERSION_INFO:
            if not _initialized:
                _exec_lazy_init(t.cast(FunctionType, f), _globals)
                _initialized = True
        else:
            f()
        try:
            return _globals[name]
        except KeyError:
            h = AttributeError(f"module {_globals['__name__']!r} has no attribute {name!r}")
            h.__suppress_context__ = True
            raise h

    _globals["__getattr__"] = __getattr__
