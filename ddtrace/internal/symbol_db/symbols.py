from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
import dis
from enum import Enum
import gzip
from hashlib import sha1
from http.client import HTTPResponse
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
import os
from pathlib import Path
import sys
from types import CodeType
from types import FunctionType
from types import ModuleType
import typing as t

from ddtrace import config
from ddtrace.internal import packages
from ddtrace.internal.compat import singledispatchmethod
from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import BaseModuleWatchdog
from ddtrace.internal.module import origin
from ddtrace.internal.runtime import get_runtime_id
from ddtrace.internal.safety import _isinstance
from ddtrace.internal.utils.cache import cached
from ddtrace.internal.utils.http import FormData
from ddtrace.internal.utils.http import connector
from ddtrace.internal.utils.http import multipart
from ddtrace.internal.utils.inspection import linenos
from ddtrace.internal.utils.inspection import undecorated
from ddtrace.settings._agent import config as agent_config
from ddtrace.settings.symbol_db import config as symdb_config


log = get_logger(__name__)

SOF = 0
EOF = 2147483647
MAX_FILE_SIZE = 1 << 20  # 1MB


@cached()
def is_from_user_code(obj: t.Any) -> t.Optional[bool]:
    try:
        path = origin(sys.modules[object.__getattribute__(obj, "__module__")])
        return packages.is_user_code(path) if path is not None else None
    except (AttributeError, KeyError):
        return None


