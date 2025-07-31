import ast
import os
import sys
import textwrap
from types import ModuleType
from typing import Optional
from typing import Text
from typing import Tuple

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._ast import iastpatch
from ddtrace.appsec._iast._logs import iast_ast_debug_log
from ddtrace.appsec._iast._logs import iast_compiling_debug_log
from ddtrace.appsec._iast._logs import iast_instrumentation_ast_patching_debug_log
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import origin
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings.asm import config as asm_config

from .visitor import AstVisitor


_VISITOR = AstVisitor()

_PREFIX = IAST.PATCH_ADDED_SYMBOL_PREFIX
IAST_PATCHING_LAZY_LOADED = True

log = get_logger(__name__)


def initialize_iast_lists():
    """Initialize IAST module lists safely from Python.

    This function initializes the user allowlist and denylist for IAST module patching.
    It is critical that this initialization happens from Python rather than from C code
    during module initialization for several reasons:

    1. Python GIL (Global Interpreter Lock) Management:
       - During C module initialization, GIL handling can be problematic
       - Python operations from C during initialization may not be fully thread-safe
       - The interpreter state might not be fully ready for certain Python API calls

    2. Module State:
       - When called from Python, we ensure the module is fully initialized
       - All required Python objects and state are properly set up
       - Memory management is handled by Python's garbage collector

    3. Error Handling:
       - Python-level initialization provides better error handling
       - Exceptions can be properly caught and managed
       - Prevents potential segmentation faults or undefined behavior

    4. Thread Safety:
       - Ensures thread-safe initialization of global lists
       - Avoids race conditions during module loading
       - Provides consistent state across all threads

    The function specifically:
    1. Builds the user allowlist from _DD_IAST_PATCH_MODULES environment variable
    2. Builds the user denylist from _DD_IAST_DENY_MODULES environment variable
    3. Imports and sets the packages_distributions function for first-party package detection

    This approach is safer than C-level initialization in init_globals() which can
    lead to inconsistent state or crashes due to GIL-related issues.
    """
    # Import and set the packages_distributions function for the C extension
    try:
        if sys.version_info < (3, 10):
            import importlib_metadata as metadata
        else:
            import importlib.metadata as metadata
        result = set(metadata.packages_distributions())
        iastpatch.set_packages_distributions(result)
    except ImportError:
        # If metadata module is not available, the C extension will handle
        # first-party detection gracefully by returning False
        log.debug("Could not import metadata module for first-party detection")
    except Exception:
        log.debug("Failed to set packages in C extension", exc_info=True)

    iastpatch.build_list_from_env(IAST.PATCH_MODULES)
    iastpatch.build_list_from_env(IAST.DENY_MODULES)


def _should_iast_patch(module_name: str) -> bool:
    """Determines whether a module should be patched by IAST instrumentation.

    This function checks if a given module should be instrumented by the IAST (Interactive Application
    Security Testing) system. It uses a series of rules to make this determination, including checking
    against allowlists and denylists.

    Performance characteristics:
        - Without debug: 0.005-0.020s per call
        - With debug: 0.017-0.024s per call (1.14x-3.08x slower)
        - Standard library modules are checked fastest
        - Third-party modules take longest to check

    Args:
        module_name (str): The fully qualified name of the module to check (e.g., "os.path", "requests")

    Returns:
        bool: True if the module should be patched, False otherwise.
            Returns True when:
            - Module is in the user allowlist
            - Module is in the static allowlist
            - Module is a first-party module
            Returns False when:
            - Module is in Python's stdlib
            - Module is in the user denylist
            - Module is in the static denylist
            - Module is not found in any list

    Note:
        When asm_config._iast_debug is True, the function will log detailed information about
        why a module was allowed or denied patching.
    """
    global IAST_PATCHING_LAZY_LOADED
    if IAST_PATCHING_LAZY_LOADED:
        initialize_iast_lists()
        IAST_PATCHING_LAZY_LOADED = False
    result = False
    try:
        result = iastpatch.should_iast_patch(module_name)
        if asm_config._iast_debug:
            if result == iastpatch.DENIED_BUILTINS_DENYLIST:
                iast_ast_debug_log(f"denying {module_name}. it's in the python_stdlib")
            elif result == iastpatch.ALLOWED_USER_ALLOWLIST:
                iast_ast_debug_log(f"allowing {module_name}. it's in the USER_ALLOWLIST")
            elif result == iastpatch.ALLOWED_STATIC_ALLOWLIST:
                iast_ast_debug_log(f"allowing {module_name}. it's in the ALLOWLIST")
            elif result == iastpatch.DENIED_USER_DENYLIST:
                iast_ast_debug_log(f"denying {module_name}. it's in the USER_DENYLIST")
            elif result == iastpatch.DENIED_STATIC_DENYLIST:
                iast_ast_debug_log(f"denying {module_name}. it's in the DENYLIST")
            elif result == iastpatch.ALLOWED_FIRST_PARTY_ALLOWLIST:
                iast_ast_debug_log(f"allowing {module_name}. it's a first party module")
            elif result == iastpatch.DENIED_NOT_FOUND:
                iast_ast_debug_log(f"denying {module_name}. it's NOT in the ALLOWLIST")
    except Exception as e:
        iast_instrumentation_ast_patching_debug_log(
            f"An error occurred while attempting to patch the {module_name} module. Error: {e}"
        )
    return result >= iastpatch.ALLOWED_USER_ALLOWLIST


