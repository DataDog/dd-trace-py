"""IAST module patching implementation.

This module provides the core functionality for patching Python functions to enable IAST analysis.
It implements the wrapper classes and utilities needed to:
1. Wrap security-sensitive functions using wrapt
2. Manage module patching state
3. Handle forced patching when needed
4. Support testing scenarios with unpatching capabilities

The module uses wrapt's function wrapping capabilities to intercept calls to security-sensitive
functions and enable taint tracking and vulnerability detection.
"""
import functools
from typing import Callable
from typing import Optional
from typing import Set
from typing import Text

from wrapt import FunctionWrapper

from ddtrace.appsec._common_module_patches import try_unwrap
from ddtrace.appsec._common_module_patches import try_wrap_function_wrapper
from ddtrace.appsec._common_module_patches import wrap_object
from ddtrace.appsec._iast._logs import iast_instrumentation_wrapt_debug_log
from ddtrace.appsec._iast.secure_marks import SecurityControl
from ddtrace.appsec._iast.secure_marks import get_security_controls_from_env
from ddtrace.appsec._iast.secure_marks.configuration import SC_SANITIZER
from ddtrace.appsec._iast.secure_marks.configuration import SC_VALIDATOR
from ddtrace.appsec._iast.secure_marks.sanitizers import create_sanitizer
from ddtrace.appsec._iast.secure_marks.validators import create_validator
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)

MODULES_TO_UNPATCH: Set["IASTFunction"] = set()


class IASTFunction:
    """Represents a function to be patched for IAST analysis.

    This class encapsulates the information needed to patch a specific function in a module
    for IAST analysis. It handles both forced and lazy patching scenarios.

    Attributes:
        name (str): The name of the module to patch
        function (str): The name of the function to wrap
        hook (callable): The wrapper function to apply
        force (bool): Whether to force immediate patching or wait for module import
    """

    name = ""
    function = ""
    hook = ""
    force = False

    def __init__(self, name, function, hook, force=False):
        """Initialize an IASTFunction instance.

        Args:
            name (str): The name of the module to patch
            function (str): The name of the function to wrap
            hook (callable): The wrapper function to apply
            force (bool, optional): Whether to force immediate patching. Defaults to False.
        """
        self.name = name
        self.function = function
        self.hook = hook
        self.force = force

    @staticmethod
    def force_wrapper(module: Text, name: Text, wrapper: Callable):
        """Force immediate wrapping of a module's function.

        This method attempts to immediately wrap a function in a module, regardless of
        whether the module has been imported yet.

        Args:
            module (str): The module name
            name (str): The function name to wrap
            wrapper (callable): The wrapper function to apply
        """
        try:
            wrap_object(module, name, FunctionWrapper, (wrapper,))
        except (ImportError, AttributeError):
            iast_instrumentation_wrapt_debug_log(f"Module {module}.{name} not exists")

    def patch(self):
        """Apply the patch to the target module and function.

        This method handles both forced and lazy patching scenarios. If force is True,
        it attempts immediate patching. Otherwise, it sets up lazy patching.

        Returns:
            bool: True if patching was attempted
        """
        if self.force is True:
            self.force_wrapper(self.name, self.function, self.hook)
        else:
            try_wrap_function_wrapper(self.name, self.function, self.hook)
        return True

    def unpatch(self):
        """Remove the patch from the target module and function.

        This method attempts to remove any existing wrapper from the target function.
        """
        try_unwrap(self.name, self.function)

    def __repr__(self):
        """Return a string representation of the IASTFunction instance."""
        return (
            f"IASTFunction(name={self.name}, "
            f"function={self.function}, "
            f"hook={self.hook}, "
            f"force={self.force})"
        )


class WrapFunctonsForIAST:
    """Manages the collection and patching of IAST modules.

    This class maintains a set of IASTFunction instances and handles their patching
    and unpatching. It supports both normal operation and testing scenarios.

    Attributes:
        functions (Set[IASTFunction]): Set of modules to be patched
        testing (bool): Whether the instance is being used in a testing context
    """

    def __init__(self) -> None:
        """Initialize a WrapFunctonsForIAST instance."""
        self.functions: Set[IASTFunction] = set()
        self.testing: bool = asm_config._iast_is_testing

    def wrap_function(self, name, function, hook):
        """Add a function for lazy patching.

        Args:
            name (str): The module name
            function (str): The function name to wrap
            hook (callable): The wrapper function to apply
        """
        self.functions.add(IASTFunction(name, function, hook))

    def add_module_forced(self, name, function, hook):
        """Add a module for forced immediate patching.

        Args:
            name (str): The module name
            function (str): The function name to wrap
            hook (callable): The wrapper function to apply
        """
        self.functions.add(IASTFunction(name, function, hook, True))

    def patch(self):
        """Apply patches to all registered functions.

        This method attempts to patch all functions in the functions set. If in testing
        mode, it also tracks the functions for later unpatching.
        """
        for module in self.functions:
            if module.patch():
                log.debug("Wrapping %s", module)
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
                log.debug("Unwrapping %s", module)
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