@cached()
def type_qualname(_type: type) -> str:
    try:
        module = object.__getattribute__(_type, "__module__")
    except AttributeError:
        module = "<unknown>"

    try:
        qualname = object.__getattribute__(_type, "__qualname__")
    except AttributeError:
        qualname = str(_type)

    return qualname if module == int.__module__ or qualname.startswith(f"{module}.") else f"{module}.{qualname}"


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
    type: t.Optional[str] = None

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
    def _get_from(cls, _: t.Any, data: ScopeData, recursive: bool = True) -> t.Optional["Scope"]:
        return None

    @_get_from.register
    @classmethod
    def _(cls, module: ModuleType, data: ScopeData, recursive: bool = True):
        if module in data.seen:
            return None
        data.seen.add(module)

        module_origin = origin(module)
        if module_origin is None:
            return None

        symbols = []
        scopes = []

        if recursive:
            for alias, child in object.__getattribute__(module, "__dict__").items():
                if _isinstance(child, ModuleType):
                    # We don't want to traverse other modules.
                    continue

                try:
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
                except Exception:
                    log.debug("Cannot get child scope %r for module %s", child, module.__name__, exc_info=True)

        source_git_hash = sha1()  # nosec B324
        source_git_hash.update(f"blob {module_origin.stat().st_size}\0".encode())
        with module_origin.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                source_git_hash.update(chunk)

        return Scope(
            scope_type=ScopeType.MODULE,
            name=module.__name__,
            source_file=str(module_origin),
            start_line=0,
            end_line=0,
            symbols=symbols,
            scopes=scopes,
            language_specifics={"file_hash": source_git_hash.hexdigest()},
        )

    @_get_from.register
    @classmethod
    def _(cls, obj: type, data: ScopeData, recursive: bool = True):
        if obj in data.seen:
            return None
        data.seen.add(obj)

        if not is_from_user_code(obj):
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

        try:
            # Exclude object and self from the MRO.
            super_classes = [type_qualname(_) for _ in obj.__mro__ if _ is not object and _ is not obj]
        except Exception:
            log.debug("Cannot get super-classes for %r", obj, exc_info=True)
            super_classes = []

        return Scope(
            scope_type=ScopeType.CLASS,
            name=type_qualname(obj),
            source_file=str(type_origin),
            start_line=start_line,
            end_line=end_line,
            symbols=symbols,
            scopes=scopes,
            language_specifics={"super_classes": super_classes},
        )

    @_get_from.register
    @classmethod
    def _(cls, code: CodeType, data: ScopeData, recursive: bool = True):
        # DEV: A code object with a mutable probe is currently not hashable, so
        # we cannot put it directly into the set.
        code_id = f"code-{id(code)}"
        if code_id in data.seen:
            return None
        data.seen.add(code_id)

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
            source_file=str(Path(code.co_filename).resolve()),
            start_line=start_line,
            end_line=end_line,
            symbols=Symbol.from_code(code),
            scopes=[
                _ for _ in (cls._get_from(_, data) for _ in code.co_consts if isinstance(_, CodeType)) if _ is not None
            ],
        )

    @_get_from.register
    @classmethod
    def _(cls, f: FunctionType, data: ScopeData, recursive: bool = True):
        if f in data.seen:
            return None
        data.seen.add(f)

        if not is_from_user_code(f):
            return None

        code = f.__dd_wrapped__.__code__ if hasattr(f, "__dd_wrapped__") else f.__code__
        code_scope = t.cast(t.Optional[Scope], cls._get_from(code, data))
        if code_scope is None:
            return None

        # Code objects define a closure scope in general. In this case we know
        # that this comes from a function scope, so we override the type.
        code_scope.scope_type = ScopeType.FUNCTION

        # Get the signature of the function and add type annotations to the
        # arguments.
        sig = signature(f)
        params = sig.parameters
        for arg in (
            _
            for _ in code_scope.symbols
            if _.symbol_type == SymbolType.ARG and params[_.name].annotation is not sig.empty
        ):
            ann = params[arg.name].annotation
            arg.type = ann if isinstance(ann, str) else type_qualname(ann)

        if sig.return_annotation is not sig.empty:
            ann = sig.return_annotation
            code_scope.language_specifics["return_type"] = ann if isinstance(ann, str) else type_qualname(ann)

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
    def _(cls, method: classmethod, data: ScopeData, recursive: bool = True):
        scope = cls._get_from(method.__func__, data)

        if scope is not None:
            scope.language_specifics["method_type"] = "class"

        return scope

    @_get_from.register
    @classmethod
    def _(cls, method: staticmethod, data: ScopeData, recursive: bool = True):
        scope = cls._get_from(method.__func__, data)

        if scope is not None:
            scope.language_specifics["method_type"] = "static"

        return scope

    @_get_from.register
    @classmethod
    def _(cls, pr: property, data: ScopeData, recursive: bool = True):
        if pr.fget in data.seen:
            return None
        data.seen.add(pr.fget)

        # TODO: These names don't match what is reported by the discovery.
        if pr.fget is None or not is_from_user_code(pr.fget):
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
            scopes=[
                _
                for _ in (cls._get_from(_, data) for _ in (pr.fget, pr.fset, pr.fdel) if _ is not None)
                if _ is not None
            ],
            language_specifics={"method_type": "property"},
        )

    # TODO: support for singledispatch

    @classmethod
    def from_module(cls, module: ModuleType, recursive: bool = True) -> "Scope":
        """Get the scope of a module.

        The module must have an origin.
        """
        module_origin = origin(module)
        if module_origin is None:
            raise ValueError(f"Cannot get scope of module with no origin '{module.__name__}'")

        return t.cast(Scope, cls._get_from(module, ScopeData(module_origin, set()), recursive))


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

    def upload(self) -> HTTPResponse:
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
                    filename=f"symbols_{os.getpid()}.json.gz",
                    data="[symbols_placeholder]",
                    content_type="gzip",
                ),
            ]
        )

        # DEV: The as_bytes method ends up writing the data line by line, which
        # breaks the final payload. We add a placeholder instead and manually
        # replace it with the compressed JSON.
        body = body.replace(b"[symbols_placeholder]", gzip.compress(json.dumps(self.to_json()).encode("utf-8")))

        with connector(agent_config.trace_agent_url, timeout=5.0)() as conn:
            log.debug("[PID %d] SymDB: Uploading symbols payload", os.getpid())
            conn.request("POST", "/symdb/v1/input", body, headers)

            return conn.getresponse()

    def __bool__(self) -> bool:
        return bool(self._scopes)

    def __len__(self) -> int:
        return len(self._scopes)


