#!/usr/bin/env python3

import ast
import codecs
import os
import pkgutil
from typing import Optional
from typing import Tuple

from ddtrace.appsec.iast._ast.visitor import AstVisitor
from ddtrace.internal.logger import get_logger


# Prefixes for modules where IAST patching is allowed
IAST_ALLOWLIST = ("tests.appsec.iast",)

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


def _should_iast_patch(module_name):
    return not module_name.startswith("ddtrace") and module_name.startswith(IAST_ALLOWLIST)


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


def astpatch_source(
    module_name,  # type: str
    module_path="",  # type: str
):  # type: (...) -> Tuple[str, str]

    if not module_path and not module_name:
        log.debug("astpatch_source called with no module path or name")
        return "", ""

    if not module_path:
        # Get the module path from the module name (foo.bar -> foo/bar.py)
        loader = pkgutil.get_loader(module_name)

        assert loader
        if hasattr(loader, "module_spec"):
            module_path = loader.module_spec.origin
            if not module_path:
                log.debug("astpatch_source couldn't get module spec origin for: %s", module_name)
                return "", ""
        else:
            # Enter in this else if the loader is instance of BuiltinImporter but
            # isinstance(loader, BuiltinImporter) doesn't work
            log.debug("astpatch_source couldn't get loader for: %s", module_name)
            return "", ""

    try:
        if os.stat(module_path).st_size == 0:
            # Don't patch empty files like __init__.py
            log.debug("empty file: %s", module_path)
            return "", ""
    except FileNotFoundError:
        log.debug("astpatch_source couldn't find the file: %s", module_path)
        return "", ""

    # Get the file extension, if it's dll, os, pyd, dyn, dynlib: return
    # If its pyc or pyo, change to .py and check that the file exists. If not,
    # return with warning.
    _, module_ext = os.path.splitext(module_path)

    if module_ext not in {".pyo", ".pyc", ".pyw", ".py"}:
        # Probably native or built-in module
        log.debug("extension not supported: %s for: %s", module_ext, module_path)
        return "", ""

    with open(module_path, "r", encoding=get_encoding(module_path)) as source_file:
        try:
            source_text = source_file.read()
        except UnicodeDecodeError:
            log.debug("unicode decode error for file: %s", module_path)
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
