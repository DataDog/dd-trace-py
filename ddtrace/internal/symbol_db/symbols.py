from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
import dis
from enum import Enum
import http
from inspect import CO_VARARGS
from inspect import CO_VARKEYWORDS
from inspect import isasyncgenfunction
from inspect import iscoroutinefunction
from inspect import isgeneratorfunction
from inspect import signature
from itertools import chain
from itertools import islice
from itertools import tee
import json
from pathlib import Path
import sys
from types import CodeType
from types import FunctionType
from types import ModuleType
import typing as t

from ddtrace import config
from ddtrace.internal import compat
from ddtrace.internal import packages
from ddtrace.internal.agent import get_trace_url
from ddtrace.internal.compat import singledispatchmethod
from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import BaseModuleWatchdog
from ddtrace.internal.module import origin
from ddtrace.internal.packages import is_stdlib
from ddtrace.internal.runtime import get_runtime_id
from ddtrace.internal.safety import _isinstance
from ddtrace.internal.utils.cache import cached
from ddtrace.internal.utils.http import FormData
from ddtrace.internal.utils.http import connector
from ddtrace.internal.utils.http import multipart
from ddtrace.internal.utils.inspection import linenos
from ddtrace.internal.utils.inspection import undecorated
from ddtrace.settings.symbol_db import config as symdb_config


log = get_logger(__name__)

SOF = 0
EOF = 2147483647


@cached()
def is_from_stdlib(obj: t.Any) -> t.Optional[bool]:
    try:
        path = origin(sys.modules[object.__getattribute__(obj, "__module__")])
        return is_stdlib(path) if path is not None else None
    except (AttributeError, KeyError):
        return None


def func_origin(f: FunctionType) -> t.Optional[str]:
    try:
        filename = f.__code__.co_filename
    except AttributeError:
        return None
    return filename if Path(filename).exists() else None


def get_fields(cls: type) -> t.Set[str]:
    # If the class has a __slots__ attribute, return it.
    try:
        return set(object.__getattribute__(cls, "__slots__"))
    except AttributeError:
        pass

    # Otherwise, look at the bytecode for the __init__ method.
    try:
        code = object.__getattribute__(cls, "__init__").__code__

        return {
            code.co_names[b.arg]
            for a, b in zip(*(islice(t, i, None) for i, t in enumerate(tee(dis.get_instructions(code), 2))))
            if a.opname == "LOAD_FAST" and a.arg == 0 and b.opname == "STORE_ATTR"
        }
    except AttributeError:
        return set()


class SymbolType(str, Enum):
    FIELD = "FIELD"
    STATIC_FIELD = "STATIC_FIELD"
    ARG = "ARG"
    LOCAL = "LOCAL"


@dataclass
class Symbol:
    """A symbol available from a scope."""

    symbol_type: str
    name: str
    line: int

    @classmethod
    def from_code(cls, code: CodeType) -> t.List["Symbol"]:
        nargs = code.co_argcount + bool(code.co_flags & CO_VARARGS) + bool(code.co_flags & CO_VARKEYWORDS)
        arg_names = code.co_varnames[:nargs]
        locals_names = code.co_varnames[nargs:]

        start_line = min(linenos(code))

        return list(
            chain(
                (cls(symbol_type=SymbolType.ARG, name=name, line=start_line) for name in arg_names),
                (cls(symbol_type=SymbolType.LOCAL, name=name, line=start_line) for name in locals_names),
            )
        )


class ScopeType(str, Enum):
    MODULE = "MODULE"
    CLASS = "CLASS"
    FUNCTION = "FUNCTION"
    CLOSURE = "CLOSURE"


@dataclass
class ScopeData:
    origin: Path
    seen: t.Set[t.Any]


