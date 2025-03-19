import ast
import os
import textwrap
from types import ModuleType
from typing import Optional
from typing import Text
from typing import Tuple

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._ast import iastpatch
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import origin
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings.asm import config as asm_config

from .._logs import iast_ast_debug_log
from .._logs import iast_compiling_debug_log
from .visitor import AstVisitor


_VISITOR = AstVisitor()

_PREFIX = IAST.PATCH_ADDED_SYMBOL_PREFIX


log = get_logger(__name__)


def _should_iast_patch(module_name: str) -> bool:
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

    return result >= iastpatch.ALLOWED_USER_ALLOWLIST


def visit_ast(
    source_text: bytes,
    module_path: Text,
    module_name: Text = "",
) -> Optional[ast.Module]:
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
    module_name = module.__name__

    module_origin = origin(module)
    if module_origin is None:
        iast_compiling_debug_log(f"could not find the module: {module_name}")
        return "", None

    module_path = str(module_origin)
    try:
        if module_origin.stat().st_size == 0:
            # Don't patch empty files like __init__.py
            iast_compiling_debug_log(f"empty file: {module_path}")
            return "", None
    except OSError:
        iast_compiling_debug_log(f"could not find the file: {module_path}", exc_info=True)
        return "", None

    # Get the file extension, if it's dll, os, pyd, dyn, dynlib: return
    # If its pyc or pyo, change to .py and check that the file exists. If not,
    # return with warning.
    _, module_ext = os.path.splitext(module_path)

    if module_ext.lower() not in {".pyo", ".pyc", ".pyw", ".py"}:
        # Probably native or built-in module
        iast_compiling_debug_log(f"Extension not supported: {module_ext} for: {module_path}")
        return "", None

    with open(module_path, "rb") as source_file:
        try:
            source_text = source_file.read()
        except UnicodeDecodeError:
            iast_compiling_debug_log(f"Encode decode error for file: {module_path}", exc_info=True)
            return "", None
        except Exception:
            iast_compiling_debug_log(f"Unexpected read error: {module_path}", exc_info=True)
            return "", None

    if len(source_text.strip()) == 0:
        # Don't patch empty files like __init__.py
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
        iast_compiling_debug_log(f"file not ast patched: {module_path}")
        return "", None

    return module_path, new_ast
