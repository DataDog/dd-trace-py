"""IAST module patching implementation.

This module provides the core functionality for patching Python functions to enable IAST analysis.
It implements the wrapper classes and utilities needed to:
1. Wrap security-sensitive functions using wrapt
2. Manage module patching state
3. Support testing scenarios with unpatching capabilities

The module uses wrapt's function wrapping capabilities to intercept calls to security-sensitive
functions and enable taint tracking and vulnerability detection.
"""

import functools
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.appsec._iast.secure_marks import SecurityControl
from ddtrace.appsec._iast.secure_marks import get_security_controls_from_env
from ddtrace.appsec._iast.secure_marks.configuration import SC_SANITIZER
from ddtrace.appsec._iast.secure_marks.configuration import SC_VALIDATOR
from ddtrace.appsec._iast.secure_marks.sanitizers import create_sanitizer
from ddtrace.appsec._iast.secure_marks.validators import create_validator
from ddtrace.internal.logger import get_logger
from ddtrace.internal.security._patch_utils import try_unwrap
from ddtrace.internal.security._patch_utils import try_wrap_function_wrapper
from ddtrace.internal.settings.asm import config as asm_config


log = get_logger(__name__)

MODULES_TO_UNPATCH: set["IASTFunction"] = set()


class IASTFunction:
    """Represents a function to be patched for IAST analysis.

    This class encapsulates the information needed to patch a specific function in a module
    for IAST analysis using lazy patching.

    Attributes:
        name (str): The name of the module to patch
        function (str): The name of the function to wrap
        hook (callable): The wrapper function to apply
    """

    name = ""
    function = ""
    hook = ""

    def __init__(self, name, function, hook):
        """Initialize an IASTFunction instance.

        Args:
            name (str): The name of the module to patch
            function (str): The name of the function to wrap
            hook (callable): The wrapper function to apply
        """
        self.name = name
        self.function = function
        self.hook = hook

    def patch(self):
        """Apply the patch to the target module and function.

        This method sets up lazy patching for the target function.

        Returns:
            bool: True if patching was attempted
        """
        try_wrap_function_wrapper(self.name, self.function, self.hook)
        return True

    def unpatch(self):
        """Remove the patch from the target module and function.

        This method attempts to remove any existing wrapper from the target function.
        """
        try_unwrap(self.name, self.function)

    def __repr__(self):
        """Return a string representation of the IASTFunction instance."""
        return f"IASTFunction(name={self.name}, function={self.function}, hook={self.hook})"


class WrapFunctonsForIAST:
    """Manages the collection and patching of IAST modules.

    This class maintains a set of IASTFunction instances and handles their patching
    and unpatching. It supports both normal operation and testing scenarios.

    Attributes:
        functions (set[IASTFunction]): set of modules to be patched
        testing (bool): Whether the instance is being used in a testing context
    """

    def __init__(self) -> None:
        """Initialize a WrapFunctonsForIAST instance."""
        self.functions: set[IASTFunction] = set()
        self.testing: bool = asm_config._iast_is_testing

    def wrap_function(self, name: str, function: str, hook: Callable[..., Any]) -> None:
        """Add a function for lazy patching.

        Args:
            name (str): The module name
            function (str): The function name to wrap
            hook (callable): The wrapper function to apply
        """
        self.functions.add(IASTFunction(name, function, hook))

    def patch(self) -> None:
        """Apply patches to all registered functions.

        This method attempts to patch all functions in the functions set. If in testing
        mode, it also tracks the functions for later unpatching.
        """
        for module in self.functions:
            if module.patch():
                if self.testing:
                    MODULES_TO_UNPATCH.add(module)

    def testing_unpatch(self):
        """Remove patches from all functions in testing mode.

        This method is used in testing scenarios to clean up all applied patches.
        It only operates if the instance is in testing mode.
        """
        log.debug("Testing: %s. Unwrapping %s", self.testing, len(MODULES_TO_UNPATCH))
        if self.testing:
            for module in MODULES_TO_UNPATCH.copy():
                module.unpatch()
                MODULES_TO_UNPATCH.remove(module)


def _testing_unpatch_iast():
    """Utility function to unpatch all IAST functions in testing mode.

    This function creates a WrapFunctonsForIAST instance and uses it to remove
    all patches that were applied during testing.
    """
    iast_funcs = WrapFunctonsForIAST()
    iast_funcs.testing_unpatch()


def _apply_custom_security_controls(iast_funcs: Optional[WrapFunctonsForIAST] = None):
    """Apply custom security controls from DD_IAST_SECURITY_CONTROLS_CONFIGURATION environment variable."""
    try:
        if iast_funcs is None:
            iast_funcs = WrapFunctonsForIAST()
        security_controls = get_security_controls_from_env()

        if not security_controls:
            log.debug("No custom security controls configured")
            return

        log.debug("Applying %s custom security controls", len(security_controls))

        for control in security_controls:
            try:
                _apply_security_control(iast_funcs, control)
            except Exception:
                log.warning("Failed to apply security control %s", control, exc_info=True)
        return iast_funcs
    except Exception:
        log.warning("Failed to load custom security controls", exc_info=True)


def _apply_security_control(iast_funcs: WrapFunctonsForIAST, control: SecurityControl):
    """Apply a single security control configuration.

    Args:
        control: SecurityControl object containing the configuration
    """
    # Create the appropriate wrapper function
    wrapper_func: Callable[..., object]
    if control.control_type == SC_SANITIZER:
        wrapper_func = functools.partial(create_sanitizer, control.vulnerability_types)
    elif control.control_type == SC_VALIDATOR:
        wrapper_func = functools.partial(create_validator, control.vulnerability_types, control.parameters)
    else:
        log.warning("Unknown control type: %s", control.control_type)
        return

    iast_funcs.wrap_function(
        control.module_path,
        control.method_name,
        wrapper_func,
    )
    log.debug(
        "Configured %s for %s.%s (vulnerabilities: %s)",
        control.control_type,
        control.module_path,
        control.method_name,
        [v.name for v in control.vulnerability_types],
    )
