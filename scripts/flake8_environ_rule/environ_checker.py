"""
flake8 plugin to disallow os.environ use. Integrated into hatch lint environment
"""

import ast
from typing import Iterator
from typing import Tuple
from typing import Type


environ_message = "ENV001 any access to os.environ is not allowed, use ddtrace.settings._env.environ instead"
getenv_message = "ENV001 any access os.getenv is not allowed, use ddtrace.settings._env.get_env instead"


class EnvironChecker:
    """Flake8 checker for os.environ access patterns."""

    name = "environ-access-checker"
    version = "1.0.0"

    def __init__(self, tree: ast.AST) -> None:
        self.tree = tree
        self._os_names = set()
        self._environ_names = set()
        self._getenv_names = set()

    def run(self) -> Iterator[Tuple[int, int, str, Type["EnvironChecker"]]]:
        # STEP 1: Collect import information first
        # This builds our _os_names, _environ_names, and _getenv_names sets to track all ways
        # that os.environ and os.getenv might be referenced in this file
        self._collect_imports()

        # STEP 2: Track seen violations to avoid duplicates
        # Some AST nodes might trigger multiple violation checks, so we deduplicate
        # using (line_number, column_offset, message) as the unique key
        seen_violations = set()

        # STEP 3: Walk through every node in the AST and check for violations
        for node in ast.walk(self.tree):
            for violation in self._check_node(node):
                # Create a unique key for this violation to prevent duplicates
                key = (violation[0], violation[1], violation[2])  # line, col, message
                if key not in seen_violations:
                    seen_violations.add(key)
                    yield violation

    def _collect_imports(self) -> None:
        """Collect os, environ, and getenv import names to track all possible access patterns."""
        for node in ast.walk(self.tree):
            # IMPORT PATTERN 1: Standard module imports
            # Handles: import os, import os as system_module
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "os":
                        # Store the name used to reference 'os' (could be aliased)
                        self._os_names.add(alias.asname or "os")

            # IMPORT PATTERN 2: Direct imports from os module
            # Handles: from os import environ, from os import environ as env_vars
            # Handles: from os import getenv, from os import getenv as get_env
            elif isinstance(node, ast.ImportFrom) and node.module == "os":
                for alias in node.names:
                    if alias.name == "environ":
                        # Store the name used to reference 'environ' (could be aliased)
                        self._environ_names.add(alias.asname or "environ")
                    elif alias.name == "getenv":
                        # Store the name used to reference 'getenv' (could be aliased)
                        self._getenv_names.add(alias.asname or "getenv")

    def _check_node(self, node: ast.AST) -> Iterator[Tuple[int, int, str, Type["EnvironChecker"]]]:
        """Check a single AST node for violations."""

        # VIOLATION CHECK 1: Subscript access patterns
        # Catches: os.environ["KEY"], os.environ.get("KEY"), environ["KEY"]
        # Examples: value = os.environ["HOME"], config = environ["DEBUG"]
        if isinstance(node, ast.Subscript):
            if self._is_environ_access(node.value):
                yield (node.lineno, node.col_offset, environ_message, type(self))

        # VIOLATION CHECK 2: Method calls on os.environ
        # Catches: os.environ.get(), os.environ.keys(), os.environ.items(), os.environ.update(), etc.
        # Examples: os.environ.get("PATH"), os.environ.keys(), environ.items()
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if self._is_environ_access(node.func.value):
                yield (node.lineno, node.col_offset, environ_message, type(self))
            # VIOLATION CHECK 6: os.getenv() function calls
            # Catches: os.getenv("KEY"), os.getenv("KEY", "default")
            # Examples: value = os.getenv("HOME"), config = os.getenv("DEBUG", "false")
            elif self._is_getenv_call(node):
                yield (node.lineno, node.col_offset, getenv_message, type(self))

        # VIOLATION CHECK 3: Membership tests using 'in' operator
        # Catches: "KEY" in os.environ, variable in os.environ
        # Examples: if "HOME" in os.environ:, if key in environ:
        elif isinstance(node, ast.Compare):
            for op, comparator in zip(node.ops, node.comparators):
                if isinstance(op, ast.In) and self._is_environ_access(comparator):
                    yield (node.lineno, node.col_offset, environ_message, type(self))

        # VIOLATION CHECK 4: Iteration over os.environ
        # Catches: for loops that iterate over os.environ or its methods
        # Examples: for key in os.environ:, for k, v in os.environ.items():
        elif isinstance(node, ast.For):
            if self._is_environ_access(node.iter):
                yield (node.lineno, node.col_offset, environ_message, type(self))

        # VIOLATION CHECK 5: Direct attribute access to os.environ and os.getenv
        # Catches: direct references to os.environ object itself and os.getenv function
        # Examples: env_dict = os.environ, my_env = environ, getenv_func = os.getenv
        elif isinstance(node, ast.Attribute):
            if self._is_environ_attribute(node):
                yield (node.lineno, node.col_offset, environ_message, type(self))
            elif self._is_getenv_attribute(node):
                yield (node.lineno, node.col_offset, getenv_message, type(self))

        # VIOLATION CHECK 7: Direct getenv() function calls (imported directly)
        # Catches: getenv("KEY"), get_env("KEY") (from 'from os import getenv as get_env')
        # Examples: value = getenv("HOME"), config = get_env("DEBUG", "false")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if self._is_getenv_call(node):
                yield (node.lineno, node.col_offset, getenv_message, type(self))

    def _is_environ_attribute(self, node: ast.Attribute) -> bool:
        """
        Check if node is os.environ attribute access.

        Detects patterns like: os.environ, system.environ (if imported as 'system')
        Returns True when:
        - node.value is a Name (like 'os')
        - that name is in our tracked os import names
        - the attribute being accessed is 'environ'
        """
        return isinstance(node.value, ast.Name) and node.value.id in self._os_names and node.attr == "environ"

    def _is_getenv_attribute(self, node: ast.Attribute) -> bool:
        """
        Check if node is os.getenv attribute access.

        Detects patterns like: os.getenv, system.getenv (if imported as 'system')
        Returns True when:
        - node.value is a Name (like 'os')
        - that name is in our tracked os import names
        - the attribute being accessed is 'getenv'
        """
        return isinstance(node.value, ast.Name) and node.value.id in self._os_names and node.attr == "getenv"

    def _is_environ_access(self, node: ast.AST) -> bool:
        """
        Check if a node represents access to os.environ in any form.

        Handles two main access patterns:
        1. Direct environ usage: environ (from 'from os import environ')
        2. Attribute access: os.environ (from 'import os')
        """
        # CASE 1: Direct environ name usage
        # Matches: environ, env_vars (if imported as 'from os import environ as env_vars')
        if isinstance(node, ast.Name) and node.id in self._environ_names:
            return True

        # CASE 2: Attribute access pattern (os.environ)
        # Matches: os.environ, system.environ (if imported as 'import os as system')
        if isinstance(node, ast.Attribute):
            return self._is_environ_attribute(node)

        return False

    def _is_getenv_call(self, node: ast.Call) -> bool:
        """
        Check if a function call node represents os.getenv() in any form.

        Handles two main call patterns:
        1. Direct getenv usage: getenv("KEY") (from 'from os import getenv')
        2. Attribute access: os.getenv("KEY") (from 'import os')
        """
        # CASE 1: Direct getenv function call
        # Matches: getenv("KEY"), get_env("KEY") (if imported as 'from os import getenv as get_env')
        if isinstance(node.func, ast.Name) and node.func.id in self._getenv_names:
            return True

        # CASE 2: Attribute access pattern (os.getenv)
        # Matches: os.getenv("KEY"), system.getenv("KEY") (if imported as 'import os as system')
        if isinstance(node.func, ast.Attribute):
            return (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id in self._os_names
                and node.func.attr == "getenv"
            )

        return False
