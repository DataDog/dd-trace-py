"""Registry of all instrumentable callables for random invocation.

The registry maintains a list of all callables that can be invoked by the target app,
along with their qualified names. It handles creating and caching class instances
for method invocation.
"""

import random
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Tuple
from typing import Type


class CallableRegistry:
    """Registry of all instrumentable callables.

    Maintains a list of (qualified_name, callable) tuples for random selection
    during execution. Handles instance creation for methods.
    """

    def __init__(self) -> None:
        """Initialize empty registry."""
        self._entries: List[Tuple[str, Callable]] = []
        self._instances: Dict[Type, Any] = {}  # Class -> instance mapping

    def register_function(self, qualified_name: str, func: Callable) -> None:
        """Register a module-level function.

        Args:
            qualified_name: Fully qualified name
            func: The function object
        """
        self._entries.append((qualified_name, func))

    def register_method(self, qualified_name: str, cls: Type, method_name: str) -> None:
        """Register an instance method.

        Creates a class instance on first call and reuses it for all methods.

        Args:
            qualified_name: Fully qualified name
            cls: The class containing the method
            method_name: Name of the method
        """
        # Create instance if not already cached
        if cls not in self._instances:
            self._instances[cls] = cls()

        # Get bound method
        instance = self._instances[cls]
        method = getattr(instance, method_name)
        self._entries.append((qualified_name, method))

    def register_staticmethod(self, qualified_name: str, cls: Type, method_name: str) -> None:
        """Register a static method.

        Args:
            qualified_name: Fully qualified name
            cls: The class containing the method
            method_name: Name of the static method
        """
        method = getattr(cls, method_name)
        self._entries.append((qualified_name, method))

    def register_classmethod(self, qualified_name: str, cls: Type, method_name: str) -> None:
        """Register a class method.

        Args:
            qualified_name: Fully qualified name
            cls: The class containing the method
            method_name: Name of the class method
        """
        method = getattr(cls, method_name)
        self._entries.append((qualified_name, method))

    def get_all_entries(self) -> List[Tuple[str, Callable]]:
        """Get all registered callables.

        Returns:
            List of (qualified_name, callable) tuples
        """
        return self._entries

    def get_random_entry(self) -> Tuple[str, Callable]:
        """Get a random callable for invocation.

        Returns:
            Tuple of (qualified_name, callable)

        Raises:
            IndexError: If registry is empty
        """
        return random.choice(self._entries)

    def __len__(self) -> int:
        """Get number of registered callables.

        Returns:
            Number of entries
        """
        return len(self._entries)
