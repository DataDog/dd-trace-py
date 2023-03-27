#!/usr/bin/env python3

import ast
import codecs
import os
from typing import NamedTuple
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from pathlib import PurePath
    from types import ModuleType
    from typing import Dict
    from typing import Optional
    from typing import Tuple

from ddtrace.appsec._constants import IAST
from ddtrace.appsec.iast._ast.visitor import AstVisitor
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import origin


# Prefixes for modules where IAST patching is allowed
IAST_ALLOWLIST = ("tests.appsec.iast",)  # type: tuple[str, ...]
IAST_DENYLIST = ("ddtrace",)  # type: tuple[str, ...]


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


# We don't store every file of every package but filter commonly used extensions
SUPPORTED_EXTENSIONS = (".py", ".so", ".dll", ".pyc")


Distribution = NamedTuple("Distribution", [("name", str), ("version", str)])


def _is_python_source_file(
    path,  # type: PurePath
):
    # type: (...) -> bool
    return os.path.splitext(path.name)[-1].lower() in SUPPORTED_EXTENSIONS


def _build_package_file_mapping():
    # type: (...) -> Dict[str, Distribution]
    mapping = {}

    for ilmd_d in il_md.distributions():
        if ilmd_d is not None and ilmd_d.files is not None:
            d = Distribution(ilmd_d.metadata["name"], ilmd_d.version)
            for f in ilmd_d.files:
                if _is_python_source_file(f):
                    mapping[os.fspath(f.locate())] = d

    return mapping


_FILE_PACKAGE_MAPPING = _build_package_file_mapping()


def _should_iast_patch(module_name):
    """
    select if module_name should be patch from the longuest prefix that match in allow or deny list.
    if a prefix is in both list, deny is selected.
    """
    max_allow = max((len(prefix) for prefix in IAST_ALLOWLIST if module_name.startswith(prefix)), default=-1)
    max_deny = max((len(prefix) for prefix in IAST_DENYLIST if module_name.startswith(prefix)), default=-1)
    diff = max_allow - max_deny
    return diff > 0 or (diff == 0 and module_name not in _FILE_PACKAGE_MAPPING)


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

    return modified_ast


def astpatch_module(module):  # type: (ModuleType) -> Tuple[str, str]
    module_name = module.__name__
    module_path = origin(module)
    try:
        if os.stat(module_path).st_size == 0:
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

    new_source = visit_ast(
        source_text,
        module_path,
        module_name=module_name,
    )
    if new_source is None:
        log.debug("file not ast patched: %s", module_path)
        return "", ""

    return module_path, new_source
