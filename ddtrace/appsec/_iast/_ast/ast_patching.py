#!/usr/bin/env python3

import ast
import codecs
import os
import re
from sys import builtin_module_names
from types import ModuleType
from typing import TYPE_CHECKING  # noqa:F401
from typing import Tuple


if TYPE_CHECKING:
    from typing import Optional  # noqa:F401

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._python_info.stdlib import _stdlib_for_python_version
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import origin

from .visitor import AstVisitor


# Prefixes for modules where IAST patching is allowed
IAST_ALLOWLIST = ("tests.appsec.iast",)  # type: tuple[str, ...]
IAST_DENYLIST = (
    "ddtrace",
    "pkg_resources",
    "encodings",  # this package is used to load encodings when a module is imported, propagation is not needed
    "inspect",  # this package is used to get the stack frames, propagation is not needed
    "pycparser",  # this package is called when a module is imported, propagation is not needed
    "Crypto",  # This module is patched by the IAST patch methods, propagation is not needed
    "api_pb2",  # Patching crashes with these auto-generated modules, propagation is not needed
    "api_pb2_grpc",  # ditto
    "unittest.mock",
    "pytest",  # Testing framework
    "freezegun",  # Testing utilities for time manipulation
    "sklearn",  # Machine learning library
)  # type: tuple[str, ...]


if IAST.PATCH_MODULES in os.environ:
    IAST_ALLOWLIST += tuple(os.environ[IAST.PATCH_MODULES].split(IAST.SEP_MODULES))

if IAST.DENY_MODULES in os.environ:
    IAST_DENYLIST += tuple(os.environ[IAST.DENY_MODULES].split(IAST.SEP_MODULES))


ENCODING = ""

log = get_logger(__name__)


def get_encoding(module_path):  # type: (str) -> str
    """
    First tries to detect the encoding for the file,
    otherwise, returns global encoding default
    """
    global ENCODING
    if not ENCODING:
        try:
            ENCODING = codecs.lookup("utf-8-sig").name
        except LookupError:
            ENCODING = codecs.lookup("utf-8").name
    return ENCODING


try:
    import importlib.metadata as il_md
except ImportError:
    import importlib_metadata as il_md  # type: ignore[no-redef]


def _build_installed_package_names_list():  # type: (...) -> set[str]
    return {
        ilmd_d.metadata["name"] for ilmd_d in il_md.distributions() if ilmd_d is not None and ilmd_d.files is not None
    }


_NOT_PATCH_MODULE_NAMES = (
    _build_installed_package_names_list() | _stdlib_for_python_version() | set(builtin_module_names)
)


def _in_python_stdlib_or_third_party(module_name):  # type: (str) -> bool
    return module_name.split(".")[0].lower() in [x.lower() for x in _NOT_PATCH_MODULE_NAMES]


def _should_iast_patch(module_name):  # type: (str) -> bool
    """
    select if module_name should be patch from the longuest prefix that match in allow or deny list.
    if a prefix is in both list, deny is selected.
    """
    # TODO: A better solution would be to migrate the original algorithm to C++:
    # max_allow = max((len(prefix) for prefix in IAST_ALLOWLIST if module_name.startswith(prefix)), default=-1)
    # max_deny = max((len(prefix) for prefix in IAST_DENYLIST if module_name.startswith(prefix)), default=-1)
    # diff = max_allow - max_deny
    # return diff > 0 or (diff == 0 and not _in_python_stdlib_or_third_party(module_name))
    if module_name.startswith(IAST_ALLOWLIST):
        return True
    if module_name.startswith(IAST_DENYLIST):
        return False
    return not _in_python_stdlib_or_third_party(module_name)


def visit_ast(
    source_text,  # type: str
    module_path,  # type: str
    module_name="",  # type: str
):  # type: (...) -> Optional[str]
    parsed_ast = ast.parse(source_text, module_path)

    visitor = AstVisitor(
        filename=module_path,
        module_name=module_name,
    )
    modified_ast = visitor.visit(parsed_ast)

    if not visitor.ast_modified:
        return None

    ast.fix_missing_locations(modified_ast)
    return modified_ast


_FLASK_INSTANCE_REGEXP = re.compile(r"(\S*)\s*=.*Flask\(.*")


def _remove_flask_run(text):  # type (str) -> str
    """
    Find and remove flask app.run() call. This is used for patching
    the app.py file and exec'ing to replace the module without creating
    a new instance.
    """
    flask_instance_name = re.search(_FLASK_INSTANCE_REGEXP, text)
    if not flask_instance_name:
        return text
    groups = flask_instance_name.groups()
    if not groups:
        return text

    instance_name = groups[-1]
    new_text = re.sub(instance_name + r"\.run\(.*\)", "pass", text)
    return new_text


def astpatch_module(module: ModuleType, remove_flask_run: bool = False) -> Tuple[str, str]:
    module_name = module.__name__

    module_origin = origin(module)
    if module_origin is None:
        log.debug("astpatch_source couldn't find the module: %s", module_name)
        return "", ""

    module_path = str(module_origin)
    try:
        if module_origin.stat().st_size == 0:
            # Don't patch empty files like __init__.py
            log.debug("empty file: %s", module_path)
            return "", ""
    except OSError:
        log.debug("astpatch_source couldn't find the file: %s", module_path, exc_info=True)
        return "", ""

    # Get the file extension, if it's dll, os, pyd, dyn, dynlib: return
    # If its pyc or pyo, change to .py and check that the file exists. If not,
    # return with warning.
    _, module_ext = os.path.splitext(module_path)

    if module_ext.lower() not in {".pyo", ".pyc", ".pyw", ".py"}:
        # Probably native or built-in module
        log.debug("extension not supported: %s for: %s", module_ext, module_path)
        return "", ""

    with open(module_path, "r", encoding=get_encoding(module_path)) as source_file:
        try:
            source_text = source_file.read()
        except UnicodeDecodeError:
            log.debug("unicode decode error for file: %s", module_path, exc_info=True)
            return "", ""

    if len(source_text.strip()) == 0:
        # Don't patch empty files like __init__.py
        log.debug("empty file: %s", module_path)
        return "", ""

    if remove_flask_run:
        source_text = _remove_flask_run(source_text)

    new_source = visit_ast(
        source_text,
        module_path,
        module_name=module_name,
    )
    if new_source is None:
        log.debug("file not ast patched: %s", module_path)
        return "", ""

    return module_path, new_source
