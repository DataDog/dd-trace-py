"""Symbol resolution from string paths to Python callables.

Parses qualified names like "module.path:Class.method" and resolves them
to actual callable objects in the running process.
"""

import importlib
from typing import Callable
from typing import Optional
from typing import Tuple


class SymbolResolver:
    """Resolve string paths to Python callables.

    Handles formats like:
    - "module.path:function"
    - "module.path:Class.method"
    - "module.path:Class.staticmethod"
    - "module.path:Class.classmethod"
    """

    @staticmethod
    def resolve(path: str) -> Optional[Tuple[str, Callable]]:
        """Resolve a path to a callable.

        Args:
            path: Qualified name (e.g., "module.path:Class.method")

        Returns:
            Tuple of (qualified_name, callable_obj) if successful, None otherwise

        Example:
            >>> resolver.resolve("tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:func_a1")
            ("tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:func_a1", <function func_a1>)
        """
        try:
            # Split module and symbol parts
            if ":" not in path:
                return None

            module_path, symbol_path = path.split(":", 1)

            # Import module
            module = importlib.import_module(module_path)

            # Navigate to symbol
            parts = symbol_path.split(".")
            target = module

            for part in parts:
                target = getattr(target, part)

            # Validate it's callable
            if not callable(target):
                return None

            # Return with normalized qualified name
            return (path, target)

        except (ImportError, AttributeError, ValueError):
            return None

    @staticmethod
    def parse_and_validate(path: str) -> Tuple[bool, str, Optional[Tuple[str, Callable]]]:
        """Parse and validate a path with detailed error messages.

        Args:
            path: Qualified name to parse

        Returns:
            Tuple of (success, message, result)
            - success: True if resolved successfully
            - message: Success or error message
            - result: (qualified_name, callable) tuple if success, None otherwise

        Example:
            >>> success, msg, result = resolver.parse_and_validate("invalid")
            >>> print(success, msg)
            False Invalid format. Use 'module.path:symbol' or 'module.path:Class.method'
        """
        if ":" not in path:
            return (
                False,
                "Invalid format. Use 'module.path:symbol' or 'module.path:Class.method'",
                None,
            )

        module_path, symbol_path = path.split(":", 1)

        # Try to import module
        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            return (False, f"Cannot import module '{module_path}': {e}", None)

        # Navigate symbol path
        parts = symbol_path.split(".")
        target = module

        for i, part in enumerate(parts):
            try:
                target = getattr(target, part)
            except AttributeError:
                partial_path = ".".join(parts[: i + 1])
                return (
                    False,
                    f"Attribute '{partial_path}' not found in module '{module_path}'",
                    None,
                )

        # Validate callable
        if not callable(target):
            return (False, f"Symbol '{path}' is not callable", None)

        return (True, f"Successfully resolved '{path}'", (path, target))