@dataclass
class Scope:
    """A scope is a collection of symbols."""

    scope_type: str
    name: str
    source_file: str
    start_line: int
    end_line: int
    symbols: t.List[Symbol]
    scopes: t.List["Scope"]

    language_specifics: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)

    @singledispatchmethod
    @classmethod
    def _get_from(cls, _: t.Any, data: ScopeData) -> t.Optional["Scope"]:
        return None

    @_get_from.register
    @classmethod
    def _(cls, module: ModuleType, data: ScopeData):
        if module in data.seen:
            return None
        data.seen.add(module)

        module_origin = origin(module)
        if module_origin is None:
            return None

        symbols = []
        scopes = []

        if is_stdlib(module_origin):
            return None

        for alias, child in object.__getattribute__(module, "__dict__").items():
            if _isinstance(child, ModuleType):
                # We don't want to traverse other modules.
                continue

            if _isinstance(child, FunctionType):
                child = undecorated(child, alias, module_origin)
            scope = Scope._get_from(child, data)
            if scope is not None:
                scopes.append(scope)
            elif not callable(child):
                symbols.append(
                    Symbol(
                        symbol_type=SymbolType.STATIC_FIELD,
                        name=alias,
                        line=0,
                    )
                )

        return Scope(
            scope_type=ScopeType.MODULE,
            name=module.__name__,
            source_file=str(module_origin),
            start_line=0,
            end_line=0,
            symbols=symbols,
            scopes=scopes,
        )

    @_get_from.register
    @classmethod
    def _(cls, obj: type, data: ScopeData):
        if obj in data.seen:
            return None
        data.seen.add(obj)

        if is_from_stdlib(obj):
            return None

        symbols = []
        scopes = []

        try:
            type_origin = origin(sys.modules[object.__getattribute__(obj, "__module__")])
            if type_origin is None or type_origin != data.origin:
                return None
        except (KeyError, AttributeError):
            return None

        # Get whatever we see in the dir() of the class.
        for name, child in object.__getattribute__(obj, "__dict__").items():
            try:
                child = object.__getattribute__(obj, name)
                if _isinstance(child, FunctionType):
                    child = undecorated(child, name, type_origin)
                try:
                    if child.__module__ == "builtins":
                        # We don't want to see builtins. This also stops an
                        # infinite recursion.
                        continue
                except AttributeError:
                    pass
            except AttributeError:
                # best effort
                continue
            scope = Scope._get_from(child, data)
            if scope is not None:
                scopes.append(scope)
            else:
                if not callable(child):
                    symbols.append(
                        Symbol(
                            symbol_type=SymbolType.STATIC_FIELD,
                            name=name,
                            line=0,
                        )
                    )

        # Infer object instance fields from __slots__ or the __init__ method.
        for f in get_fields(obj):
            symbols.append(
                Symbol(
                    symbol_type=SymbolType.FIELD,
                    name=f,
                    line=0,
                )
            )

        try:
            name = object.__getattribute__(obj, "__name__")
        except AttributeError:
            name = "<unnamed-type>"

        # Compute line numbers from child scopes
        if scopes:
            try:
                start_line = min(_.start_line for _ in scopes if _.start_line > SOF)
            except ValueError:
                start_line = SOF
            try:
                end_line = max(_.end_line for _ in scopes if _.end_line < EOF)
            except ValueError:
                end_line = EOF
        else:
            start_line = end_line = SOF

        return Scope(
            scope_type=ScopeType.CLASS,
            name=name,
            source_file=str(type_origin),
            start_line=start_line,
            end_line=end_line,
            symbols=symbols,
            scopes=scopes,
            language_specifics={
                "mro": [
                    f"{object.__getattribute__(_, '__module__')}:{object.__getattribute__(_, '__qualname__')}"
                    for _ in obj.mro()
                ],
            },
        )

    @_get_from.register
    @classmethod
    def _(cls, code: CodeType, data: ScopeData):
        if code in data.seen:
            return None
        data.seen.add(code)

        if Path(code.co_filename).resolve() != data.origin:
            # Comes from another module.
            return None

        ls = linenos(code)
        if not ls:
            return None

        start_line = min(ls)
        end_line = max(ls)

        return Scope(
            scope_type=ScopeType.CLOSURE,  # DEV: Not in the sense of a Python closure.
            name=code.co_name,
            source_file=code.co_filename,
            start_line=start_line,
            end_line=end_line,
            symbols=Symbol.from_code(code),
            scopes=[
                _ for _ in (cls._get_from(_, data) for _ in code.co_consts if isinstance(_, CodeType)) if _ is not None
            ],
        )

    @_get_from.register
    @classmethod
    def _(cls, f: FunctionType, data: ScopeData):
        if f in data.seen:
            return None
        data.seen.add(f)

        if is_from_stdlib(f):
            return None

        code_scope = cls._get_from(f.__code__, data)
        if code_scope is None:
            return None

        # Code objects define a closure scope in general. In this case we know
        # that this comes from a function scope, so we override the type.
        code_scope.scope_type = ScopeType.FUNCTION

        # Get the signature of the function in JSON format
        code_scope.language_specifics = {
            "signature": str(signature(f)),
        }

        function_type = None
        if isasyncgenfunction(f):
            function_type = "asyncgen"
        elif isgeneratorfunction(f):
            function_type = "generator"
        elif iscoroutinefunction(f):
            function_type = "coroutine"

        if function_type is not None:
            code_scope.language_specifics["function_type"] = function_type

        return code_scope

    @_get_from.register
    @classmethod
    def _(cls, method: classmethod, data: ScopeData):
        scope = cls._get_from(method.__func__, data)

        if scope is not None:
            scope.language_specifics["method_type"] = "class"

        return scope

    @_get_from.register
    @classmethod
    def _(cls, method: staticmethod, data: ScopeData):
        scope = cls._get_from(method.__func__, data)

        if scope is not None:
            scope.language_specifics["method_type"] = "static"

        return scope

    @_get_from.register
    @classmethod
    def _(cls, pr: property, data: ScopeData):
        if pr.fget in data.seen:
            return None
        data.seen.add(pr.fget)

        # TODO: These names don't match what is reported by the discovery.
        if pr.fget is None or is_from_stdlib(pr.fget):
            return None

        path = func_origin(t.cast(FunctionType, pr.fget))
        if path is None:
            return None

        # DEV: This is a "logical" scope.
        return Scope(
            scope_type=ScopeType.FUNCTION,
            name=pr.fget.__name__,
            source_file=path,
            start_line=SOF,
            end_line=SOF,
            symbols=[],
            scopes=[t.cast(Scope, cls._get_from(_, data)) for _ in (pr.fget, pr.fset, pr.fdel) if _ is not None],
            language_specifics={"method_type": "property"},
        )

    # TODO: support for singledispatch

    @classmethod
    def from_module(cls, module: ModuleType) -> "Scope":
        """Get the scope of a module.

        The module must have an origin.
        """
        module_origin = origin(module)
        if module_origin is None:
            raise ValueError(f"Cannot get scope of module with no origin '{module.__name__}'")

        return t.cast(Scope, cls._get_from(module, ScopeData(module_origin, set())))