def visit_ast(
    source_text: bytes,
    module_path: Text,
    module_name: Text = "",
) -> Optional[ast.Module]:
    """Visits and modifies a module's AST for IAST instrumentation.

    This function parses source code into an AST (Abstract Syntax Tree), visits each node
    using the IAST visitor pattern, and applies necessary modifications for security
    instrumentation. The visitor pattern is implemented by the AstVisitor class.

    Args:
        source_text (bytes): The raw source code of the module to be parsed and modified.
        module_path (Text): The file system path to the module. Used for error reporting
            and AST node location information.
        module_name (Text, optional): The fully qualified name of the module. Used by the
            visitor for context. Defaults to empty string.

    Returns:
        Optional[ast.Module]: The modified AST if changes were made, None if:
            - No modifications were needed (ast_modified flag is False)
            - The source couldn't be parsed
            - The visitor didn't make any changes

    Note:
        - Uses the global _VISITOR instance of AstVisitor
        - Automatically fixes source locations in the modified AST
        - The visitor's ast_modified flag determines if any changes were made
        - Returns None instead of unmodified AST to optimize memory usage
    """
    parsed_ast = ast.parse(source_text, module_path)
    _VISITOR.update_location(filename=module_path, module_name=module_name)
    modified_ast = _VISITOR.visit(parsed_ast)

    if not _VISITOR.ast_modified:
        return None

    ast.fix_missing_locations(modified_ast)
    return modified_ast


_DIR_WRAPPER = textwrap.dedent(
    f"""


def {_PREFIX}dir():
    orig_dir = globals().get("{_PREFIX}orig_dir__")

    if orig_dir:
        # Use the original __dir__ method and filter the results
        results = [name for name in orig_dir() if not name.startswith("{_PREFIX}")]
    else:
        # List names from the module's __dict__ and filter out the unwanted names
        results = [
            name for name in globals()
            if not (name.startswith("{_PREFIX}") or name == "__dir__")
        ]

    return results

def {_PREFIX}set_dir_filter():
    if "__dir__" in globals():
        # Store the original __dir__ method
        globals()["{_PREFIX}orig_dir__"] = __dir__

    # Replace the module's __dir__ with the custom one
    globals()["__dir__"] = {_PREFIX}dir

{_PREFIX}set_dir_filter()

    """
).encode()


def astpatch_module(module: ModuleType) -> Tuple[str, Optional[ast.Module]]:
    """Patches a Python module's AST for IAST instrumentation.

    This function processes a Python module for IAST (Interactive Application Security Testing)
    instrumentation by modifying its Abstract Syntax Tree (AST). It handles various edge cases
    and module types while ensuring proper logging of the patching process.

    The function performs the following steps:
    1. Resolves the module's file path
    2. Validates the file (size, extension, accessibility)
    3. Reads and processes the source code
    4. Optionally adds a __dir__ wrapper to hide IAST internals
    5. Generates and returns the modified AST

    Args:
        module (ModuleType): The Python module to patch. Must be an imported module object.

    Returns:
        Tuple[str, Optional[ast.Module]]: A tuple containing:
            - str: The module's file path if successful, empty string if failed
            - Optional[ast.Module]: The modified AST if successful, None if:
                - Module file cannot be found
                - File is empty (e.g., __init__.py)
                - File extension not supported (.dll, .so, etc.)
                - File cannot be read or decoded
                - AST modification was not needed

    Note:
        - Debug logging only occurs when asm_config._iast_debug is True
        - Handles various file types (.py, .pyc, .pyo, .pyw)
        - Skips binary/native modules
        - Can be controlled via IAST.ENV_NO_DIR_PATCH environment variable to disable __dir__ wrapping
    """
    module_name = module.__name__

    module_origin = origin(module)
    if module_origin is None:
        if asm_config._iast_debug:
            iast_compiling_debug_log(f"could not find the module: {module_name}")
        return "", None

    module_path = str(module_origin)
    try:
        if module_origin.stat().st_size == 0:
            # Don't patch empty files like __init__.py
            if asm_config._iast_debug:
                iast_compiling_debug_log(f"empty file: {module_path}")
            return "", None
    except OSError:
        if asm_config._iast_debug:
            iast_compiling_debug_log(f"could not find the file: {module_path}", exc_info=True)
        return "", None

    # Get the file extension, if it's dll, os, pyd, dyn, dynlib: return
    # If its pyc or pyo, change to .py and check that the file exists. If not,
    # return with warning.
    _, module_ext = os.path.splitext(module_path)

    if module_ext.lower() not in {".pyo", ".pyc", ".pyw", ".py"}:
        # Probably native or built-in module
        if asm_config._iast_debug:
            iast_compiling_debug_log(f"Extension not supported: {module_ext} for: {module_path}")
        return "", None

    with open(module_path, "rb") as source_file:
        try:
            source_text = source_file.read()
        except UnicodeDecodeError:
            if asm_config._iast_debug:
                iast_compiling_debug_log(f"Encode decode error for file: {module_path}", exc_info=True)
            return "", None
        except Exception:
            if asm_config._iast_debug:
                iast_compiling_debug_log(f"Unexpected read error: {module_path}", exc_info=True)
            return "", None

    if len(source_text.strip()) == 0:
        # Don't patch empty files like __init__.py
        if asm_config._iast_debug:
            iast_compiling_debug_log(f"Empty file: {module_path}")
        return "", None

    if not asbool(os.environ.get(IAST.ENV_NO_DIR_PATCH, "false")):
        # Add the dir filter so __ddtrace stuff is not returned by dir(module)
        source_text += _DIR_WRAPPER

    new_ast = visit_ast(
        source_text,
        module_path,
        module_name=module_name,
    )
    if new_ast is None:
        if asm_config._iast_debug:
            iast_compiling_debug_log(f"file not ast patched: {module_path}")
        return "", None

    return module_path, new_ast
