from collections import defaultdict
from collections import deque
from pathlib import Path

from wrapt import FunctionWrapper

from ddtrace.internal.utils.inspection import undecorated


try:
    from typing import Protocol
except ImportError:
    from typing_extensions import Protocol  # type: ignore[assignment]

from types import FunctionType
from types import ModuleType
from typing import Any
from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional
from typing import Tuple
from typing import Type
from typing import Union
from typing import cast

from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import origin
from ddtrace.internal.safety import _isinstance
from ddtrace.internal.utils.inspection import linenos


log = get_logger(__name__)

FunctionContainerType = Union[type, property, classmethod, staticmethod, Tuple, ModuleType]

ContainerKey = Union[str, int, Type[staticmethod], Type[classmethod]]

CONTAINER_TYPES = (type, property, classmethod, staticmethod)


class FullyNamed(Protocol):
    """A fully named object."""

    __name__: Optional[str] = None
    __fullname__: Optional[str] = None


class FullyNamedFunction(FullyNamed):
    """A fully named function object."""

    def __call__(self, *args, **kwargs):
        pass


class ContainerIterator(Iterator, FullyNamedFunction):
    """Wrapper around different types of function containers.

    A container comes with an origin, i.e. a parent container and a position
    within it in the form of a key.
    """

    def __init__(
        self,
        container: FunctionContainerType,
        origin: Optional[Union[Tuple["ContainerIterator", ContainerKey], Tuple[FullyNamedFunction, str]]] = None,
    ) -> None:
        if isinstance(container, (type, ModuleType)):
            self._iter = iter(container.__dict__.items())
            self.__name__ = container.__name__

        elif isinstance(container, tuple):
            self._iter = iter(enumerate(_.cell_contents for _ in container))  # type: ignore[arg-type]
            self.__name__ = "<locals>"

        elif isinstance(container, property):
            self._iter = iter(
                (m, getattr(container, a)) for m, a in {("getter", "fget"), ("setter", "fset"), ("deleter", "fdel")}
            )
            assert container.fget is not None  # nosec
            self.__name__ = container.fget.__name__

        elif isinstance(container, (classmethod, staticmethod)):
            self._iter = iter([(type(container), container.__func__)])  # type: ignore[list-item]
            self.__name__ = None

        else:
            raise TypeError("Unsupported container type: %s", type(container))

        self._container = container

        if origin is not None and origin[0].__fullname__ is not None:
            origin_fullname = origin[0].__fullname__
            self.__fullname__ = ".".join((origin_fullname, self.__name__)) if self.__name__ else origin_fullname
        else:
            self.__fullname__ = self.__name__

    def __iter__(self) -> Iterator[Tuple[ContainerKey, Any]]:
        return self._iter

    def __next__(self) -> Tuple[ContainerKey, Any]:
        return next(self._iter)

    next = __next__


def _local_name(name: str, f: FunctionType) -> str:
    func_name = f.__name__
    if func_name.startswith("__") and name.endswith(func_name):
        # Quite likely a mangled name
        return func_name

    if name != func_name:
        # Brought into scope by an import, or a decorator
        return ".<alias>.".join((name, func_name))

    return func_name


def _collect_functions(module: ModuleType) -> Dict[str, FullyNamedFunction]:
    """Collect functions from a given module.

    All the collected functions are augmented with a ``__fullname__`` attribute
    to disambiguate the same functions assigned to different names.
    """
    path = origin(module)
    if path is None:
        # We are not able to determine what this module actually exports.
        return {}

    containers = deque([ContainerIterator(module)])
    functions = {}
    seen_containers = set()
    seen_functions = set()

    while containers:
        c = containers.popleft()

        if id(c._container) in seen_containers:
            continue
        seen_containers.add(id(c._container))

        for k, o in c:
            code = getattr(o, "__code__", None) if _isinstance(o, (FunctionType, FunctionWrapper)) else None
            if code is not None:
                local_name = _local_name(k, o) if isinstance(k, str) else o.__name__

                if o not in seen_functions:
                    seen_functions.add(o)
                    o = cast(FullyNamedFunction, o)
                    o.__fullname__ = ".".join((c.__fullname__, local_name)) if c.__fullname__ else local_name

                for name in (k, local_name) if isinstance(k, str) and k != local_name else (local_name,):
                    fullname = ".".join((c.__fullname__, name)) if c.__fullname__ else name
                    if fullname not in functions or Path(code.co_filename).resolve() == path:
                        # Give precedence to code objects from the module and
                        # try to retrieve any potentially decorated function so
                        # that we don't end up returning the decorator function
                        # instead of the original function.
                        functions[fullname] = undecorated(o, name, path) if name == k else o

                try:
                    if o.__closure__:
                        containers.append(ContainerIterator(o.__closure__, origin=(o, "<locals>")))
                except AttributeError:
                    pass

            elif _isinstance(o, CONTAINER_TYPES):
                if _isinstance(o, property) and not isinstance(o.fget, FunctionType):
                    continue
                containers.append(ContainerIterator(o, origin=(c, k)))

    return functions


class FunctionDiscovery(defaultdict):
    """Discover all function objects in a module.

    The discovered functions can be retrieved by line number or by their
    qualified name. In principle one wants to create a function discovery
    object per module and then cache the information. For this reason,
    instances of this class should be obtained with the ``from_module`` class
    method. This builds the discovery object and caches the information on the
    module object itself.
    """

    def __init__(self, module: ModuleType) -> None:
        super().__init__(list)
        self._module = module
        self._fullname_index = {}

        functions = _collect_functions(module)
        seen_functions = set()
        module_path = origin(module)
        if module_path is None:
            # We are not going to collect anything because no code objects will
            # match the origin.
            return

        for fname, function in functions.items():
            if (
                function not in seen_functions
                and Path(cast(FunctionType, function).__code__.co_filename).resolve() == module_path
            ):
                # We only map line numbers for functions that actually belong to
                # the module.
                for lineno in linenos(cast(FunctionType, function)):
                    self[lineno].append(function)
            self._fullname_index[fname] = function
            seen_functions.add(function)

    def at_line(self, line: int) -> List[FullyNamedFunction]:
        """Get the functions at the given line.

        Note that, in general, there can be multiple copies of the same
        functions. This can happen as a result, e.g., of using decorators.
        """
        return self[line]

    def by_name(self, qualname: str) -> FullyNamedFunction:
        """Get the function by its qualified name."""
        fullname = ".".join((self._module.__name__, qualname))
        try:
            return self._fullname_index[fullname]
        except KeyError:
            raise ValueError("Function '%s' not found" % fullname)

    @classmethod
    def from_module(cls, module: ModuleType) -> "FunctionDiscovery":
        """Return a function discovery object from the given module.

        If this is called on a module for the first time, it caches the
        information on the module object itself. Subsequent calls will
        return the cached information.
        """
        # Cache the function tree on the module
        try:
            return module.__function_discovery__
        except AttributeError:
            fd = module.__function_discovery__ = cls(module)  # type: ignore[attr-defined]
            return fd