def is_module_included(module: ModuleType) -> bool:
    # Check if module name matches the include patterns
    if symdb_config.includes and symdb_config._includes_re.match(module.__name__):
        return True

    # Check if it is user code
    module_origin = origin(module)
    if module_origin is None or not module_origin.exists():
        return False

    if module_origin.stat().st_size > MAX_FILE_SIZE:
        # Skip large files
        return False

    if packages.is_user_code(module_origin):
        return True

    # Check if the package name matches the include patterns
    if symdb_config.includes:
        package = packages.filename_to_package(module_origin)
        if package is not None and symdb_config._includes_re.match(package.name):
            return True

    return False


class SymbolDatabaseUploader(BaseModuleWatchdog):
    __scope_limit__: int = 400
    __file_number_limit__: int = 10000

    shallow: bool = True

    def __init__(self) -> None:
        super().__init__()

        self._seen_modules: t.Set[str] = set()
        self._update_called = False
        self._processed_files_count = 0

        self._process_unseen_loaded_modules()

    def _process_unseen_loaded_modules(self) -> None:
        # Look for all the modules that are already imported when this is
        # installed and upload the symbols that are marked for inclusion.
        recursive = not self.shallow

        context = ScopeContext()
        for name, module in list(sys.modules.items()):
            if self._processed_files_count >= self.__file_number_limit__:
                log.debug("[PID %d] SymDB: Reached file limit of %d", os.getpid(), self.__file_number_limit__)
                break

            # Skip modules that are being initialized as they might not be
            # fully loaded yet.
            try:
                if module.__spec__._initializing:  # type: ignore[union-attr]
                    continue
            except AttributeError:
                pass

            if name in self._seen_modules:
                continue

            self._seen_modules.add(name)

            if not is_module_included(module):
                continue

            try:
                scope = Scope.from_module(module, recursive)
            except Exception:
                log.debug("Cannot get symbol scope for module %s", module.__name__, exc_info=True)
                continue

            if scope is not None:
                log.debug("[PID %d] SymDB: Adding Symbol DB module scope %r", os.getpid(), scope.name)
                context.add_scope(scope)
                self._processed_files_count += 1

            # Batching: send at most 100 module scopes at a time
            n = len(context)
            if n >= self.__scope_limit__:
                log.debug("[PID %d] SymDB: Flushing batch of %d module scopes", os.getpid(), n)
                try:
                    self._upload_context(context)
                except Exception:
                    log.error(
                        "[PID %d] SymDB: Failed to upload symbols context with %d scopes", os.getpid(), n, exc_info=True
                    )
                    return
                context = ScopeContext()

        try:
            self._upload_context(context)
        except Exception:
            log.error(
                "[PID %d] SymDB: Failed to upload symbols context with %d scopes",
                os.getpid(),
                len(context),
                exc_info=True,
            )

    def after_import(self, module: ModuleType) -> None:
        if self._processed_files_count >= self.__file_number_limit__:
            log.debug("[PID %d] SymDB: Reached file limit of %d", os.getpid(), self.__file_number_limit__)
            return

        if not is_module_included(module):
            log.debug("[PID %d] SymDB: Excluding imported module %s from symbol database", os.getpid(), module.__name__)
            return

        scope = Scope.from_module(module, recursive=not self.shallow)
        if scope is not None:
            self._upload_context(ScopeContext([scope]))
            self._processed_files_count += 1

    @classmethod
    def update(cls):
        instance = t.cast(SymbolDatabaseUploader, cls._instance)
        if instance is None:
            return

        if instance._update_called:
            return

        # We only need to update the symbol database once, in case the
        # enablement raced with module imports.
        instance._process_unseen_loaded_modules()

        instance._update_called = True

    @staticmethod
    def _upload_context(context: ScopeContext) -> None:
        if not context:
            return

        try:
            log.debug("[PID %d] SymDB: Uploading symbols context with %d scopes", os.getpid(), len(context._scopes))
            result = context.upload()
            if result.status // 100 != 2:
                log.error("[PID %d] SymDB: Bad response while uploading symbols: %s", os.getpid(), result.status)

        except Exception:
            log.exception(
                "[PID %d] SymDB: Failed to upload symbols context with %d scopes", os.getpid(), len(context._scopes)
            )

    @classmethod
    def install(cls, shallow=True):
        cls.shallow = shallow
        return super().install()
