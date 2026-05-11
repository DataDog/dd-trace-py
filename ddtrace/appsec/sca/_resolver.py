"""Symbol resolution for SCA detection targets.

Provides functionality to resolve qualified names (e.g., "module.path:Class.method")
to actual Python callables that can be instrumented.
"""

import sys
from types import FunctionType
from typing import Optional

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class SymbolResolver:
    """Resolve qualified names to Python callables.

    Supports resolving:
    - Module-level functions: "module.path:function_name"
    - Class methods: "module.path:ClassName.method_name"
    - Static methods: "module.path:ClassName.static_method"
    """

    @staticmethod
    def resolve(qualified_name: str) -> Optional[tuple[str, FunctionType]]:
        """Resolve a qualified name to a callable.

        Uses sys.modules lookup instead of importlib.import_module()
        to avoid triggering new imports.  Triggering imports at post_preload()
        time can pull in modules (e.g. urllib3 → ssl) before gevent's
        monkey.patch_all() runs, causing RecursionError in ssl.SSLContext.

        Args:
            qualified_name: Qualified name in format "module.path:symbol" or
                          "module.path:Class.method"

        Returns:
            Tuple of (qualified_name, function) or None if resolution failed.
        """
        try:
            if ":" not in qualified_name:
                log.debug("Invalid format (missing ':'): %s", qualified_name)
                return None

            module_path, symbol_path = qualified_name.split(":", 1)

            module = sys.modules.get(module_path)
            if module is None:
                log.debug("Module not yet imported: %s", module_path)
                return None

            # Navigate the attribute path. For the LAST component, use __dict__
            # lookup when the parent is a class — getattr on a class creates a
            # new bound-method wrapper each time, so inject_hook would patch a
            # throwaway copy instead of the real function in the class dict.
            parts = symbol_path.split(".")
            target = module
            for i, part in enumerate(parts):
                is_last = i == len(parts) - 1
                if is_last and isinstance(target, type) and part in target.__dict__:
                    raw = target.__dict__[part]
                    func = SymbolResolver._extract_function(raw)
                    if func is not None:
                        log.debug("Resolved via __dict__: %s -> %s", qualified_name, func)
                        return (qualified_name, func)
                    # Fallback to getattr
                    target = getattr(target, part)
                else:
                    try:
                        target = getattr(target, part)
                    except AttributeError as e:
                        log.debug("Symbol not found: %s.%s (%s)", module_path, symbol_path, e)
                        return None

            if not callable(target):
                log.debug("Target not callable: %s", qualified_name)
                return None

            func = SymbolResolver._extract_function(target)
            if func is None:
                log.debug("Cannot extract function from: %s", qualified_name)
                return None

            log.debug("Resolved: %s -> %s", qualified_name, func)
            return (qualified_name, func)

        except (ValueError, TypeError) as e:
            log.debug("Failed to resolve %s: %s", qualified_name, e)
            return None
        except Exception:
            log.debug("Unexpected error resolving %s", qualified_name, exc_info=True)
            return None

    @staticmethod
    def _extract_function(target: object) -> Optional[FunctionType]:
        """Extract FunctionType from various callable types.

        Handles wrapt FunctionWrapper (used by ddtrace monkey-patching)
        by unwrapping via __wrapped__ to get the original function that
        the wrapper delegates to.
        """
        # ddtrace monkey-patches libraries with wrapt wrappers.
        # We inject the hook into __wrapped__ so it fires when the wrapper
        # calls the original function.
        if hasattr(target, "__wrapped__"):
            wrapped = target.__wrapped__
            if isinstance(wrapped, FunctionType):
                return wrapped

        if isinstance(target, FunctionType):
            return target

        if hasattr(target, "__func__"):
            func = getattr(target, "__func__")
            if isinstance(func, FunctionType):
                return func

        if isinstance(target, (staticmethod, classmethod)):
            func = target.__func__
            if isinstance(func, FunctionType):
                return func

        return None