class ScopeContext:
    def __init__(self, scopes: t.Optional[t.List[Scope]] = None) -> None:
        self._scopes: t.List[Scope] = scopes if scopes is not None else []

        self._event_data = {
            "ddsource": "python",
            "service": config.service or DEFAULT_SERVICE_NAME,
            "runtimeId": get_runtime_id(),
        }

    def add_scope(self, scope: Scope) -> None:
        self._scopes.append(scope)

    def to_json(self) -> dict:
        return {
            "schema_version": 1,
            "service": config.service or DEFAULT_SERVICE_NAME,
            "env": config.env or "",
            "version": config.version or "",
            "language": "python",
            "scopes": [_.to_json() for _ in self._scopes],
        }

    def upload(self) -> http.client.HTTPResponse:
        body, headers = multipart(
            parts=[
                FormData(
                    name="event",
                    filename="event.json",
                    data=json.dumps(self._event_data),
                    content_type="json",
                ),
                FormData(
                    name="file",
                    filename="symdb_export.json",
                    data=json.dumps(self.to_json()),
                    content_type="json",
                ),
            ]
        )

        with connector(get_trace_url(), timeout=5.0)() as conn:
            log.debug("Uploading symbols payload %r", body)
            conn.request("POST", "/symdb/v1/input", body, headers)

            return compat.get_connection_response(conn)

    def __bool__(self) -> bool:
        return bool(self._scopes)


def is_module_included(module: ModuleType) -> bool:
    if symdb_config._includes_re.match(module.__name__):
        return True

    package = packages.module_to_package(module)
    if package is None:
        return False

    return symdb_config._includes_re.match(package.name) is not None


class SymbolDatabaseUploader(BaseModuleWatchdog):
    def __init__(self) -> None:
        super().__init__()

        # Look for all the modules that are already imported when this is
        # installed and upload the symbols that are marked for inclusion.
        context = ScopeContext()
        for scope in (Scope.from_module(m) for m in list(sys.modules.values()) if is_module_included(m)):
            if scope is not None:
                context.add_scope(scope)
        self._upload_context(context)

    def after_import(self, module: ModuleType) -> None:
        if not is_module_included(module):
            log.debug("Excluding module %s from symbol database", module.__name__)
            return

        scope = Scope.from_module(module)
        if scope is not None:
            self._upload_context(ScopeContext([scope]))

    @staticmethod
    def _upload_context(context: ScopeContext) -> None:
        if not context:
            return

        try:
            log.debug("Uploading symbols")
            result = context.upload()
            if result.status // 100 != 2:
                log.error("Bad response while uploading symbols: %s", result.status)

        except Exception:
            log.exception("Failed to upload symbols")
